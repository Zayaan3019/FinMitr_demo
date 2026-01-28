import pandas as pd
from app.timeseries import models

def test_fit_arima():
    s = pd.Series([1,2,3,4,5,6,7,8,9,10])
    result = models.fit_arima(s, order=(1,1,0))
    assert hasattr(result, 'forecast')

def test_fit_garch():
    s = pd.Series([1,2,3,4,5,6,7,8,9,10])
    result = models.fit_garch(s, p=1, q=1)
    assert hasattr(result, 'forecast')
