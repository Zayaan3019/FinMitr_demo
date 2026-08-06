"""
Transformer-based models for time-series forecasting.
"""

import numpy as np
import pandas as pd
import torch
import torch.nn as nn


class TimeSeriesTransformer(nn.Module):
    def __init__(self, input_size=1, d_model=64, nhead=4, num_layers=2, output_size=1):
        super(TimeSeriesTransformer, self).__init__()
        self.embedding = nn.Linear(input_size, d_model)
        encoder_layer = nn.TransformerEncoderLayer(d_model=d_model, nhead=nhead)
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        self.fc = nn.Linear(d_model, output_size)

    def forward(self, x):
        x = self.embedding(x)
        x = self.transformer(x)
        x = self.fc(x[-1])
        return x


# Example: Fit Transformer to univariate series (stub)
def fit_transformer(data: pd.Series, epochs=10, batch_size=16, d_model=64):
    # Data preparation and training logic to be implemented
    pass
