from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import models, router
from app.database.db import init_db
from app.ml.training import load_or_train
from app.services.orders import get_settings
from app.database.db import connection
from datetime import datetime, timezone
import json
from app.services.store import initialize_catalog


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    get_settings()
    initialize_catalog()
    models["root"], models["delay"], models["metrics"] = load_or_train()
    with connection() as db:
        timestamp = datetime.now(timezone.utc).isoformat()
        for name, metrics in models["metrics"].items():
            db.execute("INSERT INTO model_evaluation_runs(model,metrics,created_at) VALUES(?,?,?)",
                       (name, json.dumps(metrics), timestamp))
    yield


app = FastAPI(title="OrderFlow Sentinel API", version="1.0.0",
              description="Explainable local ML and safe recovery for simulated order processing.",
              lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(router)


@app.get("/")
def root():
    return {"name": "OrderFlow Sentinel", "docs": "/docs", "api": "/api"}
