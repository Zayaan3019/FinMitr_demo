import pandas as pd
from app.timeseries import visualize

def test_plot_series():
    s = pd.Series([1,2,3,4,5])
    # This should display a plot without error
    visualize.plot_series(s, title='Test Plot')
