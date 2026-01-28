"""
Model zoo for time-series: ARIMA, GARCH, Prophet, LSTM, Transformer, etc.
"""

import numpy as np
import pandas as pd
from statsmodels.tsa.arima.model import ARIMA
from arch import arch_model
from prophet import Prophet

# ARIMA example
def fit_arima(data: pd.Series, order=(1,1,1)):
    model = ARIMA(data, order=order)
    return model.fit()

# GARCH example
def fit_garch(data: pd.Series, p=1, q=1):
    model = arch_model(data, vol='Garch', p=p, q=q)
    return model.fit()

# Prophet example
def fit_prophet(data: pd.Series):
    df = pd.DataFrame({'ds': data.index, 'y': data.values})
    model = Prophet()
    model.fit(df)
    return model
