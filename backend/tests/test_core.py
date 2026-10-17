import pytest

from app.database import db as database
from app.ml.training import load_or_train
from app.services import orders
from app.services import store
from app.schemas.store import CartItem, CheckoutInput


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    database.init_db()
    orders.get_settings()
    store.initialize_catalog()
    models = load_or_train()
    yield models


def test_successful_order_progresses_through_all_stages(runtime):
    order = orders.create_order({"product": "Test item", "quantity": 1}, process=True)
    assert order["status"] == "COMPLETED"
    assert {event["stage"] for event in orders.get_events(order["id"])} == set(orders.STAGES)


def test_transient_failure_is_retried_safely_and_logged(runtime):
    order = orders.create_order({"product": "Test item", "quantity": 1}, fault="503")
    events = orders.get_events(order["id"])
    assert order["status"] == "COMPLETED"
    assert order["retries"] == 1 and order["recovered"] == 1
    assert any(e["event_type"] == "RECOVERY" and e["details"].get("idempotency_key") for e in events)
    assert any(e["event_type"] == "ERROR" and e["response_code"] == 503 for e in events)


def test_idempotency_key_prevents_duplicate_order_creation(runtime):
    first = orders.create_order({"product": "Test item", "quantity": 1, "idempotency_key": "request-test-001"})
    second = orders.create_order({"product": "Different item", "quantity": 3,
                                  "idempotency_key": "request-test-001"})
    assert first["id"] == second["id"]
    assert len(orders.list_orders()) == 1


@pytest.mark.parametrize("fault", ["payment_rejected", "inventory_unavailable"])
def test_hard_business_rejections_are_not_bypassed(runtime, fault):
    order = orders.create_order({"product": "Test item", "quantity": 1}, fault=fault)
    assert order["status"] == "MANUAL_REVIEW_REQUIRED"
    action = orders.actions_list()[0]
    assert action["requires_approval"]
    result = orders.resolve_action(action["id"], approve=True)
    assert "remains enforced" in result["result"]
    assert orders.get_order(order["id"])["status"] == "MANUAL_REVIEW_REQUIRED"


def test_root_cause_and_low_evidence_uncertainty(runtime):
    root_model, delay_model, _ = runtime
    order = orders.create_order({"product": "Test item", "quantity": 1}, fault="database_bottleneck")
    result = orders.analyze_order(order["id"], root_model, delay_model)
    assert result["root_cause"]["prediction"] == "Database bottleneck"
    unfamiliar = orders.create_order({"product": "Test item", "quantity": 1}, fault="unfamiliar")
    unknown = orders.analyze_order(unfamiliar["id"], root_model, delay_model)
    assert unknown["root_cause"]["prediction"] == "Unknown — further investigation required"


def test_delay_prediction_and_audit_are_persisted(runtime):
    root_model, delay_model, _ = runtime
    order = orders.create_order({"product": "Test item", "quantity": 1}, fault="queue_backlog")
    result = orders.analyze_order(order["id"], root_model, delay_model)
    assert 0 <= result["delay"]["probability"] <= 1
    assert order["status"] == "DELAYED"
    assert result["delay"]["likely_breach"]
    assert orders.audit_history()[0]["action"] == "ai_analysis_run"


def test_retry_exhaustion_escalates_to_manual_review(runtime):
    order = orders.create_order({"product": "Test item", "quantity": 1}, fault="recovery_fails")
    assert order["status"] == "MANUAL_REVIEW_REQUIRED"
    assert order["retries"] == 3
    assert any(e["event_type"] == "SKIPPED" for e in orders.get_events(order["id"]))


def test_correlated_incident_uses_time_window_and_multiple_services(runtime):
    for stage in ("Payment", "Inventory", "Delivery"):
        orders.create_order({"product": f"DB test {stage}", "quantity": 1, "fault_module": stage},
                            fault="database_bottleneck")
    incident = next(item for item in orders.get_incidents() if item["category"] == "database")
    assert incident["module"] == "Multiple services"
    assert len(incident["orders"]) == 3
    assert incident["causation_confirmed"] is False


def test_settings_validate_stage_thresholds(runtime):
    current = orders.get_settings()
    current["stage_limits_ms"]["Delivery"] = 100
    updated = orders.save_settings({"stage_limits_ms": current["stage_limits_ms"]})
    assert updated["stage_limits_ms"]["Delivery"] == 100
    with pytest.raises(ValueError, match="stage_limits_ms"):
        orders.save_settings({"stage_limits_ms": {"Delivery": 0}})


def test_store_checkout_creates_pipeline_order_and_deduplicates(runtime):
    payload = CheckoutInput(
        items=[CartItem(sku="sku-audio-01", quantity=1), CartItem(sku="sku-home-02", quantity=2)],
        shipping_address="1 Demo Road, Test City, 12345",
        idempotency_key="web-checkout-test-001",
    )
    created = store.checkout(payload)
    duplicate = store.checkout(payload)
    assert created["order"]["status"] == "CREATED"
    assert duplicate["duplicate"] is True
    assert duplicate["order"]["id"] == created["order"]["id"]
    assert len(created["store_order"]["items"]) == 2
    completed = store.finish_checkout(created["order"]["id"])
    assert completed["status"] == "COMPLETED"
    assert store.get_store_order(created["order"]["id"])["status"] == "COMPLETED"


def test_store_payment_rejection_does_not_reserve_or_charge_stock(runtime):
    products_before = {item["sku"]: item["stock"] for item in store.list_products()}
    payload = CheckoutInput(
        items=[CartItem(sku="sku-audio-01", quantity=1)],
        shipping_address="1 Demo Road, Test City, 12345",
        payment_method="demo_declined",
        idempotency_key="web-payment-reject-001",
    )
    created = store.checkout(payload)
    failed = store.finish_checkout(created["order"]["id"])
    products_after = {item["sku"]: item["stock"] for item in store.list_products()}
    assert failed["status"] == "MANUAL_REVIEW_REQUIRED"
    assert products_after == products_before


def test_store_validation_failure_releases_reserved_inventory(runtime):
    stock_before = next(item["stock"] for item in store.list_products() if item["sku"] == "sku-home-02")
    payload = CheckoutInput(
        items=[CartItem(sku="sku-home-02", quantity=1)],
        shipping_address="not a valid address",
        idempotency_key="web-invalid-address-001",
    )
    created = store.checkout(payload)
    failed = store.finish_checkout(created["order"]["id"])
    stock_after = next(item["stock"] for item in store.list_products() if item["sku"] == "sku-home-02")
    assert failed["status"] == "FAILED"
    assert stock_after == stock_before


def test_operator_restock_is_audited_and_visible_to_storefront(runtime):
    before = next(item for item in store.list_products() if item["sku"] == "sku-desk-04")
    updated = store.restock_product(before["sku"], 7, "Seasonal restock")
    after = next(item for item in store.list_products() if item["sku"] == "sku-desk-04")
    assert updated["stock"] == before["stock"] + 7
    assert after["stock"] == updated["stock"]
    audit = orders.audit_history()
    assert audit[0]["action"] == "inventory_restocked"
    assert audit[0]["details"]["reason"] == "Seasonal restock"
