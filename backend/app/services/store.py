import json
import threading

from app.database.db import connection, row_dict
from app.services.orders import now

CATALOG = [
    ("sku-audio-01", "Aero Wireless Headphones", "Balanced sound, soft memory-foam ear cups, and 40-hour battery.", "Audio", 8990, 18, "blue"),
    ("sku-home-02", "Arc Ceramic Pour-Over Set", "A glazed stoneware brewer and two stackable cups.", "Home", 4590, 24, "sand"),
    ("sku-travel-03", "Fieldwork Everyday Backpack", "Weather-resistant recycled canvas with a padded laptop sleeve.", "Travel", 7290, 12, "sage"),
    ("sku-desk-04", "Halo Ambient Desk Lamp", "Warm dimmable light with a compact weighted metal base.", "Workspace", 5490, 15, "violet"),
]
_CHECKOUT_LOCK = threading.Lock()


def initialize_catalog():
    timestamp = now()
    with connection() as db:
        for sku, name, description, category, price, stock, color in CATALOG:
            db.execute("""INSERT OR IGNORE INTO products
                          (sku,name,description,category,price_cents,stock,color,updated_at)
                          VALUES(?,?,?,?,?,?,?,?)""",
                       (sku, name, description, category, price, stock, color, timestamp))


def list_products():
    with connection() as db:
        return [row_dict(row) for row in db.execute(
            "SELECT sku,name,description,category,price_cents,stock,color FROM products ORDER BY name")]


def list_store_orders(limit=20):
    with connection() as db:
        rows = db.execute("""SELECT s.order_id,s.sku,s.quantity,s.unit_price_cents,s.total_cents,s.status,
                             s.created_at,s.updated_at,p.name AS product_name,o.customer_ref,o.stage,
                             o.retries,o.failure_category
                             FROM store_orders s JOIN products p ON p.sku=s.sku
                             JOIN orders o ON o.id=s.order_id
                             ORDER BY s.created_at DESC LIMIT ?""", (min(max(int(limit), 1), 100),)).fetchall()
        result = []
        for row in rows:
            order = row_dict(row)
            order["items"] = [row_dict(item) for item in db.execute(
                """SELECT i.sku,i.quantity,i.unit_price_cents,p.name FROM store_order_items i
                   JOIN products p ON p.sku=i.sku WHERE i.order_id=?""",
                (order["order_id"],)).fetchall()]
            result.append(order)
        return result


def restock_product(sku, quantity, reason, actor="store-operator"):
    with connection() as db:
        product = db.execute("SELECT sku,name,stock FROM products WHERE sku=?", (sku,)).fetchone()
        if not product:
            return None
        db.execute("UPDATE products SET stock=stock+?,updated_at=? WHERE sku=?",
                   (quantity, now(), sku))
        updated = db.execute("SELECT sku,name,stock FROM products WHERE sku=?", (sku,)).fetchone()
        db.execute("INSERT INTO audit(order_id,action,actor,details,created_at) VALUES(NULL,?,?,?,?)",
                   ("inventory_restocked", actor,
                    json.dumps({"sku": sku, "product": product["name"], "added": quantity,
                                "previous_stock": product["stock"], "new_stock": updated["stock"],
                                "reason": reason}), now()))
        return {**row_dict(updated), "added": quantity, "reason": reason}


def checkout(payload):
    with _CHECKOUT_LOCK:
        return _checkout_unlocked(payload)


def _checkout_unlocked(payload):
    quantities = {}
    for item in payload.items:
        quantities[item.sku] = quantities.get(item.sku, 0) + item.quantity
    with connection() as db:
        prior = db.execute("SELECT id FROM orders WHERE idempotency_key=?",
                           (payload.idempotency_key,)).fetchone()
        if prior:
            return {"order": row_dict(db.execute("SELECT * FROM orders WHERE id=?", (prior["id"],)).fetchone()),
                    "store_order": row_dict(db.execute("SELECT * FROM store_orders WHERE order_id=?",
                                                       (prior["id"],)).fetchone()),
                    "duplicate": True}
        products = {}
        for sku, quantity in quantities.items():
            product = db.execute("SELECT * FROM products WHERE sku=?", (sku,)).fetchone()
            if not product:
                raise ValueError(f"Product {sku} is no longer available.")
            products[sku] = (product, quantity)
        if payload.payment_method == "demo_declined" or payload.demo_fault == "payment_rejected":
            fault = "payment_rejected"
        elif payload.demo_fault == "inventory_unavailable":
            fault = "inventory_unavailable"
        elif payload.demo_fault == "invalid_address" or not _valid_address(payload.shipping_address):
            fault = "invalid_address"
        else:
            fault = {"500": "500", "503": "503", "504": "504", "429": "429", "queue_backlog": "queue_backlog",
                     "database_bottleneck": "database_bottleneck"}.get(payload.demo_fault)
        if fault not in ("payment_rejected", "inventory_unavailable"):
            for sku, (product, quantity) in products.items():
                updated = db.execute("""UPDATE products SET stock=stock-?,updated_at=?
                                        WHERE sku=? AND stock>=?""",
                                     (quantity, now(), sku, quantity))
                if updated.rowcount != 1:
                    raise ValueError(f"Not enough stock for {product['name']}.")
        product_names = [f"{product['name']} × {quantity}" for product, quantity in products.values()]
        product_name = ", ".join(product_names)
        total_cents = sum(product["price_cents"] * quantity for product, quantity in products.values())
        primary_sku, (primary_product, primary_quantity) = next(iter(products.items()))
        customer_ref = f"WEB-{payload.idempotency_key[-8:].upper()}"
        db.commit()
    try:
        from app.services.orders import create_order
        order = create_order({
            "product": product_name, "quantity": sum(quantities.values()), "customer_ref": customer_ref,
            "idempotency_key": payload.idempotency_key, "fault_module": {
                "invalid_address": "Delivery", "payment_rejected": "Payment", "inventory_unavailable": "Inventory",
                "500": "Inventory", "503": "Inventory", "504": "Inventory",
                "429": "Inventory", "queue_backlog": "Delivery", "database_bottleneck": "Inventory",
            }.get(fault),
        }, process=False, fault=fault)
    except Exception:
        if fault not in ("payment_rejected", "inventory_unavailable"):
            with connection() as db:
                for sku, quantity in quantities.items():
                    db.execute("UPDATE products SET stock=stock+?,updated_at=? WHERE sku=?",
                               (quantity, now(), sku))
        raise
    with connection() as db:
        db.execute("""INSERT INTO store_orders(order_id,sku,quantity,unit_price_cents,total_cents,status,created_at,updated_at)
                      VALUES(?,?,?,?,?,'PROCESSING',?,?)""",
                   (order["id"], primary_sku, sum(quantities.values()), primary_product["price_cents"],
                    total_cents, now(), now()))
        for sku, (product, quantity) in products.items():
            db.execute("""INSERT INTO store_order_items(order_id,sku,quantity,unit_price_cents,reserved_stock)
                          VALUES(?,?,?,?,?)""",
                       (order["id"], sku, quantity, product["price_cents"],
                        int(fault not in ("payment_rejected", "inventory_unavailable"))))
        db.execute("INSERT INTO audit(order_id,action,actor,details,created_at) VALUES(?,?,?,?,?)",
                   (order["id"], "ecommerce_checkout", "storefront",
                    json.dumps({"items": payload.items and [i.model_dump() for i in payload.items],
                                "total_cents": total_cents}),
                    now()))
    return {"order": order, "store_order": get_store_order(order["id"]), "duplicate": False}


def finish_checkout(order_id):
    from app.services.orders import _event, process_order

    try:
        result = process_order(order_id, realtime=True)
        if not result:
            raise RuntimeError(f"Storefront order {order_id} disappeared during processing.")
    except Exception as exc:
        with connection() as db:
            reserved = db.execute("SELECT sku,quantity FROM store_order_items WHERE order_id=? AND reserved_stock=1",
                                  (order_id,)).fetchall()
            for item in reserved:
                db.execute("UPDATE products SET stock=stock+?,updated_at=? WHERE sku=?",
                           (item["quantity"], now(), item["sku"]))
            db.execute("UPDATE store_order_items SET reserved_stock=0 WHERE order_id=?", (order_id,))
            db.execute("UPDATE store_orders SET status='PROCESSING_ERROR',updated_at=? WHERE order_id=?",
                       (now(), order_id))
            _event(db, order_id, "Store", "STORE_PROCESSING_ERROR",
                   "Order worker failed unexpectedly; reserved demo inventory was released.",
                   details={"error_type": type(exc).__name__})
            db.execute("INSERT INTO audit(order_id,action,actor,details,created_at) VALUES(?,?,?,?,?)",
                       (order_id, "ecommerce_processing_error", "system",
                        json.dumps({"error_type": type(exc).__name__, "stock_released": len(reserved)}), now()))
        raise
    stock_restored = result["status"] in ("FAILED", "MANUAL_REVIEW_REQUIRED")
    if stock_restored:
        with connection() as db:
            reserved = db.execute("SELECT sku,quantity FROM store_order_items WHERE order_id=? AND reserved_stock=1",
                                  (order_id,)).fetchall()
            for item in reserved:
                db.execute("UPDATE products SET stock=stock+?,updated_at=? WHERE sku=?",
                           (item["quantity"], now(), item["sku"]))
            db.execute("UPDATE store_order_items SET reserved_stock=0 WHERE order_id=?", (order_id,))
            _event(db, order_id, "Store", "STORE_STOCK_RESTORED",
                   "Unfulfilled order released its reserved demo inventory.",
                   details={"items_restored": len(reserved)})
    with connection() as db:
        db.execute("UPDATE store_orders SET status=?,updated_at=? WHERE order_id=?",
                   (result["status"], now(), order_id))
        db.execute("INSERT INTO audit(order_id,action,actor,details,created_at) VALUES(?,?,?,?,?)",
                   (order_id, "ecommerce_fulfillment_finished", "system",
                    json.dumps({"status": result["status"], "stock_restored": stock_restored}),
                    now()))
    return result


def get_store_order(order_id):
    with connection() as db:
        row = db.execute("""SELECT s.order_id,s.sku,s.quantity,s.unit_price_cents,s.total_cents,s.status,
                            s.created_at,s.updated_at,p.name AS product_name
                            FROM store_orders s JOIN products p ON p.sku=s.sku WHERE s.order_id=?""",
                         (order_id,)).fetchone()
        if not row:
            return None
        result = row_dict(row)
        result["items"] = [row_dict(item) for item in db.execute(
            """SELECT i.sku,i.quantity,i.unit_price_cents,p.name FROM store_order_items i
               JOIN products p ON p.sku=i.sku WHERE i.order_id=?""", (order_id,)).fetchall()]
        return result


def _valid_address(value):
    parts = [part.strip() for part in value.split(",")]
    return len(parts) >= 3 and all(len(part) >= 2 for part in parts)
