import pandas as pd
from app.timeseries import features


def test_create_lags():
    s = pd.Series([1, 2, 3, 4, 5])
    lags = features.create_lags(s, lags=2)
    assert lags.shape[1] == 2


def test_rolling_stats():
    s = pd.Series([1, 2, 3, 4, 5])
    stats = features.rolling_stats(s, window=2)
    assert "mean" in stats.columns
