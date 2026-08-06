"""
Visualization tools for time-series analysis.
"""

import matplotlib.pyplot as plt
import pandas as pd


def plot_series(data: pd.Series, title: str = "Time Series"):
    plt.figure(figsize=(12, 6))
    plt.plot(data)
    plt.title(title)
    plt.xlabel("Time")
    plt.ylabel("Value")
    plt.show()
