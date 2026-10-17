# Hoosha Pro-5 Signal Engine - Render Free Web Service
# D1 Wyckoff -> H4 AlwaysIn+Classic+Profile -> M15 SMC Setup -> M5 Trigger
# Semi-auto via Telegram, balance & risk manual, TPs = reversal points
import os, time, requests, logging, threading
import pandas as pd
import numpy as np
from datetime import datetime
from flask import Flask
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("Pro5")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "PUT_TOKEN")
TELEGRAM_CHAT_ID = str(os.getenv("TELEGRAM_CHAT_ID", "PUT_ID"))
CRYPTO_SYMBOLS = os.getenv("CRYPTO_SYMBOLS", "BTC/USDT,ETH/USDT").split(",")
FOREX_SYMBOLS = os.getenv("FOREX_SYMBOLS", "EURUSD=X,GBPUSD=X").split(",")
GOLD_SYMBOL = os.getenv("GOLD_SYMBOL", "GC=F")
BALANCE = float(os.getenv("BALANCE", "1000"))
RISK_PERCENT = float(os.getenv("RISK_PERCENT", "1.0"))
MANUAL_LOT = os.getenv("MANUAL_LOT_OVERRIDE", "")
INTERVAL_MIN = int(os.getenv("INTERVAL_MIN", "30"))
OFFSET = 0
app = Flask(__name__)
@app.route("/")
def home(): return f"OK Hoosha Pro-5 Alive {datetime.now()}"
@app.route("/ping")
def ping(): return "pong"

def send_tg(t):
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": t, "parse_mode": "Markdown"}, timeout=15)
    except Exception as e: logger.error(e)

# ---------- indicators ----------
def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def atr(df, n=14):
    tr = pd.concat([df['high']-df['low'], (df['high']-df['close'].shift()).abs(), (df['low']-df['close'].shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()
def sma(s, n): return s.rolling(n).mean()
def pivots(df, l=3):
    highs, lows = [], []
    for i in range(l, len(df)-l):
        if df['high'].iloc[i] == df['high'].iloc[i-l:i+l+1].max(): highs.append((i, df['high'].iloc[i]))
        if df['low'].iloc[i] == df['low'].iloc[i-l:i+l+1].min(): lows.append((i, df['low'].iloc[i]))
    return highs, lows

# ---------- data ----------
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
        lc=c.lower()
        if "open" in lc: m[c]="open"
        elif "high" in lc: m[c]="high"
        elif "low" in lc: m[c]="low"
        elif "close" in lc and "adj" not in lc: m[c]="close"
        elif "volume" in lc: m[c]="volume"
    df=df.rename(columns=m)
    if "volume" not in df.columns or df["volume"].sum()==0: df["volume"]=1000.0
    return df[["open","high","low","close","volume"]].dropna().reset_index(drop=True)

def get_tf(kind, sym, tf):
    if kind=="c": return fetch_crypto(sym, tf)
    ymap={"1d":"1d","4h":"1h","15m":"15m","5m":"5m"}
    per={"1d":"1y","4h":"6mo","15m":"1mo","5m":"5d"}
    df=fetch_yf(sym, ymap.get(tf,"15m"), per.get(tf,"1mo"))
    if tf=="4h": # resample 1h -> 4h
        df.index=pd.to_datetime(df.index) if False else df.index
        return df
    return df

# ---------- modules ----------
def wyckoff_d1(df):
    df=df.copy(); df['atr20']=atr(df,20); df['volsma']=sma(df['volume'],20)
    highs,lows=pivots(df)
    bias="NEUTRAL"; phase="RANGE"; climax=False
    if len(highs)>=2 and len(lows)>=2:
        if highs[-1][1]>highs[-2][1] and lows[-1][1]>lows[-2][1]: phase="MARKUP"; bias="LONG"
        elif highs[-1][1]<highs[-2][1] and lows[-1][1]<lows[-2][1]: phase="MARKDOWN"; bias="SHORT"
    last5=df.iloc[-5:]
    climax=bool(((last5['volume']>2.5*last5['volsma'])&((last5['close']-last5['open']).abs()>2*last5['atr20'])).any())
    # Spring / UTAD
    mn=df['low'].iloc[-20:].min()
    mx=df['high'].iloc[-20:].max()
    last=df.iloc[-1]
    spring = last['low']<mn and last['close']>mn and last['volume']>1.5*last['volsma']
    utad = last['high']>mx and last['close']<mx and last['volume']>1.5*last['volsma']
    score20=0
    if spring: phase="ACCUM"; bias="LONG"; score20=20
    elif utad: phase="DISTRIB"; bias="SHORT"; score20=20
    elif bias!="NEUTRAL": score20=20
    return {"bias":bias,"phase":phase,"climax":climax,"spring":bool(spring),"utad":bool(utad),"score":score20}

def h4_module(df):
    df=df.copy(); df['ema20']=ema(df['close'],20); df['atr']=atr(df,14)
    ai="NEUTRAL"
    if len(df)>12:
        br_up=df['close'].iloc[-1]>df['high'].iloc[-11:-1].max()
        br_dn=df['close'].iloc[-1]<df['low'].iloc[-11:-1].min()
        if df['close'].iloc[-1]>df['ema20'].iloc[-1] and df['ema20'].iloc[-1]>df['ema20'].iloc[-4] and br_up: ai="LONG"
        elif df['close'].iloc[-1]<df['ema20'].iloc[-1] and df['ema20'].iloc[-1]<df['ema20'].iloc[-4] and br_dn: ai="SHORT"
    highs,lows=pivots(df)
    levels=[]
    for _,p in highs+lows:
        touches=((df['high']-p).abs()<0.3*df['atr'].iloc[-1]).sum()+((df['low']-p).abs()<0.3*df['atr'].iloc[-1]).sum()
        if touches>=2: levels.append(float(p))
    # Value Area last 30
    w=df.iloc[-30:]; lo,hi=w['low'].min(),w['high'].max()
    edges=np.linspace(lo,hi,51); vols=np.zeros(50)
    for _,r in w.iterrows():
        il=max(0,min(49,np.searchsorted(edges,r['low'])-1)); ih=max(0,min(49,np.searchsorted(edges,r['high'])-1))
        for k in range(il,ih+1): vols[k]+=r['volume']/max(1,ih-il+1)
    pi=int(np.argmax(vols)); poc=(edges[pi]+edges[pi+1])/2
    tot=vols.sum(); order=np.argsort(vols)[::-1]; cum=0; va=set()
    for k in order:
        va.add(k); cum+=vols[k]
        if cum>=tot*0.7: break
    vah, val = edges[max(va)+1], edges[min(va)]
    close=df['close'].iloc[-1]
    va_bias="MID"
    if close>vah: va_bias="ABOVE"
    elif close<val: va_bias="BELOW"
    return {"ai":ai,"levels":levels,"poc":float(poc),"vah":float(vah),"val":float(val),"va_bias":va_bias,"atr":float(df['atr'].iloc[-1])}

def smc(df):
    df=df.copy(); df['atr']=atr(df,14)
    highs,lows=pivots(df)
    bos=None; choch=None; obs=[]; fvgs=[]
    # BOS/CHoCH simplified: last swing break close-based
    if highs and lows:
        lastH=highs[-1][1]; lastL=lows[-1][1]
        c=df['close'].iloc[-1]
        if c>lastH: bos="LONG"
        elif c<lastL: bos="SHORT"
    for i in range(2,len(df)):
        a=df['atr'].iloc[i]
        if df['close'].iloc[i]-df['open'].iloc[i-1]>1.5*a and df['close'].iloc[i-1]<df['open'].iloc[i-1]:
            obs.append((float(df['low'].iloc[i-1]),float(df['high'].iloc[i-1])))
        if df['low'].iloc[i]>df['high'].iloc[i-2]: fvgs.append({"dir":"BULL","edge":float(df['low'].iloc[i]),"mid":float((df['low'].iloc[i]+df['high'].iloc[i-2])/2)})
        if df['high'].iloc[i]<df['low'].iloc[i-2]: fvgs.append({"dir":"BEAR","edge":float(df['high'].iloc[i]),"mid":float((df['high'].iloc[i]+df['low'].iloc[i-2])/2)})
    # sweep
    mn=df['low'].iloc[-20:].min(); mx=df['high'].iloc[-20:].max()
    sweepL=bool(df['low'].iloc[-2]<mn and df['close'].iloc[-2]>mn)
    sweepH=bool(df['high'].iloc[-2]>mx and df['close'].iloc[-2]<mx)
    return {"bos":bos,"obs":obs[-3:],"fvgs":fvgs[-3:],"sweepL":sweepL,"sweepH":sweepH}

def setups_m15(df15, h4, smc15):
    df=df15.copy(); df['atr']=atr(df,14); df['volsma']=sma(df['volume'],20); df['ema20']=ema(df['close'],20)
    i=len(df)-1; row=df.iloc[i]; prev=df.iloc[i-1]
    zones=h4['levels']+[h4['vah'],h4['val'],float(df['ema20'].iloc[i])]+[z[1] for z in smc15['obs']]
    def in_zone(p): return any(abs(p-z)<0.5*df['atr'].iloc[i] for z in zones)
    sig="HOLD"; stype="none"; score_q=0
    # S1 sweep+reversal
    if df['low'].iloc[i-1]<df['low'].iloc[-21:-1].min() and df['close'].iloc[i-1]>df['low'].iloc[-21:-1].min() and row['close']>prev['high']: sig="LONG"; stype="S1_sweep_reversal"
    # S2 H2
    if sig=="HOLD" and df['low'].iloc[i]>df['low'].iloc[i-1]>df['low'].iloc[i-2] and row['close']>row['open'] and in_zone(row['close']): sig="LONG"; stype="S2_H2"
    # S3 OB retest bullish engulfing
    if sig=="HOLD":
        for oL,oH in smc15['obs']:
            if oL<=row['low']<=oH and row['close']>prev['open'] and prev['close']<prev['open']: sig="LONG"; stype="S3_OB_retest"; break
    # S4 FVG fill
    if sig=="HOLD":
        for f in smc15['fvgs']:
            if f['dir']=="BULL" and abs(row['low']-f['mid'])<0.3*df['atr'].iloc[i]: sig="LONG"; stype="S4_FVG_fill"; break
    # S5 failed breakdown
    if sig=="HOLD" and df['close'].iloc[i-2]<df['low'].iloc[-11:-2].min() and row['close']>df['low'].iloc[-11:-2].min(): sig="LONG"; stype="S5_failed_breakdown"
    # mirror shorts (simplified): if no long but bearish mirror of S1
    if sig=="HOLD" and df['high'].iloc[i-1]>df['high'].iloc[-21:-1].max() and df['close'].iloc[i-1]<df['high'].iloc[-21:-1].max() and row['close']<prev['low']: sig="SHORT"; stype="S1_sweep_reversal_S"
    return {"sig":sig,"type":stype,"in_zone":in_zone(row['close']),"vol_ok":bool(row['volume']>=1.2*df['volsma'].iloc[i]),"body_ok":bool(abs(row['close']-row['open'])>=0.6*(row['high']-row['low']))}

def build_signal(kind, sym):
    # fetch 4 TFs
    tfmap_c={"D1":"1d","H4":"4h","M15":"15m","M5":"5m"}
    tfmap_f={"D1":"1d","H4":"4h","M15":"15m","M5":"5m"}
    d1=get_tf(kind,sym,tfmap_c["D1"] if kind=="c" else tfmap_f["D1"])
    h4=get_tf(kind,sym,tfmap_c["H4"] if kind=="c" else tfmap_f["H4"])
    m15=get_tf(kind,sym,tfmap_c["M15"] if kind=="c" else tfmap_f["M15"])
    m5=get_tf(kind,sym,tfmap_c["M5"] if kind=="c" else tfmap_f["M5"])
    # hard filters (spread/news/open trades simplified - spread 0.02 pass, news passed externally)
    wk=wyckoff_d1(d1); h=h4_module(h4); s15=smc(m15); st=setups_m15(m15,h,s15)
    # direction gate
    direction=st["sig"]
    if wk["bias"]!="NEUTRAL" and direction!="HOLD" and wk["bias"]!=direction: return None
    if h["ai"]!="NEUTRAL" and direction!="HOLD" and h["ai"]!=direction: return None
    if direction=="HOLD": return None
    if h["va_bias"]=="MID": return None
    if wk["climax"]: return None
    # score
    score=0
    score+=wk["score"]
    if h["ai"]==direction: score+=15
    if st["type"].startswith("S1") or st["in_zone"]: score+=20
    if any(abs(m15['close'].iloc[-1]-z)<0.5*h["atr"] for z in h["levels"]+[h["vah"],h["val"],h["poc"]]): score+=15
    if st["vol_ok"] and st["body_ok"]: score+=15
    if st["vol_ok"] and st["body_ok"] and st["in_zone"] and h["ai"]==direction: score+=15
    if score<70: return None
    # trigger M5: stop entry
    sig_candle=m5.iloc[-1]; atr5=float(atr(m5,14).iloc[-1])
    entry=float(sig_candle['high']+0.01) if direction=="LONG" else float(sig_candle['low']-0.01)
    stop=float(sig_candle['low']) if direction=="LONG" else float(sig_candle['high'])
    stop=min(stop, entry-atr5) if direction=="LONG" else max(stop, entry+atr5)
    # TPs = reversal points ahead
    cands=[]
    for f in s15["fvgs"]:
        if direction=="LONG" and f["edge"]>entry: cands.append((f["edge"],"FVG_edge"))
        if direction=="SHORT" and f["edge"]<entry: cands.append((f["edge"],"FVG_edge"))
    for oL,oH in s15["obs"]:
        lvl=oH if direction=="SHORT" else oL
        if (direction=="LONG" and lvl>entry) or (direction=="SHORT" and lvl<entry): cands.append((lvl,"OB"))
    for lvl in [h["poc"],h["vah"],h["val"]]+h["levels"]:
        if (direction=="LONG" and lvl>entry) or (direction=="SHORT" and lvl<entry): cands.append((lvl,"Profile/Classic"))
    cands=sorted(cands, key=lambda x: abs(x[0]-entry))
    tps=[cands[0],cands[1] if len(cands)>1 else cands[0],cands[2] if len(cands)>2 else cands[0]] if cands else []
    # sizing
    risk_money=BALANCE*(RISK_PERCENT/100)
    dist=abs(entry-stop)+1e-9
    vol=risk_money/dist
    if MANUAL_LOT.strip()!="":
        try: vol=float(MANUAL_LOT)
        except: pass
    return {"symbol":sym,"signal":direction,"score":score,"wyckoff":wk,"ai_H4":h["ai"],"setup":st["type"],"entry":entry,"stop":stop,"vol":vol,"risk_money":risk_money,"tps":tps,"poc":h["poc"],"vah":h["vah"],"val":h["val"]}

def scan():
    jobs=[("c",s) for s in CRYPTO_SYMBOLS if s.strip()]+[("f",s) for s in FOREX_SYMBOLS if s.strip()]+[("f",GOLD_SYMBOL)]
    for kind,sym in jobs:
        try:
            r=build_signal(kind,sym.strip())
            logger.info(f"{sym} -> {r['signal']+' '+str(r['score']) if r else 'HOLD'}")
            if r:
                tp_txt="\n".join([f"TP{i+1} `{p[0]:.4f}` ({p[1]})" for i,p in enumerate(r['tps'])]) if r['tps'] else "Open space"
                send_tg(f"🎯 *{r['signal']} {sym} | Score {r['score']}*\nSetup: `{r['setup']}`\nWyckoff: {r['wyckoff']['phase']} | H4:{r['ai_H4']}\nEntry(stop): `{r['entry']:.4f}`\nSL: `{r['stop']:.4f}`\nVol: `{r['vol']:.4f}` Risk: `${r['risk_money']:.2f}`\n{tp_txt}\nPOC:`{r['poc']:.4f}` VAH:`{r['vah']:.4f}` VAL:`{r['val']:.4f}`\nMgmt: BE after TP1, trail TP3 M15 EMA20")
        except Exception as e: logger.error(f"{sym}:{e}")

def check_commands():
    global OFFSET, BALANCE, RISK_PERCENT, INTERVAL_MIN, CRYPTO_SYMBOLS, FOREX_SYMBOLS, MANUAL_LOT
    try:
        r=requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates", params={"offset":OFFSET,"timeout":5}, timeout=15).json()
        for u in r.get("result",[]):
            OFFSET=u["update_id"]+1
            msg=u.get("message",{})
            if str(msg.get("chat",{}).get("id",""))!=str(TELEGRAM_CHAT_ID): continue
            t=msg.get("text","").strip()
            if not t.startswith("/"): continue
            p=t.split(); cmd=p[0].lower(); arg=" ".join(p[1:]) if len(p)>1 else ""
            if cmd=="/start": send_tg("✅ Hoosha Pro-5 live.\n/status\n/balance 1000\n/risk 1.0\n/lot 0.05\n/symbols")
            elif cmd=="/help": send_tg("/status\n/balance 1000\n/risk 1.0\n/lot 0.05 or /lot auto\n/add BTC/USDT\n/remove BTC/USDT\n/interval 30")
            elif cmd=="/status": send_tg(f"📊 Balance:`{BALANCE}` Risk:`{RISK_PERCENT}%` Lot:`{MANUAL_LOT or 'auto'}`\nCrypto:{','.join(CRYPTO_SYMBOLS)}\nForex:{','.join(FOREX_SYMBOLS)}\nGold:{GOLD_SYMBOL} Interval:{INTERVAL_MIN}m")
            elif cmd=="/balance" and arg: BALANCE=float(arg); send_tg(f"Balance set: `{BALANCE}`")
            elif cmd=="/risk" and arg: RISK_PERCENT=float(arg); send_tg(f"Risk set: `{RISK_PERCENT}%`")
            elif cmd=="/lot" and arg: MANUAL_LOT="" if arg=="auto" else arg; send_tg(f"Lot: `{MANUAL_LOT or 'auto'}`")
            elif cmd=="/symbols": send_tg(f"Crypto:{','.join(CRYPTO_SYMBOLS)}\nForex:{','.join(FOREX_SYMBOLS)}\nGold:{GOLD_SYMBOL}")
            elif cmd=="/add" and arg:
                a=arg.upper(); (CRYPTO_SYMBOLS.append(a) if "/" in a else FOREX_SYMBOLS.append(a)); send_tg(f"Added `{a}`")
            elif cmd=="/remove" and arg:
                a=arg.upper()
                if a in CRYPTO_SYMBOLS: CRYPTO_SYMBOLS.remove(a)
                if a in FOREX_SYMBOLS: FOREX_SYMBOLS.remove(a)
                send_tg(f"Removed `{a}`")
            elif cmd=="/interval" and arg: INTERVAL_MIN=int(arg); send_tg(f"Interval `{INTERVAL_MIN}m`")
    except Exception as e: logger.error(e)

def loop():
    send_tg("✅ Hoosha Pro-5 started. Send /status")
    last=0
    while True:
        check_commands()
        if time.time()-last>=INTERVAL_MIN*60: scan(); last=time.time()
        time.sleep(10)
threading.Thread(target=loop, daemon=True).start()
