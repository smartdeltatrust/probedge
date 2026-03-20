# Risk-Neutral Density Probability Cone

Interactive options analytics tool that extracts the risk-neutral density (RND) 
from options prices and visualizes it as a probability cone over historical prices.

## Features
- Real-time options chain data via Massive (Polygon.io)
- Historical OHLC from Financial Modeling Prep
- Breeden-Litzenberger RND extraction with PCHIP interpolation
- 68%/95% probability bands visualization
- Bloomberg/tastytrade-style dark theme

## Stack
- Python 3.13 | Streamlit | Plotly | NumPy | SciPy

## Setup
1. Clone the repo
2. Create .env with MASSIVE_API_KEY and FMP_API_KEY
3. pip install -r requirements.txt
4. streamlit run app.py

## Data Providers
- Massive (ex-Polygon.io) — Options snapshots with Greeks, IV, OI
- FMP — Historical OHLC stock data

## License
Private — All rights reserved.
