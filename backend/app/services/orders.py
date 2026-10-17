import json
import os
import time
import uuid
from datetime import datetime, timezone

import pandas as pd
from app.database.db import connection, json_value, row_dict
from app.ml.training import ROOT_FEATURES

STAGES = ["Order Management", "Payment", "Inventory", "Delivery", "Notification"]
CONFIDENCE_THRESHOLD = float(os.getenv("ORDERFLOW_CONFIDENCE_THRESHOLD", "0.48"))


def now():
    return datetime.now(timezone.utc).isoformat()


def _audit(db, action, order_id=None, actor="system", details=None):
    db.execute("INSERT INTO audit(order_id,action,actor,details,created_at) VALUES(?,?,?,?,?)",
               (order_id, action, actor, json.dumps(details or {}), now()))


def _event(db, order_id, stage, event_type, message, response_code=None, response_ms=None, details=None):
    db.execute("""INSERT INTO events(order_id,stage,event_type,message,created_at,response_code,response_ms,details)
                  VALUES(?,?,?,?,?,?,?,?)""",
               (order_id, stage, event_type, message, now(), response_code, response_ms, json.dumps(details or {})))
    if response_code is not None:
        db.execute("""INSERT INTO api_logs(order_id,module,method,response_code,response_ms,error_category,created_at)
                      VALUES(?,?,?,?,?,?,?)""",
                   (order_id, stage, "POST", response_code, response_ms,
                    (details or {}).get("error_category"), now()))


def _action(db, order_id, action, risk, requires_approval, evidence):
    action_id = f"act_{uuid.uuid4().hex[:10]}"
    status = "PENDING_APPROVAL" if requires_approval else "RECOMMENDED"
    db.execute("""INSERT INTO actions(id,order_id,action,risk,requires_approval,status,evidence,created_at)
                  VALUES(?,?,?,?,?,?,?,?)""",
               (action_id, order_id, action, risk, int(requires_approval), status, json.dumps(evidence), now()))
    _audit(db, "recovery_recommended", order_id, details={"action_id": action_id, "risk": risk})
    return action_id


def _fault_profile(fault, module):
    profile = {
        "503": (503, "server", "Service returned HTTP 503 Service Unavailable.", 1740),
        "500": (500, "server", "Service returned HTTP 500 Internal Server Error.", 1480),
        "504": (504, "timeout", "Gateway timed out with HTTP 504.", 2800),
        "429": (429, "rate_limit", "Service rate-limited the request (HTTP 429).", 220),
        "auth": (401, "auth", "Authentication failed; request was not authorized.", 110),
        "malformed": (502, "malformed", "The service returned a malformed response.", 520),
        "invalid_address": (422, "validation", "Address validation rejected the delivery request.", 130),
        "payment_rejected": (402, "business_rejection", "Payment was rejected by the simulated payment service.", 240),
        "inventory_unavailable": (409, "business_rejection", "Requested quantity is unavailable in inventory.", 190),
        "queue_backlog": (504, "timeout", "Queue backlog exceeded the stage service timeout.", 2100),
        "database_bottleneck": (503, "database", "Database latency exceeded the service budget.", 1600),
        "recovery_fails": (503, "server", "Service remains unavailable after the retry limit.", 1900),
    }
    return profile.get(fault, (500, "server", f"Unexpected error in {module}.", 900))


def create_order(payload, process=True, fault=None):
    order_id = f"ord_{uuid.uuid4().hex[:8].upper()}"
    idempotency_key = payload.get("idempotency_key") or f"order:{order_id}"
    created = now()
    order = {"id": order_id, "customer_ref": payload.get("customer_ref") or f"C-{uuid.uuid4().hex[:6].upper()}",
             "product": payload.get("product", "Wireless Headphones"), "quantity": int(payload.get("quantity", 1)),
             "created_at": created, "stage": "Order Management", "status": "CREATED", "elapsed_ms": 0,
             "retries": 0, "failure_category": None, "fault": fault, "fault_module": payload.get("fault_module"),
             "recovered": 0}
    with connection() as db:
        inserted = db.execute("""INSERT OR IGNORE INTO orders(id,customer_ref,product,quantity,created_at,stage,status,elapsed_ms,retries,
                      failure_category,fault,fault_module,recovered,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (order["id"], order["customer_ref"], order["product"], order["quantity"], created, order["stage"],
                    order["status"], 0, 0, None, fault, order["fault_module"], 0, idempotency_key))
        if inserted.rowcount == 0:
            existing = db.execute("SELECT id FROM orders WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if not existing:
                raise RuntimeError("Order idempotency conflict did not resolve to an existing order.")
            return get_order(existing["id"], db=db)
        _event(db, order_id, STAGES[0], "INFO", "Order accepted by Order Management.", 200, 42,
               {"idempotency_key": f"order:{order_id}", "retry_safe": True})
        _audit(db, "order_created", order_id, details={"product": order["product"], "quantity": order["quantity"]})
    if process:
        process_order(order_id, fault=fault, fault_module=payload.get("fault_module"))
    return get_order(order_id)


def process_order(order_id, fault=None, fault_module=None, realtime=False):
    with connection() as db:
        order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not order:
            return None
        if order["status"] in ("COMPLETED", "MANUAL_REVIEW_REQUIRED"):
            return row_dict(order)
        fault = fault if fault is not None else order["fault"]
        target = fault_module or order["fault_module"] or {"invalid_address": "Delivery", "payment_rejected": "Payment",
                                  "inventory_unavailable": "Inventory", "queue_backlog": "Delivery",
                                  "database_bottleneck": "Inventory"}.get(fault, "Inventory")
        elapsed = 0
        retries = 0
        recovered = False
        terminal = False
        failure_category = None
        setting = db.execute("SELECT value FROM settings WHERE key='retry_limit'").fetchone()
        retry_limit = int(json_value(setting["value"])) if setting else 3
        speed_setting = db.execute("SELECT value FROM settings WHERE key='simulation_speed'").fetchone()
        simulation_speed = int(json_value(speed_setting["value"])) if speed_setting else 1
        limits_row = db.execute("SELECT value FROM settings WHERE key='stage_limits_ms'").fetchone()
        stage_limits = json_value(limits_row["value"]) if limits_row else {}
        delay_detected = False
        pipeline_stages = STAGES if fault and target == STAGES[0] else STAGES[1:]
        for stage in pipeline_stages:
            if realtime:
                time.sleep(0.55 / simulation_speed)
            db.execute("UPDATE orders SET stage=?,status='PROCESSING' WHERE id=?", (stage, order_id))
            if fault and stage == target:
                code, category, message, latency = _fault_profile(fault, stage)
                elapsed += latency
                details = {"error_category": category, "failed_module": stage, "retry_safe": code in (500, 503, 504),
                           "db_latency_ms": 1050 if fault == "database_bottleneck" else 35,
                           "queue_length": 96 if fault == "queue_backlog" else 9,
                           "recent_error_frequency": 6 if fault in ("database_bottleneck", "queue_backlog") else 2,
                           "affected_orders": 7 if fault == "database_bottleneck" else 1,
                           "related_module_failures": 3 if fault == "database_bottleneck" else 0,
                           "recent_deployment": 0, "ground_truth_scenario": fault}
                if fault == "unfamiliar":
                    details.update({"error_category": "unfamiliar", "http_status": 418,
                                    "message_category": "unclassified"})
                    code = 418
                if fault == "database_bottleneck":
                    details["error_category"] = "database"
                _event(db, order_id, stage, "ERROR", message, code, latency, details)
                failure_category = "API_ERROR" if code >= 500 or code in (401, 429, 502) else (
                    "REJECTION" if code in (402, 409, 422) else "API_ERROR")
                if fault == "429":
                    _action(db, order_id, "Throttle requests and retry after the server's Retry-After window.",
                            "LOW", False, {"http_status": code, "retry_after_seconds": 30})
                    retries = 1
                    recovered = True
                    _event(db, order_id, stage, "RECOVERY", "Throttled and retried once; simulated response succeeded.",
                           200, 360, {"retry": retries, "safe_to_repeat": True, "retry_after_seconds": 30})
                    db.commit()
                    continue
                if code in (500, 503, 504) and fault != "recovery_fails":
                    retries = min(1, retry_limit)
                    retry_latency = 410
                    elapsed += retry_latency
                    _event(db, order_id, stage, "RECOVERY", "Transient failure retried once with an idempotency key.",
                           200, retry_latency, {"retry": retries, "safe_to_repeat": True,
                                      "idempotency_key": f"{order_id}:{stage}:retry-1"})
                    wait_ms = 85000 if fault == "queue_backlog" else 76000 if fault == "database_bottleneck" else 0
                    if wait_ms:
                        elapsed += wait_ms
                        delay_detected = wait_ms > int(stage_limits.get(stage, 5000))
                        _event(db, order_id, stage, "DELAY", "Queue/database wait exceeded the configured stage SLA.",
                               200, wait_ms, {"queue_length": 96 if fault == "queue_backlog" else 9,
                                              "sla_limit_ms": stage_limits.get(stage, 5000),
                                              "delay_detected": delay_detected})
                    recovered = True
                    db.commit()
                    continue
                if code in (401, 403):
                    _action(db, order_id, "Inspect authorized credentials and token refresh configuration.",
                            "HIGH", True, {"http_status": code})
                elif code in (402, 409):
                    _action(db, order_id, "Keep the business rejection; request operator/customer review.",
                            "HIGH", True, {"http_status": code, "must_not_bypass": True})
                elif code == 422:
                    _action(db, order_id, "Ask the operator to correct the delivery address, then revalidate.",
                            "MEDIUM", True, {"http_status": code, "must_revalidate": True})
                elif fault == "recovery_fails":
                    retries = retry_limit
                    _action(db, order_id, "Stop retries and escalate to manual review.",
                            "HIGH", True, {"http_status": code, "retry_limit": 3})
                else:
                    _action(db, order_id, "Inspect service health and logs; keep the order in manual review.",
                            "MEDIUM", True, {"http_status": code})
                db.commit()
                terminal = True
                for skipped in STAGES[STAGES.index(stage) + 1:]:
                    _event(db, order_id, skipped, "SKIPPED",
                           f"{skipped} was not called because {stage} requires review.")
                break
            duration = {"Payment": 340, "Inventory": 280, "Delivery": 480, "Notification": 120}[stage]
            elapsed += duration
            if duration > int(stage_limits.get(stage, 5000)):
                delay_detected = True
                _event(db, order_id, stage, "DELAY", f"Stage processing exceeded the {stage} SLA.",
                       200, duration, {"sla_limit_ms": stage_limits.get(stage, 5000)})
            _event(db, order_id, stage, "INFO", f"{stage} completed successfully.", 200, duration,
                   {"idempotency_key": f"{order_id}:{stage}", "retry_safe": True})
            db.commit()
        if terminal:
            status = "MANUAL_REVIEW_REQUIRED" if fault in ("recovery_fails", "payment_rejected",
                                                            "inventory_unavailable", "auth") else "FAILED"
        else:
            status = "COMPLETED"
            if recovered:
                _event(db, order_id, "Notification", "INFO", "Order completed after a safe recovery.", 200, 35,
                       {"recovered": True})
        if not terminal and delay_detected:
            status = "DELAYED"
        db.execute("""UPDATE orders SET status=?,elapsed_ms=?,retries=?,failure_category=?,recovered=?,
                      fault=? WHERE id=?""",
                   (status, elapsed, retries, failure_category, int(recovered), fault, order_id))
        _event(db, order_id, order["stage"], "ORDER_STATUS", f"Order processing finished with status {status}.",
               details={"status": status, "elapsed_ms": elapsed, "retries": retries})
        _audit(db, "order_processing_finished", order_id,
               details={"status": status, "retries": retries, "recovered": recovered})
        db.commit()
        return get_order(order_id, db=db)


def list_orders(search=None, status=None, limit=100):
    query, params = "SELECT * FROM orders", []
    conditions = []
    if status:
        conditions.append("status=?")
        params.append(status)
    if search:
        conditions.append("(id LIKE ? OR product LIKE ? OR customer_ref LIKE ?)")
        params.extend([f"%{search}%"] * 3)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(min(max(int(limit), 1), 500))
    with connection() as db:
        return [row_dict(r) for r in db.execute(query, params).fetchall()]


def get_order(order_id, db=None):
    if db is not None:
        return row_dict(db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone())
    with connection() as conn:
        return row_dict(conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone())


def get_events(order_id):
    with connection() as db:
        rows = db.execute("SELECT * FROM events WHERE order_id=? ORDER BY id", (order_id,)).fetchall()
        return [{**row_dict(r), "details": json_value(r["details"])} for r in rows]


def analyze_order(order_id, root_model, delay_model):
    order = get_order(order_id)
    if not order:
        return None
    events = get_events(order_id)
    failures = [e for e in events if e["event_type"] == "ERROR"]
    failure = failures[-1] if failures else None
    if failure:
        d = failure["details"]
        sample = {name: d.get(name, 0) for name in ROOT_FEATURES}
        sample.update({"failed_module": failure["stage"], "http_status": failure["response_code"] or 0,
                       "response_ms": failure["response_ms"] or 0, "error_category": d.get("error_category", "unknown")})
        prediction = str(root_model.predict(pd.DataFrame([sample]))[0])
        probability = float(max(root_model.predict_proba(pd.DataFrame([sample]))[0]))
        if d.get("error_category") == "unfamiliar" or probability < CONFIDENCE_THRESHOLD:
            prediction = "Unknown — further investigation required"
        rule = {"detected": True, "module": failure["stage"], "http_status": failure["response_code"],
                "category": order["failure_category"], "matched_rule": f"HTTP {failure['response_code']} at {failure['stage']}"}
        evidence = [f"Observed HTTP {failure['response_code']} from {failure['stage']}.",
                    f"Response time: {failure['response_ms']} ms.",
                    f"Error category: {d.get('error_category', 'unknown')}.",
                    f"Database latency: {d.get('db_latency_ms', 0)} ms; queue length: {d.get('queue_length', 0)}."]
        if d.get("related_module_failures", 0):
            evidence.append(f"{d['related_module_failures']} related module failures and {d.get('affected_orders', 1)} affected orders are reported.")
        explanation = f"The {failure['stage']} stage returned HTTP {failure['response_code']}. "
        if prediction.startswith("Unknown"):
            explanation += "Current evidence is insufficient to support a specific root cause."
        else:
            explanation += f"The local classifier favors {prediction.lower()} ({probability:.0%} model confidence); this is a hypothesis, not confirmed causation."
    else:
        prediction, probability = "No observed failure", 1.0
        rule = {"detected": False, "module": None, "http_status": None, "category": None, "matched_rule": "No failure event"}
        evidence = ["No API error or rejection event was recorded for this order."]
        explanation = "The order has no recorded failure; ordinary rule-based monitoring is sufficient here."
    delay_sample = {
        "elapsed_ms": order["elapsed_ms"], "historical_stage_ms": failure["response_ms"] if failure else order["elapsed_ms"] // 4,
        "queue_length": failure["details"].get("queue_length", 0) if failure else 4,
        "api_latency_ms": failure["response_ms"] if failure else 80, "retry_count": order["retries"],
        "preceding_failures": len(failures),
    }
    delay_prob = float(delay_model.predict_proba(pd.DataFrame([delay_sample]))[0][1])
    delay_prediction = {"likely_breach": delay_prob >= .5, "probability": round(delay_prob, 4),
                       "threshold": .5, "features": delay_sample,
                       "evidence": ["Current elapsed time, observed stage latency, queue depth, retries, and preceding failures are model inputs."]}
    recovery = "Do not bypass payment, inventory, authentication, or validation controls."
    if failure:
        code = failure["response_code"]
        recovery = ("Respect Retry-After and throttle." if code == 429 else
                    "Retry once with an idempotency key." if code in (500, 503, 504) and order["recovered"] else
                    "Stop and require authorized operator review.")
    result = {"order": order, "rule_based": rule, "root_cause": {
        "prediction": prediction, "confidence": round(probability, 4), "threshold": CONFIDENCE_THRESHOLD,
        "is_confirmed": False, "supporting_evidence": evidence, "alternative_hypotheses": [],
        "explanation": explanation, "model": "local scikit-learn RandomForest"},
        "delay": delay_prediction, "recommended_action": recovery, "events": events}
    with connection() as db:
        db.execute("""INSERT INTO root_cause_predictions(order_id,prediction,confidence,evidence,created_at)
                      VALUES(?,?,?,?,?)""", (order_id, prediction, round(probability, 4), json.dumps(evidence), now()))
        db.execute("""INSERT INTO delay_predictions(order_id,likely_breach,probability,features,created_at)
                      VALUES(?,?,?,?,?)""", (order_id, int(delay_prediction["likely_breach"]),
                                             round(delay_prob, 4), json.dumps(delay_sample), now()))
        _audit(db, "ai_analysis_run", order_id, details={"root_cause": prediction,
                                                         "confidence": round(probability, 4),
                                                         "delay_probability": round(delay_prob, 4)})
    return result


def get_metrics():
    with connection() as db:
        total = db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        statuses = {r["status"]: r["n"] for r in db.execute("SELECT status,COUNT(*) n FROM orders GROUP BY status")}
        module_failures = {r["stage"]: r["n"] for r in db.execute(
            "SELECT stage,COUNT(*) n FROM events WHERE event_type='ERROR' GROUP BY stage")}
        recovered = db.execute("SELECT COUNT(*) FROM orders WHERE recovered=1").fetchone()[0]
        active = db.execute("""SELECT COUNT(DISTINCT e.order_id) FROM events e JOIN orders o ON o.id=e.order_id
                               WHERE e.event_type='ERROR' AND o.status!='COMPLETED'""").fetchone()[0]
        average = db.execute("SELECT COALESCE(AVG(elapsed_ms),0) FROM orders").fetchone()[0]
    return {"total_orders": total, "completed": statuses.get("COMPLETED", 0), "failed": statuses.get("FAILED", 0),
            "delayed": statuses.get("DELAYED", 0), "manual_review": statuses.get("MANUAL_REVIEW_REQUIRED", 0),
            "recovered": recovered, "active_incidents": active, "sla_breach_rate": round(
                sum(statuses.get(k, 0) for k in ("DELAYED", "FAILED", "MANUAL_REVIEW_REQUIRED")) / total, 3) if total else 0,
            "average_elapsed_ms": round(average), "failures_by_module": module_failures,
            "status_distribution": statuses}


def get_incidents():
    with connection() as db:
        rows = db.execute("""SELECT e.order_id,e.stage,e.response_code,e.message,e.created_at,e.details,o.status
                             FROM events e JOIN orders o ON o.id=e.order_id
                             WHERE e.event_type='ERROR' ORDER BY e.created_at DESC""").fetchall()
    groups = {}
    for row in rows:
        details = json_value(row["details"])
        cause_hint = ("Database latency elevated across related services" if details.get("error_category") == "database"
                      else "Queue depth is elevated" if details.get("queue_length", 0) > 65
                      else "Shared service dependency may be degraded" if details.get("affected_orders", 1) > 1
                      else "No shared cause confirmed; investigate this order in isolation")
        time_window = int(datetime.fromisoformat(row["created_at"]).timestamp() // 900)
        category = details.get("error_category", "unknown")
        key = (category, time_window)
        group = groups.setdefault(key, {"id": f"inc-{len(groups)+1:03}", "category": category, "module": row["stage"],
                                        "orders": [], "events": [], "severity": "MEDIUM",
                                        "probable_shared_cause": cause_hint,
                                        "causation_confirmed": False, "status": "INVESTIGATING"})
        if row["stage"] != group["module"]:
            group["module"] = "Multiple services"
        if len(group["orders"]) == 0 and cause_hint != "No shared cause confirmed; investigate this order in isolation":
            group["probable_shared_cause"] = cause_hint
        elif cause_hint.startswith("Database latency") or cause_hint.startswith("Queue depth"):
            group["probable_shared_cause"] = cause_hint
        group["orders"].append(row["order_id"])
        group["events"].append({"order_id": row["order_id"], "http_status": row["response_code"],
                                "message": row["message"], "created_at": row["created_at"],
                                "telemetry": {"database_latency_ms": details.get("db_latency_ms", 0),
                                              "queue_length": details.get("queue_length", 0),
                                              "affected_orders": details.get("affected_orders", 1),
                                              "related_module_failures": details.get("related_module_failures", 0)}})
        if row["response_code"] in (500, 503, 504) or len(group["orders"]) > 2:
            group["severity"] = "HIGH"
    result = list(groups.values())
    with connection() as db:
        for group in result:
            db.execute("""INSERT OR REPLACE INTO incidents(id,category,payload,updated_at) VALUES(?,?,?,?)""",
                       (group["id"], group["category"], json.dumps(group), now()))
    return result


def actions_list():
    with connection() as db:
        rows = db.execute("SELECT * FROM actions ORDER BY created_at DESC").fetchall()
    return [{**row_dict(r), "requires_approval": bool(r["requires_approval"]),
             "evidence": json_value(r["evidence"])} for r in rows]


def resolve_action(action_id, approve, actor="operator"):
    with connection() as db:
        action = db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        if not action:
            return None
        if action["status"] not in ("PENDING_APPROVAL", "RECOMMENDED"):
            return {"error": f"Action is already {action['status']}."}
        if action["requires_approval"] and not approve:
            return {"error": "Operator approval is required before this action can run."}
        order = get_order(action["order_id"], db=db)
        if action["requires_approval"] and order and order["failure_category"] == "REJECTION":
            # Approval records a review decision; it can never override hard business constraints.
            result, status = "Reviewed; original payment/inventory rejection remains enforced.", "REVIEWED"
        else:
            result, status = "Simulated safe recovery action completed; no external service was called.", "COMPLETED"
        db.execute("UPDATE actions SET status=?,result=? WHERE id=?", (status, result, action_id))
        _audit(db, "recovery_action_reviewed" if action["requires_approval"] else "recovery_action_run",
               action["order_id"], actor, {"action_id": action_id, "approved": approve, "result": result})
        return {"id": action_id, "status": status, "result": result}


def audit_history(limit=100):
    with connection() as db:
        rows = db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (min(limit, 500),)).fetchall()
    return [{**row_dict(r), "details": json_value(r["details"])} for r in rows]


def get_settings():
    defaults = {"stage_limits_ms": {"Order Management": 5000, "Payment": 5000, "Inventory": 5000,
                                    "Delivery": 10000, "Notification": 3000},
                "retry_limit": 3, "simulation_speed": 1, "fault_injection_enabled": True}
    with connection() as db:
        for key, value in defaults.items():
            db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, json.dumps(value)))
        return {r["key"]: json_value(r["value"]) for r in db.execute("SELECT * FROM settings")}


def save_settings(settings):
    current = get_settings()
    with connection() as db:
        current.update(settings)
        if not 1 <= int(current["retry_limit"]) <= 5:
            raise ValueError("retry_limit must be between 1 and 5.")
        if not 1 <= int(current["simulation_speed"]) <= 10:
            raise ValueError("simulation_speed must be between 1 and 10.")
        limits = current["stage_limits_ms"]
        expected_stages = set(STAGES)
        if set(limits) != expected_stages or any(not 100 <= int(value) <= 600000 for value in limits.values()):
            raise ValueError("stage_limits_ms must contain all five stages with values from 100 to 600000.")
        for key, value in current.items():
            db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, json.dumps(value)))
        _audit(db, "settings_updated", actor="operator", details=settings)
    return get_settings()


def clear_demo_data():
    with connection() as db:
        db.execute("DELETE FROM audit")
        db.execute("DELETE FROM actions")
        db.execute("DELETE FROM events")
        db.execute("DELETE FROM orders")
        _audit(db, "demo_data_cleared")
