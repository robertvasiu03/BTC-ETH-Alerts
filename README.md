\# BTC/ETH Indicator and Alerts (Streamlit + CoinGecko)



A simple Streamlit app that pulls live BTC and ETH prices from the CoinGecko Demo API and shows:

\- BTC weekly close vs 50-week SMA

\- Alerts:

&nbsp; - 1 weekly close below the 50W SMA

&nbsp; - 2 weekly closes below the 50W SMA (exit)

&nbsp; - BTC new all-time high (vs last 365d daily-close max)

&nbsp; - ETH ≥ $5,000



\## Run locally

```bash

python -m venv .venv

.\\.venv\\Scripts\\activate   # Windows

\# source .venv/bin/activate  # macOS/Linux

pip install -r requirements.txt

streamlit run app.py

