import os
import asyncio
import json
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from app.database.db import connection
from app.schemas.api import ActionDecision, AnalyzeInput, Explanation, FaultInput, OrderInput, SettingsInput
from app.schemas.store import CheckoutInput, RestockInput
from app.services import orders
from app.services import store

router = APIRouter(prefix="/api")
models = {"root": None, "delay": None, "metrics": None}
SCENARIOS = [
    ("Temporary HTTP 503 in Inventory", "503", "Inventory"),
    ("Database bottleneck across services", "database_bottleneck", "Payment"),
    ("Queue backlog and predicted SLA breach", "queue_backlog", "Delivery"),
    ("HTTP 429 rate limit", "429", "Inventory"),
    ("Authentication failure", "auth", "Payment"),
    ("Invalid delivery address", "invalid_address", "Delivery"),
    ("Payment rejection - do not bypass", "payment_rejected", "Payment"),
    ("Successful order", None, None),
    ("Unfamiliar incident - should remain uncertain", "unfamiliar", "Notification"),
    ("Recovery exhausted - manual escalation", "recovery_fails", "Inventory"),
]


@router.get("/health")
def health():
    return {"status": "ok", "models_loaded": models["root"] is not None, "mode": "local-ml"}


@router.get("/stream")
async def stream_events(request: Request):
    last_event_id = request.headers.get("last-event-id")
    try:
        cursor = int(last_event_id) if last_event_id else None
        if cursor is not None and cursor < 0:
            raise ValueError
    except ValueError:
        raise HTTPException(400, "Last-Event-ID must be a non-negative integer.")
    if cursor is None:
        with connection() as db:
            cursor = db.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]

    async def event_stream():
        nonlocal cursor
        yield f"data: {json.dumps({'type': 'connected', 'at': datetime.now(timezone.utc).isoformat()})}\n\n"
        while not await request.is_disconnected():
            with connection() as db:
                rows = db.execute("""SELECT e.*,o.status,o.stage AS current_stage,o.elapsed_ms,o.retries,
                                     o.failure_category,o.product
                                     FROM events e JOIN orders o ON o.id=e.order_id
                                     WHERE e.id>? ORDER BY e.id LIMIT 100""", (cursor,)).fetchall()
                for row in rows:
                    cursor = row["id"]
                    payload = {"type": "order_event", "event_id": row["id"], "order_id": row["order_id"],
                               "event_type": row["event_type"], "stage": row["stage"], "message": row["message"],
                               "response_code": row["response_code"], "created_at": row["created_at"],
                               "order": {"id": row["order_id"], "status": row["status"],
                                         "stage": row["current_stage"], "elapsed_ms": row["elapsed_ms"],
                                         "retries": row["retries"], "failure_category": row["failure_category"],
                                         "product": row["product"]}}
                    yield f"id: {cursor}\ndata: {json.dumps(payload)}\n\n"
            yield ": keep-alive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/store/products")
def store_products():
    return store.list_products()


@router.post("/store/products/{sku}/restock")
def restock_store_product(sku: str, payload: RestockInput):
    result = store.restock_product(sku, payload.quantity, payload.reason)
    if not result:
        raise HTTPException(404, "Product not found")
    return result


@router.get("/store/orders")
def store_orders(limit: int = Query(20, ge=1, le=100)):
    return store.list_store_orders(limit)


@router.post("/store/checkout", status_code=202)
def store_checkout(payload: CheckoutInput, background_tasks: BackgroundTasks):
    if payload.demo_fault != "none" and not orders.get_settings()["fault_injection_enabled"]:
        raise HTTPException(403, "Demo fault injection is disabled in Settings.")
    try:
        result = store.checkout(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not result["duplicate"]:
        background_tasks.add_task(store.finish_checkout, result["order"]["id"])
    return result


@router.get("/store/orders/{order_id}")
def store_order_status(order_id: str):
    result = store.get_store_order(order_id)
    if not result:
        raise HTTPException(404, "Store order not found")
    return {"store_order": result, "order": orders.get_order(order_id),
            "events": orders.get_events(order_id)}


@router.get("/dashboard")
def dashboard():
    return orders.get_metrics()


@router.get("/orders")
def list_orders(search: str | None = None, status: str | None = None,
                limit: int = Query(100, ge=1, le=500)):
    return orders.list_orders(search, status, limit)


@router.post("/orders", status_code=201)
def create_order(payload: OrderInput):
    if payload.fault and not orders.get_settings()["fault_injection_enabled"]:
        raise HTTPException(403, "Fault injection is disabled in Settings.")
    return orders.create_order(payload.model_dump(exclude={"fault"}),
                               process=payload.process, fault=payload.fault)


@router.post("/orders/{order_id}/process")
def process_order(order_id: str):
    result = orders.process_order(order_id)
    if not result:
        raise HTTPException(404, "Order not found")
    return result


@router.post("/orders/{order_id}/inject")
def inject_fault(order_id: str, fault: FaultInput):
    if not orders.get_settings()["fault_injection_enabled"]:
        raise HTTPException(403, "Fault injection is disabled in Settings.")
    if not orders.get_order(order_id):
        raise HTTPException(404, "Order not found")
    with connection() as db:
        db.execute("UPDATE orders SET status='CREATED',fault=?,fault_module=? WHERE id=?",
                   (fault.fault, fault.module, order_id))
    return orders.process_order(order_id, fault.fault, fault.module)


@router.get("/orders/{order_id}")
def order_detail(order_id: str):
    result = orders.get_order(order_id)
    if not result:
        raise HTTPException(404, "Order not found")
    return {**result, "events": orders.get_events(order_id)}


@router.get("/orders/{order_id}/events")
def order_events(order_id: str):
    if not orders.get_order(order_id):
        raise HTTPException(404, "Order not found")
    return orders.get_events(order_id)


@router.post("/analysis")
def analysis(payload: AnalyzeInput):
    result = orders.analyze_order(payload.order_id, models["root"], models["delay"])
    if not result:
        raise HTTPException(404, "Order not found")
    return result


@router.post("/analysis/explain")
async def explain(payload: AnalyzeInput):
    result = orders.analyze_order(payload.order_id, models["root"], models["delay"])
    if not result:
        raise HTTPException(404, "Order not found")
    cause = result["root_cause"]
    local = Explanation(
        what_happened=(f"Observed {result['rule_based']['matched_rule']}." if result["rule_based"]["detected"]
                       else "No failure was recorded in the order event timeline."),
        affected_module=result["rule_based"]["module"] or "None observed",
        evidence=cause["supporting_evidence"],
        likely_impact=(f"Model estimates a {result['delay']['probability']:.0%} chance of SLA breach."
                       if result["delay"]["likely_breach"] else "No current SLA breach warning from the delay model."),
        next_step=result["recommended_action"],
        additional_evidence_needed=["Service health and deployment history", "Related order and dependency telemetry"],
    )
    api_key = os.getenv("ORDERFLOW_LLM_API_KEY")
    if not api_key:
        return {"source": "local-template", "explanation": local.model_dump(),
                "note": "Set ORDERFLOW_LLM_API_KEY on the backend to enable optional LLM explanations."}
    model_name = os.getenv("ORDERFLOW_LLM_MODEL", "gpt-4o-mini")
    request_body = {
        "model": model_name, "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "Explain synthetic order incidents using only supplied evidence. Separate observations from hypotheses. Never claim a cause is confirmed, never execute or propose bypassing policy, and return JSON with keys: what_happened, affected_module, evidence (array), likely_impact, next_step, additional_evidence_needed (array)."},
            {"role": "user", "content": str({"order": result["order"], "events": result["events"],
                                               "root_cause_prediction": cause, "delay_prediction": result["delay"],
                                               "safe_recovery_options": result["recommended_action"]})},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.post("https://api.openai.com/v1/chat/completions",
                                         headers={"Authorization": f"Bearer {api_key}"}, json=request_body)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("LLM response content was not text.")
        validated = Explanation.model_validate_json(content)
        return {"source": "optional-llm", "explanation": validated.model_dump()}
    except (httpx.HTTPError, KeyError, ValueError, TypeError, ValidationError) as exc:
        with connection() as db:
            db.execute("INSERT INTO audit(order_id,action,actor,details,created_at) VALUES(?,?,?,?,?)",
                       (payload.order_id, "llm_explanation_fallback", "system",
                        '{"reason":"LLM unavailable or returned invalid output"}', orders.now()))
        return {"source": "local-template", "explanation": local.model_dump(),
                "note": f"Optional LLM was unavailable; local explanation used ({type(exc).__name__})."}


@router.post("/analysis/compare")
def compare(payload: AnalyzeInput):
    result = orders.analyze_order(payload.order_id, models["root"], models["delay"])
    if not result:
        raise HTTPException(404, "Order not found")
    return {"system_a": {"name": "Rule-Based Monitoring", "detection": result["rule_based"],
                         "root_cause": "Not predicted; exact rules match observed codes only.",
                         "delay": "Fixed SLA threshold only; no probability estimate.",
                         "action": result["recommended_action"], "confidence": "Deterministic rule match"},
            "system_b": {"name": "AI-Assisted Analysis", "detection": result["rule_based"],
                         "root_cause": result["root_cause"], "delay": result["delay"],
                         "action": result["recommended_action"]},
            "evaluation": models["metrics"], "order_id": payload.order_id}


@router.get("/incidents")
def incidents():
    return orders.get_incidents()


@router.get("/actions")
def actions():
    return orders.actions_list()


@router.post("/actions/{action_id}/decision")
def action_decision(action_id: str, decision: ActionDecision):
    result = orders.resolve_action(action_id, decision.approve, decision.actor)
    if result is None:
        raise HTTPException(404, "Recovery action not found")
    if "error" in result:
        raise HTTPException(409, result["error"])
    return result


@router.get("/evaluation")
def evaluation():
    return models["metrics"]


@router.get("/audit")
def audit(limit: int = Query(100, ge=1, le=500)):
    return orders.audit_history(limit)


@router.get("/settings")
def settings():
    return orders.get_settings()


@router.put("/settings")
def update_settings(payload: SettingsInput):
    try:
        values = {key: value for key, value in payload.model_dump().items() if value is not None}
        return orders.save_settings(values)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/scenarios")
def scenarios():
    return [{"name": n, "fault": f, "module": m} for n, f, m in SCENARIOS]


@router.post("/scenarios/load")
def load_scenarios():
    existing = {o["product"] for o in orders.list_orders(limit=500)}
    created = []
    for index, (name, fault, module) in enumerate(SCENARIOS):
        product = f"Demo · {name}"
        if product in existing:
            continue
        item = orders.create_order({"product": product, "quantity": index % 3 + 1, "fault_module": module},
                                   process=True, fault=fault)
        if fault == "database_bottleneck" and item:
            # Correlate service symptoms across distinct stages without exposing the label as a feature.
            for stage in ("Inventory", "Delivery"):
                extra = orders.create_order({"product": f"Demo · DB signal · {stage}", "quantity": 1,
                                             "fault_module": stage},
                                            process=True, fault="database_bottleneck")
            created.append(item["id"])
        if item:
            created.append(item["id"])
    return {"created": len(created), "order_ids": created, "scenarios": len(SCENARIOS)}


@router.delete("/demo")
def reset_demo():
    orders.clear_demo_data()
    return {"status": "cleared"}
