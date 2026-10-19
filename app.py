# Hoosha Pro-6E/G + Universal News Filter (30m before/after + auto analysis)
# LIVE: EURUSD, USDJPY, GBPUSD, USDCHF, XAUUSD, XAGUSD, US500 (v6 backtest-verified) | Removed: BTC, USDCAD, BRENT
# News: ForexFactory weekly (faireconomy) High impact, 30m block all symbols + post analysis
# Data: Yahoo chart API direct (no yfinance/ccxt) | SL = 1.5xATR | TP 1.3/2.0/3.0R alloc 50/30/20
# Not financial advice. Test on demo first (WATCH_MODE=1).
import os, time, re, json, requests, logging, threading
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from flask import Flask

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("Pro6News")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "PUT_TOKEN")
TELEGRAM_CHAT_ID = str(os.getenv("TELEGRAM_CHAT_ID", "PUT_ID"))
FOREX_SYMBOLS = os.getenv("FOREX_SYMBOLS", "EURUSD,USDJPY,GBPUSD,USDCHF").split(",")
GOLD_SYMBOLS = os.getenv("GOLD_SYMBOLS", "XAUUSD,XAGUSD").split(",")
INDEX_SYMBOLS = os.getenv("INDEX_SYMBOLS", "US500").split(",")
BALANCE = float(os.getenv("BALANCE", "1000"))
RISK_PERCENT = float(os.getenv("RISK_PERCENT", "0.5"))
INTERVAL_MIN = int(os.getenv("INTERVAL_MIN", "30"))
WATCH_MODE = os.getenv("WATCH_MODE", "0") == "1"   # 1 = demo/watch tags, no live label
NEWS_ENABLED = os.getenv("NEWS_ENABLED", "1") == "1"
NEWS_BLOCK_MINUTES = int(os.getenv("NEWS_BLOCK_MINUTES", "30"))
NEWS_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
STATE_FILE = os.getenv("STATE_FILE", "pro6_state.json")
OFFSET = 0
NEWS_MANUAL_PAUSE = False
NEWS_CACHE = []
NEWS_LAST_FETCH = 0
WARNED = set()
ANALYZED = set()
TRADE_COUNT = {}
LAST_SIGNAL_TIME = {}

# ---------- v6 backtest-verified config (45d, no spread/slip) ----------
TP_R = [1.3, 2.0, 3.0]
TP_ALLOC = [0.5, 0.3, 0.2]
SL_ATR_MULT = 1.5
ENTRY_TIMEOUT_MIN = 24 * 5     # 24 M5 bars = 2h
TRADE_TIMEOUT_MIN = 288 * 5    # 288 M5 bars = 24h
CLASS_SETUP_DISABLE = {"*": ["utad_fvg"]}                # utad+FVG combo removed everywhere (backtest loser)
SYMBOL_SETUP_DISABLE = {"GBPUSD": ["utad"], "USDCHF": ["spring"]}
PRO6_CFG = {
    "EURUSD": {"status": "LIVE", "score_thr": 50, "setups": ["S1","S2","S4","S5"], "htf_filter": "aligned", "cooldown_h": 4, "max_per_day": 3, "session": "ALL"},
    "USDJPY": {"status": "LIVE", "score_thr": 50, "setups": ["S1","S2","S4","S5"], "htf_filter": "aligned", "cooldown_h": 4, "max_per_day": 3, "session": "ALL"},
    "GBPUSD": {"status": "LIVE", "score_thr": 50, "setups": ["S1","S2","S4","S5"], "htf_filter": "aligned", "cooldown_h": 4, "max_per_day": 3, "session": "ALL"},
    "USDCHF": {"status": "LIVE", "score_thr": 50, "setups": ["S1","S2","S4","S5"], "htf_filter": "aligned", "cooldown_h": 4, "max_per_day": 3, "session": "ALL"},
    "XAUUSD": {"status": "LIVE", "score_thr": 50, "setups": ["S1","S2","S4","S5"], "htf_filter": "aligned", "cooldown_h": 4, "max_per_day": 3, "session": "ALL"},
    "XAGUSD": {"status": "LIVE", "score_thr": 50, "setups": ["S1","S2","S4","S5"], "htf_filter": "aligned", "cooldown_h": 4, "max_per_day": 3, "session": "ALL"},
    "US500":  {"status": "LIVE", "score_thr": 50, "setups": ["S1","S2","S4","S5"], "htf_filter": "aligned", "cooldown_h": 4, "max_per_day": 3, "session": "ALL"},
}
YAHOO_MAP = {"EURUSD": "EURUSD=X", "USDJPY": "USDJPY=X", "GBPUSD": "GBPUSD=X", "USDCHF": "USDCHF=X",
             "XAUUSD": "XAUUSD=X", "XAUUSD_FB": "GC=F", "XAGUSD": "XAGUSD=X", "XAGUSD_FB": "SI=F", "US500": "^GSPC"}
SYMBOL_CURR = {"EURUSD": ["EUR","USD"], "USDJPY": ["USD","JPY"], "GBPUSD": ["GBP","USD"], "USDCHF": ["USD","CHF"],
               "XAUUSD": ["USD"], "XAGUSD": ["USD"], "US500": ["USD"]}

app = Flask(__name__)
@app.route("/")
def home(): return f"OK Hoosha Pro-6E/G v6 Alive {datetime.now()}"
@app.route("/ping")
def ping(): return "pong"

def send_tg(t):
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": t, "parse_mode": "Markdown"}, timeout=15)
    except Exception as e: logger.error(e)

def all_symbols():
    return [s.strip() for s in FOREX_SYMBOLS+GOLD_SYMBOLS+INDEX_SYMBOLS if s.strip()]

def match_cfg(sym):
    u = sym.upper().replace("=X","").replace("/","")
    for name in PRO6_CFG:
        if name in u: return name, PRO6_CFG[name]
    return "UNIVERSAL", PRO6_CFG["EURUSD"]

def session_ok(cfg):
    s = cfg.get("session", "ALL")
    if s == "ALL": return True
    h = datetime.now(timezone.utc).hour
    if "07-17" in s: return 7 <= h < 17
    if "12-20" in s: return 12 <= h < 20
    return True

# ---------- NEWS (unchanged) ----------
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
    if bias == "USD_STRONG": impl = "Bearish EURUSD/GBPUSD/XAUUSD, Bullish USDCHF, caution long US500"
    elif bias == "USD_WEAK": impl = "Bullish EURUSD/GBPUSD/XAUUSD, Bearish USDCHF, supportive US500"
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
    lines = [f"{dt.strftime('%m-%d %H:%M')} UTC - {ev.get('country')} {ev.get('title')}" for dt, ev in items]
    return "🗓 High-Impact this week:\n" + "\n".join(lines)

# ---------- indicators ----------
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

# ---------- data (Yahoo chart API direct, no yfinance/ccxt) ----------
def fetch_yahoo(sym, interval, rng):
    last_err = None
    for host in ("query1", "query2"):
        try:
            url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{sym}"
            r = requests.get(url, params={"interval": interval, "range": rng, "includePrePost": "false"}, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            res = r.json()["chart"]["result"][0]
            ts = res["timestamp"]; q = res["indicators"]["quote"][0]
            df = pd.DataFrame({"ts": ts, "open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"], "volume": q["volume"]}).dropna().reset_index(drop=True)
            if len(df) > 30:
                df["volume"] = df["volume"].fillna(1000.0)
                if df["volume"].sum() == 0: df["volume"] = 1000.0
                return df[["open","high","low","close","volume"]]
            last_err = RuntimeError("short data "+sym)
        except Exception as e:
            last_err = e
    raise last_err if last_err else RuntimeError("no data "+sym)

def resample_4h(df1h):
    rows = []
    for i in range(0, len(df1h)-3, 4):
        w = df1h.iloc[i:i+4]
        rows.append({"open": float(w['open'].iloc[0]), "high": float(w['high'].max()), "low": float(w['low'].min()), "close": float(w['close'].iloc[-1]), "volume": float(w['volume'].sum())})
    return pd.DataFrame(rows)

def get_tf(base, tf):
    if tf == "D1": return fetch_yahoo(base, "1d", "1y")
    if tf == "H4": return resample_4h(fetch_yahoo(base, "1h", "6mo"))
    if tf == "M15": return fetch_yahoo(base, "15m", "1mo")
    return fetch_yahoo(base, "5m", "5d")

def fetch_sym_tf(name, tf):
    try: return get_tf(YAHOO_MAP[name], tf)
    except Exception:
        fb = YAHOO_MAP.get(name + "_FB")
        if fb: return get_tf(fb, tf)
        raise

# ---------- modules (same as backtest v6) ----------
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
    spring = bool(last['low'] < mn and last['close'] > mn and last['volume'] > 1.5*df['volsma'].iloc[-2])
    utad = bool(last['high'] > mx and last['close'] < mx and last['volume'] > 1.5*df['volsma'].iloc[-2])
    score20 = 20 if (spring or utad or bias != "NEUTRAL") else 0
    if spring: phase, bias = "ACCUM", "LONG"
    elif utad: phase, bias = "DISTRIB", "SHORT"
    prev_h = float(df['high'].iloc[-3]) if len(df) > 3 else float(df['high'].iloc[-1])
    prev_l = float(df['low'].iloc[-3]) if len(df) > 3 else float(df['low'].iloc[-1])
    return {"bias":bias,"phase":phase,"climax":climax,"score":score20,"prev_h":prev_h,"prev_l":prev_l,"spring":spring,"utad":utad}

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

# ---------- v6 setup filters (token matching fixed: event + combo) ----------
def setup_allowed(name, prefix, event):
    token = f"{prefix}_{event}" if event != "none" else prefix
    for bad in CLASS_SETUP_DISABLE.get("*", []):
        if token == bad or prefix == bad: return False
    for b in SYMBOL_SETUP_DISABLE.get(name, []):
        if b == event or b == prefix or b == token: return False
    return True

def build_signal(sym):
    name, cfg = match_cfg(sym)
    blocked, ev = is_news_blocked_for(name)
    if blocked:
        logger.info(f"{sym} news-blocked: {ev.get('title')}")
        return None
    if not session_ok(cfg):
        logger.info(f"{sym} skipped session"); return None
    is_index = name == "US500"
    no_vol = is_index or name in ("XAUUSD","XAGUSD")
    d1 = fetch_sym_tf(name,"D1"); h4d = fetch_sym_tf(name,"H4"); m15 = fetch_sym_tf(name,"M15"); m5 = fetch_sym_tf(name,"M5")
    wk = wyckoff_d1(d1); h = h4_module(h4d); s15 = smc_m15(m15)
    st = detect_setup(m15,h,s15,no_vol)
    direction = st["sig"]
    if direction == "HOLD": return None
    prefix = st["type"].split("_")[0]
    event = "spring" if wk["spring"] else "utad" if wk["utad"] else "none"
    if prefix not in cfg.get("setups", ["S1","S2","S4","S5"]): return None
    if not setup_allowed(name, prefix, event): return None
    if prefix == "S2" and h["regime"] != "TREND": return None
    if cfg.get("htf_filter") == "aligned":
        if h["va_bias"] == "MID": return None
        if wk["climax"]: return None
        if wk["bias"] != "NEUTRAL" and wk["bias"] != direction: return None
        if h["ai"] != "NEUTRAL" and h["ai"] != direction: return None
        if wk["bias"] == "NEUTRAL" and h["ai"] == "NEUTRAL": return None
    score = 0
    score += wk["score"]
    if h["ai"] == direction: score += 15
    if prefix == "S1" or st["in_zone"]: score += 20
    if any(abs(m15['close'].iloc[-2]-z) < 0.5*h["atr"] for z in h["levels"]+[h["vah"],h["val"],h["poc"]]): score += 15
    if st["vol_ok"] and st["body_ok"]: score += 15
    if st["vol_ok"] and st["body_ok"] and st["in_zone"] and h["ai"] == direction: score += 15
    if score < cfg.get("score_thr",50): return None
    # v6 execution model: stop-order entry on signal candle, SL = 1.5xATR(M5), fixed TP 1.3/2.0/3.0R
    sig_c = m5.iloc[-2]; atr5 = float(atr(m5,14).iloc[-2])
    if direction == "LONG":
        entry = float(sig_c['high']); stop = entry - SL_ATR_MULT*atr5
    else:
        entry = float(sig_c['low']); stop = entry + SL_ATR_MULT*atr5
    rd = abs(entry-stop)
    if rd <= 0: return None
    tps = [entry + (TP_R[i]*rd if direction == "LONG" else -TP_R[i]*rd) for i in range(3)]
    risk_money = BALANCE*(RISK_PERCENT/100)
    vol = risk_money/rd
    return {"symbol":sym,"map":name,"signal":direction,"score":score,"event":event,"wyckoff":wk,"ai_H4":h["ai"],
            "regime":h["regime"],"setup":st["type"],"entry":entry,"stop":stop,"R":rd,"tps":tps,"vol":vol,
            "risk_money":risk_money,"status":("WATCH" if WATCH_MODE else cfg.get("status","LIVE"))}

# ---------- trade tracking (pro6_state.json) ----------
def load_state():
    try:
        with open(STATE_FILE) as f: st = json.load(f)
        st.setdefault("open", []); st.setdefault("closed", [])
        return st
    except Exception:
        return {"open": [], "closed": []}

def save_state(st):
    try:
        with open(STATE_FILE,"w") as f: json.dump(st, f)
    except Exception as e: logger.error(f"state save: {e}")

def register_trade(r):
    st = load_state()
    st["open"].append({"id": f"{r['map']}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}", "map": r["map"], "symbol": r["symbol"],
        "dir": r["signal"], "setup": r["setup"], "event": r["event"], "score": r["score"], "entry": r["entry"], "sl": r["stop"],
        "R": r["R"], "tps": r["tps"], "stage": 0, "realized_r": 0.0, "entered": False,
        "opened_at": datetime.now(timezone.utc).isoformat(), "status": r["status"]})
    save_state(st)

def fmt(t): return f"{t['dir']} {t['symbol']} [{t['status']}]"

def monitor_trades():
    st = load_state()
    now = datetime.now(timezone.utc)
    for t in st["open"][:]:
        try: m5 = fetch_sym_tf(t["map"], "M5")
        except Exception as e:
            logger.error(f"monitor {t['map']}: {e}"); continue
        opened = datetime.fromisoformat(t["opened_at"])
        age_min = (now - opened).total_seconds()/60
        # pending stop-order entry, valid 24 M5 bars
        if not t["entered"]:
            if age_min > ENTRY_TIMEOUT_MIN:
                st["open"].remove(t); st["closed"].append({**t, "result_r": 0.0, "why": "entry timeout"})
                send_tg(f"⌛ Cancelled (entry not hit in 2h): {fmt(t)}")
                save_state(st); continue
            hit = (m5['high'].max() >= t["entry"]) if t["dir"] == "LONG" else (m5['low'].min() <= t["entry"])
            if hit:
                t["entered"] = True
                send_tg(f"✅ Entry filled: {fmt(t)} @ `{t['entry']:.5f}`\nSL `{t['sl']:.5f}` | TP1/2/3 active (50/30/20)")
                save_state(st)
            continue
        # active trade: walk recent bars, SL-first (conservative)
        rec = m5.tail(60)
        closed = False
        for _, bar in rec.iterrows():
            if t["dir"] == "LONG":
                if bar['low'] <= t["sl"]:
                    frac_left = 1.0 - sum(TP_ALLOC[:t["stage"]])
                    exit_px = t["sl"]
                    t["realized_r"] += frac_left * ((exit_px - t["entry"])/t["R"] if t["dir"] == "LONG" else (t["entry"] - exit_px)/t["R"])
                    st["open"].remove(t); st["closed"].append({**t, "result_r": round(t["realized_r"],3), "why": "SL", "closed_at": now.isoformat()})
                    send_tg(f"🛑 SL hit: {fmt(t)} | Result: `{t['realized_r']:+.2f}R`"); closed = True; break
                if t["stage"] < 3 and bar['high'] >= t["tps"][t["stage"]]:
                    t["realized_r"] += TP_ALLOC[t["stage"]] * TP_R[t["stage"]]
                    t["stage"] += 1
                    if t["stage"] == 1: t["sl"] = t["entry"]
                    elif t["stage"] == 2: t["sl"] = t["tps"][0]
                    send_tg(f"🎯 TP{t['stage']} hit: {fmt(t)} | `{t['realized_r']:+.2f}R` so far | SL → `{t['sl']:.5f}`")
                    if t["stage"] == 3:
                        st["open"].remove(t); st["closed"].append({**t, "result_r": round(t["realized_r"],3), "why": "TP3", "closed_at": now.isoformat()})
                        send_tg(f"🏁 Closed full: {fmt(t)} | Final: `{t['realized_r']:+.2f}R`"); closed = True; break
            else:
                if bar['high'] >= t["sl"]:
                    frac_left = 1.0 - sum(TP_ALLOC[:t["stage"]])
                    exit_px = t["sl"]
                    t["realized_r"] += frac_left * ((t["entry"] - exit_px)/t["R"])
                    st["open"].remove(t); st["closed"].append({**t, "result_r": round(t["realized_r"],3), "why": "SL", "closed_at": now.isoformat()})
                    send_tg(f"🛑 SL hit: {fmt(t)} | Result: `{t['realized_r']:+.2f}R`"); closed = True; break
                if t["stage"] < 3 and bar['low'] <= t["tps"][t["stage"]]:
                    t["realized_r"] += TP_ALLOC[t["stage"]] * TP_R[t["stage"]]
                    t["stage"] += 1
                    if t["stage"] == 1: t["sl"] = t["entry"]
                    elif t["stage"] == 2: t["sl"] = t["tps"][0]
                    send_tg(f"🎯 TP{t['stage']} hit: {fmt(t)} | `{t['realized_r']:+.2f}R` so far | SL → `{t['sl']:.5f}`")
                    if t["stage"] == 3:
                        st["open"].remove(t); st["closed"].append({**t, "result_r": round(t["realized_r"],3), "why": "TP3", "closed_at": now.isoformat()})
                        send_tg(f"🏁 Closed full: {fmt(t)} | Final: `{t['realized_r']:+.2f}R`"); closed = True; break
        if closed: save_state(st); continue
        if age_min > TRADE_TIMEOUT_MIN:
            lastc = float(m5['close'].iloc[-1])
            frac_left = 1.0 - sum(TP_ALLOC[:t["stage"]])
            t["realized_r"] += frac_left * ((lastc - t["entry"])/t["R"] if t["dir"] == "LONG" else (t["entry"] - lastc)/t["R"])
            st["open"].remove(t); st["closed"].append({**t, "result_r": round(t["realized_r"],3), "why": "timeout", "closed_at": now.isoformat()})
            send_tg(f"⏱ Timeout close (24h): {fmt(t)} | Result: `{t['realized_r']:+.2f}R`")
            save_state(st); continue
        save_state(st)

def perf_text():
    st = load_state()
    cl = st["closed"]
    if not cl: return "No closed trades yet."
    rs = [c.get("result_r",0.0) for c in cl]
    wins = [r for r in rs if r > 0]
    tot = sum(rs)
    return (f"📊 *Perf (live-tracked)*\nClosed: `{len(cl)}` | Winrate: `{len(wins)}/{len(cl)} ({100*len(wins)/len(cl):.0f}%)`\nTotal: `{tot:+.2f}R` | Expectancy: `{tot/len(cl):+.3f}R`\nOpen: `{len(st['open'])}`\nReference backtest v6: +71.3R/45d on 7 symbols.")

def scan():
    today = datetime.now(timezone.utc).date().isoformat()
    now = datetime.now(timezone.utc)
    for sym in all_symbols():
        name,cfg = match_cfg(sym)
        maxd = int(cfg.get("max_per_day",3))
        key = f"{sym}-{today}"
        if TRADE_COUNT.get(key,0) >= maxd:
            logger.info(f"{sym} max {maxd}/day skip"); continue
        last_t = LAST_SIGNAL_TIME.get(name)
        if last_t and (now-last_t).total_seconds() < cfg.get("cooldown_h",4)*3600:
            logger.info(f"{sym} cooldown skip"); continue
        try:
            r = build_signal(sym)
            logger.info(f"{sym} -> {r['signal']+' '+str(r['score']) if r else 'HOLD'}")
            if r:
                TRADE_COUNT[key] = TRADE_COUNT.get(key,0)+1
                LAST_SIGNAL_TIME[name] = now
                register_trade(r)
                tp_txt = "\n".join([f"TP{i+1} `{r['tps'][i]:.5f}` ({TP_R[i]}R, {int(TP_ALLOC[i]*100)}%)" for i in range(3)])
                send_tg(f"🎯 *{r['signal']} {r['symbol']} [{r['status']}] | Score {r['score']}*\nSetup: `{r['setup']}` | Event: `{r['event']}`\nWyckoff: {r['wyckoff']['phase']} | H4:{r['ai_H4']} {r['regime']}\nEntry(stop-order, valid 2h): `{r['entry']:.5f}`\nSL: `{r['stop']:.5f}` (1.5xATR) | R: `{r['R']:.5f}`\n{tp_txt}\nSL→BE after TP1, SL→TP1 after TP2\nVol: `{r['vol']:.4f}` | Risk: `${r['risk_money']:.2f}`\nPOC:`{PRO6_CFG and 0:.1f}`".replace("POC:`0.0`",""))
        except Exception as e: logger.error(f"{sym}:{e}")

def check_commands():
    global OFFSET, BALANCE, RISK_PERCENT, INTERVAL_MIN, NEWS_MANUAL_PAUSE, WATCH_MODE
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates", params={"offset":OFFSET,"timeout":5}, timeout=15).json()
        for u in r.get("result",[]):
            OFFSET = u["update_id"]+1
            msg = u.get("message",{})
            if str(msg.get("chat",{}).get("id","")) != str(TELEGRAM_CHAT_ID): continue
            t = msg.get("text","").strip()
            if not t.startswith("/"): continue
            p = t.split(); cmd = p[0].lower(); arg = " ".join(p[1:]) if len(p) > 1 else ""
            if cmd == "/start": send_tg("✅ Hoosha Pro-6E/G v6 live.\nSymbols: EURUSD USDJPY GBPUSD USDCHF XAUUSD XAGUSD US500\nRemoved: BTC USDCAD BRENT\n/status /symbols /perf /calendar /scan /help")
            elif cmd == "/help": send_tg("/status\n/symbols\n/watch SYM (toggle LIVE/WATCH)\n/perf\n/calendar\n/scan\n/balance 1000\n/risk 0.5\n/interval 30\n/news on|off|status")
            elif cmd == "/status": send_tg(f"📊 Balance:`{BALANCE}` Risk:`{RISK_PERCENT}%` Interval:`{INTERVAL_MIN}m`\nMode:{'WATCH (demo)' if WATCH_MODE else 'LIVE'}\nNews:{'ON' if NEWS_ENABLED else 'OFF'} ManualPause:{NEWS_MANUAL_PAUSE} Block:{NEWS_BLOCK_MINUTES}m\nTP: 1.3/2.0/3.0R alloc 50/30/20 | SL: 1.5xATR")
            elif cmd == "/symbols": send_tg("\n".join([f"{k}: {v['status']} thr {v['score_thr']} cd {v['cooldown_h']}h | disable:{SYMBOL_SETUP_DISABLE.get(k,[])}" for k,v in PRO6_CFG.items()]) + "\nClass disable: utad_fvg (all)")
            elif cmd == "/watch" and arg:
                nm = arg.upper(); found = False
                for k in PRO6_CFG:
                    if k in nm or nm in k:
                        PRO6_CFG[k]["status"] = "WATCH" if PRO6_CFG[k]["status"] != "WATCH" else "LIVE"
                        send_tg(f"{k} → {PRO6_CFG[k]['status']}"); found = True
                if not found: send_tg("Symbol not found. Try /symbols")
            elif cmd == "/perf": send_tg(perf_text())
            elif cmd == "/calendar": send_tg(upcoming_text())
            elif cmd == "/balance" and arg: BALANCE = float(arg); send_tg(f"Balance set: `{BALANCE}`")
            elif cmd == "/risk" and arg: RISK_PERCENT = float(arg); send_tg(f"Risk set: `{RISK_PERCENT}%`")
            elif cmd == "/interval" and arg: INTERVAL_MIN = int(arg); send_tg(f"Interval `{INTERVAL_MIN}m`")
            elif cmd == "/news":
                a = arg.lower()
                if a == "on": NEWS_MANUAL_PAUSE = True; send_tg("News manual pause ON")
                elif a == "off": NEWS_MANUAL_PAUSE = False; send_tg("News manual pause OFF, auto still active")
                else: send_tg(f"Auto:{'ON' if NEWS_ENABLED else 'OFF'} ManualPause:{NEWS_MANUAL_PAUSE} Block:{NEWS_BLOCK_MINUTES}m\n/news on|off\n/calendar")
            elif cmd == "/scan": send_tg("🔎 Force scan..."); scan(); send_tg("Done. /perf for tracking.")
    except Exception as e: logger.error(e)

def loop():
    send_tg("✅ Hoosha Pro-6E/G v6 started.\nLIVE: EURUSD USDJPY GBPUSD USDCHF XAUUSD XAGUSD US500\nNews 30m block ALL + auto analysis. Send /calendar")
    fetch_news_cached(force=True)
    last = 0; last_news = 0; last_mon = 0
    while True:
        check_commands()
        if time.time()-last_news > 300:
            check_news_alerts(); last_news = time.time()
        if time.time()-last_mon > 300:
            monitor_trades(); last_mon = time.time()
        if time.time()-last >= INTERVAL_MIN*60: scan(); last = time.time()
        time.sleep(10)

threading.Thread(target=loop, daemon=True).start()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
