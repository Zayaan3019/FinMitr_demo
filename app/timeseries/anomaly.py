"""
Anomaly and regime change detection for time-series.
"""

import numpy as np
import pandas as pd
from pyod.models.iforest import IForest


# Example: Isolation Forest anomaly detection
def detect_anomalies(data: pd.Series) -> pd.Series:
    model = IForest()
    model.fit(data.values.reshape(-1, 1))
    preds = model.predict(data.values.reshape(-1, 1))
    return pd.Series(preds, index=data.index)
