import pandas as pd
import numpy as np
from app.timeseries import backtest


class DummyModel:
    def fit(self, X):
        pass

    def predict(self, X):
        return [X[0][-1]]


def test_walk_forward_backtest():
    s = pd.Series(np.arange(100))
    model = DummyModel()
    preds = backtest.walk_forward_backtest(model, s, window=10)
    assert len(preds) == 90
