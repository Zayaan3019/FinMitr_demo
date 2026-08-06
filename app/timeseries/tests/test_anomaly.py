import pandas as pd
from app.timeseries import anomaly


def test_detect_anomalies():
    s = pd.Series([1] * 10 + [100] + [1] * 10)
    preds = anomaly.detect_anomalies(s)
    assert preds.sum() > 0
