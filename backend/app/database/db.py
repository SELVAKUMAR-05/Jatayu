import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.getenv("ORDERFLOW_DATABASE", str(ROOT / "data" / "orderflow.db")))
if not DB_PATH.is_absolute():
    DB_PATH = ROOT / DB_PATH
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def connection():
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db():
    with connection() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY, customer_ref TEXT NOT NULL, product TEXT NOT NULL,
            quantity INTEGER NOT NULL, created_at TEXT NOT NULL, stage TEXT NOT NULL,
            status TEXT NOT NULL, elapsed_ms INTEGER NOT NULL DEFAULT 0,
            retries INTEGER NOT NULL DEFAULT 0, failure_category TEXT,
            fault TEXT, fault_module TEXT, recovered INTEGER NOT NULL DEFAULT 0, idempotency_key TEXT UNIQUE
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL,
            stage TEXT NOT NULL, event_type TEXT NOT NULL, message TEXT NOT NULL,
            created_at TEXT NOT NULL, response_code INTEGER, response_ms INTEGER,
            details TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS actions (
            id TEXT PRIMARY KEY, order_id TEXT NOT NULL, action TEXT NOT NULL,
            risk TEXT NOT NULL, requires_approval INTEGER NOT NULL,
            status TEXT NOT NULL, evidence TEXT NOT NULL, created_at TEXT NOT NULL,
            result TEXT
        );
        CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, action TEXT NOT NULL,
            actor TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS api_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL, module TEXT NOT NULL,
            method TEXT NOT NULL, response_code INTEGER, response_ms INTEGER, error_category TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY, category TEXT NOT NULL, payload TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS root_cause_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL, prediction TEXT NOT NULL,
            confidence REAL NOT NULL, evidence TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS delay_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL, likely_breach INTEGER NOT NULL,
            probability REAL NOT NULL, features TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS model_evaluation_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT NOT NULL, metrics TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS products (
            sku TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
            category TEXT NOT NULL, price_cents INTEGER NOT NULL, stock INTEGER NOT NULL,
            color TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS store_orders (
            order_id TEXT PRIMARY KEY, sku TEXT NOT NULL, quantity INTEGER NOT NULL,
            unit_price_cents INTEGER NOT NULL, total_cents INTEGER NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS store_order_items (
            order_id TEXT NOT NULL, sku TEXT NOT NULL, quantity INTEGER NOT NULL,
            unit_price_cents INTEGER NOT NULL, reserved_stock INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(order_id, sku)
        );
        """)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(orders)")}
        if "idempotency_key" not in columns:
            db.execute("ALTER TABLE orders ADD COLUMN idempotency_key TEXT")
        if "fault_module" not in columns:
            db.execute("ALTER TABLE orders ADD COLUMN fault_module TEXT")
        db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_idempotency_key
                      ON orders(idempotency_key) WHERE idempotency_key IS NOT NULL""")


def row_dict(row):
    return dict(row) if row is not None else None


def json_value(value):
    return json.loads(value) if value else {}
