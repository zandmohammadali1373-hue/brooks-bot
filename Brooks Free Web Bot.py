# Brooks Free Web Bot - Render Free Web Service + UptimeRobot
# Start Command: gunicorn brooks_free_web_bot:app --bind 0.0.0.0:$PORT

import os, time, requests, logging, threading
import pandas as pd
import numpy as np
from datetime import datetime
from flask import Flask

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("BrooksFree")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "PUT_TOKEN")
TELEGRAM_CHAT_ID = str(os.getenv("TELEGRAM_CHAT_ID", "PUT_ID"))
CRYPTO_SYMBOLS = os.getenv("CRYPTO_SYMBOLS", "BTC/USDT,ETH/USDT,SOL/USDT").split(",")
FOREX_SYMBOLS = os.getenv("FOREX_SYMBOLS", "EURUSD=X,XAUUSD=X").split(",")
TIMEFRAME = os.getenv("TIMEFRAME", "15m")
RISK = float(os.getenv("RISK", "1.0"))
INTERVAL_MIN = int(os.getenv("INTERVAL_MIN", "15"))
OFFSET = 0

app = Flask(__name__)

@app.route("/")
def home():
    return f"OK Brooks Bot Alive {datetime.now()} | TF:{TIMEFRAME} Risk:{RISK}%"

@app.route("/ping")
def ping():
    return "pong"

def send_tg(text):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=15)
    except Exception as e:
        logger.error(e)

def check_commands():
    global OFFSET, TIMEFRAME, RISK, INTERVAL_MIN, CRYPTO_SYMBOLS, FOREX_SYMBOLS
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": OFFSET, "timeout": 5}, timeout=15).json()
        for u in r.get("result", []):
            OFFSET = u["update_id"] + 1
            msg = u.get("message", {})
            if str(msg.get("chat", {}).get("id", "")) != str(TELEGRAM_CHAT_ID):
                continue
            text = msg.get("text", "").strip()
            if not text.startswith("/"): continue
            p = text.split(); cmd = p[0].lower(); arg = " ".join(p[1:]) if len(p) > 1 else ""
            if cmd == "/start":
                send_tg("✅ ربات رایگان روشن است.\n/status\n/symbols\n/help")
            elif cmd == "/help":
                send_tg("دستورات:\n/status\n/symbols\n/add BTC/USDT\n/remove BTC/USDT\n/timeframe 1h\n/risk 0.5\n/interval 15")
            elif cmd == "/status":
                send_tg(f"📊 Crypto:{','.join(CRYPTO_SYMBOLS)}\nForex:{','.join(FOREX_SYMBOLS)}\nTF:{TIMEFRAME}\nRisk:{RISK}%\nInterval:{INTERVAL_MIN}min")
            elif cmd == "/symbols":
                send_tg(f"Crypto:{','.join(CRYPTO_SYMBOLS)}\nForex:{','.join(FOREX_SYMBOLS)}")
            elif cmd == "/add" and arg:
                a = arg.upper()
                (CRYPTO_SYMBOLS.append(a) if "/" in a else FOREX_SYMBOLS.append(a))
                send_tg(f"➕ اضافه شد: {a}")
            elif cmd == "/remove" and arg:
                a = arg.upper()
                if a in CRYPTO_SYMBOLS: CRYPTO_SYMBOLS.remove(a)
                if a in FOREX_SYMBOLS: FOREX_SYMBOLS.remove(a)
                send_tg(f"➖ حذف شد: {a}")
            elif cmd == "/timeframe" and arg:
                TIMEFRAME = arg; send_tg(f"⏱ شد:{TIMEFRAME}")
            elif cmd == "/risk" and arg:
                RISK = float(arg); send_tg(f"⚖️ ریسک:{RISK}%")
            elif cmd == "/interval" and arg:
                INTERVAL_MIN = int(arg); send_tg(f"🔁 هر {INTERVAL_MIN} دقیقه")
    except Exception as e:
        logger.error(f"cmd:{e}")

def fetch_crypto(symbol, timeframe, limit=300):
    import ccxt
    ex = ccxt.binance({"enableRateLimit": True})
    o = ex.fetch_ohlcv(symbol, timeframe, limit=limit)
    return pd.DataFrame(o, columns=["ts","open","high","low","close","volume"])[["open","high","low","close","volume"]]

def fetch_forex(symbol, interval):
    import yfinance as yf
    df = yf.download(symbol, period="1mo", interval=interval, progress=False, auto_adjust=True)
    if df.empty: raise RuntimeError("no data")
    if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
    df.columns = [str(c).lower() for c in df.columns]
    df = df.reset_index(drop=True)
    m = {}
    for c in df.columns:
        lc = c.lower()
        if "open" in lc: m[c]="open"
        elif "high" in lc: m[c]="high"
        elif "low" in lc: m[c]="low"
        elif "close" in lc and "adj" not in lc: m[c]="close"
        elif "volume" in lc: m[c]="volume"
    df = df.rename(columns=m)
    if "volume" not in df.columns or df["volume"].sum()==0: df["volume"]=1000.0
    return df[["open","high","low","close","volume"]].dropna().reset_index(drop=True)

def add_ind(df):
    df=df.copy()
    rng=(df['high']-df['low']).replace(0,1e-9)
    clv=((df['close']-df['low'])-(df['high']-df['close']))/rng
    df['delta']=clv*df['volume']
    df['cvd']=df['delta'].rolling(14).sum()
    df['cvd_slope']=df['cvd'].diff(3)
    df['range']=df['high']-df['low']
    df['vol_sma']=df['volume'].rolling(20).mean()
    df['absorb_bull']=(df['volume']>df['vol_sma']*1.8)&(df['range']<df['range'].rolling(20).mean()*0.7)&(clv>0.3)
    df['absorb_bear']=(df['volume']>df['vol_sma']*1.8)&(df['range']<df['range'].rolling(20).mean()*0.7)&(clv<-0.3)
    tr=pd.concat([df['high']-df['low'],(df['high']-df['close'].shift()).abs(),(df['low']-df['close'].shift()).abs()],axis=1).max(axis=1)
    df['atr']=tr.rolling(14).mean()
    return df.dropna().reset_index(drop=True)

def vp_calc(df, lookback=100, bins=24):
    w=df.iloc[-lookback:]
    low,high=w['low'].min(),w['high'].max()
    edges=np.linspace(low,high,bins+1)
    vols=np.zeros(bins)
    for _,r in w.iterrows():
        il=max(0,min(bins-1,np.searchsorted(edges,r['low'])-1))
        ih=max(0,min(bins-1,np.searchsorted(edges,r['high'])-1))
        for k in range(il,ih+1): vols[k]+=r['volume']/max(1,ih-il+1)
    pi=int(np.argmax(vols)); poc=(edges[pi]+edges[pi+1])/2
    tot=vols.sum(); order=np.argsort(vols)[::-1]; cum=0; va=set()
    for k in order:
        va.add(k); cum+=vols[k]
        if cum>=tot*0.7: break
    return {"poc":poc,"vah":edges[max(va)+1],"val":edges[min(va)]}

def bar_type(row, prev):
    body=row['close']-row['open']; rng=row['high']-row['low']+1e-9; bp=abs(body)/rng
    uw=row['high']-max(row['close'],row['open'])
    if bp<0.15: return "doji"
    if body>0 and bp>0.6 and uw/rng<0.2: return "strong_bull"
    if body<0 and bp>0.6 and uw/rng<0.2: return "strong_bear"
    return "bull" if body>0 else "bear"

def always_in(df,i):
    w=df.iloc[max(0,i-20):i+1]
    bull=((w['close']>w['open']).sum()+(w['close']>w['close'].shift(1)).sum())
    bear=((w['close']<w['open']).sum()+(w['close']<w['close'].shift(1)).sum())
    if bull>bear+3: return "LONG"
    if bear>bull+3: return "SHORT"
    return "NEUTRAL"

def detect(df,i,vp):
    if i<30: return ("HOLD","none",None)
    row,prev,prev2=df.iloc[i],df.iloc[i-1],df.iloc[i-2]
    price,a=row['close'],df.iloc[i]['atr']
    ai=always_in(df,i); bar=bar_type(row,prev); pb=bar_type(prev,prev2)
    sh=df.iloc[i-10:i]['high'].max(); slw=df.iloc[i-10:i]['low'].min()
    br_up=row['close']>sh; br_dn=row['close']<slw
    poc,vah,val=vp['poc'],vp['vah'],vp['val']
    cu=row['cvd_slope']>0; cd=row['cvd_slope']<0
    bull_pb=row['low']<prev['low'] and ai=="LONG"
    bear_pb=row['high']>prev['high'] and ai=="SHORT"
    s_long=bull_pb and df.iloc[i-1]['low']<df.iloc[i-2]['low'] and bar in ["strong_bull","bull"]
    s_short=bear_pb and df.iloc[i-1]['high']>df.iloc[i-2]['high'] and bar in ["strong_bear","bear"]
    m_db=abs(row['low']-prev['low'])<a*0.2 and pb in ["bear","strong_bear"] and bar in ["bull","strong_bull","doji"]
    m_dt=abs(row['high']-prev['high'])<a*0.2 and pb in ["bull","strong_bull"] and bar in ["bear","strong_bear","doji"]
    if ai=="LONG" and s_long and cu and price>val: return ("LONG","SecondEntry Long",row['low'])
    if br_up and bull_pb and bar in ["strong_bull","bull"] and cu: return ("LONG","Breakout Pullback Long",row['low'])
    if m_db and (row['absorb_bull'] or cu): return ("LONG","Micro Double Bottom",min(row['low'],prev['low'])-a*0.1)
    if df.iloc[i-1]['close']<val and row['close']>val and bar in ["strong_bull","bull"]: return ("LONG","Failed Breakdown VAL",row['low'])
    if ai=="SHORT" and s_short and cd and price<vah: return ("SHORT","SecondEntry Short",row['high'])
    if br_dn and bear_pb and bar in ["strong_bear","bear"] and cd: return ("SHORT","Breakout Pullback Short",row['high'])
    if m_dt and (row['absorb_bear'] or cd): return ("SHORT","Micro Double Top",max(row['high'],prev['high'])+a*0.1)
    if df.iloc[i-1]['close']>vah and row['close']<vah and bar in ["strong_bear","bear"]: return ("SHORT","Failed Breakout VAH",row['high'])
    return ("HOLD","none",None)

def scan():
    for kind,sym in [("c",s) for s in CRYPTO_SYMBOLS]+[("f",s) for s in FOREX_SYMBOLS]:
        try:
            df=fetch_crypto(sym,TIMEFRAME) if kind=="c" else fetch_forex(sym,TIMEFRAME)
            df=add_ind(df); vp=vp_calc(df)
            sig,setup,sl_p=detect(df,len(df)-1,vp)
            price=df.iloc[-1]['close']; a=df.iloc[-1]['atr']
            logger.info(f"{sym} -> {sig}")
            if sig!="HOLD":
                if sl_p is None: sl_p=price-a if sig=="LONG" else price+a
                risk=abs(price-sl_p)+a*0.2
                sl=price-risk if sig=="LONG" else price+risk
                tp=price+risk*2.0 if sig=="LONG" else price-risk*2.0
                e="🟢" if sig=="LONG" else "🔴"
                send_tg(f"{e} *{sig} {sym} ({TIMEFRAME})*\nSetup:{setup}\nEntry:`{price:.4f}`\nSL:`{sl:.4f}`\nTP:`{tp:.4f}`\nPOC:`{vp['poc']:.4f}` VAL:`{vp['val']:.4f}` VAH:`{vp['vah']:.4f}`\n{datetime.now().strftime('%H:%M')}")
        except Exception as e: logger.error(f"{sym}:{e}")

def bot_loop():
    send_tg("✅ ربات رایگان روشن شد. /help")
    last=0
    while True:
        check_commands()
        if time.time()-last >= INTERVAL_MIN*60:
            scan(); last=time.time()
        time.sleep(10)

threading.Thread(target=bot_loop, daemon=True).start()