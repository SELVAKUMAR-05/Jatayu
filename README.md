# OrderFlow Sentinel

Explainable, AI-assisted order monitoring with a real local processing simulator, SQLite persistence, trained scikit-learn models, and deterministic safety-gated recovery. All sample customers and orders are synthetic. The app does not connect to a real payment or fulfillment service.

## What is implemented

- A five-stage Order Management → Payment → Inventory → Delivery → Notification pipeline with persisted orders, API/event logs, incident groups, predictions, recovery recommendations, and an audit trail.
- Rule-based API/rejection detection alongside separately trained RandomForest models for root-cause classification and SLA breach probabilities. Reproducible synthetic data, held-out metrics, confusion matrices, per-class precision/recall/F1, and persisted model artifacts are generated locally.
- Explicit uncertainty for unfamiliar evidence, evidence attached to predictions, multi-service incident correlation labeled as correlation (not causation), and an optional validated LLM explanation endpoint. Without an LLM key, deterministic local templates work.
- Idempotent order creation, bounded retries, Retry-After-aware rate-limit simulation, human approval for risky actions, and hard prohibitions against bypassing payment or inventory rejections.
- A customer-facing demo storefront with a multi-item cart, live catalog stock, demo payment authorization/decline, atomic inventory reservation, idempotent checkout, fulfillment compensation on rejection, and order tracking against the same five-stage processing pipeline.
- A Server-Sent Events stream broadcasts committed stage, error, recovery, and final-status events to the storefront and operations views. Event IDs support reconnect/resume, while the operations view also refreshes periodically as a fallback.
- Recent store order history and order-ID lookup survive page refreshes because orders are read back from SQLite; demo operators can also restock catalog items through an audited inventory control.
- Responsive dashboard, searchable order monitor, five-stage event timeline, AI root-cause analysis, incidents, resolution approvals, model comparison, model evaluation, and settings. UI data comes from the API.

## Requirements

- Windows 10/11, Python 3.11+ (3.13 recommended for broad ML wheel support), and Node.js 20+ with npm.
- No paid AI account or API key is needed. First backend start trains and saves both local models.

## Windows setup

Open two PowerShell terminals at the project root (`e:\Jatayu` in this workspace).

### Backend

```powershell
cd backend
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
python -m uvicorn app.main:app --reload
```

If Python 3.13 is unavailable, use an installed supported Python with `py -m venv .venv`. The first startup creates `data/orderflow.db`, reproducible CSV training data, model artifacts in `data/models`, and evaluation metrics. Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) for interactive REST documentation.

### Frontend

```powershell
cd frontend
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). The frontend expects the API at `http://127.0.0.1:8000/api`; set `VITE_API_URL` before starting Vite to override it.

### Generate and train data manually

From the backend directory with the virtual environment active:

```powershell
python -m app.ml.training
```

The command recreates seeded training CSVs, fits both models on stratified 75/25 train/test splits, reports actual held-out metrics, and persists `.joblib` model files. The UI's **Load demo scenarios** button creates the ten synthetic scenarios, including an unknown-evidence case and retry exhaustion. Creating orders and injecting faults is also available in the Live Order Monitor. The **E-commerce Store** page offers a live multi-item demo checkout connected to Sentinel. Use its optional fault selector to observe a recoverable HTTP error, rate limit, validation/payment/inventory rejection, or delay in the live event tracker.

Run the test suite:

```powershell
python -m pip install pytest
python -m pytest -q
```

## API overview

| Endpoint | Purpose |
|---|---|
| `GET /api/dashboard`, `GET /api/health` | Order KPIs and backend/model health |
| `GET /api/stream` | Server-Sent Events for committed order-stage, error, recovery, and status updates; honors `Last-Event-ID` on reconnect |
| `GET /api/store/products` | Current live catalog and available stock |
| `POST /api/store/products/{sku}/restock` | Add validated demo inventory and record the operator/reason in the audit log |
| `GET /api/store/orders?limit=20` | Read recent persisted storefront checkouts |
| `POST /api/store/checkout` | Idempotent multi-item demo checkout; returns promptly and processes the order in a background task |
| `GET /api/store/orders/{id}` | Store total, line items, current order status, and processing timeline |
| `GET/POST /api/orders` | Search/list orders and create/process with optional simulated fault |
| `POST /api/orders/{id}/inject`, `POST /api/orders/{id}/process` | Fault injection and pipeline processing |
| `GET /api/orders/{id}`, `GET /api/orders/{id}/events` | Detail and ordered timeline |
| `POST /api/analysis`, `POST /api/analysis/compare` | Evidence-backed ML, delay prediction, and rule comparison |
| `POST /api/analysis/explain` | Optional schema-validated LLM explanation or local template |
| `GET /api/incidents`, `GET /api/actions` | Correlated incident groups and recovery queue |
| `POST /api/actions/{id}/decision` | Approve/decline a risk-gated action; business rejections remain enforced |
| `GET /api/evaluation`, `GET /api/audit` | Held-out metrics and action/analysis audit history |
| `GET/PUT /api/settings`, `POST /api/scenarios/load` | Simulator configuration and seeded demo scenarios |

## Optional LLM

The application runs without an LLM. To enable optional explanations, configure `ORDERFLOW_LLM_API_KEY` and optionally `ORDERFLOW_LLM_MODEL` in `backend/.env` (the API key stays on the backend). The `/api/analysis/explain` endpoint sends only the selected order's synthetic evidence and requires a schema-valid structured response. Network, HTTP, and invalid-output failures return the built-in template. The LLM never runs recovery actions.

## Safety and interpretation

- Root-cause outputs are probabilistic hypotheses with model confidence and supporting telemetry, never confirmed causes.
- An unfamiliar incident is explicitly labeled “Unknown — further investigation required.”
- Database/queue correlations are candidate shared explanations, not proof of causality.
- Automatic transient retries are bounded and use per-order/stage idempotency keys. HTTP 429 simulation respects a retry-after interval. Repeated failure escalates.
- Payment/inventory business rejections are never automatically retried or bypassed. Risky recovery recommendations require operator approval and create audit records.
- The local data is reproducible synthetic data with clear signal. Its measured metrics are not a promise of production performance.

## Project layout

```text
backend/
  app/api/           FastAPI REST endpoints
  app/database/      SQLite initialization and connection management
  app/ml/            Seeded data generation, training, evaluation, persistence
  app/schemas/       Validated request/response models
  app/services/      Pipeline, analysis, safety policy, audit and incident logic
  tests/             Pipeline, ML, idempotency, safety and audit tests
  data/              Created on first run: SQLite, CSVs and model artifacts
frontend/
  src/App.tsx        Connected monitoring, analysis, resolution and evaluation pages
  src/styles.css     Responsive enterprise dashboard theme
```

## Troubleshooting

- **Frontend shows API disconnected:** start the backend and check `http://127.0.0.1:8000/api/health`; confirm `VITE_API_URL` if using a different port.
- **Python package installation fails on Python 3.14:** install Python 3.13 and recreate the virtual environment with `py -3.13 -m venv .venv`.
- **PowerShell blocks activation:** run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or run `.venv\Scripts\python.exe -m pip ...` without activation.
- **Models need a clean retrain:** run `python -m app.ml.training` in the backend virtual environment. No DB reset is required.
- **Port in use:** run Uvicorn with `--port 8001` and set the frontend's `VITE_API_URL` accordingly.
#   J a t a y u  
 