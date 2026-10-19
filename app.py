# Hoosha Pro-6E/G + Universal News Filter (30m before/after + auto analysis)
# LIVE: BTC, USDCAD | DEMO: EURUSD, XAUUSD | WATCH: US500
# News: ForexFactory weekly (faireconomy) High impact, 30m block all symbols + post analysis
# Not financial advice. Test on demo first.
import os, time, re, requests, logging, threading
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from flask import Flask

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("Pro6News")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "PUT_TOKEN")
TELEGRAM_CHAT_ID = str(os.getenv("TELEGRAM_CHAT_ID", "PUT_ID"))
CRYPTO_SYMBOLS = os.getenv("CRYPTO_SYMBOLS", "BTC/USDT").split(",")
FOREX_SYMBOLS = os.getenv("FOREX_SYMBOLS", "USDCAD=X,EURUSD=X").split(",")
INDEX_SYMBOLS = os.getenv("INDEX_SYMBOLS", "ES=F").split(",")
GOLD_SYMBOLS = os.getenv("GOLD_SYMBOLS", "GC=F").split(",")
BALANCE = float(os.getenv("BALANCE", "1000"))
RISK_PERCENT = float(os.getenv("RISK_PERCENT", "0.5"))
MANUAL_LOT = os.getenv("MANUAL_LOT_OVERRIDE", "")
INTERVAL_MIN = int(os.getenv("INTERVAL_MIN", "30"))
NEWS_ENABLED = os.getenv("NEWS_ENABLED", "1") == "1"
NEWS_BLOCK_MINUTES = int(os.getenv("NEWS_BLOCK_MINUTES", "30"))
NEWS_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
OFFSET = 0
NEWS_MANUAL_PAUSE = False
NEWS_CACHE = []
NEWS_LAST_FETCH = 0
WARNED = set()
ANALYZED = set()
TRADE_COUNT = {}

PRO6_CFG = {
    "BTCUSD": {"status": "LIVE_CANDIDATE", "score_thr": 55, "setups": ["S1","S2","S4","S5"], "htf_filter": "aligned", "min_tp1_R": 1.3, "sl_buf_ATR": 0.3, "session": "ALL", "max_per_day": 3},
    "USDCAD": {"status": "LIVE_CANDIDATE", "score_thr": 60, "setups": ["S1","S2","S4"], "htf_filter": "any", "min_tp1_R": 1.3, "sl_buf_ATR": 0.1, "session": "07-17 UTC", "max_per_day": 3},
    "US500": {"status": "WATCH", "score_thr": 55, "setups": ["S1","S2","S4"], "htf_filter": "aligned", "min_tp1_R": 1.3, "sl_buf_ATR": 0.1, "session": "ALL", "S2_trend_only": True, "max_per_day": 3},
    "EURUSD": {"status": "LIVE-DEMO", "score_thr": 62, "setups": ["S4","S5"], "htf_filter": "va_fade", "regime": "RANGE", "min_tp1_R": 1.5, "sl_buf_ATR": 0.15, "session": "07-17 UTC", "max_per_day": 1},
    "XAUUSD": {"status": "LIVE-DEMO", "score_thr": 60, "setups": ["S1","S4"], "htf_filter": "aligned", "regime": "TREND", "min_tp1_R": 1.5, "sl_buf_ATR": 0.35, "session": "12-20 UTC", "max_per_day": 3},
    "_universal": {"score_thr": 55, "htf_filter": "aligned", "min_tp1_R": 1.3, "sl_buf_ATR": 0.1, "max_per_day": 3},
}
SYMBOL_CURR = {"BTCUSD": ["USD"], "USDCAD": ["USD","CAD"], "EURUSD": ["USD","EUR"], "XAUUSD": ["USD"], "US500": ["USD"], "UNIVERSAL": ["USD","EUR","CAD"]}

app = Flask(__name__)
@app.route("/")
def home(): return f"OK Hoosha Pro-6 News Alive {datetime.now()}"
@app.route("/ping")
def ping(): return "pong"

def send_tg(t):
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": t, "parse_mode": "Markdown"}, timeout=15)
    except Exception as e: logger.error(e)

def match_cfg(sym):
    u = sym.upper()
    if "EURUSD" in u or "EUR/USD" in u: return "EURUSD", PRO6_CFG["EURUSD"]
    if "XAU" in u or "GC=F" in u or "GOLD" in u or "PAXG" in u: return "XAUUSD", PRO6_CFG["XAUUSD"]
    if "BTC" in u: return "BTCUSD", PRO6_CFG["BTCUSD"]
    if "USDCAD" in u or ("CAD" in u and "USD" in u): return "USDCAD", PRO6_CFG["USDCAD"]
    if "ES=F" in u or "US500" in u or "SPX" in u or "SPY" in u: return "US500", PRO6_CFG["US500"]
    if "GBPUSD" in u: return "GBPUSD", {"status": "DISABLED"}
    if "USDJPY" in u: return "USDJPY", {"status": "DISABLED"}
    return "UNIVERSAL", PRO6_CFG["_universal"]

def session_ok(cfg):
    s = cfg.get("session", "ALL")
    if s == "ALL": return True
    h = datetime.now(timezone.utc).hour
    if "07-17" in s: return 7 <= h < 17
    if "12-20" in s: return 12 <= h < 20
    return True

def spread_price(sym):
    u = sym.upper()
    if "EURUSD" in u: return 0.00008
    if "USDCAD" in u: return 0.00015
    if "ES" in u or "US500" in u or "SPX" in u: return 0.5
    if "BTC" in u: return 20.0
    if "XAU" in u or "GC" in u or "GOLD" in u: return 0.35
    return 0.0

# ---------- NEWS ----------
def fetch_news_cached(force=False):
    global NEWS_CACHE, NEWS_LAST_FETCH
    if not force and NEWS_CACHE and time.time()-NEWS_LAST_FETCH < 1800: return NEWS_CACHE
    try:
        r = requests.get(NEWS_URL, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        data = r.json()
        NEWS_CACHE = [e for e in data if str(e.get("impact","")).lower() == "high"]
        NEWS_LAST_FETCH = time.time()
        logger.info(f"news fetched {len(NEWS_CACHE)} high")
    except Exception as e: logger.error(f"news fetch failed: {e}")
    return NEWS_CACHE

def parse_ev_time(ev):
    try:
        s = ev.get("date","")
        dt = datetime.fromisoformat(s.replace("Z","+00:00"))
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except: return None

def is_news_blocked_for(map_name):
    if not NEWS_ENABLED: return False, None
    if NEWS_MANUAL_PAUSE: return True, {"title": "Manual pause", "country": "-"}
    evs = fetch_news_cached()
    now = datetime.now(timezone.utc)
    win = timedelta(minutes=NEWS_BLOCK_MINUTES)
    rel = SYMBOL_CURR.get(map_name, ["USD"])
    for ev in evs:
        c = ev.get("country","")
        if c == "USD": pass
        elif c not in rel: continue
        dt = parse_ev_time(ev)
        if not dt: continue
        if dt - win <= now <= dt + win: return True, ev
    return False, None

def get_num(x):
    if x is None or str(x).strip() in ("", "-"): return None
    m = re.search(r"-?\d+(\.\d+)?", str(x).replace(",",""))
    return float(m.group(0)) if m else None

def analyze_event_text(ev, dt):
    title = ev.get("title",""); country = ev.get("country","")
    actual = ev.get("actual",""); fore = ev.get("forecast",""); prev = ev.get("previous","")
    a = get_num(actual); f = get_num(fore)
    bias = "NEUTRAL"; reason = "as-expected"
    tl = title.lower()
    if a is not None and f is not None:
        if "unemployment" in tl:
            bias = "USD_STRONG" if a < f else "USD_WEAK" if a > f else "NEUTRAL"
            reason = "lower unemployment = strong USD"
        elif any(k in tl for k in ["nfp","non-farm","payroll","gdp","retail","ism","pce","cpi","ppi","employment","jobless","claims"]):
            bias = "USD_STRONG" if a > f else "USD_WEAK" if a < f else "NEUTRAL"
            reason = "beat = strong USD, miss = weak USD"
        elif any(k in tl for k in ["rate","fomc","federal","powell","interest"]):
            bias = "USD_STRONG" if a >= f else "NEUTRAL"
            reason = "hawkish = strong USD"
    if bias == "USD_STRONG": impl = "Bearish EURUSD/XAUUSD, Bullish USDCAD, caution long US500/BTC"
    elif bias == "USD_WEAK": impl = "Bullish EURUSD/XAUUSD, Bearish USDCAD, supportive US500/BTC"
    else: impl = "Mixed. Wait for trend re-confirmation, no chase."
    return f"📰 *News: {title} ({country})*\nTime: `{dt.strftime('%Y-%m-%d %H:%M')} UTC`\nActual: `{actual}` vs Forecast: `{fore}` Prev: `{prev}`\nVerdict: *{bias}* ({reason})\nImplication: {impl}\nPause {NEWS_BLOCK_MINUTES}m after event still active. Not financial advice."

def check_news_alerts():
    try:
        evs = fetch_news_cached()
        now = datetime.now(timezone.utc)
        win = timedelta(minutes=NEWS_BLOCK_MINUTES)
        for ev in evs:
            title = ev.get("title",""); dt = parse_ev_time(ev)
            if not dt: continue
            key = f"{title}|{ev.get('date')}"
            to_ev = (dt - now).total_seconds()/60
            if 0 < to_ev <= NEWS_BLOCK_MINUTES and key not in WARNED:
                WARNED.add(key)
                send_tg(f"⏸ *High-Impact in {int(to_ev)}m: {title} ({ev.get('country')})*\nTime UTC: `{dt.strftime('%Y-%m-%d %H:%M')}`\nAll symbols paused {NEWS_BLOCK_MINUTES}m before/after.")
            since = (now - dt).total_seconds()/60
            if 1 <= since <= 120 and key not in ANALYZED and ev.get("actual"):
                ANALYZED.add(key)
                send_tg(analyze_event_text(ev, dt))
    except Exception as e: logger.error(f"news alerts: {e}")

def upcoming_text():
    evs = fetch_news_cached(force=False)
    now = datetime.now(timezone.utc)
    items = []
    for ev in evs:
        dt = parse_ev_time(ev)
        if dt and dt > now - timedelta(minutes=30): items.append((dt, ev))
    items = sorted(items, key=lambda x: x[0])[:6]
    if not items: return "No High-Impact events found this week."
    lines = []
    for dt, ev in items:
        lines.append(f"{dt.strftime('%m-%d %H:%M')} UTC - {ev.get('country')} {ev.get('title')}")
    return "🗓 High-Impact this week:\n" + "\n".join(lines)

# ---------- indicators / data ----------
def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def sma(s, n): return s.rolling(n).mean()
def atr(df, n=14):
    tr = pd.concat([df['high']-df['low'], (df['high']-df['close'].shift()).abs(), (df['low']-df['close'].shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def pivots_confirmed(df, l=3):
    use = df.iloc[:-3] if len(df) > 10 else df
    highs, lows = [], []
    for i in range(l, len(use)-l):
        if use['high'].iloc[i] == use['high'].iloc[i-l:i+l+1].max(): highs.append((i, float(use['high'].iloc[i])))
        if use['low'].iloc[i] == use['low'].iloc[i-l:i+l+1].min(): lows.append((i, float(use['low'].iloc[i])))
    return highs, lows

def efficiency_regime(df, n=10, thr=0.3):
    if len(df) < n+2: return "RANGE", 0.0
    c = df['close']
    er = abs(c.iloc[-1]-c.iloc[-1-n]) / (c.diff().abs().iloc[-n:].sum() + 1e-9)
    return ("TREND" if er > thr else "RANGE"), float(er)

def fetch_crypto(sym, tf, lim=300):
    import ccxt
    ex = ccxt.binance({"enableRateLimit": True})
    o = ex.fetch_ohlcv(sym, tf, limit=lim)
    return pd.DataFrame(o, columns=["ts","open","high","low","close","volume"])[["open","high","low","close","volume"]]

def fetch_yf(sym, interval, period="6mo"):
    import yfinance as yf
    df = yf.download(sym, period=period, interval=interval, progress=False, auto_adjust=True)
    if df.empty: raise RuntimeError("no data "+sym)
    if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
    df.columns = [str(c).lower() for c in df.columns]
    df = df.reset_index(drop=True)
    m = {}
    for c in df.columns:
        lc = c.lower()
        if "open" in lc: m[c] = "open"
        elif "high" in lc: m[c] = "high"
        elif "low" in lc: m[c] = "low"
        elif "close" in lc and "adj" not in lc: m[c] = "close"
        elif "volume" in lc: m[c] = "volume"
    df = df.rename(columns=m)
    if "volume" not in df.columns or df["volume"].sum() == 0: df["volume"] = 1000.0
    return df[["open","high","low","close","volume"]].dropna().reset_index(drop=True)

def resample_4h(df1h):
    rows = []
    for i in range(0, len(df1h)-3, 4):
        w = df1h.iloc[i:i+4]
        rows.append({"open": float(w['open'].iloc[0]), "high": float(w['high'].max()), "low": float(w['low'].min()), "close": float(w['close'].iloc[-1]), "volume": float(w['volume'].sum())})
    return pd.DataFrame(rows)

def get_tf(kind, sym, tf):
    if kind == "c":
        mp = {"D1":"1d","H4":"4h","M15":"15m","M5":"5m"}
        return fetch_crypto(sym, mp.get(tf,"15m"))
    if tf == "D1": return fetch_yf(sym, "1d", "1y")
    if tf == "H4": return resample_4h(fetch_yf(sym, "1h", "6mo"))
    if tf == "M15": return fetch_yf(sym, "15m", "1mo")
    if tf == "M5": return fetch_yf(sym, "5m", "5d")
    return fetch_yf(sym, "15m", "1mo")

def wyckoff_d1(df):
    df = df.copy(); df['atr20'] = atr(df,20); df['volsma'] = sma(df['volume'],20)
    highs, lows = pivots_confirmed(df)
    bias, phase = "NEUTRAL", "RANGE"
    if len(highs) >= 2 and len(lows) >= 2:
        if highs[-1][1] > highs[-2][1] and lows[-1][1] > lows[-2][1]: phase, bias = "MARKUP", "LONG"
        elif highs[-1][1] < highs[-2][1] and lows[-1][1] < lows[-2][1]: phase, bias = "MARKDOWN", "SHORT"
    last5 = df.iloc[-6:-1] if len(df) > 6 else df.iloc[-5:]
    climax = bool(((last5['volume']>2.5*last5['volsma']) & ((last5['close']-last5['open']).abs()>2*last5['atr20'])).any())
    mn = df['low'].iloc[-21:-1].min(); mx = df['high'].iloc[-21:-1].max()
    last = df.iloc[-2] if len(df) > 2 else df.iloc[-1]
    spring = last['low'] < mn and last['close'] > mn and last['volume'] > 1.5*df['volsma'].iloc[-2]
    utad = last['high'] > mx and last['close'] < mx and last['volume'] > 1.5*df['volsma'].iloc[-2]
    score20 = 20 if (spring or utad or bias != "NEUTRAL") else 0
    if spring: phase, bias = "ACCUM", "LONG"
    elif utad: phase, bias = "DISTRIB", "SHORT"
    prev_h = float(df['high'].iloc[-3]) if len(df) > 3 else float(df['high'].iloc[-1])
    prev_l = float(df['low'].iloc[-3]) if len(df) > 3 else float(df['low'].iloc[-1])
    return {"bias":bias,"phase":phase,"climax":bool(climax),"score":score20,"prev_h":prev_h,"prev_l":prev_l}

def h4_module(df):
    df = df.copy(); df['ema20'] = ema(df['close'],20); df['atr'] = atr(df,14)
    i = len(df)-2; ai = "NEUTRAL"
    if i > 12:
        br_up = df['close'].iloc[i] > df['high'].iloc[i-10:i].max()
        br_dn = df['close'].iloc[i] < df['low'].iloc[i-10:i].min()
        if df['close'].iloc[i] > df['ema20'].iloc[i] and df['ema20'].iloc[i] > df['ema20'].iloc[i-3] and br_up: ai = "LONG"
        elif df['close'].iloc[i] < df['ema20'].iloc[i] and df['ema20'].iloc[i] < df['ema20'].iloc[i-3] and br_dn: ai = "SHORT"
    highs, lows = pivots_confirmed(df)
    levels = []
    for _,p in highs+lows:
        touches = ((df['high']-p).abs()<0.3*df['atr'].iloc[i]).sum()+((df['low']-p).abs()<0.3*df['atr'].iloc[i]).sum()
        if touches >= 2: levels.append(float(p))
    regime, er = efficiency_regime(df.iloc[:i+1])
    w = df.iloc[max(0,i-30):i+1]; lo,hi = w['low'].min(), w['high'].max()
    edges = np.linspace(lo,hi,51); vols = np.zeros(50)
    for _,r in w.iterrows():
        il = max(0,min(49,np.searchsorted(edges,r['low'])-1)); ih = max(0,min(49,np.searchsorted(edges,r['high'])-1))
        for k in range(il,ih+1): vols[k] += r['volume']/max(1,ih-il+1)
    pi = int(np.argmax(vols)); poc = (edges[pi]+edges[pi+1])/2
    tot = vols.sum(); order = np.argsort(vols)[::-1]; cum = 0; va = set()
    for k in order:
        va.add(k); cum += vols[k]
        if cum >= tot*0.7: break
    vah, val = float(edges[max(va)+1]), float(edges[min(va)])
    close = float(df['close'].iloc[i])
    va_bias = "ABOVE" if close > vah else "BELOW" if close < val else "MID"
    return {"ai":ai,"levels":levels,"poc":float(poc),"vah":vah,"val":val,"va_bias":va_bias,"atr":float(df['atr'].iloc[i]),"regime":regime,"er":er,"piv_h":[x[1] for x in highs[-3:]],"piv_l":[x[1] for x in lows[-3:]]}

def smc_m15(df):
    df = df.copy(); df['atr'] = atr(df,14)
    highs, lows = pivots_confirmed(df)
    bos = None
    if highs and lows:
        c = float(df['close'].iloc[-2])
        if c > highs[-1][1]: bos = "LONG"
        elif c < lows[-1][1]: bos = "SHORT"
    obs, fvgs = [], []
    for i in range(2,len(df)-1):
        a = float(df['atr'].iloc[i])
        if df['close'].iloc[i]-df['open'].iloc[i-1] > 1.5*a and df['close'].iloc[i-1] < df['open'].iloc[i-1]:
            obs.append((float(df['low'].iloc[i-1]),float(df['high'].iloc[i-1])))
        if df['low'].iloc[i] > df['high'].iloc[i-2]: fvgs.append({"dir":"BULL","edge":float(df['low'].iloc[i]),"mid":float((df['low'].iloc[i]+df['high'].iloc[i-2])/2)})
        if df['high'].iloc[i] < df['low'].iloc[i-2]: fvgs.append({"dir":"BEAR","edge":float(df['high'].iloc[i]),"mid":float((df['high'].iloc[i]+df['low'].iloc[i-2])/2)})
    return {"bos":bos,"obs":obs[-3:],"fvgs":fvgs[-3:],"piv_h":[x[1] for x in highs[-3:]],"piv_l":[x[1] for x in lows[-3:]]}

def detect_setup(df15, h4, s15, no_vol=False):
    df = df15.copy(); df['atr'] = atr(df,14); df['volsma'] = sma(df['volume'],20); df['ema20'] = ema(df['close'],20)
    i = len(df)-2
    row, prev = df.iloc[i], df.iloc[i-1]
    zones = h4['levels']+[h4['vah'],h4['val'],float(df['ema20'].iloc[i])]+[z[1] for z in s15['obs']]
    def in_zone(p): return any(abs(p-z) < 0.5*df['atr'].iloc[i] for z in zones)
    sig, stype = "HOLD", "none"
    if df['low'].iloc[i-1] < df['low'].iloc[i-21:i-1].min() and df['close'].iloc[i-1] > df['low'].iloc[i-21:i-1].min() and row['close'] > prev['high']: sig, stype = "LONG", "S1_sweep_reversal"
    if sig == "HOLD" and df['high'].iloc[i-1] > df['high'].iloc[i-21:i-1].max() and df['close'].iloc[i-1] < df['high'].iloc[i-21:i-1].max() and row['close'] < prev['low']: sig, stype = "SHORT", "S1_sweep_reversal"
    if sig == "HOLD" and df['low'].iloc[i] > df['low'].iloc[i-1] > df['low'].iloc[i-2] and row['close'] > row['open'] and in_zone(row['close']): sig, stype = "LONG", "S2_H2"
    if sig == "HOLD":
        for f in s15['fvgs']:
            if f['dir'] == "BULL" and abs(row['low']-f['mid']) < 0.3*df['atr'].iloc[i]: sig, stype = "LONG", "S4_FVG_fill"; break
            if f['dir'] == "BEAR" and abs(row['high']-f['mid']) < 0.3*df['atr'].iloc[i]: sig, stype = "SHORT", "S4_FVG_fill"; break
    if sig == "HOLD" and df['close'].iloc[i-2] < df['low'].iloc[i-11:i-2].min() and row['close'] > df['low'].iloc[i-11:i-2].min(): sig, stype = "LONG", "S5_failed_breakdown"
    vol_ok = True if no_vol else bool(row['volume'] >= 1.2*df['volsma'].iloc[i])
    body_ok = bool(abs(row['close']-row['open']) >= 0.6*(row['high']-row['low']))
    return {"sig":sig,"type":stype,"in_zone":in_zone(row['close']),"vol_ok":vol_ok,"body_ok":body_ok,"atr15":float(df['atr'].iloc[i])}

def build_signal(kind, sym):
    name, cfg = match_cfg(sym)
    if cfg.get("status") == "DISABLED": return None
    blocked, ev = is_news_blocked_for(name)
    if blocked:
        logger.info(f"{sym} news-blocked: {ev.get('title')}")
        return None
    if not session_ok(cfg):
        logger.info(f"{sym} skipped session"); return None
    is_index = ("ES" in sym.upper() or "US500" in sym.upper() or "SPX" in sym.upper())
    is_gold = ("XAU" in sym.upper() or "GC" in sym.upper() or "GOLD" in sym.upper())
    no_vol = is_index or is_gold
    d1 = get_tf(kind,sym,"D1"); h4 = get_tf(kind,sym,"H4"); m15 = get_tf(kind,sym,"M15"); m5 = get_tf(kind,sym,"M5")
    wk = wyckoff_d1(d1); h = h4_module(h4); s15 = smc_m15(m15)
    st = detect_setup(m15,h,s15,no_vol)
    direction = st["sig"]
    if direction == "HOLD": return None
    prefix = st["type"].split("_")[0]
    if prefix not in cfg.get("setups", ["S1","S2","S4"]): return None
    if prefix == "S2" and h["regime"] != "TREND": return None
    req_reg = cfg.get("regime")
    if req_reg and h["regime"] != req_reg: return None
    hf = cfg.get("htf_filter", "aligned")
    if h["va_bias"] == "MID": return None
    if wk["climax"]: return None
    if hf == "aligned":
        if wk["bias"] != "NEUTRAL" and wk["bias"] != direction: return None
        if h["ai"] != "NEUTRAL" and h["ai"] != direction: return None
        if wk["bias"] == "NEUTRAL" and h["ai"] == "NEUTRAL": return None
    elif hf == "va_fade":
        if direction == "LONG" and h["va_bias"] != "BELOW": return None
        if direction == "SHORT" and h["va_bias"] != "ABOVE": return None
    score = 0
    score += wk["score"]
    if h["ai"] == direction: score += 15
    if prefix == "S1" or st["in_zone"]: score += 20
    if any(abs(m15['close'].iloc[-2]-z) < 0.5*h["atr"] for z in h["levels"]+[h["vah"],h["val"],h["poc"]]): score += 15
    if st["vol_ok"] and st["body_ok"]: score += 15
    if st["vol_ok"] and st["body_ok"] and st["in_zone"] and h["ai"] == direction: score += 15
    if score < cfg.get("score_thr",55): return None
    if name == "EURUSD":
        c1 = m5.iloc[-2]; c2 = m5.iloc[-3]
        if direction == "LONG" and not (c1['close'] > c1['open'] and c2['close'] > c2['open']): return None
        if direction == "SHORT" and not (c1['close'] < c1['open'] and c2['close'] < c2['open']): return None
    sig_c = m5.iloc[-2]; atr5 = float(atr(m5,14).iloc[-2])
    buf = cfg.get("sl_buf_ATR",0.1) * st["atr15"]
    spr = spread_price(sym)/2.0
    if direction == "LONG":
        entry = float(sig_c['high'] + spr); stop = float(sig_c['low'] - buf - spr); stop = min(stop, entry - 0.5*atr5)
    else:
        entry = float(sig_c['low'] - spr); stop = float(sig_c['high'] + buf + spr); stop = max(stop, entry + 0.5*atr5)
    R = abs(entry-stop)+1e-9
    cands = []
    for f in s15["fvgs"]:
        if direction == "LONG" and f["edge"] > entry: cands.append((f["edge"],"FVG_edge"))
        if direction == "SHORT" and f["edge"] < entry: cands.append((f["edge"],"FVG_edge"))
    for oL,oH in s15["obs"]:
        lvl = oL if direction == "LONG" else oH
        if (direction == "LONG" and lvl > entry) or (direction == "SHORT" and lvl < entry): cands.append((lvl,"OB"))
    for lvl in [h["poc"],h["vah"],h["val"]]+h["levels"]+h["piv_h"]+h["piv_l"]+[wk["prev_h"],wk["prev_l"]]+s15["piv_h"]+s15["piv_l"]:
        if (direction == "LONG" and lvl > entry) or (direction == "SHORT" and lvl < entry): cands.append((float(lvl),"Profile/Classic"))
    cands = sorted(cands, key=lambda x: abs(x[0]-entry))
    filt = []
    for p,src in cands:
        if not filt or abs(p-filt[-1][0]) > 0.2*R: filt.append((p,src))
        if len(filt) >= 3: break
    if not filt: return None
    while len(filt) < 3: filt.append(filt[-1])
    if abs(filt[0][0]-entry)/R < cfg.get("min_tp1_R",1.3): return None
    risk_money = BALANCE*(RISK_PERCENT/100)
    vol = risk_money/R
    if MANUAL_LOT.strip() != "":
        try: vol = float(MANUAL_LOT)
        except: pass
    return {"symbol":sym,"map":name,"signal":direction,"score":score,"wyckoff":wk,"ai_H4":h["ai"],"regime":h["regime"],"setup":st["type"],"entry":entry,"stop":stop,"R":R,"vol":vol,"risk_money":risk_money,"tps":filt,"poc":h["poc"],"vah":h["vah"],"val":h["val"],"status":cfg.get("status","LIVE")}

def scan(force=False):
    jobs = [("c",s) for s in CRYPTO_SYMBOLS if s.strip()]+[("f",s) for s in FOREX_SYMBOLS if s.strip()]+[("f",s) for s in INDEX_SYMBOLS if s.strip()]+[("f",s) for s in GOLD_SYMBOLS if s.strip()]
    today = datetime.now(timezone.utc).date().isoformat()
    for kind,sym in jobs:
        sym = sym.strip()
        if not sym: continue
        name,cfg = match_cfg(sym)
        if cfg.get("status") == "DISABLED":
            logger.info(f"{sym} DISABLED skip"); continue
        maxd = int(cfg.get("max_per_day",3))
        key = f"{sym}-{today}"
        if TRADE_COUNT.get(key,0) >= maxd:
            logger.info(f"{sym} max {maxd}/day skip"); continue
        try:
            r = build_signal(kind,sym)
            logger.info(f"{sym} -> {r['signal']+' '+str(r['score']) if r else 'HOLD'}")
            if r:
                TRADE_COUNT[key] = TRADE_COUNT.get(key,0)+1
                tp_txt = "\n".join([f"TP{i+1} `{p[0]:.5f}` ({p[1]})" for i,p in enumerate(r['tps'])])
                send_tg(f"🎯 *{r['signal']} {sym} [{r['status']}] | Score {r['score']}*\nSetup: `{r['setup']}`\nWyckoff: {r['wyckoff']['phase']} | H4:{r['ai_H4']} {r['regime']}\nEntry(stop): `{r['entry']:.5f}`\nSL: `{r['stop']:.5f}` R:`{r['R']:.5f}`\nVol: `{r['vol']:.4f}` Risk: `${r['risk_money']:.2f}`\n{tp_txt}\nAlloc 50/30/20 | BE after TP1, SL to TP1 after TP2\nPOC:`{r['poc']:.5f}` VAH:`{r['vah']:.5f}` VAL:`{r['val']:.5f}`")
        except Exception as e: logger.error(f"{sym}:{e}")

def check_commands():
    global OFFSET, BALANCE, RISK_PERCENT, INTERVAL_MIN, CRYPTO_SYMBOLS, FOREX_SYMBOLS, INDEX_SYMBOLS, GOLD_SYMBOLS, MANUAL_LOT, NEWS_MANUAL_PAUSE
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates", params={"offset":OFFSET,"timeout":5}, timeout=15).json()
        for u in r.get("result",[]):
            OFFSET = u["update_id"]+1
            msg = u.get("message",{})
            if str(msg.get("chat",{}).get("id","")) != str(TELEGRAM_CHAT_ID): continue
            t = msg.get("text","").strip()
            if not t.startswith("/"): continue
            p = t.split(); cmd = p[0].lower(); arg = " ".join(p[1:]) if len(p) > 1 else ""
            if cmd == "/start": send_tg("✅ Hoosha Pro-6 News live.\n/status\n/config\n/calendar\n/scan\n/news status")
            elif cmd == "/help": send_tg("/status\n/config\n/calendar\n/balance 1000\n/risk 0.5\n/lot auto\n/scan\n/news on|off|status\n/interval 30")
            elif cmd == "/status": send_tg(f"📊 Balance:`{BALANCE}` Risk:`{RISK_PERCENT}%` Lot:`{MANUAL_LOT or 'auto'}`\nCrypto:{','.join(CRYPTO_SYMBOLS)}\nForex:{','.join(FOREX_SYMBOLS)}\nIndex:{','.join(INDEX_SYMBOLS)}\nGold:{','.join(GOLD_SYMBOLS)}\nNews:{'ON' if NEWS_ENABLED else 'OFF'} ManualPause:{NEWS_MANUAL_PAUSE} Block:{NEWS_BLOCK_MINUTES}m")
            elif cmd == "/config": send_tg("BTCUSD LIVE 55 ALL aligned\nUSDCAD LIVE 60 S1,S2,S4 any 07-17UTC\nUS500 WATCH 55\nEURUSD DEMO 62 S4,S5 va_fade RANGE\nXAUUSD DEMO 60 S1,S4 TREND\nNews: 30m block ALL + auto analysis")
            elif cmd == "/calendar": send_tg(upcoming_text())
            elif cmd == "/balance" and arg: BALANCE = float(arg); send_tg(f"Balance set: `{BALANCE}`")
            elif cmd == "/risk" and arg: RISK_PERCENT = float(arg); send_tg(f"Risk set: `{RISK_PERCENT}%`")
            elif cmd == "/lot" and arg: MANUAL_LOT = "" if arg == "auto" else arg; send_tg(f"Lot: `{MANUAL_LOT or 'auto'}`")
            elif cmd == "/interval" and arg: INTERVAL_MIN = int(arg); send_tg(f"Interval `{INTERVAL_MIN}m`")
            elif cmd == "/news":
                a = arg.lower()
                if a == "on": NEWS_MANUAL_PAUSE = True; send_tg("News manual pause ON")
                elif a == "off": NEWS_MANUAL_PAUSE = False; send_tg("News manual pause OFF, auto still active")
                else: send_tg(f"Auto:{'ON' if NEWS_ENABLED else 'OFF'} ManualPause:{NEWS_MANUAL_PAUSE} Block:{NEWS_BLOCK_MINUTES}m\n/news on|off\n/calendar")
            elif cmd == "/scan": send_tg("🔎 Force scan..."); scan(force=True); send_tg("Done.")
            elif cmd == "/test": send_tg("✅ Test OK. Send /calendar for news, /scan for scan.")
    except Exception as e: logger.error(e)

def loop():
    send_tg("✅ Hoosha Pro-6 News started. 30m news block ALL + auto analysis. Send /calendar")
    fetch_news_cached(force=True)
    last = 0; last_news = 0
    while True:
        check_commands()
        if time.time()-last_news > 300:
            check_news_alerts(); last_news = time.time()
        if time.time()-last >= INTERVAL_MIN*60: scan(); last = time.time()
        time.sleep(10)
threading.Thread(target=loop, daemon=True).start()
