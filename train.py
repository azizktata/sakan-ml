"""
LightGBM training script for SAKAN price estimation.

Reads from `properties_clean`, trains a LightGBM regressor on price_per_m2,
evaluates MAE + MAPE on an 80/20 split, and saves model.pkl.

Run:
  python train.py [--model-version v1.0]

Outputs:
  ml/model.pkl      — trained LightGBM model
  ml/encoders.pkl   — metadata (feature names, model version, city list)
"""

import argparse
import json
import os
import pickle
from datetime import datetime

import lightgbm as lgb
import mysql.connector
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

# ── DB config ─────────────────────────────────────────────────────────────────

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_NAME = os.getenv("DB_NAME", "sakan_db")
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "root")

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH  = os.path.join(SCRIPT_DIR, "model.pkl")
ENCODER_PATH = os.path.join(SCRIPT_DIR, "encoders.pkl")

# ── Feature config ────────────────────────────────────────────────────────────

CATEGORICAL_FEATURES = ["city", "property_type", "transaction_type", "condition_state"]
NUMERIC_FEATURES     = [
    "surface", "bedrooms", "bathrooms", "floor", "zone_score", "amenities_count",
    "garden_surface", "parking_spaces", "terrace_surface", "building_age",
]
ALL_FEATURES         = CATEGORICAL_FEATURES + NUMERIC_FEATURES
TARGET               = "price_per_m2"

# ── Data loading ──────────────────────────────────────────────────────────────

def load_data() -> pd.DataFrame:
    conn = mysql.connector.connect(
        host=DB_HOST, port=DB_PORT,
        database=DB_NAME, user=DB_USER, password=DB_PASS,
        charset="utf8mb4",
    )
    query = """
        SELECT
            price_per_m2,
            surface,
            city,
            governorate,
            zone_score,
            property_type,
            transaction_type,
            bedrooms,
            condition_state,
            amenities,
            COALESCE(bathrooms,        0) AS bathrooms,
            COALESCE(floor,            0) AS floor,
            COALESCE(garden_surface,   0) AS garden_surface,
            COALESCE(parking_spaces,   0) AS parking_spaces,
            COALESCE(terrace_surface,  0) AS terrace_surface,
            COALESCE(building_age,    15) AS building_age
        FROM properties_clean
        WHERE price_per_m2 > 0
    """
    df = pd.read_sql(query, conn)
    conn.close()
    return df


def compute_amenities_count(amenities_json) -> int:
    if not amenities_json:
        return 0
    if isinstance(amenities_json, str):
        amenities_json = json.loads(amenities_json)
    return sum(1 for v in amenities_json.values() if v)

# ── Evaluation helpers ────────────────────────────────────────────────────────

def mape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    mask = y_true != 0
    return float(np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100)

# ── Confidence score ──────────────────────────────────────────────────────────

def build_confidence_lookup(df_train: pd.DataFrame) -> dict:
    """
    Precompute per-group (city, property_type, transaction_type) statistics
    used to derive a confidence score at prediction time.

    confidence = 1 - (std / mean) clamped to [0, 1]
    """
    lookup = {}
    group_keys = ["city", "property_type", "transaction_type"]
    for keys, group in df_train.groupby(group_keys):
        prices = group[TARGET].values
        mean = float(np.mean(prices))
        std  = float(np.std(prices))
        cv   = std / mean if mean > 0 else 1.0  # coefficient of variation
        count = len(prices)
        lookup[keys] = {
            "mean":  mean,
            "std":   std,
            "cv":    cv,
            "count": count,
        }
    return lookup


def compute_confidence(city: str, property_type: str, transaction_type: str,
                        lookup: dict) -> float:
    key = (city, property_type, transaction_type)
    info = lookup.get(key)
    if not info or info["count"] < 3:
        return 0.4  # low confidence when sparse data
    cv = info["cv"]
    # Invert CV: low variation → high confidence
    # CV=0 → confidence=1.0, CV=0.5 → confidence=0.5, CV≥1 → confidence≈0
    confidence = max(0.0, min(1.0, 1.0 - cv))
    # Boost slightly for larger samples
    sample_boost = min(0.1, info["count"] / 500 * 0.1)
    return round(min(1.0, confidence + sample_boost), 2)

# ── Training ──────────────────────────────────────────────────────────────────

def train(model_version: str = "v1.0"):
    print(f"Loading data from MySQL [{DB_NAME}]...")
    df = load_data()
    print(f"Loaded {len(df)} rows")

    if len(df) < 50:
        print("WARNING: Very few training samples (<50). Results may be unreliable.")

    # Feature engineering
    df["amenities_count"] = df["amenities"].apply(compute_amenities_count)

    # Ensure numeric columns exist and are numeric (backward compat with old data)
    for col in ["bathrooms", "floor", "garden_surface", "parking_spaces", "terrace_surface", "building_age"]:
        if col not in df.columns:
            df[col] = 0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # Cast categoricals
    for col in CATEGORICAL_FEATURES:
        df[col] = df[col].astype("category")

    X = df[ALL_FEATURES]
    y_raw = df[TARGET].values.astype(float)

    # Log-transform the target: vente ~1000-5000 DT/m2, location ~8-30 DT/m2.
    # Without log, the model can't learn both scales simultaneously.
    # At prediction time, we exp() the output back to DT/m2.
    y = np.log1p(y_raw)

    # 80/20 split — stratify on transaction_type to keep both vente/location in test
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42,
        stratify=df["transaction_type"].values,
    )
    y_test_raw = np.expm1(y_test)   # for evaluation in original scale
    print(f"Train: {len(X_train)}, Test: {len(X_test)}")

    # LightGBM dataset — let LGBM handle categoricals natively
    train_data = lgb.Dataset(
        X_train, label=y_train,
        categorical_feature=CATEGORICAL_FEATURES,
    )
    valid_data = lgb.Dataset(
        X_test, label=y_test,
        categorical_feature=CATEGORICAL_FEATURES,
        reference=train_data, 
    )

    params = {
        "objective":      "regression",
        "metric":         ["mae", "mape"],
        "boosting_type":  "gbdt",
        "num_leaves":     63,
        "learning_rate":  0.05,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq":   5,
        "min_child_samples": 5,
        "verbose":        -1,
    }

    print("Training LightGBM...")
    callbacks = [lgb.early_stopping(50, verbose=True), lgb.log_evaluation(100)]
    model = lgb.train(
        params,
        train_data,
        num_boost_round=1000,
        valid_sets=[valid_data],
        callbacks=callbacks,
    )

    # Evaluate — convert log predictions back to DT/m2 for meaningful metrics
    y_pred_log = model.predict(X_test)
    y_pred_raw = np.expm1(y_pred_log)
    mae_val  = float(np.mean(np.abs(y_test_raw - y_pred_raw)))
    mape_val = mape(y_test_raw, y_pred_raw)
    print(f"\nTest MAE:  {mae_val:.1f} DT/m2")
    print(f"Test MAPE: {mape_val:.1f}%")

    # Per-group MAPE (vente vs location have 100x different scales)
    tx_col = X_test["transaction_type"].astype(str).values
    for tx in np.unique(tx_col):
        mask = tx_col == tx
        if mask.sum() > 0:
            group_mape = mape(y_test_raw[mask], y_pred_raw[mask])
            print(f"  [{tx}] MAPE: {group_mape:.1f}%  (n={mask.sum()})")

    if mape_val > 50:
        print("  [!] Overall MAPE high -- check per-group above")
    elif mape_val > 15:
        print("  [!] MAPE > 15% -- consider collecting more data or tuning features")
    else:
        print("  [OK] MAPE within target (<15%)")

    # Build confidence lookup from training data (use raw scale, not log)
    y_train_raw = np.expm1(y_train)
    confidence_lookup = build_confidence_lookup(X_train.assign(**{TARGET: y_train_raw}))

    # Save model
    with open(MODEL_PATH, "wb") as f:
        pickle.dump(model, f)
    print(f"Saved model -> {MODEL_PATH}")

    # Save encoders / metadata
    meta = {
        "model_version":      model_version,
        "trained_at":         datetime.utcnow().isoformat(),
        "features":           ALL_FEATURES,
        "categorical":        CATEGORICAL_FEATURES,
        "target":             TARGET,
        "log_target":         True,   # model predicts log1p(price_per_m2); API must expm1()
        "test_mae":           mae_val,
        "test_mape":          mape_val,
        "confidence_lookup":  confidence_lookup,
        "city_values":        list(df["city"].cat.categories),
        "property_types":     list(df["property_type"].cat.categories),
        "medians": {
            "bathrooms":       float(df["bathrooms"].median()),
            "floor":           float(df["floor"].median()),
            "garden_surface":  float(df["garden_surface"].median()),
            "parking_spaces":  float(df["parking_spaces"].median()),
            "terrace_surface": float(df["terrace_surface"].median()),
            "building_age":    float(df["building_age"].median()),
        },
    }
    with open(ENCODER_PATH, "wb") as f:
        pickle.dump(meta, f)
    print(f"Saved metadata -> {ENCODER_PATH}")
    print(f"\nModel version: {model_version}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-version", default="v1.0", help="Version tag stored in encoders.pkl")
    args = parser.parse_args()
    train(args.model_version)
