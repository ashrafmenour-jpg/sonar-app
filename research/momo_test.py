"""Historical test of the 🚀 strong-move detector (MOMO) — Binance USDT-M futures 5-minute candles, variants pre-registered in
research/momo_rules.json. Writes momo-latest.json / .md next to the main backtest report. Runs on GitHub Actions (backtest.yml).
Look-ahead guard: every trigger uses candles up to the trigger candle only; entry is the NEXT candle's open; a stop and a target
in the same candle count as the stop."""
import argparse, concurrent.futures as cf, datetime as dt, hashlib, io, json, os, sys, time, urllib.request, zipfile
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = 'https://data.binance.vision/data/futures/um'

def _get(url, tries=3):
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'sonar-research'}), timeout=60) as r: return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404: return None
        except Exception: pass
        time.sleep(2 * (k + 1))
    return None

def _parse(zb):
    with zipfile.ZipFile(io.BytesIO(zb)) as z: raw = z.read(z.namelist()[0]).decode()
    rows = [l.split(',') for l in raw.strip().splitlines() if l and l[0].isdigit()]
    if not rows: return None
    a = np.array([[float(x) for x in r[:8]] for r in rows])
    t = a[:, 0]; t = np.where(t > 1e14, t / 1000, t)
    return pd.DataFrame({'t': t.astype('int64'), 'o': a[:, 1], 'h': a[:, 2], 'l': a[:, 3], 'c': a[:, 4], 'q': a[:, 7]})

def load(sym, start, cache):
    os.makedirs(cache, exist_ok=True); today = dt.date.today(); y, m = start; months = []
    while (y, m) < (today.year, today.month):
        months.append((y, m)); m += 1
        if m == 13: y, m = y + 1, 1
    def month(ym):
        y, m = ym; f = os.path.join(cache, f'{sym}-5m-{y}-{m:02d}.csv')
        if os.path.exists(f): return pd.read_csv(f)
        zb = _get(f'{BASE}/monthly/klines/{sym}/5m/{sym}-5m-{y}-{m:02d}.zip'); df = _parse(zb) if zb else None
        if df is None: df = pd.DataFrame(columns=['t', 'o', 'h', 'l', 'c', 'q'])
        df.to_csv(f, index=False); return df
    with cf.ThreadPoolExecutor(8) as ex: parts = [p for p in ex.map(month, months) if len(p)]
    if not parts: return None
    df = pd.concat(parts).drop_duplicates('t').sort_values('t').reset_index(drop=True)
    df.index = pd.to_datetime(df.t, unit='ms', utc=True)
    return df[['o', 'h', 'l', 'c', 'q']].astype(float)

def triggers(df):
    """The live trigger, on closed 5-min candles: 15-min move >= 5% and 15-min quote volume >= 4x its 24h average."""
    c, q = df.c, df.q
    c15 = (c / c.shift(3) - 1) * 100
    q15 = q.rolling(3).sum(); avg15 = q.shift(3).rolling(288).sum() / 96; q24 = q.rolling(288).sum()
    ch24 = (c / c.shift(288) - 1) * 100
    ok = (c15.abs() >= 5) & (q15 / avg15.replace(0, np.nan) >= 4) & (q24 >= 2e6)
    ok &= ~((c15 > 0) & (ch24 >= 60))            # the live detector does not chase a long after +60% in a day
    h1 = df.resample('1h').agg({'h': 'max', 'l': 'min'}); atr1h = (h1.h - h1.l).rolling(14).mean().shift(1)   # previous closed hours only
    df['atr1h'] = atr1h.reindex(df.index, method='ffill')
    idx = np.flatnonzero(ok.values)
    return [(i, 1 if c15.iat[i] > 0 else -1) for i in idx]

def simulate(df, i, sg, v, cost_pct):
    n = len(df)
    if i + 1 >= n: return None
    O, H, L, C = df.o.values, df.h.values, df.l.values, df.c.values
    e = O[i + 1]
    if v['stop'] == 'swing4': ref = (L[i - 3:i + 1].min() if sg > 0 else H[i - 3:i + 1].max()) * (1 - sg * .005); d = abs(e - ref) / e
    elif v['stop'] == 'start': ref = (L[max(0, i - 5):i - 2].min() if sg > 0 else H[max(0, i - 5):i - 2].max()) * (1 - sg * .005); d = abs(e - ref) / e
    elif v['stop'] == 'atr1h': a = df.atr1h.iat[i]; d = v.get('atr_k', 1.5) * a / e if a == a and a > 0 else np.nan
    else: d = v['fixed']
    if not (d == d) or d <= 0: return None
    if 'clamp' in v: d = min(max(d, v['clamp'][0]), v['clamp'][1])
    stop = e * (1 - sg * d); tp1, tp2 = e * (1 + sg * .10), e * (1 + sg * .20)
    half = False; end = min(n - 1, i + 72)               # 6 h of 5-min candles
    exit_p, why, j = None, 'time', end
    for j in range(i + 1, end + 1):
        hs = L[j] <= stop if sg > 0 else H[j] >= stop
        if hs: exit_p, why = stop, ('be' if half else 'stop'); break
        if v.get('no_tp'): continue
        if not half and (H[j] >= tp1 if sg > 0 else L[j] <= tp1): half, stop = True, e
        if half and (H[j] >= tp2 if sg > 0 else L[j] <= tp2): exit_p, why = tp2, 'tp2'; break
    if exit_p is None: exit_p = C[end]
    r = lambda p: sg * (p - e) / (e * d)
    gross = (.5 * r(tp1) + .5 * r(exit_p)) if half else r(exit_p)
    cost = 2 * cost_pct / 100 / d
    pct = (gross * d) * 100 - 2 * cost_pct
    return {'t': df.index[i + 1], 'exit_t': df.index[j], 'R': gross - cost, 'pct': pct, 'why': why, 'stop_pct': d * 100, 'side': sg}

def stats(R, days=None):
    R = pd.Series(R, dtype=float)
    if not len(R): return {'n': 0}
    o = {'n': int(len(R)), 'mean': round(float(R.mean()), 3), 'win': round(float((R > 0).mean() * 100), 1)}
    if days is not None and len(R) > 1:
        dsum = R.groupby(np.asarray(days)).sum()
        if len(dsum) > 1 and dsum.std() > 0: o['t'] = round(float(dsum.mean() / dsum.std() * np.sqrt(len(dsum))), 2); o['days'] = int(len(dsum))
    return o

def verdict(S):
    a, ho, h1, h2 = S['all'], S['holdout'], S['half1'], S['half2']
    if a['n'] < 100: return ('few', 'صفقات قليلة')
    if a['mean'] > 0 and a.get('t', 0) >= 2 and ho.get('mean', -1) > 0 and h1.get('mean', -1) > 0 and h2.get('mean', -1) > 0: return ('proven', 'مثبت')
    if a['mean'] > 0: return ('fragile', 'موجب بس مش ثابت')
    return ('no', 'مش بيكسب')

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--out', default=os.path.join(HERE, 'out')); ap.add_argument('--cache', default=os.path.join(HERE, 'cache5m'))
    ap.add_argument('--symbols', default=''); a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
    raw = open(os.path.join(HERE, 'momo_rules.json'), 'rb').read(); cfg = json.loads(raw)
    y, m = map(int, cfg['from'].split('-')); syms = a.symbols.split(',') if a.symbols else cfg['universe']
    t0 = time.time(); data = {}
    for s in syms:
        df = load(s, (y, m), a.cache)
        if df is not None and len(df) > 288 * 30: data[s] = df; print(f'{s}: {len(df)} candles from {df.index[0].date()}', flush=True)
        else: print(f'{s}: no data', flush=True)
    T = {s: triggers(df) for s, df in data.items()}
    print('triggers:', sum(len(v) for v in T.values()), flush=True)
    split = pd.Timestamp(cfg['split']['holdout_from'], tz='UTC'); out = []
    for v in cfg['variants']:
        trades = []
        for s, df in data.items():
            busy_until, cool_until = None, None
            for i, sg in T[s]:
                t = df.index[i]
                if busy_until is not None and t < busy_until: continue          # one trade per coin at a time
                if cool_until is not None and t < cool_until: continue
                r = simulate(df, i, sg, v, cfg['costs_pct_per_side'])
                if not r: continue
                r['sym'] = s; trades.append(r); busy_until = r['exit_t']
                cool_until = (r['exit_t'] + pd.Timedelta(hours=v['cool_after_stop_h'])) if r['why'] == 'stop' else (t + pd.Timedelta(hours=v['cool_h']))
        X = pd.DataFrame(trades).sort_values('t') if trades else pd.DataFrame()
        if not len(X): out.append({'id': v['id'], 'name': v['name'], 'stats': {'all': {'n': 0}}, 'verdict': ['few', 'ولا صفقة']}); continue
        day = X.t.dt.floor('D').astype(str).values; hf = len(X) // 2
        S = {'all': stats(X.R, day), 'train': stats(X.R[X.t < split], day[(X.t < split).values]), 'holdout': stats(X.R[X.t >= split], day[(X.t >= split).values]),
             'half1': stats(X.R[:hf], day[:hf]), 'half2': stats(X.R[hf:], day[hf:]),
             'long': stats(X.R[X.side > 0]), 'short': stats(X.R[X.side < 0]), 'pct_mean': round(float(X.pct.mean()), 2),
             'exits': X.why.value_counts().to_dict(), 'median_stop_pct': round(float(X.stop_pct.median()), 2), 'coins': int(X.sym.nunique())}
        vd = verdict(S); out.append({'id': v['id'], 'name': v['name'], 'spec': v, 'stats': S, 'verdict': list(vd)})
        print(f"{v['id']}: n={S['all']['n']} mean={S['all'].get('mean')}R t={S['all'].get('t')} holdout={S['holdout'].get('mean')} pct/trade={S['pct_mean']}% exits={S['exits']} -> {vd[0]}", flush=True)
    rep = {'app': 'sonar-momo-test', 'v': 1, 'at': dt.datetime.now(dt.timezone.utc).isoformat()[:19] + 'Z', 'rules_sha256': hashlib.sha256(raw).hexdigest(),
           'registered': cfg['registered'], 'symbols': list(data), 'from': cfg['from'], 'seconds': round(time.time() - t0), 'variants': out}
    json.dump(rep, open(os.path.join(a.out, 'momo-latest.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
    L = [f"# اختبار كاشف الحركات القوية — فيوتشر 5 دقايق من {cfg['from']} · {len(data)} عملة", '',
         '| النسخة | صفقات | كسب % | متوسط R | t | 2025 | 2026 | النص 1 / 2 | متوسط % للصفقة | الوقف الوسيط | الحكم |', '|' + '---|' * 11]
    for r in out:
        S = r['stats']; g = lambda x: '—' if not x or not x.get('n') else f"{x['mean']:+.2f} ({x['n']})"
        if not S['all'].get('n'): L.append(f"| {r['id']} | 0 |" + ' — |' * 8 + f" {r['verdict'][1]} |"); continue
        L.append(f"| {r['id']} | {S['all']['n']} | {S['all']['win']} | {S['all']['mean']:+.3f} | {S['all'].get('t', '—')} | {g(S['train'])} | {g(S['holdout'])} | {g(S['half1'])} / {g(S['half2'])} | {S['pct_mean']:+.2f}% | {S['median_stop_pct']}% | {r['verdict'][1]} |")
    open(os.path.join(a.out, 'momo-latest.md'), 'w', encoding='utf-8').write('\n'.join(L) + '\n'); print('\n'.join(L))

if __name__ == '__main__': main()
