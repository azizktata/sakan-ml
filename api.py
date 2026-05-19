"""
SAKAN ML Prediction Microservice — FastAPI

Run:
  uvicorn ml.api:app --host 0.0.0.0 --port 8001
  # or from the ml/ directory:
  uvicorn api:app --host 0.0.0.0 --port 8001

Endpoint:
  POST /predict
  Body: PredictRequest
  Response: PredictResponse
"""

import os
import pickle
from pathlib import Path
from contextlib import asynccontextmanager

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator

# ── Model loading ─────────────────────────────────────────────────────────────

SCRIPT_DIR   = Path(__file__).parent
MODEL_PATH   = SCRIPT_DIR / "model.pkl"
ENCODER_PATH = SCRIPT_DIR / "encoders.pkl"

_model = None
_meta  = None


def load_model():
    global _model, _meta
    if not MODEL_PATH.exists():
        return False
    with open(MODEL_PATH, "rb") as f:
        _model = pickle.load(f)
    with open(ENCODER_PATH, "rb") as f:
        _meta = pickle.load(f)
    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    ok = load_model()
    if ok:
        print(f"[ml-api] Model loaded — version {_meta.get('model_version', '?')}, "
              f"MAPE {_meta.get('test_mape', '?'):.1f}%")
    else:
        print("[ml-api] WARNING: model.pkl not found — run ml/train.py first")
    yield


app = FastAPI(title="SAKAN ML Service", version="1.0.0", lifespan=lifespan)

# ── Schemas ───────────────────────────────────────────────────────────────────

VALID_TRANSACTION_TYPES = {"vente", "location"}
VALID_PROPERTY_TYPES    = {"apartment", "villa", "house", "land", "commercial", "office"}
VALID_CONDITIONS        = {"neuf", "bon_etat", "a_renover"}


class PredictRequest(BaseModel):
    city:             str         = Field(..., examples=["tunis"])
    property_type:    str         = Field(..., examples=["apartment"])
    transaction_type: str         = Field(..., examples=["vente"])
    surface:          float       = Field(..., gt=0, examples=[80])
    bedrooms:         int         = Field(default=2, ge=0)
    bathrooms:        int | None  = Field(default=None, ge=0, examples=[1])
    floor:            int | None  = Field(default=None, ge=0, examples=[2])
    condition:        str         = Field(default="bon_etat", examples=["bon_etat"])
    zone_score:       int         = Field(default=3, ge=1, le=5)
    amenities_count:  int         = Field(default=0, ge=0)
    # Optional location context — not model features, used for richer confidence/response
    governorate:      str | None  = Field(default=None, examples=["Tunis"])
    neighborhood:     str | None  = Field(default=None, examples=["Khezama"])
    # Extended amenity sub-inputs
    garden_surface:   float | None = Field(default=None, ge=0, examples=[60])
    parking_spaces:   int   | None = Field(default=None, ge=0, examples=[1])
    terrace_surface:  float | None = Field(default=None, ge=0, examples=[20])
    building_age:     int   | None = Field(default=None, ge=0, examples=[15])
    # Geographic coordinates — passed through for logging; not used as model features in v3.x
    latitude:         float | None = Field(default=None, examples=[36.8])
    longitude:        float | None = Field(default=None, examples=[10.18])

    @model_validator(mode="after")
    def validate_enums(self):
        if self.transaction_type not in VALID_TRANSACTION_TYPES:
            raise ValueError(f"transaction_type must be one of {VALID_TRANSACTION_TYPES}")
        if self.property_type not in VALID_PROPERTY_TYPES:
            raise ValueError(f"property_type must be one of {VALID_PROPERTY_TYPES}")
        if self.condition not in VALID_CONDITIONS:
            raise ValueError(f"condition must be one of {VALID_CONDITIONS}")
        return self


class PredictResponse(BaseModel):
    low:          int
    mid:          int
    high:         int
    unit:         str
    confidence:   float
    model_version: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def round_price(value: float, transaction_type: str) -> int:
    """Round to nearest 1000 DT (sale) or 50 DT (rent), matching the frontend engine."""
    step = 50 if transaction_type == "location" else 1000
    return int(round(value / step) * step)


def compute_confidence(city: str, property_type: str, transaction_type: str) -> float:
    if _meta is None:
        return 0.4
    lookup = _meta.get("confidence_lookup", {})
    key = (city, property_type, transaction_type)
    info = lookup.get(key)
    if not info or info["count"] < 3:
        return 0.4
    cv = info["cv"]
    confidence = max(0.0, min(1.0, 1.0 - cv))
    sample_boost = min(0.1, info["count"] / 500 * 0.1)
    return round(min(1.0, confidence + sample_boost), 2)


def _impute(value, key: str, default: float) -> float:
    """Use the provided value, or fall back to training median, or a hardcoded default."""
    if value is not None:
        return float(value)
    if _meta and "medians" in _meta and key in _meta["medians"]:
        return float(_meta["medians"][key])
    return default


def build_feature_row(req: PredictRequest) -> pd.DataFrame:
    row = {
        "city":             req.city,
        "property_type":    req.property_type,
        "transaction_type": req.transaction_type,
        "condition_state":  req.condition,
        "surface":          float(req.surface),
        "bedrooms":         int(req.bedrooms),
        "bathrooms":        _impute(req.bathrooms,       "bathrooms",       0.0),
        "floor":            _impute(req.floor,           "floor",           0.0),
        "zone_score":       int(req.zone_score),
        "amenities_count":  int(req.amenities_count),
        "garden_surface":   _impute(req.garden_surface,  "garden_surface",  0.0),
        "parking_spaces":   _impute(req.parking_spaces,  "parking_spaces",  0.0),
        "terrace_surface":  _impute(req.terrace_surface, "terrace_surface", 0.0),
        "building_age":     _impute(req.building_age,    "building_age",    15.0),
    }
    df = pd.DataFrame([row])
    # Cast categoricals to match training dtype
    for col in ["city", "property_type", "transaction_type", "condition_state"]:
        df[col] = df[col].astype("category")
    return df

# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status": "ok" if _model is not None else "no_model",
        "model_version": _meta.get("model_version") if _meta else None,
    }


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    if _model is None:
        raise HTTPException(
            status_code=503,
            detail="Model not loaded. Run ml/train.py to train the model first.",
        )

    df = build_feature_row(req)

    try:
        raw_pred = float(_model.predict(df)[0])
        # If model was trained on log1p(target), convert back to DT/m2
        if _meta and _meta.get("log_target"):
            import math
            price_per_m2_pred = math.expm1(raw_pred)
        else:
            price_per_m2_pred = raw_pred
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Prediction error: {exc}")

    mid_raw  = price_per_m2_pred * req.surface
    mid      = round_price(mid_raw, req.transaction_type)
    low      = round_price(mid_raw * 0.88, req.transaction_type)
    high     = round_price(mid_raw * 1.12, req.transaction_type)
    unit     = "DT/mois" if req.transaction_type == "location" else "DT"

    confidence    = compute_confidence(req.city, req.property_type, req.transaction_type)
    model_version = _meta.get("model_version", "unknown") if _meta else "unknown"
    # neighborhood is passed through for context but does not alter the model features

    return PredictResponse(
        low=low,
        mid=mid,
        high=high,
        unit=unit,
        confidence=confidence,
        model_version=model_version,
    )
