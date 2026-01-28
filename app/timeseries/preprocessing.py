"""
Preprocessing utilities for time-series data: resampling, missing data, normalization, etc.
"""

import numpy as np
import pandas as pd

# Example: Fill missing values
def fill_missing(data: pd.Series, method: str = 'ffill') -> pd.Series:
    return data.fillna(method=method)

# Example: Resample time-series
def resample_series(data: pd.Series, rule: str = 'D') -> pd.Series:
    return data.resample(rule).mean()

# Example: Normalize series
def normalize_series(data: pd.Series) -> pd.Series:
    return (data - data.mean()) / data.std()
