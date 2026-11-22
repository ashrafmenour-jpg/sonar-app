"""
Sonar BTC model — can a model learn where BTC goes next (24h / 3 days / 7 days) and how big the move will be?
(Ashraf, 21 Nov 2026: «نبني موديل يبدأ يتنبأ بحركة البيتكوين … يتعلم يحلل فريمات صغيرة وكبيرة»)

Honest set-up, so the model cannot fool us:
  • one row per day, taken at the daily close (00:00 UTC); every feature uses only candles already closed at that moment
  • walk-forward: for each month from 2020 on, train on everything before it (minus a purge gap as long as the horizon,
    so no training target overlaps the test month) and predict that month — the model never sees the month it is judged on
  • judged against dumb baselines: «same as the last N days» (persistence), «above the 200-day average = up» (trend),
    and «the usual answer so far» (majority) — a model that cannot beat them on 2023+ is useless, whatever its accuracy looks like
  • features from small and large frames: 4h (momentum, RSI, position), daily (returns, averages, RSI, ATR, drawdown),
    weekly (return, position), hourly volatility, ETH/BTC, and breadth of the other 19 large coins

Output: btc-model-latest.json / .md (published to the research-data branch with the historical test).
Run: python research/btc_model.py --out /tmp/out     (data from research/cache, shared with backtest.py)
"""
import argparse, json, math, os, sys, time
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import backtest as B   # loader, universe, helpers — same candles as the historical test

HORIZONS = {'d1': 1, 'd3': 3, 'd7': 7}          # days ahead
START_TEST = '2020-01-01'; HOLDOUT = '2023-01-01'


def rsi(c, n=14):
    d = c.diff(); up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(); dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def day_end(s, idx):
    """value of a sub-daily series at the last bar of each UTC day (known at that day's close)"""
    return s.resample('1D', label='left', closed='left').last().reindex(idx)


def bitstamp_daily(cache, until):
    """BTC/USD daily candles from Bitstamp (2011 →) for the years before Binance existed (Aug 2017); cached as CSV.
    Only the daily frame uses them — 4h/hourly features, ETH and breadth stay empty for those years."""
    f = os.path.join(cache, 'bitstamp-btcusd-1d.csv')
    if os.path.exists(f): d = pd.read_csv(f)
    else:
        rows, start = [], 1325376000   # 2012-01-01
        while start < 1504224000:      # 2017-09-01
            try: j = json.loads(B._get(f'https://www.bitstamp.net/api/v2/ohlc/btcusd/?step=86400&limit=1000&start={start}'))
            except Exception as e: print('bitstamp:', e); break
            o = (j or {}).get('data', {}).get('ohlc', [])
            if not o: break
            rows += o; nxt = int(o[-1]['timestamp']) + 86400
            if nxt <= start: break
            start = nxt; time.sleep(.5)
        if not rows: return None
        d = pd.DataFrame(rows)[['timestamp', 'open', 'high', 'low', 'close', 'volume']]; os.makedirs(cache, exist_ok=True); d.to_csv(f, index=False)
    d = d.astype(float).drop_duplicates('timestamp').sort_values('timestamp')
    d.index = pd.to_datetime(d.timestamp.astype('int64'), unit='s', utc=True)
    d = d.rename(columns={'open': 'o', 'high': 'h', 'low': 'l', 'close': 'c', 'volume': 'v'})[['o', 'h', 'l', 'c', 'v']]
    return d[(d.index < until) & (d.c > 0)]


def build(data, old=None):
    btc = data['BTCUSDT']; kd = B.resample(btc, '1D'); k4 = B.resample(btc, '4h')
    if old is not None and len(old):   # older BTC history before Binance (daily only)
        kd = pd.concat([old[old.index < kd.index[0]], kd])
    c = kd.c; X = pd.DataFrame(index=kd.index)
    # daily frame
    for n in (1, 3, 7, 14, 30, 90): X[f'ret_{n}d'] = c / c.shift(n) - 1
    for n in (20, 50, 200): X[f'dist_ema{n}'] = c / B.ema(c, n) - 1
    X['ema50_200'] = B.ema(c, 50) / B.ema(c, 200) - 1
    X['rsi_1d'] = rsi(c)
    tr = pd.concat([kd.h - kd.l, (kd.h - c.shift()).abs(), (kd.l - c.shift()).abs()], axis=1).max(axis=1)
    X['atr_pct'] = tr.rolling(14).mean() / c
    X['range_today'] = (kd.h - kd.l) / c
    X['close_in_bar'] = (c - kd.l) / (kd.h - kd.l).replace(0, np.nan)
    X['dd_60d'] = c / kd.h.rolling(60).max() - 1
    X['up_60d'] = c / kd.l.rolling(60).min() - 1
    X['pos_20d'] = (c - kd.l.rolling(20).min()) / (kd.h.rolling(20).max() - kd.l.rolling(20).min()).replace(0, np.nan)
    X['vol_ratio'] = kd.v / kd.v.rolling(20).mean()
    if old is not None and len(old): X.loc[X.index < btc.index[0] + pd.Timedelta(days=21), 'vol_ratio'] = np.nan   # another exchange's volume
    X['streak'] = np.sign(c.diff()).groupby((np.sign(c.diff()) != np.sign(c.diff()).shift()).cumsum()).cumcount() * np.sign(c.diff())
    # weekly frame
    X['ret_4w'] = c / c.shift(28) - 1
    X['pos_52w'] = (c - kd.l.rolling(365).min()) / (kd.h.rolling(365).max() - kd.l.rolling(365).min()).replace(0, np.nan)
    # 4h frame (value at the day's last 4h close)
    c4 = k4.c
    X['ret_4h'] = day_end(c4 / c4.shift(1) - 1, kd.index); X['ret_12h'] = day_end(c4 / c4.shift(3) - 1, kd.index)
    X['rsi_4h'] = day_end(rsi(c4), kd.index); X['dist_ema20_4h'] = day_end(c4 / B.ema(c4, 20) - 1, kd.index)
    X['dir_4h'] = day_end(pd.Series(B.direction(c4), index=c4.index), kd.index)
    X['dir_1d'] = pd.Series(B.direction(c), index=c.index)
    # hourly volatility
    r1 = btc.c.pct_change()
    X['rv_24h'] = day_end(r1.rolling(24).std(), kd.index); X['rv_7d'] = day_end(r1.rolling(168).std(), kd.index)
    X['rv_ratio'] = X.rv_24h / X.rv_7d.replace(0, np.nan)
    # ETH / BTC
    if 'ETHUSDT' in data:
        ce = B.resample(data['ETHUSDT'], '1D').c.reindex(kd.index); eb = ce / c
        X['ethbtc_7d'] = eb / eb.shift(7) - 1; X['eth_1d'] = ce / ce.shift(1) - 1
    # breadth of the other large coins
    alts = {s: B.resample(d, '1D').c.reindex(kd.index) for s, d in data.items() if s in B.UNIVERSE and s != 'BTCUSDT'}
    if alts:
        A = pd.DataFrame(alts); ok = A.notna().sum(axis=1)
        X['br_up_1d'] = (A / A.shift(1) > 1).sum(axis=1) / ok.replace(0, np.nan)
        X['br_above50'] = (A > A.apply(lambda s: B.ema(s, 50))).sum(axis=1) / ok.replace(0, np.nan)
        X['alt_vs_btc_7d'] = (A / A.shift(7) - 1).mean(axis=1) - X.ret_7d
        X.loc[ok < 8, ['br_up_1d', 'br_above50', 'alt_vs_btc_7d']] = np.nan
    X['dow'] = kd.index.dayofweek
    # regime label (same definition as the historical test's report), for slicing the results
    s50, s200, r7, r20 = c.rolling(50).mean(), c.rolling(200).mean(), c / c.shift(7) * 100 - 100, c / c.shift(20) * 100 - 100
    lab = np.where((c < s200) & ((c < s50) | (r20 <= -10)), 'DOWN', np.where((c > s200) & (s50 > s200), 'UP', 'RANGE'))
    reg = pd.Series(np.where((lab == 'DOWN') & (r7 >= 8), 'REBOUND', lab), index=kd.index)
    # targets
    Y = pd.DataFrame(index=kd.index)
    for k, h in HORIZONS.items():
        Y[f'ret_{k}'] = c.shift(-h) / c - 1
        Y[f'up_{k}'] = (Y[f'ret_{k}'] > 0).astype(float).where(Y[f'ret_{k}'].notna())
        Y[f'big_{k}'] = (Y[f'ret_{k}'].abs() > X.atr_pct * math.sqrt(h)).astype(float).where(Y[f'ret_{k}'].notna() & X.atr_pct.notna())
    X = X.replace([np.inf, -np.inf], np.nan)
    return X, Y, reg, c


def models():
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.impute import SimpleImputer
    return {
        'logit': lambda: make_pipeline(SimpleImputer(strategy='median'), StandardScaler(), LogisticRegression(C=.05, max_iter=2000)),
        'gbm': lambda: HistGradientBoostingClassifier(max_depth=3, learning_rate=.03, max_iter=250, l2_regularization=1.0, min_samples_leaf=40, random_state=7),
    }


def auc(y, p):
    y, p = np.asarray(y, float), np.asarray(p, float); m = ~(np.isnan(y) | np.isnan(p)); y, p = y[m], p[m]
    n1, n0 = (y == 1).sum(), (y == 0).sum()
    if n1 == 0 or n0 == 0: return None
    r = pd.Series(p).rank().values
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def walk_forward(X, y, horizon, mk):
    """monthly expanding-window training with a purge gap = horizon; returns out-of-sample probabilities"""
    p = pd.Series(np.nan, index=X.index); months = pd.date_range(pd.Timestamp(START_TEST, tz=X.index.tz), X.index[-1] + pd.Timedelta(days=1), freq='MS')
    for m0 in months:
        m1 = m0 + pd.offsets.MonthBegin(1); test = (X.index >= m0) & (X.index < m1)
        if not test.any(): continue
        tr = (X.index < m0 - pd.Timedelta(days=horizon)) & y.notna() & (X.index >= X.index[0] + pd.Timedelta(days=200))   # older years train only; the test still starts in 2020
        if tr.sum() < 400: continue
        mdl = mk(); mdl.fit(X[tr].values, y[tr].values.astype(int))
        p[test] = mdl.predict_proba(X[test].values)[:, 1]
    return p


def score(y, p, ret, every):
    """accuracy / AUC / trading proxy (go with the call's sign); t on non-overlapping rows (every `every` days)"""
    m = y.notna() & p.notna(); y, p, ret = y[m], p[m], ret[m]
    if len(y) < 30: return {'n': int(len(y))}
    call = (p > .5).astype(float); acc = float((call == y).mean())
    sg = np.where(p > .5, 1, -1); g = pd.Series(sg * ret.values, index=ret.index)
    nn = g.iloc[::every]; t = float(nn.mean() / (nn.std(ddof=1) / math.sqrt(len(nn)))) if len(nn) > 2 and nn.std(ddof=1) > 0 else 0.0
    conf = (p >= .6) | (p <= .4); ca = float((call[conf] == y[conf]).mean()) if conf.sum() >= 20 else None
    return {'n': int(len(y)), 'acc': round(acc, 3), 'auc': None if auc(y, p) is None else round(auc(y, p), 3), 'base_rate': round(float(y.mean()), 3),
            'edge_pct': round(float(g.mean() * 100), 3), 't': round(t, 2), 'conf_share': round(float(conf.mean()), 2), 'conf_acc': None if ca is None else round(ca, 3)}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--out', default=os.path.join(HERE, 'out')); ap.add_argument('--cache', default=os.path.join(HERE, 'cache'))
    ap.add_argument('--from', dest='start', default='2017-08'); ap.add_argument('--no-old', action='store_true'); a = ap.parse_args(); os.makedirs(a.out, exist_ok=True); t0 = time.time()
    y0, m0 = map(int, a.start.split('-')); data = {}
    for s in B.UNIVERSE:
        df = B.load(s, (y0, m0), a.cache)
        if df is not None and len(df) > 24 * 300: data[s] = df
    if 'BTCUSDT' not in data: sys.exit('no BTC data')
    old = None
    if not a.no_old:
        try: old = bitstamp_daily(a.cache, data['BTCUSDT'].index[0])
        except Exception as e: print('old history skipped:', e)
        print('older BTC history:', 0 if old is None else len(old), 'days', '' if old is None or not len(old) else f'from {old.index[0].date()}')
    X, Y, reg, close = build(data, old); feats = list(X.columns); M = models(); res = {}; latest = {}
    for k, h in HORIZONS.items():
        ret = Y[f'ret_{k}']; res[k] = {}
        base = {'persist': pd.Series(np.where(X[f'ret_{h}d' if f'ret_{h}d' in X else 'ret_7d'] > 0, .51, .49), index=X.index),
                'trend200': pd.Series(np.where(X.dist_ema200 > 0, .51, .49), index=X.index)}
        for tgt in ('up', 'big'):
            y = Y[f'{tgt}_{k}']; out = {}
            P = {nm: walk_forward(X, y, h, mk) for nm, mk in M.items()}
            P['blend'] = (P['logit'] + P['gbm']) / 2
            P['majority'] = y.expanding().mean().shift(h)        # «always the usual answer» — the share known so far
            if tgt == 'up': P.update(base)
            else:   # a move's size is partly predictable from volatility alone — the model must add something beyond that
                P['big_yesterday'] = pd.Series(np.where(X.range_today > X.atr_pct, .51, .49), index=X.index)
                V = ['atr_pct', 'rv_24h', 'rv_7d', 'range_today']; P['vol_only'] = walk_forward(X[V], y, h, M['logit'])
            rsign = ret if tgt == 'up' else (ret.abs() - X.atr_pct * math.sqrt(h))   # «big» proxy: did the move beat the usual size
            for nm, p in P.items():
                T0, T1 = pd.Timestamp(START_TEST, tz=p.index.tz), pd.Timestamp(HOLDOUT, tz=p.index.tz)
                sl = {'all': p.index >= T0, 'h2020_22': (p.index >= T0) & (p.index < T1), 'h2023': p.index >= T1}
                o = {kk: score(y[v], p[v], rsign[v], h) for kk, v in sl.items()}
                o['by_regime'] = {g: score(y[(reg == g) & (p.index >= T0)], p[(reg == g) & (p.index >= T0)], rsign[(reg == g) & (p.index >= T0)], h) for g in ('DOWN', 'REBOUND', 'RANGE', 'UP')}
                out[nm] = o
            # verdict: the best model must beat every baseline on 2023+ (AUC by ≥ 0.02 and accuracy), and be > 0.5 in both periods
            bases = [b for b in out if b not in M and b != 'blend']
            best = max(['logit', 'gbm', 'blend'], key=lambda nm: (out[nm]['h2023'].get('auc') or 0))
            ba = max((out[b]['h2023'].get('acc') or 0) for b in bases); bu = max((out[b]['h2023'].get('auc') or .5) for b in bases)
            o23, o20 = out[best]['h2023'], out[best]['h2020_22']
            ok = (o23.get('auc') or 0) >= bu + .02 and (o23.get('acc') or 0) >= ba and (o20.get('auc') or 0) > .5
            out['_verdict'] = {'best': best, 'beats_baselines': bool(ok), 'auc_2023': o23.get('auc'), 'best_base_auc_2023': round(bu, 3), 'acc_2023': o23.get('acc'), 'best_base_acc_2023': round(ba, 3)}
            res[k][tgt] = out
            # today's reading: fit on everything with a known target, predict the latest closed day
            tr = y.notna() & (X.index >= X.index[0] + pd.Timedelta(days=200)); pr = []
            for nm, mk in M.items():
                mdl = mk(); mdl.fit(X[tr].values, y[tr].values.astype(int)); pr.append(float(mdl.predict_proba(X.iloc[[-1]].values)[0, 1]))
                if nm == 'gbm' and tgt == 'up' and k == 'd3':
                    try:
                        from sklearn.inspection import permutation_importance
                        H1 = pd.Timestamp(HOLDOUT, tz=X.index.tz); te = (X.index >= H1) & y.notna(); m2 = mk(); m2.fit(X[tr & (X.index < H1)].values, y[tr & (X.index < H1)].values.astype(int))
                        pi = permutation_importance(m2, X[te].values, y[te].values.astype(int), scoring='roc_auc', n_repeats=8, random_state=7)
                        res['importance_d3_up'] = sorted([[f, round(float(v), 4)] for f, v in zip(feats, pi.importances_mean)], key=lambda z: -z[1])[:12]
                    except Exception as e: res['importance_d3_up'] = str(e)
            latest[f'{tgt}_{k}'] = round(float(np.mean(pr)), 3)
    rep = {'generated': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'last_day': str(X.index[-1].date()), 'first_day': str(X.index[0].date()), 'btc_close': round(float(close.iloc[-1]), 1),
           'regime_now': str(reg.iloc[-1]), 'features': feats, 'horizons_days': HORIZONS, 'test_from': START_TEST, 'holdout_from': HOLDOUT,
           'results': res, 'today': latest, 'secs': round(time.time() - t0)}
    json.dump(rep, open(os.path.join(a.out, 'btc-model-latest.json'), 'w'), ensure_ascii=False, indent=1, default=str)
    L = [f"# Sonar BTC model — {rep['generated']}", f"BTC {rep['btc_close']} · regime {rep['regime_now']} · last closed day {rep['last_day']}", '',
         '| horizon | target | best | AUC 2023+ | best baseline AUC | acc 2023+ | best baseline acc | beats baselines |', '|---|---|---|---|---|---|---|---|']
    for k in HORIZONS:
        for tgt in ('up', 'big'):
            v = res[k][tgt]['_verdict']; L.append(f"| {k} | {tgt} | {v['best']} | {v['auc_2023']} | {v['best_base_auc_2023']} | {v['acc_2023']} | {v['best_base_acc_2023']} | {'✅' if v['beats_baselines'] else '❌'} |")
    L += ['', 'Today (probability, experimental): ' + ', '.join(f'{k} {v}' for k, v in latest.items())]
    open(os.path.join(a.out, 'btc-model-latest.md'), 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
