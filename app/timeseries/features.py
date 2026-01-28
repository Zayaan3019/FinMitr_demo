"""
Feature extraction for time-series: lags, rolling stats, Fourier, wavelet, etc.
"""

import numpy as np
import pandas as pd

# Example: Create lag features
def create_lags(data: pd.Series, lags: int = 3) -> pd.DataFrame:
    return pd.concat([data.shift(i) for i in range(1, lags+1)], axis=1)

# Example: Rolling statistics
def rolling_stats(data: pd.Series, window: int = 5) -> pd.DataFrame:
    return pd.DataFrame({
        'mean': data.rolling(window).mean(),
        'std': data.rolling(window).std(),
        'min': data.rolling(window).min(),
        'max': data.rolling(window).max()
    })
