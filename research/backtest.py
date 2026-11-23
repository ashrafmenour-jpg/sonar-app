"""
Sonar historical test — the pre-registered rules in research/rules.json, on Binance spot 1h candles from 2018 to now.

Answers the three goals with history instead of waiting months:
  1) many decisions (hundreds, not 12) — and how they would have done with costs
  2) is the second half as good as the first? (halves + every year)
  3) does it survive a market change? (results per BTC regime: UP / DOWN / RANGE / REBOUND)
Honesty rules (from the TARGET review):
  * rules are registered BEFORE results (rules.json + its sha256 go into the report); changing a rule = new id
  * train ≤ 2022, holdout ≥ 2023: a rule is "proven" only if the holdout is positive too
  * t-stat on DAILY sums (trades on the same day are not independent)
  * costs: fee + slippage both sides; same-bar stop and target counts as stop
Limits (written into the report): no USDT/BTC dominance history (that part of «مزاج السوق» is left out), no funding,
spot candles not futures, 1h bars (no 15m), no order book.

Data: https://data.binance.vision (public monthly/daily kline zips), cached in research/cache/. From Aug 2017 (Binance's start) since 23 Nov 2026 (was Jan 2018).
Usage: python research/backtest.py [--symbols BTCUSDT,ETHUSDT] [--from 2018-01] [--out research/out]
"""
import argparse, concurrent.futures as cf, datetime as dt, hashlib, io, json, math, os, sys, time, urllib.request, zipfile
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
UNIVERSE = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'XRPUSDT', 'ADAUSDT', 'LTCUSDT', 'LINKUSDT', 'TRXUSDT', 'ETCUSDT', 'XLMUSDT',
            'BCHUSDT', 'DOGEUSDT', 'ATOMUSDT', 'DOTUSDT', 'SOLUSDT', 'AVAXUSDT', 'NEARUSDT', 'UNIUSDT', 'FILUSDT', 'AAVEUSDT']
BASE = 'https://data.binance.vision/data/spot'
# smaller / younger coins where +70–100 % pumps happen — used ONLY by rules with "universe": "alts" (the old rules keep the 20 above)
ALTS = ['AXSUSDT', 'SANDUSDT', 'MANAUSDT', 'GALAUSDT', 'CHZUSDT', 'ENJUSDT', 'RUNEUSDT', 'ONEUSDT', 'HBARUSDT', 'ALGOUSDT', 'VETUSDT', 'THETAUSDT',
        'ZILUSDT', 'KAVAUSDT', 'LRCUSDT', 'IOTXUSDT', 'CELRUSDT', 'ANKRUSDT', 'COTIUSDT', 'HOTUSDT', 'RSRUSDT', 'OGNUSDT', 'CTSIUSDT', 'STORJUSDT',
        'ARPAUSDT', 'DUSKUSDT', 'SKLUSDT', 'GRTUSDT', '1INCHUSDT', 'SUSHIUSDT', 'CRVUSDT', 'SNXUSDT', 'COMPUSDT', 'INJUSDT', 'DYDXUSDT', 'APEUSDT',
        'GMTUSDT', 'OPUSDT', 'ARBUSDT', 'APTUSDT', 'SUIUSDT', 'PEPEUSDT', 'WLDUSDT', 'TIAUSDT', 'PENDLEUSDT', 'FETUSDT', 'IMXUSDT', 'LDOUSDT',
        'STXUSDT', 'MASKUSDT', 'CFXUSDT', 'MAGICUSDT', 'IDUSDT', 'ORDIUSDT', 'BLURUSDT', 'TRBUSDT', 'ARKMUSDT', 'WIFUSDT', 'BOMEUSDT', 'ENAUSDT',
        'BONKUSDT', 'FLOKIUSDT', 'SHIBUSDT', 'JASMYUSDT', 'ROSEUSDT', 'AUDIOUSDT', 'C98USDT', 'ALICEUSDT', 'BAKEUSDT', 'TWTUSDT']

# ---------------------------------------------------------------------------------------------------- data
def _get(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'sonar-research'}), timeout=60) as r: return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404: return None
            time.sleep(2 * (i + 1))
        except Exception: time.sleep(2 * (i + 1))
    raise RuntimeError(f'download failed: {url}')

def _parse(zb):
    with zipfile.ZipFile(io.BytesIO(zb)) as z:
        raw = z.read(z.namelist()[0]).decode()
    rows = [l.split(',') for l in raw.strip().splitlines() if l and l[0].isdigit()]
    if not rows: return None
    a = np.array([[float(x) for x in r[:6]] for r in rows])
    t = a[:, 0]; t = np.where(t > 1e14, t / 1000, t)            # Binance spot files switched to microseconds in 2025
    return pd.DataFrame({'t': t.astype('int64'), 'o': a[:, 1], 'h': a[:, 2], 'l': a[:, 3], 'c': a[:, 4], 'v': a[:, 5]})

def load(sym, start, cache):
    os.makedirs(cache, exist_ok=True)
    today = dt.date.today(); months = []
    y, m = start
    while (y, m) < (today.year, today.month):
        months.append((y, m)); m += 1
        if m == 13: y, m = y + 1, 1
    def month(ym):
        y, m = ym; f = os.path.join(cache, f'{sym}-1h-{y}-{m:02d}.csv')
        if os.path.exists(f): return pd.read_csv(f)
        zb = _get(f'{BASE}/monthly/klines/{sym}/1h/{sym}-1h-{y}-{m:02d}.zip')
        df = _parse(zb) if zb else None
        if df is None: df = pd.DataFrame(columns=['t', 'o', 'h', 'l', 'c', 'v'])
        df.to_csv(f, index=False); return df
    def day(d):   # the current month is not in a monthly file yet
        if os.environ.get("BT_OFFLINE"): return None
        try: zb = _get(f'{BASE}/daily/klines/{sym}/1h/{sym}-1h-{d.isoformat()}.zip'); return _parse(zb) if zb else None
        except Exception: return None
    with cf.ThreadPoolExecutor(8) as ex:
        parts = list(ex.map(month, months))
        days = [today.replace(day=1) + dt.timedelta(days=i) for i in range((today - today.replace(day=1)).days)]
        parts += [p for p in ex.map(day, days) if p is not None]
    parts = [p for p in parts if len(p)]
    if not parts: return None
    df = pd.concat(parts).drop_duplicates('t').sort_values('t').reset_index(drop=True)
    df.index = pd.to_datetime(df.t, unit='ms', utc=True)
    return df[['o', 'h', 'l', 'c', 'v']].astype(float)

# ---------------------------------------------------------------------------------------------------- features
def N_(x):
    try: return x == x and abs(x) != float('inf')
    except Exception: return False
def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def direction(c):
    e20, e50 = ema(c, 20), ema(c, 50)
    return np.where((c > e20) & (e20 > e50), 1, np.where((c < e20) & (e20 < e50), -1, 0))
def resample(df, rule):
    return df.resample(rule, label='left', closed='left').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last', 'v': 'sum'}).dropna()
def closed_on_hours(x, idx, rule):
    """value of a higher-timeframe series known at the CLOSE of each 1h bar (only bars already closed — no look-ahead)"""
    s = pd.Series(x.values if hasattr(x, 'values') else x, index=x.index if hasattr(x, 'index') else None)
    s.index = s.index + pd.Timedelta(rule)                       # a bar is known when it closes
    return s.reindex(idx + pd.Timedelta('1h'), method='ffill').values   # 1h bar at idx closes at idx+1h

def features(df):
    f = pd.DataFrame(index=df.index)
    f['c'], f['h'], f['l'], f['o'] = df.c, df.h, df.l, df.o
    f['v'] = df.v
    f['d1h'] = direction(df.c)
    k4 = resample(df, '4h'); kd = resample(df, '1D')
    f['d4h'] = closed_on_hours(pd.Series(direction(k4.c), index=k4.index), df.index, '4h')
    f['d1d'] = closed_on_hours(pd.Series(direction(kd.c), index=kd.index), df.index, '1D')
    tr = pd.concat([k4.h - k4.l, (k4.h - k4.c.shift()).abs(), (k4.l - k4.c.shift()).abs()], axis=1).max(axis=1)
    f['atr4h'] = closed_on_hours(tr.rolling(14).mean(), df.index, '4h')
    hi48, lo48 = df.h.rolling(48).max(), df.l.rolling(48).min()
    f['pos48'] = (df.c - lo48) / (hi48 - lo48).replace(0, np.nan)
    f['r24'] = df.c / df.c.shift(24) - 1
    # 4h strong candles (abo_ammar): body ≥ 55% of the range
    body = (k4.c - k4.o).abs(); rng = (k4.h - k4.l).replace(0, np.nan); strong = body / rng >= .55
    last_bear_hi = k4.h.where(strong & (k4.c < k4.o)).ffill().shift(1)
    last_bull_lo = k4.l.where(strong & (k4.c > k4.o)).ffill().shift(1)
    brk = np.where(k4.c > last_bear_hi, 1, np.where(k4.c < last_bull_lo, -1, 0))
    # alternate signals only: keep a break only if it differs from the previous non-zero one
    alt, prev = np.zeros(len(brk), int), 0
    for i, b in enumerate(brk):
        if b and b != prev: alt[i] = b; prev = b
    f['brk4h'] = closed_on_hours(pd.Series(alt, index=k4.index), df.index, '4h')
    return f

def btc_context(btc, breadth):
    """«مزاج السوق» rebuilt from history (without USDT/BTC dominance) + a regime label for the report"""
    kd = resample(btc, '1D'); c = kd.c
    s50, s200 = c.rolling(50).mean(), c.rolling(200).mean()
    hi60 = kd.h.rolling(60).max(); dd = (c / hi60 - 1) * 100; r7 = (c / c.shift(7) - 1) * 100; r20 = (c / c.shift(20) - 1) * 100
    reg = np.where((c < s50) & (s50 < s200), 'down', np.where((c > s50) & (s50 > s200), 'up', 'side'))
    ctx = pd.DataFrame({'dd': dd, 'r7': r7, 'r20': r20, 'above200': c > s200, 'reg': reg, 's50': s50, 's200': s200, 'c': c}, index=kd.index)
    # regime for the report (fixes the TARGET mislabel: a −20%/20d drop under both averages is DOWN, not RANGE)
    lab = np.where((c < s200) & ((c < s50) | (r20 <= -10)), 'DOWN', np.where((c > s200) & (s50 > s200), 'UP', 'RANGE'))
    lab = np.where((lab == 'DOWN') & (r7 >= 8), 'REBOUND', lab)
    ctx['regime'] = lab
    ctx = ctx.shift(1)                                           # known at the start of the next day
    ctx.index = ctx.index
    h = btc.index
    out = pd.DataFrame(index=h)
    for k in ['dd', 'r7', 'above200', 'reg', 'regime']: out[k] = ctx[k].reindex(h, method='ffill').values
    out['breadth'] = breadth.reindex(h).values
    def mood(r):
        dd, r7, a2, reg, br = r.dd, r.r7, r.above200, r.reg, r.breadth
        if pd.isna(dd): return 'side'
        if dd <= -15 or (a2 is False and reg == 'down'): return 'deep'
        if dd <= -5 and (reg != 'up' or r7 < 0): return 'pullback'
        if reg == 'up' or r7 >= 3: return 'rally' if (pd.isna(br) or br >= .6) else 'btc_only'
        return 'side'
    out['mood'] = [mood(r) for r in out.itertuples()]
    return out

# ---------------------------------------------------------------------------------------------------- simulation
def candidates(sym, f, ctx, rule):
    side = rule['side']; sig = rule['signal']
    if sig == 'mtf_all':
        a = np.where((f.d1h == 1) & (f.d4h == 1) & (f.d1d == 1), 1, np.where((f.d1h == -1) & (f.d4h == -1) & (f.d1d == -1), -1, 0))
    elif sig == 'cond':   # every listed timeframe direction + where the price sits in its 48h range
        ok = pd.Series(True, index=f.index)
        for k, v in rule.get('dirs', {}).items(): ok &= f[k] == v
        if rule.get('pos_min') is not None: ok &= f.pos48 >= rule['pos_min']
        if rule.get('pos_max') is not None: ok &= f.pos48 <= rule['pos_max']
        a = np.where(ok, side, 0)
    elif sig == 'range_edge':   # trade a coin's own sideways range: long at the floor, short at the ceiling, after a rejection candle
        n = int(rule.get('rng_h', 96)); hi = f.h.shift(1).rolling(n).max(); lo = f.l.shift(1).rolling(n).min(); w = (hi - lo) / lo
        f[f'rhi{n}'], f[f'rlo{n}'] = hi, lo
        flat = (w >= rule.get('w_min', .06)) & (w <= rule.get('w_max', .15)) & ((f.c.shift(n) / f.c - 1).abs() <= w * .5)
        bar = (f.h - f.l).replace(0, np.nan); edge = rule.get('edge', .15)
        lw = (np.minimum(f.o, f.c) - f.l) / bar; uw = (f.h - np.maximum(f.o, f.c)) / bar
        lg = flat & (f.l <= lo + edge * (hi - lo)) & (f.c > f.o) & (lw >= .4) & (f.c > lo)
        sh = flat & (f.h >= hi - edge * (hi - lo)) & (f.c < f.o) & (uw >= .4) & (f.c < hi)
        a = np.where(lg, 1, np.where(sh, -1, 0))
    elif sig == 'daily_event':   # daily-close events with volume vs its 20-day average (HanyStock: «كسر بحجم ضعيف = غالباً كاذب»، «حجم ضخم في القاع = استسلام»)
        d = f[['o', 'h', 'l', 'c', 'v']].resample('1D', label='left', closed='left').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last', 'v': 'sum'}).dropna()
        nb = int(rule.get('lookback_d', 20))
        hi20, lo20, va = d.h.shift(1).rolling(nb).max(), d.l.shift(1).rolling(nb).min(), d.v.shift(1).rolling(20).mean()
        vr = d.v / va.replace(0, np.nan); ev = rule['event']
        if ev in ('sweep_lo', 'sweep_hi'):   # false breakdown / breakout: the day pierces the N-day low (high) and closes back inside
            e = ((d.l < lo20) & (d.c > lo20)) if ev == 'sweep_lo' else ((d.h > hi20) & (d.c < hi20))
            f['_dl'] = closed_on_hours(d.l, f.index, '1D'); f['_dh'] = closed_on_hours(d.h, f.index, '1D')
        else:
            e = (d.c > hi20) if ev == 'brk_hi' else (d.c < lo20) if ev == 'brk_lo' else (d.l <= lo20)   # 'capit': a new N-day low
        if rule.get('vr_min') is not None: e &= vr >= rule['vr_min']
        if rule.get('vr_max') is not None: e &= vr < rule['vr_max']
        x = pd.Series(np.where(e, 1, 0), index=d.index)
        hit = pd.Series(closed_on_hours(x, f.index, '1D'), index=f.index).fillna(0)
        a = np.where(hit > 0, side if side else 1, 0)
    elif sig == 'pump':   # a coin that pumped ≥ min_pump (e.g. +70 %) within 10 days — fade it after the top, or ride the first reclaim
        W = int(rule.get('pump_h', 240)); hi = f.h.rolling(W).max(); lo = f.l.rolling(W).min(); newhi = f.h >= hi
        pk = f.h.where(newhi).ffill(); plo = lo.where(newhi).ffill(); age = newhi.astype(int).groupby(newhi.cumsum()).cumcount()
        f['_pk'], f['_plo'] = pk, plo
        base = (pk / plo >= rule.get('min_pump', 1.7)) & (age >= 1) & (age <= rule.get('max_age_h', 120))
        k4 = resample(f[['o', 'h', 'l', 'c']].assign(v=0), '4h'); e4 = closed_on_hours(ema(k4.c, 20), f.index, '4h'); c4 = closed_on_hours(k4.c, f.index, '4h')
        tr = rule['trig']
        if tr == 'top': ok = base & (age <= 3) & (f.c >= pk * .95)                                  # control: short right at the top
        elif tr == 'ema4h': ok = base & (pd.Series(c4, index=f.index) < pd.Series(e4, index=f.index))   # first 4h close under its EMA20
        elif tr == 'dd15': ok = base & (f.c <= pk * .85)                                             # 15 % off the peak
        elif tr == 'daylow':
            kd = resample(f[['o', 'h', 'l', 'c']].assign(v=0), '1D'); x = pd.Series(np.where(kd.c < kd.l.shift(1), 1, 0), index=kd.index)
            ok = base & (pd.Series(closed_on_hours(x, f.index, '1D'), index=f.index) > 0)          # first daily close under the previous day's low
        elif tr == 'zone':   # ASI-style «لا تطارد»: after the run, wait for a pullback into 50–61.8 % of it, enter on the first green hour closing above the previous hour's high
            rng_ = pk - plo; mid = pk - .5 * rng_; deep = pk - .618 * rng_
            ok = base & (f.l.rolling(6).min() <= mid) & (f.c >= deep) & (f.c > f.o) & (f.c > f.h.shift(1)) & (f.c < mid + .25 * rng_)
        elif tr == 'reclaim':   # pullback 12–30 % from the peak, then a 4h close back above EMA20 → long (continuation)
            ok = base & (f.l.rolling(48).min() <= pk * .88) & (f.c >= pk * .70) & (pd.Series(c4, index=f.index) > pd.Series(e4, index=f.index))
        else: ok = base & False
        a = np.where(ok.fillna(False), side, 0)
    elif sig == 'revert':   # a big move over the last k hours, measured in 4h-ATRs, is faded (Target 4.2's minute bias pointed the wrong way at 1–4 h)
        k = int(rule.get('k_h', 4)); mv = (f.c - f.c.shift(k)) / f.atr4h
        thr = rule.get('thr_atr', 2.0); a = np.where(mv <= -thr, 1, np.where(mv >= thr, -1, 0))
    else:
        a = f.brk4h.fillna(0).values.astype(int)
    a = pd.Series(a, index=f.index)
    onset = (a != 0) & (a != a.shift(1))                       # only when the setup appears, not every hour it lasts
    if side: onset &= a == side
    if rule.get('mood'): onset &= ctx.mood.reindex(f.index).isin(rule['mood']).values
    if rule.get('regime'): onset &= ctx.regime.reindex(f.index).isin(rule['regime']).values
    # BTC's own 7-day move when the setup appears (e.g. «fast shorts only while BTC is still dumping»)
    if rule.get('btc_r7_max') is not None: onset &= (ctx.r7.reindex(f.index).astype(float) <= rule['btc_r7_max']).values
    if rule.get('btc_r7_min') is not None: onset &= (ctx.r7.reindex(f.index).astype(float) >= rule['btc_r7_min']).values
    loc = rule.get('loc')
    if loc:
        p = f.pos48
        # short: chase = price already at the bottom of the 48h range; long: chase = at the top
        chase = np.where(a < 0, p < .25, p > .75); mid = (p > .33) & (p < .67)
        onset &= ~pd.Series(chase, index=f.index) if loc == 'no_chase' else ~(pd.Series(chase, index=f.index) | mid)
    sup = rule.get('sup')   # Sonar's live «دعم/مقاومة قوي قريب» check, approximated by yesterday's low (short) / high (long) 0.8–3 % away
    if sup:
        d = resample(f, '1D'); pl = closed_on_hours(d.l, f.index, '1D'); ph = closed_on_hours(d.h, f.index, '1D')
        dist = pd.Series(np.where(a < 0, (f.c - pl) / f.c, (ph - f.c) / f.c), index=f.index)
        near = (dist > .008) & (dist < .03)
        onset &= ~near if sup == 'avoid' else near
    onset &= f.atr4h.notna() & (f.atr4h > 0)
    return [(t, sym, int(a[t])) for t in f.index[onset.values]]

def simulate(f, t, sg, rule, cost_r_pct):
    i = f.index.get_loc(t) + 1
    if i >= len(f): return None
    entry = f.o.iat[i]
    rng = None
    if rule.get('signal') == 'range_edge':   # stop just outside the range, targets inside it
        n = int(rule.get('rng_h', 96)); rhi, rlo = f[f'rhi{n}'].iat[i - 1], f[f'rlo{n}'].iat[i - 1]; buf = rule.get('stop_buf_atr4h', .25) * f.atr4h.iat[i - 1]
        if not (rhi > rlo) or not (buf >= 0): return None
        rng = (rhi, rlo); risk = (entry - (min(rlo, f.l.iat[i - 1]) - buf)) if sg > 0 else ((max(rhi, f.h.iat[i - 1]) + buf) - entry)   # beyond the range and the rejection wick
    elif rule.get('stop_mode') == 'zone':   # below the 78.6 % retrace of the run (the bottom of the follow zone)
        pk_, plo_ = f['_pk'].iat[i - 1], f['_plo'].iat[i - 1]; lvl = plo_ + .214 * (pk_ - plo_) - rule.get('stop_buf_atr4h', .1) * f.atr4h.iat[i - 1]
        risk = entry - lvl if sg > 0 else None
        if risk is None: return None
    elif rule.get('stop_mode') == 'swing12h':   # tighter: just beyond the last 12 hours' extreme (≈ the last three 4h candles), not the whole pump
        w = f.h.values[max(0, i - 12):i].max() if sg < 0 else f.l.values[max(0, i - 12):i].min()
        risk = ((w - entry) if sg < 0 else (entry - w)) + rule.get('stop_buf_atr4h', .25) * f.atr4h.iat[i - 1]
    elif rule.get('stop_mode') == 'sweep':   # beyond the wick of the sweep day
        w = f['_dl'].iat[i - 1] if sg > 0 else f['_dh'].iat[i - 1]
        risk = ((entry - w) if sg > 0 else (w - entry)) + rule.get('stop_buf_atr4h', .25) * f.atr4h.iat[i - 1]
    elif rule.get('stop_mode') == 'peak':   # short a pump: stop just above its peak
        risk = (f['_pk'].iat[i - 1] + rule.get('stop_buf_atr4h', .25) * f.atr4h.iat[i - 1]) - entry if sg < 0 else None
        if risk is None: risk = rule['stop_atr4h'] * f.atr4h.iat[i - 1]
    else:
        risk = entry * rule['stop_pct'] / 100 if rule.get('stop_pct') else rule['stop_atr4h'] * f.atr4h.iat[i - 1]
    if not (risk > 0) or risk / entry > .25: return None
    stop = entry - sg * risk; be = False
    # targets: one target at tp_r · or the brain's picks: half at tp_pct[0] (stop → entry), the rest at tp_pct[1]
    if rule.get('tp_mode') == 'peak' and '_pk' in f:   # target: the day's high again
        tps = [f['_pk'].iat[i - 1]]
        if sg * (tps[0] - entry) <= 0: return None
    elif rng and rule.get('tp_mode') in ('mid', 'far'):
        tps = [(rng[0] + rng[1]) / 2 if rule['tp_mode'] == 'mid' else (rng[0] if sg > 0 else rng[1])]
        if sg * (tps[0] - entry) <= 0: return None
    else:
        tps = [entry * (1 + sg * x / 100) for x in rule['tp_pct']] if rule.get('tp_pct') else [entry + sg * rule.get('tp_r', 1e9) * risk]
    if rule.get('no_tp'): tps = [entry + sg * 1e9 * risk]          # flexible exits: no fixed target, the trailing stop decides
    half = len(tps) > 1; banked = 0.0; frac = .5
    pf = rule.get('partial')                                        # quick + long in one trade: bank `frac` at +at_r, stop → entry, the rest runs
    if pf: tps = [entry + sg * pf.get('at_r', 1.0) * risk, entry + sg * 1e9 * risk]; half = True; frac = pf.get('frac', .5)
    trail = rule.get('trail'); pyr = rule.get('pyramid'); e2 = None; A4 = f.atr4h.values
    end = min(len(f) - 1, i + rule['max_hours'] - 1); H, L, C = f.h.values, f.l.values, f.c.values
    D4 = f.d4h.values; dead = rule.get('dead'); mfe = 0.0
    exit_p, why, j = None, 'time', end
    for j in range(i, end + 1):
        tp = tps[1] if (half and banked) else tps[0]
        hit_stop = L[j] <= stop if sg > 0 else H[j] >= stop
        hit_tp = H[j] >= tp if sg > 0 else L[j] <= tp
        if hit_stop: exit_p, why = stop, 'be' if be else 'stop'; break
        if hit_tp:
            if half and not banked: banked = frac * sg * (tp - entry) / risk; stop, be = entry, True; continue
            exit_p, why = tp, 'target'; break
        r_now = sg * (C[j] - entry) / risk; mfe = max(mfe, sg * ((H[j] if sg > 0 else L[j]) - entry) / risk)
        if pyr and e2 is None and r_now >= pyr.get('at_r', 1.0):   # add a second unit only once the first is risk-free
            e2 = C[j]; stop = entry if sg * (entry - stop) > 0 else stop; be = True
        if trail and mfe >= trail.get('after_r', 1.0):               # the stop only ever moves in our favour
            if trail['mode'] == 'atr':
                ext = (H[i:j + 1].max() if sg > 0 else L[i:j + 1].min()); cand = ext - sg * trail.get('k', 3.0) * A4[j]
            else:   # 'struct': beyond the last 12 hours' extreme (≈ three 4h candles)
                cand = (L[max(i, j - 11):j + 1].min() if sg > 0 else H[max(i, j - 11):j + 1].max()) - sg * .25 * A4[j]
            if N_(cand) and sg * (cand - stop) > 0: stop = cand
        if not be and rule.get('be_at_r') and r_now >= rule['be_at_r']: stop, be = entry, True
        if dead and j - i + 1 >= dead['hours'] and mfe < dead['mfe_r'] and not banked: exit_p, why = C[j], 'dead'; break
        if rule.get('flip4h') and D4[j] == -sg and not banked: exit_p, why = C[j], 'flip'; break
    if exit_p is None: exit_p = C[end]
    gross = (banked + (1 - frac) * sg * (exit_p - entry) / risk) if (half and banked) else sg * (exit_p - entry) / risk
    cost = cost_r_pct / 100 * entry / risk                       # round-trip fee + slippage in R
    if e2 is not None: gross += sg * (exit_p - e2) / risk; cost += cost_r_pct / 100 * e2 / risk   # the added unit, in the first unit's R
    return {'t': t, 'exit_t': f.index[j], 'entry': entry, 'R': gross - cost, 'gross': gross, 'why': why, 'stop_pct': risk / entry * 100}

def stats(R, days=None):
    R = np.asarray(R, float)
    if not len(R): return {'n': 0}
    o = {'n': int(len(R)), 'win': round(float((R > 0).mean() * 100), 1), 'mean': round(float(R.mean()), 3), 'sum': round(float(R.sum()), 1)}
    eq = np.cumsum(R); o['maxdd'] = round(float((np.maximum.accumulate(eq) - eq).max()), 1)
    if days is not None and len(R) > 2:
        d = pd.Series(R).groupby(pd.Series(days).values).sum()
        o['days'] = int(len(d)); o['t'] = round(float(d.mean() / (d.std(ddof=1) / math.sqrt(len(d)))) if len(d) > 2 and d.std(ddof=1) > 0 else 0.0, 2)
    return o

def verdict(s):
    a, tr, ho, h1, h2 = s['all'], s['train'], s['holdout'], s['half1'], s['half2']
    if a.get('n', 0) < 60: return ('few', 'عدد الصفقات قليل (أقل من 60) — مفيش حكم')
    if a['mean'] > 0 and a.get('t', 0) >= 2 and ho.get('mean', -1) > 0 and h1.get('mean', -1) > 0 and h2.get('mean', -1) > 0:
        return ('proven', 'مثبت: كسبان على 8 سنين، وفي الفترة اللي ما اتشافتش (2023+)، وفي النصين')
    if a['mean'] > 0 and a.get('t', 0) >= 1 and ho.get('mean', -1) > 0: return ('promising', 'واعد: كسبان والفترة الجديدة كسبانة، بس الثقة الإحصائية أو أحد النصين ناقص')
    if a['mean'] > 0: return ('fragile', 'هش: كسبان على التاريخ كله بس خسران في الفترة الجديدة (2023+) — غالبًا مش هيعيش')
    return ('no', 'مش بيكسب بعد المصاريف')

# ---------------------------------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--symbols', default=','.join(UNIVERSE + ALTS)); ap.add_argument('--from', dest='start', default='2017-08')
    ap.add_argument('--out', default=os.path.join(HERE, 'out')); ap.add_argument('--cache', default=os.path.join(HERE, 'cache'))
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
    rules_raw = open(os.path.join(HERE, 'rules.json'), 'rb').read(); cfg = json.loads(rules_raw)
    y, m = map(int, a.start.split('-')); syms = a.symbols.split(',')
    t0 = time.time(); data = {}
    for s in syms:
        df = load(s, (y, m), a.cache)
        if df is not None and len(df) > 24 * 300: data[s] = df; print(f'{s}: {len(df)} hours from {df.index[0].date()}', flush=True)
        else: print(f'{s}: not enough data', flush=True)
    if 'BTCUSDT' not in data: sys.exit('BTCUSDT data missing — cannot build market context')
    F = {s: features(d) for s, d in data.items()}
    r24 = pd.DataFrame({s: F[s].r24 for s in F if s != 'BTCUSDT'})
    r24 = r24[[c for c in r24.columns if c in UNIVERSE]]              # breadth from the original 20 only, so old results don't move
    breadth = (r24 > 0).sum(axis=1) / r24.notna().sum(axis=1).replace(0, np.nan)
    breadth[r24.notna().sum(axis=1) < 8] = np.nan
    ctx = btc_context(data['BTCUSDT'], breadth)
    cost = 2 * (cfg['costs']['fee_pct_per_side'] + cfg['costs']['slippage_pct_per_side'])
    split = pd.Timestamp(cfg['split']['holdout_from'], tz='UTC'); out_rules = []
    for rule in cfg['rules']:
        pool = ALTS if rule.get('universe') == 'alts' else UNIVERSE
        cands = sorted(c for s in F if s in pool for c in candidates(s, F[s], ctx, rule))
        open_until, per_coin, trades, stops = [], {}, [], []
        max_open = rule.get('max_open', cfg['max_open_per_rule'])                 # portfolio cap (all trades of a rule share one side)
        pause = pd.Timedelta(hours=rule['pause_after_stop_h']) if rule.get('pause_after_stop_h') else None
        for t, s, sg in cands:
            open_until = [x for x in open_until if x > t]
            if len(open_until) >= max_open or per_coin.get(s, t) > t: continue
            if pause is not None and any(t - pause <= x <= t for x in stops[-50:]): continue   # a full stop in the last N hours (only stops already closed at t)
            r = simulate(F[s], t, sg, rule, cost)
            if not r: continue
            r.update(sym=s, side=sg, regime=str(ctx.regime.get(t, 'RANGE')), mood=str(ctx.mood.get(t, '')))
            trades.append(r); open_until.append(r['exit_t']); per_coin[s] = r['exit_t']
            if r['why'] == 'stop': stops.append(r['exit_t']); stops.sort()
        T = pd.DataFrame(trades)
        if not len(T): out_rules.append({'id': rule['id'], 'name': rule['name'], 'stats': {'all': {'n': 0}}, 'verdict': ['few', 'ولا صفقة']}); continue
        day = T.t.dt.floor('D').astype(str).values; half = len(T) // 2
        S = {'all': stats(T.R, day), 'train': stats(T.R[T.t < split], day[(T.t < split).values]), 'holdout': stats(T.R[T.t >= split], day[(T.t >= split).values]),
             'half1': stats(T.R[:half], day[:half]), 'half2': stats(T.R[half:], day[half:])}
        reg = pd.Timestamp(rule.get('registered', cfg['registered']), tz='UTC')
        S['after_reg'] = stats(T.R[T.t >= reg], day[(T.t >= reg).values])   # the honest test for rules designed after seeing results
        S['by_regime'] = {g: stats(T.R[T.regime == g], day[(T.regime == g).values]) for g in ['DOWN', 'REBOUND', 'RANGE', 'UP']}
        S['by_year'] = {str(yy): stats(T.R[T.t.dt.year == yy]) for yy in sorted(T.t.dt.year.unique())}
        S['by_side'] = {('long' if k > 0 else 'short'): stats(T.R[T.side == k]) for k in sorted(T.side.unique())}
        S['exits'] = T.why.value_counts().to_dict(); S['median_stop_pct'] = round(float(T.stop_pct.median()), 2)
        S['last'] = [{'t': str(x.t)[:16], 'sym': x.sym, 'side': int(x.side), 'R': round(float(x.R), 2), 'why': x.why} for x in T.tail(12).itertuples()]
        v = verdict(S)
        if rule.get('seen'): v = (v[0], v[1] + ' — اتصممت بعد ما شفنا النتايج، فالحكم الحقيقي من صفقاتها بعد ' + rule['registered'])
        out_rules.append({'id': rule['id'], 'name': rule['name'], 'spec': rule, 'stats': S, 'verdict': list(v)})
        print(f"{rule['id']}: n={S['all']['n']} mean={S['all'].get('mean')} t={S['all'].get('t')} holdout={S['holdout'].get('mean')} -> {v[0]}", flush=True)
    rc = pd.Series(ctx.regime.values, index=ctx.index).resample('1D').last().value_counts().to_dict()
    report = {'app': 'sonar-backtest', 'v': 1, 'at': dt.datetime.now(dt.timezone.utc).isoformat()[:19] + 'Z', 'rules_sha256': hashlib.sha256(rules_raw).hexdigest(),
              'registered': cfg['registered'], 'symbols': list(data), 'from': str(min(d.index[0] for d in data.values()))[:10], 'to': str(max(d.index[-1] for d in data.values()))[:16],
              'costs_pct_round_trip': cost, 'split': cfg['split'], 'regime_days': {str(k): int(v) for k, v in rc.items()},
              'current': {'regime': str(ctx.regime.iloc[-1]), 'mood': str(ctx.mood.iloc[-1])}, 'seconds': round(time.time() - t0),
              'limits': ['من غير استحواذ USDT/BTC (مفيش تاريخ ليه) — باقي مزاج السوق محسوب', 'شموع سبوت ساعة (مش فيوتشر ومش 15 دقيقة)، من غير تمويل ولا دفتر أوامر',
                         'وقف وهدف في نفس الشمعة = وقف', 'القواعد متسجلة قبل النتايج — أي تعديل = قاعدة جديدة باسم جديد', 'كذا قاعدة اتجربت مع بعض: «مثبت» محتاج t ≥ 2 والفترة الجديدة والنصين'],
              'rules': out_rules}
    json.dump(report, open(os.path.join(a.out, 'backtest-latest.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
    lines = [f"# اختبار Sonar على التاريخ — {report['from']} → {report['to']}", '', f"{len(data)} عملة · مصاريف {cost}% ذهاب وعودة · القواعد اتسجلت {cfg['registered']} (sha256 {report['rules_sha256'][:12]})", '',
             '| القاعدة | صفقات | كسب % | متوسط R | t | 2018–2022 | 2023+ | النص 1 / 2 | هابط | ارتداد | عرضي | صاعد | الحكم |', '|' + '---|' * 13]
    for r in out_rules:
        S = r['stats']; g = lambda x: '—' if not x or not x.get('n') else f"{x['mean']:+.2f} ({x['n']})"
        if not S['all'].get('n'): lines.append(f"| {r['id']} | 0 |" + ' — |' * 10 + f" {r['verdict'][1]} |"); continue
        B = S['by_regime']
        lines.append(f"| {r['id']} | {S['all']['n']} | {S['all']['win']} | {S['all']['mean']:+.3f} | {S['all'].get('t', '—')} | {g(S['train'])} | {g(S['holdout'])} | {g(S['half1'])} / {g(S['half2'])} | {g(B['DOWN'])} | {g(B['REBOUND'])} | {g(B['RANGE'])} | {g(B['UP'])} | {r['verdict'][1]} |")
    lines += ['', '**حدود:** ' + ' · '.join(report['limits'])]
    open(os.path.join(a.out, 'backtest-latest.md'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    print('\n'.join(lines))

if __name__ == '__main__': main()
