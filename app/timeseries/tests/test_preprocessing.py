import pandas as pd
from app.timeseries import preprocessing


def test_fill_missing():
    s = pd.Series([1, None, 2, None, 3])
    filled = preprocessing.fill_missing(s)
    assert filled.isnull().sum() == 0


def test_resample_series():
    idx = pd.date_range("2020-01-01", periods=4, freq="H")
    s = pd.Series([1, 2, 3, 4], index=idx)
    resampled = preprocessing.resample_series(s, "2H")
    assert len(resampled) == 2
