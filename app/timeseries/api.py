"""
API endpoints for time-series analysis (to be integrated with Finguru's main API).
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from app.timeseries import preprocessing, features, anomaly, backtest, models

router = APIRouter()


# Pydantic model for preprocess endpoint
class PreprocessRequest(BaseModel):
    data: List[Optional[float]]
    method: Optional[str] = "ffill"


@router.post("/timeseries/preprocess")
def preprocess_endpoint(request: PreprocessRequest):
    import pandas as pd

    series = pd.Series(request.data)
    result = preprocessing.fill_missing(series, request.method)
    return result.tolist()


# ... Add more endpoints for features, anomaly detection, modeling, etc.
