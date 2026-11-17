"""
Sonar server runner — keeps Sonar's brain running 24/7 on a server.

It opens the exact same Sonar app (web/index.html from the GitHub repo) in a headless Chromium with a persistent
profile, so the learning, paper trades and settings survive restarts exactly like on the tablet. Around it:
  * watchdog: heartbeat every minute, restarts the browser if the page hangs or crashes
  * updates: pulls the repo every 20 min and reloads the page when web/version.json changes
  * snapshots: every 15 min saves the diagnosis file, the learning file and the all-data backup (the app's own exporters)
  * errors: every page error / console error goes to /data/logs/console.log
  * dashboard on :8090 (password protected): status, latest files to download, a screenshot of the app
  * first start: if /data/import/*.json holds a «كل بياناتي» export from the tablet, it is loaded so the server
    continues the same learning instead of starting from zero
No real orders: Sonar itself never places orders; this runner only keeps it awake.
"""
import base64, glob, html, http.server, json, os, shutil, socketserver, subprocess, threading, time, traceback, urllib.request
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

DATA = os.environ.get('DATA_DIR', '/data')
REPO = os.environ.get('REPO_DIR', '/app/repo')              # git clone of ashrafmenour-jpg/sonar-app
WEB = os.environ.get('WEB_DIR', os.path.join(REPO, 'web'))
PASS = os.environ.get('SONAR_PASS', '')                      # dashboard password (user: sonar)
APP_PORT = int(os.environ.get('APP_PORT', '8080'))           # internal only, serves web/
DASH_PORT = int(os.environ.get('DASH_PORT', '8090'))
SNAP_MIN = float(os.environ.get('SNAP_MIN', '15'))
PULL_MIN = float(os.environ.get('PULL_MIN', '20'))
KEEP_DAYS = int(os.environ.get('KEEP_DAYS', '14'))
CHROME = os.environ.get('CHROME_PATH') or None

for d in ('profile', 'out', 'logs', 'import', 'backup'):
    os.makedirs(os.path.join(DATA, d), exist_ok=True)
OUT, LOGS = os.path.join(DATA, 'out'), os.path.join(DATA, 'logs')
STATE = {'started': time.time(), 'browser_starts': 0, 'last_beat': 0, 'version': '', 'errors_1h': [], 'last_snap': 0,
         'last_pull': 0, 'net': {}, 'notes': []}
LOCK = threading.Lock()

def now(): return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
def log(msg, f='runner.log'):
    line = f'[{now()}] {msg}'
    print(line, flush=True)
    p = os.path.join(LOGS, f)
    try:
        if os.path.exists(p) and os.path.getsize(p) > 5_000_000: os.replace(p, p + '.1')
        with open(p, 'a', encoding='utf-8') as fh: fh.write(line + '\n')
    except Exception: pass
def note(msg):
    with LOCK: STATE['notes'] = ([f'{now()} — {msg}'] + STATE['notes'])[:30]
    log(msg)
def web_version():
    try: return json.load(open(os.path.join(WEB, 'version.json'), encoding='utf-8')).get('web', '')
    except Exception: return ''

# ---------- internal static server for web/ (fixed origin => localStorage persists across updates) ----------
class Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=WEB, **k)
    def log_message(self, *a): pass
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store'); super().end_headers()
class TS(socketserver.ThreadingMixIn, http.server.HTTPServer): daemon_threads = True; allow_reuse_address = True
threading.Thread(target=TS(('127.0.0.1', APP_PORT), Quiet).serve_forever, daemon=True).start()
APP_URL = f'http://localhost:{APP_PORT}/index.html'

# ---------- dashboard ----------
def latest(prefix):
    L = sorted(glob.glob(os.path.join(OUT, prefix + '*')), key=os.path.getmtime)
    return L[-1] if L else None
class Dash(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _auth(self):
        if not PASS: return True
        h = self.headers.get('Authorization', '')
        ok = h.startswith('Basic ') and base64.b64decode(h[6:]).decode('utf-8', 'ignore') == 'sonar:' + PASS
        if not ok:
            self.send_response(401); self.send_header('WWW-Authenticate', 'Basic realm="Sonar"'); self.end_headers()
        return ok
    def _file(self, p, ctype, name=None):
        if not p or not os.path.exists(p): self.send_response(404); self.end_headers(); self.wfile.write(b'not yet'); return
        b = open(p, 'rb').read(); self.send_response(200); self.send_header('Content-Type', ctype)
        if name: self.send_header('Content-Disposition', f'attachment; filename="{name}"')
        self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path == '/health':  # no auth: for uptime monitors
            ok = time.time() - STATE['last_beat'] < 180
            self.send_response(200 if ok else 503); self.end_headers(); self.wfile.write(b'ok' if ok else b'stale'); return
        if not self._auth(): return
        if self.path == '/status.json':
            b = json.dumps(status(), ensure_ascii=False, indent=1).encode(); self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8'); self.end_headers(); self.wfile.write(b); return
        for key, pre, ct in (('/diagnosis', 'Sonar-diagnosis', 'application/json'), ('/learning', 'Sonar-learning', 'application/json'),
                             ('/all', 'Sonar-all', 'application/json'), ('/screen', 'screen', 'image/png'), ('/errors', 'console', 'text/plain')):
            if self.path.startswith(key):
                p = os.path.join(LOGS, 'console.log') if key == '/errors' else latest(pre)
                return self._file(p, ct + ('; charset=utf-8' if ct.startswith('text') else ''), None if ct.startswith(('image', 'text')) else os.path.basename(p or ''))
        s = status(); rows = ''.join(f'<tr><td>{html.escape(k)}</td><td>{html.escape(str(v))}</td></tr>' for k, v in s.items() if k not in ('notes', 'net'))
        net = ''.join(f'<li>{html.escape(h)}: {"✅" if v.get("ok") else "❌ " + html.escape(str(v.get("err", "")))}</li>' for h, v in s['net'].items())
        notes = ''.join(f'<li>{html.escape(n)}</li>' for n in s['notes'])
        page = f'''<!doctype html><html dir="rtl" lang="ar"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60"><title>Sonar · السيرفر</title><style>body{{font-family:system-ui;background:#0b1220;color:#e6edf3;margin:16px}}
a.b{{display:inline-block;background:#1f6feb;color:#fff;padding:10px 14px;border-radius:10px;margin:4px;text-decoration:none}}
table{{border-collapse:collapse}}td{{border-bottom:1px solid #223;padding:4px 8px}}img{{max-width:100%;border-radius:10px;margin-top:10px}}</style>
<h2>{"🟢" if s["alive"] else "🔴"} Sonar على السيرفر</h2>
<a class="b" href="/diagnosis">🩺 ملف التشخيص</a><a class="b" href="/learning">📓 ملف التعلم</a><a class="b" href="/all">📦 كل البيانات</a><a class="b" href="/errors">⚠️ الأخطاء</a>
<table>{rows}</table><h3>المنصات من السيرفر</h3><ul>{net}</ul><h3>آخر الأحداث</h3><ul>{notes}</ul><img src="/screen?{int(time.time())}"></html>'''
        b = page.encode(); self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8'); self.end_headers(); self.wfile.write(b)
def status():
    with LOCK:
        cut = time.time() - 3600; STATE['errors_1h'] = [t for t in STATE['errors_1h'] if t > cut]
        up = time.time() - STATE['started']
        return {'alive': time.time() - STATE['last_beat'] < 180, 'نسخة Sonar': STATE['version'] or web_version(),
                'شغال من': f'{up / 3600:.1f} ساعة', 'آخر نبضة': f'{time.time() - STATE["last_beat"]:.0f} ث' if STATE['last_beat'] else '—',
                'إعادة تشغيل المتصفح': STATE['browser_starts'], 'أخطاء آخر ساعة': len(STATE['errors_1h']),
                'آخر ملفات': datetime.fromtimestamp(STATE['last_snap'], timezone.utc).strftime('%H:%M UTC') if STATE['last_snap'] else '—',
                'آخر سحب تحديث': datetime.fromtimestamp(STATE['last_pull'], timezone.utc).strftime('%H:%M UTC') if STATE['last_pull'] else '—',
                'net': STATE['net'], 'notes': list(STATE['notes'])}
threading.Thread(target=TS(('0.0.0.0', DASH_PORT), Dash).serve_forever, daemon=True).start()

# ---------- exchange reachability from this server (Binance blocks some countries, e.g. the US) ----------
NET_CHECKS = {'Binance Futures': 'https://fapi.binance.com/fapi/v1/ping', 'Binance Spot': 'https://api.binance.com/api/v3/ping',
              'Bybit': 'https://api.bybit.com/v5/market/time', 'OKX': 'https://www.okx.com/api/v5/public/time',
              'Bitget': 'https://api.bitget.com/api/v2/public/time', 'Gate': 'https://api.gateio.ws/api/v4/spot/time',
              'XT': 'https://sapi.xt.com/v4/public/time', 'KuCoin': 'https://api.kucoin.com/api/v1/timestamp', 'Groq (AI)': 'https://api.groq.com/'}
def check_net():
    res = {}
    for name, u in NET_CHECKS.items():
        try:
            r = urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'}), timeout=10)
            res[name] = {'ok': True, 'code': r.status}
        except urllib.error.HTTPError as e:
            res[name] = {'ok': e.code in (401, 403, 404) and 'Groq' in name, 'err': f'HTTP {e.code}'}  # Groq root answers 4xx = reachable
        except Exception as e:
            res[name] = {'ok': False, 'err': str(e)[:80]}
    with LOCK: STATE['net'] = res
    bad = [k for k, v in res.items() if not v['ok']]
    note('المنصات: كله واصل' if not bad else 'منصات مش واصلة من السيرفر: ' + '، '.join(bad))

# ---------- repo updates ----------
def pull():
    if not os.path.isdir(os.path.join(REPO, '.git')): return False
    old = web_version()
    try:
        subprocess.run(['git', '-C', REPO, 'fetch', '-q', 'origin'], timeout=120, check=True)
        subprocess.run(['git', '-C', REPO, 'reset', '-q', '--hard', 'origin/main'], timeout=60, check=True)
    except Exception as e:
        log(f'pull failed: {e}'); return False
    with LOCK: STATE['last_pull'] = time.time()
    new = web_version()
    if new != old: note(f'تحديث جديد من GitHub: {old} ← {new}'); return True
    return False

# ---------- browser ----------
ARGS = ['--disable-web-security', '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
        '--disable-backgrounding-occluded-windows', '--disable-features=CalculateNativeWinOcclusion', '--no-sandbox',
        '--disable-dev-shm-usage', '--autoplay-policy=no-user-gesture-required']
def on_dialog(d):
    m = d.message or ''
    log(f'dialog {d.type}: {m[:200]}')
    try:
        # accept only harmless "reopen the app now?" prompts; anything else (e.g. "enter anyway?") is declined
        if d.type in ('alert', 'beforeunload') or (d.type == 'confirm' and 'أعيد فتح' in m): d.accept()
        else: d.dismiss()
    except Exception: pass
def on_console(msg):
    if msg.type in ('error', 'warning'):
        t = msg.text[:400]
        # network failures (an exchange or relay not answering) are logged but not counted as app errors
        if msg.type == 'error' and not any(x in t for x in ('Failed to load resource', 'WebSocket connection', 'net::ERR_')):
            with LOCK: STATE['errors_1h'].append(time.time())
        log(f'{msg.type}: {t}', 'console.log')
def on_pageerror(e):
    with LOCK: STATE['errors_1h'].append(time.time())
    log(f'PAGE ERROR: {str(e)[:600]}', 'console.log')

def import_first(page):
    """Load a «كل بياناتي» export (from the tablet) once, before Sonar starts learning on its own."""
    files = sorted(glob.glob(os.path.join(DATA, 'import', '*.json')), key=os.path.getmtime)
    if not files: return
    f = files[-1]
    try: o = json.load(open(f, encoding='utf-8'))
    except Exception as e: note(f'ملف الاستيراد مش سليم: {e}'); return
    if o.get('app') != 'sonar-pro-all' or not isinstance(o.get('keys'), dict):
        note('ملف الاستيراد مش «كل بياناتي» — اتجاهل'); return
    page.evaluate("""(o) => { window.__SONAR_PASSIVE = true; for (let i = localStorage.length - 1; i >= 0; i--) { const k = localStorage.key(i); if (k && k.startsWith('sonarpro.')) localStorage.removeItem(k); }
        for (const [k, v] of Object.entries(o.keys)) if (k.startsWith('sonarpro.')) localStorage.setItem(k, v); }""", o)
    os.makedirs(os.path.join(DATA, 'import', 'done'), exist_ok=True)
    shutil.move(f, os.path.join(DATA, 'import', 'done', os.path.basename(f)))
    note(f'اتنقلت بيانات التابلت ({len(o["keys"])} جزء) — السيرفر بيكمّل من نفس التعلم')
    page.goto(APP_URL, wait_until='load', timeout=120000)

def snapshot(page):
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d-%H-%M')
    jobs = (('Sonar-diagnosis', 'HC.download()'), ('Sonar-learning', 'TRN.exportFile()'), ('Sonar-all', 'MIG.export()'))
    for pre, js in jobs:
        try:
            with page.expect_download(timeout=60000) as dl:
                page.evaluate(js)
            dl.value.save_as(os.path.join(OUT, f'{pre}-{stamp}.json'))
        except Exception as e:
            log(f'snapshot {pre} failed: {str(e)[:200]}')
    try: page.screenshot(path=os.path.join(OUT, f'screen-{stamp}.png'), timeout=30000)
    except Exception as e: log(f'screenshot failed: {e}')
    with LOCK: STATE['last_snap'] = time.time()
    # keep: everything from the last 24 h, then one all-data backup per day for KEEP_DAYS days
    cut, day = time.time() - 86400, {}
    for p in sorted(glob.glob(os.path.join(OUT, '*')), key=os.path.getmtime):
        if os.path.getmtime(p) >= cut: continue
        if os.path.basename(p).startswith('Sonar-all') and os.path.getmtime(p) > time.time() - KEEP_DAYS * 86400:
            d, dst = os.path.basename(p)[10:20], os.path.join(DATA, 'backup', os.path.basename(p))
            if d not in day:
                day[d] = p
                if not os.path.exists(dst): shutil.copy2(p, dst)
        try: os.remove(p)
        except Exception: pass
    for p in glob.glob(os.path.join(DATA, 'backup', '*')):
        if os.path.getmtime(p) < time.time() - KEEP_DAYS * 86400: os.remove(p)

def run_browser(pw):
    ctx = pw.chromium.launch_persistent_context(os.path.join(DATA, 'profile'), headless=True, executable_path=CHROME, args=ARGS,
                                                viewport={'width': 1280, 'height': 900}, accept_downloads=True, locale='ar-EG',
                                                timezone_id='Africa/Cairo')
    with LOCK: STATE['browser_starts'] += 1
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.on('dialog', on_dialog); page.on('console', on_console); page.on('pageerror', on_pageerror)
    crashed = {'v': False}; page.on('crash', lambda *_: crashed.__setitem__('v', True))
    page.goto(APP_URL, wait_until='load', timeout=120000)
    import_first(page)
    page.evaluate("window.SONAR_SERVER = 1")
    with LOCK: STATE['version'] = web_version()
    note(f'Sonar اشتغل على السيرفر (نسخة {web_version()})')
    fails, last_snap, last_pull, started = 0, 0, time.time(), time.time()
    while True:
        time.sleep(60)
        if crashed['v']: raise RuntimeError('page crashed')
        try:
            page.evaluate('Date.now()', )
            with LOCK: STATE['last_beat'] = time.time()
            fails = 0
        except Exception as e:
            fails += 1; log(f'heartbeat failed ({fails}): {str(e)[:120]}')
            if fails >= 3: raise RuntimeError('page not answering')
            continue
        if time.time() - last_snap > SNAP_MIN * 60: snapshot(page); last_snap = time.time()
        if time.time() - last_pull > PULL_MIN * 60:
            last_pull = time.time()
            if pull():
                try: page.evaluate('try { store.flushAll(); } catch (e) {}')
                except Exception: pass
                page.goto(APP_URL, wait_until='load', timeout=120000); page.evaluate("window.SONAR_SERVER = 1")
                with LOCK: STATE['version'] = web_version()
        # fresh browser once a day (memory); state lives in the profile so nothing is lost
        if time.time() - started > 24 * 3600:
            try: page.evaluate('try { store.flushAll(); } catch (e) {}')
            except Exception: pass
            ctx.close(); note('إعادة تشغيل يومية للمتصفح (للذاكرة)'); return

def main():
    if not PASS: log('WARNING: SONAR_PASS is empty — the dashboard is open to anyone')
    pull(); check_net()
    threading.Thread(target=lambda: [time.sleep(6 * 3600) or check_net() for _ in iter(int, 1)], daemon=True).start()
    with sync_playwright() as pw:
        while True:
            try: run_browser(pw)
            except Exception as e:
                note(f'المتصفح وقع وبيتشغّل تاني: {str(e)[:120]}'); log(traceback.format_exc())
                time.sleep(20)

if __name__ == '__main__': main()
