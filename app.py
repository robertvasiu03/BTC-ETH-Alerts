import os
import json
from datetime import datetime, timezone
import requests
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
import smtplib, ssl
from email.message import EmailMessage

# -------- config --------
API_BASE = "https://api.coingecko.com/api/v3"
STATE_FILE = "alerts_state.json"   # avoids duplicate alerts
DEMO_API_KEY = os.environ.get("COINGECKO_DEMO_KEY", "").strip()
HEADERS = {"x-cg-demo-api-key": DEMO_API_KEY} if DEMO_API_KEY else {}
VS = "usd"
# ------------------------

st.set_page_config(page_title="BTC/ETH Alerts", layout="wide")
st.title("BTC/ETH Indicator and Alerts")
st.caption("CoinGecko Demo API. BTC weekly close vs 50W SMA, BTC new ATH, ETH ≥ $5k.")

VS = "usd"

CURRENCY_SYMBOLS = {"usd": "$", "eur": "€", "gbp": "£"}
SYM = CURRENCY_SYMBOLS.get(VS.lower(), "$")

@st.cache_data(ttl=60)
def cg(url, params=None):
    params = params or {}
    r = requests.get(url, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()

@st.cache_data(ttl=300)
def get_spot(ids=("bitcoin","ethereum"), vs=VS):
    rows = cg(f"{API_BASE}/coins/markets", params={"vs_currency": vs, "ids": ",".join(ids)})
    df = pd.DataFrame(rows)[["id","symbol","name","current_price","ath","ath_change_percentage"]]
    df.set_index("id", inplace=True)
    return df

@st.cache_data(ttl=1800)
def get_daily(coin_id: str, days=365, vs=VS):
    try:
        data = cg(f"{API_BASE}/coins/{coin_id}/market_chart",
                  params={"vs_currency": vs, "days": days})
        df = pd.DataFrame(data["prices"], columns=["ts","price"])
    except requests.HTTPError as e:
        if "401" in str(e) or "Unauthorized" in str(e):
            ohlc = cg(f"{API_BASE}/coins/{coin_id}/ohlc",
                      params={"vs_currency": vs, "days": days})
            df = pd.DataFrame(ohlc, columns=["ts","open","high","low","close"])
            df["price"] = df["close"]
        else:
            raise
    df["time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    daily = df.set_index("time")[["price"]].sort_index().resample("1D").last().dropna()
    return daily

def weekly_close_and_sma50(daily: pd.DataFrame) -> pd.DataFrame:
    wk = daily["price"].resample("W-SUN").last().dropna()
    sma50 = wk.rolling(50).mean()
    return pd.DataFrame({"close": wk, "sma50": sma50})

def consecutive_below(close: pd.Series, sma: pd.Series) -> int:
    flags = (close < sma).astype(int).dropna().tolist()
    n = 0
    for v in reversed(flags):
        if v == 1: n += 1
        else: break
    return n

def load_state():
    if os.path.exists(STATE_FILE):
        return json.load(open(STATE_FILE, "r", encoding="utf-8"))
    return {}

def save_state(s):
    json.dump(s, open(STATE_FILE, "w", encoding="utf-8"), indent=2)

# data
try:
    spot = get_spot(("bitcoin","ethereum"), vs=VS)
    btc_daily = get_daily("bitcoin", vs=VS)
    wk = weekly_close_and_sma50(btc_daily)
    consec = consecutive_below(wk["close"], wk["sma50"])
except Exception as e:
    st.error(f"Data error: {e}")
    st.stop()

# header metrics
c1, c2 = st.columns(2)
c1.metric(f"BTC Price ({VS.upper()})", f"{SYM}{spot.loc['bitcoin','current_price']:,.0f}")
c2.metric(f"ETH Price ({VS.upper()})", f"{SYM}{spot.loc['ethereum','current_price']:,.0f}")
st.caption(f"Last updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")

# chart
st.subheader("BTC Weekly Close vs 50-Week SMA")
fig = go.Figure()
fig.add_trace(go.Scatter(x=wk.index, y=wk["close"], name="Weekly Close"))
fig.add_trace(go.Scatter(x=wk.index, y=wk["sma50"], name="50W SMA"))
fig.update_layout(height=420, margin=dict(l=10, r=10, t=30, b=10))
st.plotly_chart(fig, use_container_width=True)

# signals
alerts = []
state = load_state()

if consec >= 1 and state.get("btc_one_below") != wk.index[-1].strftime("%Y-%m-%d"):
    alerts.append(f"BTC has {consec} consecutive weekly close(s) below the 50W SMA (week ending {wk.index[-1].date()}).")
    state["btc_one_below"] = wk.index[-1].strftime("%Y-%m-%d")

if consec >= 2 and state.get("btc_two_below") != wk.index[-1].strftime("%Y-%m-%d"):
    alerts.append("BTC has 2 or more consecutive weekly closes below the 50W SMA.")
    state["btc_two_below"] = wk.index[-1].strftime("%Y-%m-%d")

prior_ath_close = float(btc_daily["price"].max())
btc_spot = float(spot.loc["bitcoin","current_price"])
if btc_spot > prior_ath_close and state.get("btc_new_ath") != datetime.now(timezone.utc).date().isoformat():
    alerts.append(f"BTC printed a new all-time high: ${btc_spot:,.0f} (prior daily-close ATH ${prior_ath_close:,.0f}).")
    state["btc_new_ath"] = datetime.now(timezone.utc).date().isoformat()

eth_spot = float(spot.loc["ethereum","current_price"])
if eth_spot >= 5000 and state.get("eth_5k") != datetime.now(timezone.utc).date().isoformat():
    alerts.append(f"ETH has broken $5,000: ${eth_spot:,.0f}.")
    state["eth_5k"] = datetime.now(timezone.utc).date().isoformat()

state["last_run_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
state["consecutive_below"] = consec
save_state(state)

def send_email(subject: str, body: str, to_addr: str | None = None):
    FROM = os.environ.get("ALERTS_EMAIL_FROM")
    TO   = to_addr or os.environ.get("ALERTS_EMAIL_TO", FROM)
    USER = os.environ.get("ALERTS_EMAIL_USER", FROM)
    PASS = os.environ.get("ALERTS_EMAIL_PASS")
    HOST = os.environ.get("ALERTS_SMTP_SERVER", "smtp.gmail.com")
    PORT = int(os.environ.get("ALERTS_SMTP_PORT", "587"))

    if not (FROM and TO and USER and PASS):
        raise RuntimeError("Email env vars not set")

    msg = EmailMessage()
    msg["From"] = FROM
    msg["To"] = TO
    msg["Subject"] = subject
    msg.set_content(body)

    ctx = ssl.create_default_context()
    with smtplib.SMTP(HOST, PORT) as s:
        s.starttls(context=ctx)
        s.login(USER, PASS)
        s.send_message(msg)

st.subheader("Signals")

if alerts:
    # 1) Show alerts on the page
    for a in alerts:
        st.success(a)

    # 2) Send email notification
    subject = f"[Crypto Alerts] {len(alerts)} new signal(s)"
    SYM = "$"  # Currency symbol

    body = "\n".join([
        *alerts,
        "",
        f"BTC spot: {SYM}{spot.loc['bitcoin','current_price']:,.0f}",
        f"BTC last weekly close: {SYM}{wk['close'].iloc[-1]:,.0f}",
        f"BTC 50W SMA: {SYM}{wk['sma50'].iloc[-1]:,.0f}" if pd.notna(wk['sma50'].iloc[-1]) else "BTC 50W SMA: n/a",
        f"ETH spot: {SYM}{spot.loc['ethereum','current_price']:,.0f}",
        f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC",
    ])

    try:
        send_email(subject, body)
        st.info("Email sent successfully.")
    except Exception as e:
        st.warning(f"Email failed: {e}")

else:
    st.info("No new alerts right now.")

with st.expander("Email test"):
    if st.button("Send me a test email"):
        try:
            send_email("Test from BTC/ETH Alerts", "This is a test message.")
            st.success("Test email sent.")
        except Exception as e:
            st.error(f"Test failed: {e}")

with st.expander("Details"):
    st.write("Spot / ATH")
    st.dataframe(spot)
    st.write("Recent weekly closes")
    st.dataframe(wk.tail(12))
