"""Historical test of the 'Crypto Futures Setup Validator' prompt (research/validator_rules.json), on Binance USDT-M futures
5-minute candles resampled to 15m / 1h / 4h. Shares the candle cache and helpers with momo_test.py.
Look-ahead guard: every higher-timeframe feature is indexed by the CLOSE time of its candle and joined backward onto the
5-minute close time; swings are used only once confirmed; entry is the next 5-minute open."""
import argparse, datetime as dt, hashlib, json, os, sys, time
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from momo_test import load, stats, verdict  # noqa: E402

AGG = {'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last', 'q': 'sum'}

def tf(df, rule):
    x = df.resample(rule, label='left', closed='left').agg(AGG).dropna()
    x['close_t'] = x.index + pd.Timedelta(rule); return x

def swings(x, k):
    """Walk the candles; return per candle (at its close) the last two confirmed swing highs/lows and BOS events."""
    H, L, C, Q = x.h.values, x.l.values, x.c.values, x.q.values; n = len(x)
    out = np.full((n, 8), np.nan)   # lastH, prevH, lastL, prevL, bos_dir, bos_age, choch, volx
    lh = ph = ll = pl = np.nan; brokeH = brokeL = True; last_bos = 0; bos_i = -10**9; bos_dir = 0; choch = 0; volx = np.nan
    qavg = pd.Series(Q).rolling(20).mean().shift(1).values
    for i in range(n):
        j = i - k                                    # pivot candle j is confirmed at the close of i = j + k
        if j - k >= 0:
            if H[j] == H[j - k:j + k + 1].max(): ph, lh, brokeH = lh, H[j], False
            if L[j] == L[j - k:j + k + 1].min(): pl, ll, brokeL = ll, L[j], False
        if not brokeH and C[i] > lh:
            brokeH = True; choch = 1 if last_bos == -1 else 0; last_bos = bos_dir = 1; bos_i = i; volx = Q[i] / qavg[i] if qavg[i] > 0 else np.nan
        elif not brokeL and C[i] < ll:
            brokeL = True; choch = 1 if last_bos == 1 else 0; last_bos = bos_dir = -1; bos_i = i; volx = Q[i] / qavg[i] if qavg[i] > 0 else np.nan
        out[i] = [lh, ph, ll, pl, bos_dir, i - bos_i, choch, volx]
    f = pd.DataFrame(out, columns=['lastH', 'prevH', 'lastL', 'prevL', 'bos', 'bos_age', 'choch', 'volx'])
    f['bos_id'] = (x.index[0].value + np.arange(n) - f.bos_age.values) if n else []
    f['close_t'] = x.close_t.values; f['c'] = C
    return f

def fvg15(x):
    """Latest unfilled fair-value gap of each side from the last 24 closed 15m candles: (bottom, top) per candle close."""
    H, L = x.h.values, x.l.values; n = len(x); res = np.full((n, 4), np.nan)
    bull, bear = [], []                                # (k, bottom, top)
    for i in range(n):
        if i >= 2:
            if L[i] > H[i - 2]: bull.append((i, H[i - 2], L[i]))     # gap up: support zone
            if H[i] < L[i - 2]: bear.append((i, H[i], L[i - 2]))     # gap down: resistance zone
        bull = [g for g in bull if i - g[0] <= 24 and L[i] > g[1]]   # filled when price trades back through the bottom
        bear = [g for g in bear if i - g[0] <= 24 and H[i] < g[2]]
        if bull: res[i, 0:2] = bull[-1][1:]
        if bear: res[i, 2:4] = bear[-1][1:]
    f = pd.DataFrame(res, columns=['bu_b', 'bu_t', 'be_b', 'be_t']); f['close_t'] = x.close_t.values; return f

def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def join(base_t, f, cols, pre):
    a = pd.DataFrame({'t': pd.to_datetime(base_t, utc=True)}); b = f[['close_t'] + cols].reset_index(drop=True).rename(columns={c: pre + c for c in cols}).rename(columns={'close_t': 't'})
    b['t'] = pd.to_datetime(b['t'], utc=True)
    return pd.merge_asof(a, b.sort_values('t'), on='t', direction='backward').drop(columns='t')

def features(df, btc):
    t5 = df.index + pd.Timedelta('5min')
    h1, h4, m15 = tf(df, '1h'), tf(df, '4h'), tf(df, '15min')
    s1 = swings(h1, 3); h4['ema50'] = ema(h4.c, 50); m15['dir'] = np.sign(m15.c - m15.o); fv = fvg15(m15)
    F = pd.concat([join(t5, s1, ['lastH', 'prevH', 'lastL', 'prevL', 'bos', 'bos_age', 'choch', 'volx', 'bos_id'], 's_'),
                   join(t5, h4, ['ema50'], 'p4_'), join(t5, m15, ['dir'], 'm15_'), join(t5, fv, ['bu_b', 'bu_t', 'be_b', 'be_t'], 'fv_'),
                   join(t5, btc, ['strong', 'h1ok'], 'btc_')], axis=1)
    F.index = df.index
    F['c'] = df.c.values; F['dir5'] = np.sign(df.c - df.o).values; F['ch24'] = (df.c / df.c.shift(288) - 1).values * 100
    return F

def btc_frame(b):
    h4, h1 = tf(b, '4h'), tf(b, '1h')
    s4 = swings(h4, 2); e4 = ema(h4.c, 50).values
    bear = (h4.c.values < e4) & (s4.lastH < s4.prevH).values & (s4.lastL < s4.prevL).values
    bull = (h4.c.values > e4) & (s4.lastH > s4.prevH).values & (s4.lastL > s4.prevL).values
    a = pd.DataFrame({'close_t': h4.close_t.values, 'strong': np.where(bear, -1, np.where(bull, 1, 0))})
    s1 = swings(h1, 3); e1 = ema(h1.c, 50).values
    c1 = pd.DataFrame({'close_t': h1.close_t.values, 'h1ok': ((h1.c.values > e1) & (s1.lastL > s1.prevL).values).astype(int)})
    t = pd.merge_asof(c1.sort_values('close_t'), a.sort_values('close_t'), on='close_t', direction='backward')
    return t

def candidates(F, v):
    """Per 5m close: direction allowed by the variant, or 0."""
    c = F.c; sig = []
    for sg in (1, -1):
        if v.get('side') and v['side'] != sg: continue
        if sg > 0:
            st = (F.s_bos == 1) & (F.s_lastL > F.s_prevL)
            fvg = F.fv_bu_t.notna() & (c >= F.fv_bu_b) & (c <= F.fv_bu_t * 1.01)
            g = (F.btc_strong != -1) & (F.btc_h1ok == 1) & (c >= F.p4_ema50 * .95) & (F.ch24 >= -15) & (F.dir5 > 0) & (F.m15_dir >= 0)
        else:
            st = (F.s_bos == -1) & (F.s_lastH < F.s_prevH)
            fvg = F.fv_be_b.notna() & (c <= F.fv_be_t) & (c >= F.fv_be_b * .99)
            g = (F.btc_strong != 1) & (c <= F.p4_ema50 * 1.05) & (F.ch24 <= 15) & (F.dir5 < 0) & (F.m15_dir <= 0)
        st &= F.s_bos_age <= 3
        score = 8 + 3 * (F.s_choch == 1) + 7 * fvg + 5 * (F.s_volx >= 1.5)
        ok = st & (score >= v['min_score'])
        if v.get('gate', True): ok &= g
        ok &= F.p4_ema50.notna() & F.ch24.notna()
        sig.append(pd.Series(np.where(ok.fillna(False).values, sg, 0), index=F.index))
    s = sig[0] if len(sig) == 1 else sig[0].where(sig[0] != 0, sig[1])
    return s

def simulate(df, i, sg, mode, cost_pct):
    n = len(df); O, H, L, C = df.o.values, df.h.values, df.l.values, df.c.values
    if i + 1 >= n: return None
    e = O[i + 1]; end = min(n - 1, i + 576); risk = .02
    lvl = lambda p: e * (1 + sg * p)
    sl, half, exit_p, why, j = lvl(-.02), False, None, 'time', end
    fav = lambda j, p: (H[j] >= lvl(p)) if sg > 0 else (L[j] <= lvl(p))
    hit = lambda j, s: (L[j] <= s) if sg > 0 else (H[j] >= s)
    closed_through = lambda j, s: (C[j] <= s) if sg > 0 else (C[j] >= s)
    for j in range(i + 1, end + 1):
        if mode == 'plain':
            if hit(j, sl): exit_p, why = sl, 'stop'; break
            if fav(j, .04): exit_p, why = lvl(.04), 'tp'; break
        elif mode == 'prompt':
            if closed_through(j, sl) and not fav(j, .03): exit_p, why = C[j], ('be' if half else 'stop'); break
            if fav(j, .03): exit_p, why = lvl(.03), 'close15'; break
            if not half and fav(j, .02): half, sl = True, e          # breakeven (half=flag only, nothing is closed)
        else:  # fixed
            if hit(j, lvl(-.03)): exit_p, why = lvl(-.03), 'hard'; break
            if closed_through(j, sl): exit_p, why = C[j], ('be' if half else 'stop'); break
            if not half and fav(j, .02): half, sl = True, e; continue
            if half and fav(j, .04): exit_p, why = lvl(.04), 'tp2'; break
    if exit_p is None: exit_p = C[end]
    r = lambda p: sg * (p - e) / (e * risk)
    gross = (.5 * r(lvl(.02)) + .5 * r(exit_p)) if (mode == 'fixed' and half) else r(exit_p)
    R = gross - 2 * cost_pct / 100 / risk
    return {'t': df.index[i + 1], 'exit_t': df.index[j], 'R': R, 'why': why, 'side': sg}

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--out', default=os.path.join(HERE, 'out')); ap.add_argument('--cache', default=os.path.join(HERE, 'cache5m'))
    ap.add_argument('--symbols', default=''); a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
    raw = open(os.path.join(HERE, 'validator_rules.json'), 'rb').read(); cfg = json.loads(raw)
    momo = json.load(open(os.path.join(HERE, 'momo_rules.json'), encoding='utf-8'))
    syms = a.symbols.split(',') if a.symbols else list(dict.fromkeys(cfg['universe_extra'] + momo['universe']))
    y, m = map(int, cfg['from'].split('-')); t0 = time.time()
    btc = load('BTCUSDT', (y, m), a.cache)
    if btc is None: print('no BTC data'); return
    B = btc_frame(btc); data = {}
    for s in syms:
        df = btc if s == 'BTCUSDT' else load(s, (y, m), a.cache)
        if df is None or len(df) < 288 * 30: print(f'{s}: no data', flush=True); continue
        try: data[s] = (df, features(df, B)); print(f'{s}: {len(df)} candles', flush=True)
        except Exception as e: print(f'{s}: feature error {e}', flush=True)
    split = pd.Timestamp(cfg['split']['holdout_from'], tz='UTC'); out = []
    for v in cfg['variants']:
        trades = []
        for s, (df, F) in data.items():
            sig = candidates(F, v).values; bos = F.s_bos_id.values; busy = None; used = set()
            for i in np.flatnonzero(sig):
                t = df.index[i]
                if busy is not None and t < busy: continue
                if bos[i] in used: continue
                r = simulate(df, i, int(sig[i]), v['exit'], cfg['costs_pct_per_side'])
                if not r: continue
                r['sym'] = s; trades.append(r); used.add(bos[i]); busy = r['exit_t'] + pd.Timedelta('1h')
        X = pd.DataFrame(trades).sort_values('t') if trades else pd.DataFrame()
        if not len(X): out.append({'id': v['id'], 'name': v['name'], 'stats': {'all': {'n': 0}}, 'verdict': ['few', 'ولا صفقة']}); print(v['id'], 'no trades'); continue
        day = X.t.dt.floor('D').astype(str).values; hf = len(X) // 2
        S = {'all': stats(X.R, day), 'train': stats(X.R[X.t < split], day[(X.t < split).values]), 'holdout': stats(X.R[X.t >= split], day[(X.t >= split).values]),
             'half1': stats(X.R[:hf], day[:hf]), 'half2': stats(X.R[hf:], day[hf:]), 'long': stats(X.R[X.side > 0]), 'short': stats(X.R[X.side < 0]),
             'exits': X.why.value_counts().to_dict(), 'coins': int(X.sym.nunique()), 'per_month': round(len(X) / max(1, (X.t.max() - X.t.min()).days / 30.4), 1)}
        vd = verdict(S); out.append({'id': v['id'], 'name': v['name'], 'spec': v, 'stats': S, 'verdict': list(vd)})
        print(f"{v['id']}: n={S['all']['n']} mean={S['all'].get('mean')} t={S['all'].get('t')} holdout={S['holdout'].get('mean')} long={S['long'].get('mean')}/{S['long'].get('n')} short={S['short'].get('mean')}/{S['short'].get('n')} exits={S['exits']} -> {vd[0]}", flush=True)
    rep = {'app': 'sonar-validator-test', 'v': 1, 'at': dt.datetime.now(dt.timezone.utc).isoformat()[:19] + 'Z', 'rules_sha256': hashlib.sha256(raw).hexdigest(),
           'registered': cfg['registered'], 'symbols': list(data), 'from': cfg['from'], 'seconds': round(time.time() - t0), 'variants': out}
    json.dump(rep, open(os.path.join(a.out, 'validator-latest.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
    Lm = [f"# اختبار برومبت «Setup Validator» — فيوتشر 5 دقايق من {cfg['from']} · {len(data)} عملة", '',
          '| النسخة | صفقات | في الشهر | كسب % | متوسط R | t | 2025 | 2026 | لونج | شورت | الحكم |', '|' + '---|' * 11]
    for r in out:
        S = r['stats']; g = lambda x: '—' if not x or not x.get('n') else f"{x['mean']:+.2f} ({x['n']})"
        if not S['all'].get('n'): Lm.append(f"| {r['id']} | 0 |" + ' — |' * 8 + f" {r['verdict'][1]} |"); continue
        Lm.append(f"| {r['id']} | {S['all']['n']} | {S['per_month']} | {S['all']['win']} | {S['all']['mean']:+.3f} | {S['all'].get('t', '—')} | {g(S['train'])} | {g(S['holdout'])} | {g(S['long'])} | {g(S['short'])} | {r['verdict'][1]} |")
    open(os.path.join(a.out, 'validator-latest.md'), 'w', encoding='utf-8').write('\n'.join(Lm) + '\n'); print('\n'.join(Lm))

if __name__ == '__main__': main()
