import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, precision_recall_fscore_support
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

load_dotenv()
BASE = Path(__file__).resolve().parents[2]
MODEL_DIR = Path(os.getenv("ORDERFLOW_MODEL_DIR", str(BASE / "data" / "models")))
if not MODEL_DIR.is_absolute():
    MODEL_DIR = BASE / MODEL_DIR
MODEL_DIR.mkdir(parents=True, exist_ok=True)
ROOT_FEATURES = ["failed_module", "http_status", "response_ms", "db_latency_ms", "queue_length",
                 "recent_error_frequency", "recent_deployment", "error_category", "affected_orders",
                 "related_module_failures"]
NUMERIC = ["http_status", "response_ms", "db_latency_ms", "queue_length",
           "recent_error_frequency", "recent_deployment", "affected_orders", "related_module_failures"]
CAUSES = ["Temporary downstream service failure", "Database bottleneck", "Queue congestion",
          "Authentication failure", "Rate limiting", "Invalid request data"]
STAGES = ["Order Management", "Payment", "Inventory", "Delivery", "Notification"]


def generate_dataset(size=2400, seed=73):
    rng = np.random.default_rng(seed)
    rows = []
    for _ in range(size):
        cause = CAUSES[int(rng.integers(len(CAUSES)))]
        module = str(rng.choice(STAGES))
        row = dict(failed_module=module, http_status=500, response_ms=300, db_latency_ms=40,
                   queue_length=8, recent_error_frequency=2, recent_deployment=int(rng.random() < .14),
                   error_category="server", affected_orders=1, related_module_failures=0, root_cause=cause)
        if cause == CAUSES[0]:
            row.update(http_status=int(rng.choice([500, 503, 504])), response_ms=int(rng.normal(1700, 350)),
                       error_category="server", recent_error_frequency=int(rng.integers(1, 10)))
        elif cause == CAUSES[1]:
            row.update(db_latency_ms=int(rng.normal(950, 170)), response_ms=int(rng.normal(1500, 260)),
                       related_module_failures=int(rng.integers(2, 6)), affected_orders=int(rng.integers(3, 18)),
                       error_category="database", recent_error_frequency=int(rng.integers(3, 12)))
        elif cause == CAUSES[2]:
            row.update(queue_length=int(rng.normal(85, 16)), response_ms=int(rng.normal(1100, 300)),
                       affected_orders=int(rng.integers(5, 25)), related_module_failures=int(rng.integers(1, 4)),
                       error_category="timeout")
        elif cause == CAUSES[3]:
            row.update(http_status=int(rng.choice([401, 403])), error_category="auth",
                       response_ms=int(rng.normal(90, 25)))
        elif cause == CAUSES[4]:
            row.update(http_status=429, error_category="rate_limit", response_ms=int(rng.normal(180, 45)))
        else:
            row.update(http_status=int(rng.choice([400, 422])), error_category="validation",
                       response_ms=int(rng.normal(100, 30)))
        rows.append(row)
    return pd.DataFrame(rows)


def generate_delay_dataset(size=3000, seed=91):
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame({
        "elapsed_ms": rng.integers(1000, 180000, size),
        "historical_stage_ms": rng.integers(400, 90000, size),
        "queue_length": rng.integers(0, 120, size),
        "api_latency_ms": rng.integers(20, 4000, size),
        "retry_count": rng.integers(0, 5, size),
        "preceding_failures": rng.integers(0, 6, size),
    })
    # Label is generated from observable operational signals, not a feature copied from the target.
    risk = (frame.elapsed_ms > 80000) | (frame.queue_length > 65) | \
           ((frame.api_latency_ms > 1400) & (frame.retry_count > 1)) | (frame.preceding_failures > 2)
    frame["sla_breach"] = risk.astype(int)
    return frame


def _root_pipeline():
    pre = ColumnTransformer([
        ("categorical", OneHotEncoder(handle_unknown="ignore"), ["failed_module", "error_category"]),
        ("numeric", "passthrough", NUMERIC),
    ])
    return Pipeline([("features", pre), ("classifier", RandomForestClassifier(
        n_estimators=180, min_samples_leaf=2, class_weight="balanced", random_state=73, n_jobs=-1))])


def train_models():
    from joblib import dump

    root = generate_dataset()
    x_train, x_test, y_train, y_test = train_test_split(
        root[ROOT_FEATURES], root.root_cause, test_size=.25, random_state=73, stratify=root.root_cause)
    root_model = _root_pipeline().fit(x_train, y_train)
    pred = root_model.predict(x_test)
    labels = list(root_model.classes_)
    precision, recall, f1, support = precision_recall_fscore_support(y_test, pred, labels=labels, zero_division=0)
    root_metrics = {
        "dataset_size": len(root), "train_size": len(x_train), "test_size": len(x_test),
        "split": "stratified random 75/25, seed 73", "accuracy": round(float(accuracy_score(y_test, pred)), 4),
        "labels": labels, "confusion_matrix": confusion_matrix(y_test, pred, labels=labels).tolist(),
        "per_class": [{"label": label, "precision": round(float(p), 4), "recall": round(float(r), 4),
                       "f1": round(float(f), 4), "support": int(s)}
                      for label, p, r, f, s in zip(labels, precision, recall, f1, support)],
        "report": classification_report(y_test, pred, labels=labels, output_dict=True, zero_division=0),
    }
    delay = generate_delay_dataset()
    dx_train, dx_test, dy_train, dy_test = train_test_split(
        delay.drop(columns="sla_breach"), delay.sla_breach, test_size=.25, random_state=91, stratify=delay.sla_breach)
    delay_model = RandomForestClassifier(n_estimators=160, min_samples_leaf=3, random_state=91, class_weight="balanced")
    delay_model.fit(dx_train, dy_train)
    delay_pred = delay_model.predict(dx_test)
    dp, dr, df, ds = precision_recall_fscore_support(dy_test, delay_pred, labels=[0, 1], zero_division=0)
    delay_metrics = {
        "dataset_size": len(delay), "train_size": len(dx_train), "test_size": len(dx_test),
        "split": "stratified random 75/25, seed 91", "accuracy": round(float(accuracy_score(dy_test, delay_pred)), 4),
        "confusion_matrix": confusion_matrix(dy_test, delay_pred, labels=[0, 1]).tolist(),
        "per_class": [{"label": ["On track", "SLA breach"][i], "precision": round(float(dp[i]), 4),
                       "recall": round(float(dr[i]), 4), "f1": round(float(df[i]), 4), "support": int(ds[i])}
                      for i in range(2)],
        "features": list(dx_train.columns),
    }
    dump(root_model, MODEL_DIR / "root_cause.joblib")
    dump(delay_model, MODEL_DIR / "sla_delay.joblib")
    metrics = {"root_cause": root_metrics, "delay": delay_metrics}
    (MODEL_DIR / "evaluation.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    root.to_csv(MODEL_DIR.parent / "root_cause_training.csv", index=False)
    delay.to_csv(MODEL_DIR.parent / "delay_training.csv", index=False)
    return root_model, delay_model, metrics


def load_or_train():
    from joblib import load
    root_path, delay_path, metrics_path = (MODEL_DIR / "root_cause.joblib", MODEL_DIR / "sla_delay.joblib",
                                           MODEL_DIR / "evaluation.json")
    if root_path.exists() and delay_path.exists() and metrics_path.exists():
        return load(root_path), load(delay_path), json.loads(metrics_path.read_text(encoding="utf-8"))
    return train_models()


def main():
    _, _, metrics = train_models()
    print(json.dumps({"root_cause_accuracy": metrics["root_cause"]["accuracy"],
                      "sla_accuracy": metrics["delay"]["accuracy"],
                      "artifacts": str(MODEL_DIR)}, indent=2))


if __name__ == "__main__":
    main()
