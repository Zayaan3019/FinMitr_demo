"""
Backtesting and benchmarking utilities for time-series models.
"""

import numpy as np
import pandas as pd
from sklearn.metrics import mean_squared_error


# Example: Simple walk-forward backtest
def walk_forward_backtest(model, data: pd.Series, window: int = 50):
    preds = []
    for i in range(window, len(data)):
        train = data[i - window : i]
        model.fit(train.values)
        pred = model.predict(np.array([train.values]))[0]
        preds.append(pred)
    return preds


# Example: Benchmarking
def benchmark(y_true, y_pred) -> float:
    return mean_squared_error(y_true, y_pred)
