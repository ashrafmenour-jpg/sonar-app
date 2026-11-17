"""
Sonar server — ONE Sonar brain running 24/7 on a server, used from any browser.

The exact Sonar app (web/index.html from the GitHub repo) runs in a headless Chromium with a persistent profile
(learning, paper trades and settings survive restarts). You open https://<server>/ from the computer (or tablet/phone):
you see that same running Sonar live and use it normally — clicks, scrolling, typing, confirm questions, files —
because the screen is streamed from the server and your input is sent back to it. Nothing runs on your device, so there
is only one brain and nothing to sync.

Around it:
  * watchdog: heartbeat every minute, restarts the browser if the page hangs or crashes
  * updates: pulls the repo every 20 min and reloads the page when web/version.json changes
  * snapshots: every 15 min the diagnosis file, the learning file and an all-data backup (Sonar's own exporters)
  * errors: page errors / console errors -> /data/logs/console.log
  * /status: server health, exchange reachability, latest files
  * first start: /data/import/*.json («كل بياناتي» export from the tablet) is loaded so learning continues
No real orders: Sonar never places orders on an exchange.
"""
import asyncio, base64, glob, hashlib, hmac, html, json, os, shutil, subprocess, time, traceback, urllib.request
from datetime import datetime, timezone
from aiohttp import web, WSMsgType
from playwright.async_api import async_playwright

DATA = os.environ.get('DATA_DIR', '/data')
REPO = os.environ.get('REPO_DIR', '/app/repo')              # git clone of ashrafmenour-jpg/sonar-app
WEB = os.environ.get('WEB_DIR', os.path.join(REPO, 'web'))
PASS = os.environ.get('SONAR_PASS', '')
APP_PORT = int(os.environ.get('APP_PORT', '8080'))           # internal only: serves web/ to the headless browser
DASH_PORT = int(os.environ.get('DASH_PORT', '8090'))
SNAP_MIN = float(os.environ.get('SNAP_MIN', '15'))
PULL_MIN = float(os.environ.get('PULL_MIN', '20'))
KEEP_DAYS = int(os.environ.get('KEEP_DAYS', '14'))
CHROME = os.environ.get('CHROME_PATH') or None
HERE = os.path.dirname(os.path.abspath(__file__))

for d in ('profile', 'out', 'logs', 'import', 'backup', 'upload'):
    os.makedirs(os.path.join(DATA, d), exist_ok=True)
OUT, LOGS, UPL = os.path.join(DATA, 'out'), os.path.join(DATA, 'logs'), os.path.join(DATA, 'upload')
ST = {'started': time.time(), 'browser_starts': 0, 'last_beat': 0, 'version': '', 'errors_1h': [], 'last_snap': 0,
      'last_pull': 0, 'net': {}, 'notes': []}
APP_URL = f'http://localhost:{APP_PORT}/index.html'

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
    ST['notes'] = ([f'{now()} — {msg}'] + ST['notes'])[:30]; log(msg)
def web_version():
    try: return json.load(open(os.path.join(WEB, 'version.json'), encoding='utf-8')).get('web', '')
    except Exception: return ''

# ====================================================================================================== browser
class Brain:
    def __init__(self):
        self.ctx = self.page = self.cdp = None
        self.viewers = set(); self.vp = {'w': 1280, 'h': 900, 'dpr': 1}; self.casting = False
        self.waits = {}; self.wid = 0; self.crashed = False

    # ---------- viewers ----------
    async def send(self, obj):
        dead = []
        for ws in list(self.viewers):
            try: await ws.send_json(obj)
            except Exception: dead.append(ws)
        for ws in dead: self.viewers.discard(ws)
    async def ask(self, obj, timeout):
        """send a question to the viewers and wait for the first answer"""
        if not self.viewers: return None
        self.wid += 1; obj['id'] = self.wid; fut = asyncio.get_event_loop().create_future(); self.waits[self.wid] = fut
        await self.send(obj)
        try: return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError: return None
        finally: self.waits.pop(obj['id'], None)
    def answer(self, i, v):
        f = self.waits.get(i)
        if f and not f.done(): f.set_result(v)

    async def apply_viewport(self):
        if not self.cdp: return
        w, h, d = self.vp['w'], self.vp['h'], self.vp['dpr']
        try:
            await self.cdp.send('Emulation.setDeviceMetricsOverride', {'width': w, 'height': h, 'deviceScaleFactor': d, 'mobile': w < 700})
        except Exception as e: log(f'viewport: {e}')
    async def cast(self, on):
        if not self.cdp: self.casting = False; return
        try:
            if on:
                await self.apply_viewport()
                await self.cdp.send('Page.startScreencast', {'format': 'jpeg', 'quality': self.vp.get('q', 55), 'maxWidth': int(self.vp['w'] * self.vp['dpr']),
                                                             'maxHeight': int(self.vp['h'] * self.vp['dpr']), 'everyNthFrame': 1})
            elif self.casting:
                await self.cdp.send('Page.stopScreencast')
            self.casting = on
        except Exception as e: log(f'screencast {on}: {e}')
    async def on_frame(self, p):
        # ack only AFTER the frame went out: Chrome waits for the ack before the next frame, so a slow connection gets
        # fewer, fresh frames instead of a growing queue of old ones (that queue is what makes it feel slow)
        b = base64.b64decode(p['data']); dead = []
        for ws in list(self.viewers):
            try: await asyncio.wait_for(ws.send_bytes(b), 10)
            except Exception: dead.append(ws)
        for ws in dead: self.viewers.discard(ws)
        try: await self.cdp.send('Page.screencastFrameAck', {'sessionId': p['sessionId']})
        except Exception: pass
        if not self.viewers: await self.cast(False)

    # ---------- input from a viewer ----------
    async def input(self, m):
        t, c = m.get('t'), self.cdp
        if not c: return
        try:
            if t == 'mouse':
                await c.send('Input.dispatchMouseEvent', {'type': m['type'], 'x': m['x'], 'y': m['y'], 'button': m.get('button', 'left'),
                                                          'buttons': 1 if m['type'] == 'mousePressed' else 0, 'clickCount': m.get('n', 1)})
            elif t == 'wheel':
                await c.send('Input.dispatchMouseEvent', {'type': 'mouseWheel', 'x': m['x'], 'y': m['y'], 'deltaX': m.get('dx', 0), 'deltaY': m.get('dy', 0)})
            elif t == 'key':
                mods = (1 if m.get('alt') else 0) | (2 if m.get('ctrl') else 0) | (4 if m.get('meta') else 0) | (8 if m.get('shift') else 0)
                base = {'key': m.get('key', ''), 'code': m.get('code', ''), 'windowsVirtualKeyCode': m.get('kc', 0), 'modifiers': mods}
                if m.get('text') and not (mods & 6):
                    await c.send('Input.dispatchKeyEvent', dict(base, type='keyDown', text=m['text']))
                else:
                    await c.send('Input.dispatchKeyEvent', dict(base, type='rawKeyDown'))
                await c.send('Input.dispatchKeyEvent', dict(base, type='keyUp'))
            elif t == 'text':
                await c.send('Input.insertText', {'text': str(m.get('s', ''))[:5000]})
            elif t == 'set':   # touch devices: the whole value of the focused field
                await self.page.evaluate("""v => { const e = document.activeElement; if (!e || !('value' in e)) return;
                    const d = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(e), 'value'); d && d.set ? d.set.call(e, v) : (e.value = v);
                    e.dispatchEvent(new Event('input', { bubbles: true })); e.dispatchEvent(new Event('change', { bubbles: true })); }""", str(m.get('v', '')))
                if m.get('enter'):
                    await c.send('Input.dispatchKeyEvent', {'type': 'keyDown', 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13, 'text': '\r'})
                    await c.send('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13})
            elif t == 'hello':
                self.vp = {'w': max(360, min(2560, int(m.get('w', 1280)))), 'h': max(480, min(1600, int(m.get('h', 900)))),
                           'dpr': 2 if m.get('sharp') and float(m.get('dpr', 1)) >= 2 else 1, 'q': max(30, min(85, int(m.get('q', 55))))}
                await self.cast(True)
            elif t == 'answer':
                self.answer(m.get('id'), m.get('v'))
            elif t == 'eval' and os.environ.get('DEBUG_EVAL') == '1':   # tests only, off in production
                asyncio.ensure_future(self.page.evaluate(m['js']))
            elif t == 'reload':
                await self.page.reload(wait_until='load', timeout=120000)
        except Exception as e:
            log(f'input {t} failed: {str(e)[:160]}')

    # ---------- page events ----------
    async def on_dialog(self, d):
        m, kind = d.message or '', d.type
        log(f'dialog {kind}: {m[:200]}')
        try:
            if kind == 'beforeunload': return await d.accept()
            if self.viewers:
                v = await self.ask({'t': 'dialog', 'kind': kind, 'msg': m, 'def': d.default_value or ''}, 600)
                if kind == 'alert': return await d.accept()
                if v is None or v is False: return await d.dismiss()
                return await (d.accept(str(v)) if kind == 'prompt' else d.accept())
            # nobody watching: accept only harmless "reopen the app now?" prompts, decline everything else
            if kind == 'alert' or (kind == 'confirm' and 'أعيد فتح' in m): await d.accept()
            else: await d.dismiss()
        except Exception as e: log(f'dialog error: {e}')
    async def on_filechooser(self, fc):
        if not self.viewers: return
        v = await self.ask({'t': 'pick', 'multiple': fc.is_multiple()}, 600)
        if not v: return
        files = [os.path.join(UPL, os.path.basename(x)) for x in (v if isinstance(v, list) else [v])]
        files = [f for f in files if os.path.exists(f)]
        if files:
            try: await fc.set_files(files)
            except Exception as e: log(f'set_files: {e}')
    async def on_download(self, dl):
        name = os.path.basename(dl.suggested_filename or f'file-{int(time.time())}')
        try: await dl.save_as(os.path.join(OUT, name))
        except Exception as e: return log(f'download: {e}')
        await self.send({'t': 'file', 'name': name, 'url': '/dl/' + name})
    def on_console(self, msg):
        t = msg.text
        if t.startswith('__SONAR_FOCUS__'):
            asyncio.ensure_future(self.send({'t': 'focus', **json.loads(t[15:] or '{}')})); return
        if t.startswith('__SONAR_BLUR__'):
            asyncio.ensure_future(self.send({'t': 'blur'})); return
        if msg.type in ('error', 'warning'):
            if msg.type == 'error' and not any(x in t for x in ('Failed to load resource', 'WebSocket connection', 'net::ERR_', 'Manifest fetch', 'fonts.googleapis', 'bad HTTP response code (404)')):
                ST['errors_1h'].append(time.time())
            log(f'{msg.type}: {t[:400]}', 'console.log')
    def on_pageerror(self, e):
        ST['errors_1h'].append(time.time()); log(f'PAGE ERROR: {str(e)[:600]}', 'console.log')

    # ---------- lifecycle ----------
    async def start(self, pw):
        args = ['--disable-web-security', '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
                '--disable-backgrounding-occluded-windows', '--no-sandbox', '--disable-dev-shm-usage', '--autoplay-policy=no-user-gesture-required']
        self.ctx = await pw.chromium.launch_persistent_context(os.path.join(DATA, 'profile'), headless=True, executable_path=CHROME, args=args,
                                                               viewport={'width': 1280, 'height': 900}, accept_downloads=True, locale='ar-EG',
                                                               timezone_id='Africa/Cairo')
        ST['browser_starts'] += 1; self.crashed = False
        await self.ctx.add_init_script("""(() => { const T = e => /^(INPUT|TEXTAREA)$/.test(e.tagName) && !/^(checkbox|radio|file|range|button|submit|color)$/i.test(e.type || '');
            document.addEventListener('focusin', e => { if (T(e.target)) console.debug('__SONAR_FOCUS__' + JSON.stringify({ v: String(e.target.value || '').slice(0, 2000), kind: e.target.type || 'text', ph: e.target.placeholder || '' })); }, true);
            document.addEventListener('focusout', e => { if (T(e.target)) console.debug('__SONAR_BLUR__'); }, true); window.SONAR_SERVER = 1; })();""")
        self.page = self.ctx.pages[0] if self.ctx.pages else await self.ctx.new_page()
        p = self.page
        p.on('dialog', lambda d: asyncio.ensure_future(self.on_dialog(d)))
        p.on('filechooser', lambda f: asyncio.ensure_future(self.on_filechooser(f)))
        p.on('download', lambda d: asyncio.ensure_future(self.on_download(d)))
        p.on('console', self.on_console); p.on('pageerror', self.on_pageerror)
        p.on('crash', lambda *_: setattr(self, 'crashed', True))
        await p.goto(APP_URL, wait_until='load', timeout=120000)
        await self.import_first()
        self.cdp = await self.ctx.new_cdp_session(p)
        self.cdp.on('Page.screencastFrame', lambda e: asyncio.ensure_future(self.on_frame(e)))
        self.casting = False
        if self.viewers: await self.cast(True)
        ST['version'] = web_version(); note(f'Sonar اشتغل على السيرفر (نسخة {ST["version"]})')
    async def stop(self):
        try: await self.page.evaluate('try { store.flushAll(); } catch (e) {}')
        except Exception: pass
        try: await self.ctx.close()
        except Exception: pass
        self.ctx = self.page = self.cdp = None; self.casting = False

    async def import_first(self):
        files = sorted(glob.glob(os.path.join(DATA, 'import', '*.json')), key=os.path.getmtime)
        if not files: return
        f = files[-1]
        try: o = json.load(open(f, encoding='utf-8'))
        except Exception as e: return note(f'ملف الاستيراد مش سليم: {e}')
        if o.get('app') != 'sonar-pro-all' or not isinstance(o.get('keys'), dict): return note('ملف الاستيراد مش «كل بياناتي» — اتجاهل')
        await self.page.evaluate("""(o) => { window.__SONAR_PASSIVE = true; for (let i = localStorage.length - 1; i >= 0; i--) { const k = localStorage.key(i); if (k && k.startsWith('sonarpro.')) localStorage.removeItem(k); }
            for (const [k, v] of Object.entries(o.keys)) if (k.startsWith('sonarpro.') && k !== 'sonarpro.inst') localStorage.setItem(k, v); }""", o)
        os.makedirs(os.path.join(DATA, 'import', 'done'), exist_ok=True)
        shutil.move(f, os.path.join(DATA, 'import', 'done', os.path.basename(f)))
        note(f'اتنقلت بيانات التابلت ({len(o["keys"])} جزء) — السيرفر بيكمّل من نفس التعلم')
        await self.page.goto(APP_URL, wait_until='load', timeout=120000)

    # Sonar's own exporters, captured in the page (no browser download) so they never mix with files you download yourself
    GRAB = """async (fn) => { const C = HTMLAnchorElement.prototype.click; let got = null;
        HTMLAnchorElement.prototype.click = function () { if (this.download && String(this.href).startsWith('blob:')) { got = { name: this.download, href: this.href }; return; } return C.call(this); };
        try { (0, eval)(fn); } finally { HTMLAnchorElement.prototype.click = C; }   // sync exporters only: the override lasts microseconds
        if (!got) return null; const t = await (await fetch(got.href)).text(); return { name: got.name, text: t }; }"""
    async def snapshot(self):
        stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d-%H-%M')
        try:   # diagnosis: the same report the 🩺 button saves
            t = await asyncio.wait_for(self.page.evaluate("async () => { if (!HC.last) { try { await HC.run(); } catch (e) {} } return JSON.stringify(HC.report(), null, 1); }"), 120)
            with open(os.path.join(OUT, f'Sonar-diagnosis-{stamp}.json'), 'w', encoding='utf-8') as fh: fh.write(t)
        except Exception as e: log(f'snapshot diagnosis failed: {str(e)[:200]}')
        for pre, js in (('Sonar-learning', 'TRN.exportFile()'), ('Sonar-all', 'MIG.export()')):
            try:
                r = await asyncio.wait_for(self.page.evaluate(self.GRAB, js), 90)
                if not r: raise RuntimeError('exporter produced no file')
                with open(os.path.join(OUT, f'{pre}-{stamp}.json'), 'w', encoding='utf-8') as fh: fh.write(r['text'])
            except Exception as e: log(f'snapshot {pre} failed: {str(e)[:200]}')
        try: await self.page.screenshot(path=os.path.join(OUT, f'screen-{stamp}.png'), timeout=30000)
        except Exception as e: log(f'screenshot failed: {e}')
        ST['last_snap'] = time.time(); prune()

    async def loop(self, pw):
        while True:
            try:
                await self.start(pw)
                fails, last_snap, last_pull, born = 0, 0, time.time(), time.time()
                while True:
                    await asyncio.sleep(60)
                    if self.crashed: raise RuntimeError('page crashed')
                    try:
                        await asyncio.wait_for(self.page.evaluate('Date.now()'), 30); ST['last_beat'] = time.time(); fails = 0
                    except Exception as e:
                        fails += 1; log(f'heartbeat failed ({fails}): {str(e)[:120]}')
                        if fails >= 3: raise RuntimeError('page not answering')
                        continue
                    if time.time() - last_snap > SNAP_MIN * 60: await self.snapshot(); last_snap = time.time()
                    if time.time() - last_pull > PULL_MIN * 60:
                        last_pull = time.time()
                        if await asyncio.get_event_loop().run_in_executor(None, pull):
                            await self.page.evaluate('try { store.flushAll(); } catch (e) {}')
                            await self.page.goto(APP_URL, wait_until='load', timeout=120000); ST['version'] = web_version()
                            await self.send({'t': 'note', 'msg': f'Sonar اتحدّث لنسخة {ST["version"]}'})
                    if time.time() - born > 24 * 3600 and not self.viewers:   # fresh browser once a day, never while you are using it
                        note('إعادة تشغيل يومية للمتصفح (للذاكرة)'); break
            except Exception as e:
                note(f'المتصفح وقع وبيتشغّل تاني: {str(e)[:120]}'); log(traceback.format_exc())
            await self.stop(); await asyncio.sleep(10)

BRAIN = Brain()

def prune():
    cut, day = time.time() - 86400, set()
    for p in sorted(glob.glob(os.path.join(OUT, '*')), key=os.path.getmtime, reverse=True):
        if os.path.getmtime(p) >= cut: continue
        b = os.path.basename(p)
        if b.startswith('Sonar-all-') and b[10:20] not in day:
            day.add(b[10:20]); dst = os.path.join(DATA, 'backup', b)
            if not os.path.exists(dst): shutil.copy2(p, dst)
        try: os.remove(p)
        except Exception: pass
    for p in glob.glob(os.path.join(DATA, 'backup', '*')) + glob.glob(os.path.join(UPL, '*')):
        if os.path.getmtime(p) < time.time() - KEEP_DAYS * 86400:
            try: os.remove(p)
            except Exception: pass

# ====================================================================================================== repo + network
def pull():
    if not os.path.isdir(os.path.join(REPO, '.git')): return False
    old = web_version()
    try:
        subprocess.run(['git', '-C', REPO, 'fetch', '-q', 'origin'], timeout=120, check=True)
        subprocess.run(['git', '-C', REPO, 'reset', '-q', '--hard', 'origin/main'], timeout=60, check=True)
    except Exception as e:
        log(f'pull failed: {e}'); return False
    ST['last_pull'] = time.time(); new = web_version()
    if new != old: note(f'تحديث جديد من GitHub: {old} ← {new}'); return True
    return False

NET_CHECKS = {'Binance Futures': 'https://fapi.binance.com/fapi/v1/ping', 'Binance Spot': 'https://api.binance.com/api/v3/ping',
              'Bybit': 'https://api.bybit.com/v5/market/time', 'OKX': 'https://www.okx.com/api/v5/public/time',
              'Bitget': 'https://api.bitget.com/api/v2/public/time', 'Gate': 'https://api.gateio.ws/api/v4/spot/time',
              'XT': 'https://sapi.xt.com/v4/public/time', 'KuCoin': 'https://api.kucoin.com/api/v1/timestamp', 'Groq (AI)': 'https://api.groq.com/openai/v1/models'}
def check_net():
    res = {}
    for name, u in NET_CHECKS.items():
        try:
            r = urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'}), timeout=10); res[name] = {'ok': True}
        except urllib.error.HTTPError as e:
            res[name] = {'ok': 'Groq' in name and e.code == 401, 'err': f'HTTP {e.code}'}   # Groq without key answers 401 = reachable
        except Exception as e:
            res[name] = {'ok': False, 'err': str(e)[:80]}
    ST['net'] = res; bad = [k for k, v in res.items() if not v['ok']]
    note('المنصات: كله واصل' if not bad else 'منصات مش واصلة من السيرفر: ' + '، '.join(bad))

# ====================================================================================================== web
TOKEN = hmac.new((PASS or 'open').encode(), b'sonar-viewer-v1', hashlib.sha256).hexdigest()
READ_TOKEN = os.environ.get('READ_TOKEN', '')   # read-only key: status + report files only (for Claude's checks)
def can_read(req):
    return bool(READ_TOKEN) and hmac.compare_digest(req.query.get('k', '') or req.headers.get('X-Sonar-Key', ''), READ_TOKEN)
def authed(req):
    if not PASS: return True
    if hmac.compare_digest(req.cookies.get('sonar_t', ''), TOKEN): return True
    h = req.headers.get('Authorization', '')
    try: return h.startswith('Basic ') and base64.b64decode(h[6:]).decode('utf-8', 'ignore') == 'sonar:' + PASS
    except Exception: return False
LOGIN = '''<!doctype html><html dir="rtl" lang="ar"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Sonar</title>
<style>body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;background:#0b1220;color:#e6edf3;font-family:system-ui}
form{background:#111a2c;padding:28px;border-radius:16px;min-width:280px;text-align:center}input,button{width:100%;box-sizing:border-box;padding:12px;border-radius:10px;border:1px solid #2a3a55;background:#0b1220;color:#e6edf3;font-size:16px;margin-top:10px}
button{background:#1f6feb;border:0;font-weight:600;cursor:pointer}.e{color:#ff7b72;margin-top:8px}</style>
<form method="post" action="/login"><div style="font-size:36px">📡</div><h2 style="margin:6px 0 4px">Sonar</h2><div style="color:#8b9bb4">الدخول على السيرفر</div>
<input type="password" name="p" placeholder="الباسورد" autofocus>{ERR}<button>دخول</button></form></html>'''

async def h_login(req):
    if req.method == 'POST':
        f = await req.post()
        if PASS and hmac.compare_digest(str(f.get('p', '')), PASS):
            r = web.HTTPFound('/'); r.set_cookie('sonar_t', TOKEN, max_age=90 * 86400, httponly=True, samesite='Lax', secure=req.secure)
            raise r
        await asyncio.sleep(1.5)
        return web.Response(text=LOGIN.replace('{ERR}', '<div class="e">الباسورد غلط</div>'), content_type='text/html')
    return web.Response(text=LOGIN.replace('{ERR}', ''), content_type='text/html')
STATE_CACHE = {'t': 0, 'b': None}
async def h_state(req):
    """the server's saved Sonar data (every sonarpro.* key) — what the fast view loads at open"""
    if not authed(req): raise web.HTTPForbidden()
    if time.time() - STATE_CACHE['t'] > 20 or STATE_CACHE['b'] is None:
        if not BRAIN.page: raise web.HTTPServiceUnavailable(text='Sonar is starting')
        keys = await BRAIN.page.evaluate("""() => { try { store.flushAll(); } catch (e) {} const o = {};
            for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); if (k && k.startsWith('sonarpro.')) o[k] = localStorage.getItem(k); } return o; }""")
        STATE_CACHE['b'] = json.dumps({'at': int(time.time() * 1000), 'ver': web_version(), 'keys': keys}, ensure_ascii=False).encode('utf-8'); STATE_CACHE['t'] = time.time()
    r = web.Response(body=STATE_CACHE['b'], content_type='application/json', headers={'Cache-Control': 'no-store'}); r.enable_compression(); return r
def repo_file(*p):
    v = os.path.join(REPO, *p)
    return v if os.path.exists(v) else os.path.join(HERE, p[-1])
async def h_fast(req):
    """/ : Sonar itself running in your browser (fast scrolling), loaded with the server's data — see mirror.js"""
    if not authed(req): raise web.HTTPFound('/login')
    html_ = open(os.path.join(WEB, 'index.html'), encoding='utf-8').read()
    inject = '<script>' + open(repo_file('server', 'mirror.js'), encoding='utf-8').read() + '</script>'
    html_ = html_.replace('<head>', '<head>' + inject, 1)
    r = web.Response(text=html_, content_type='text/html', headers={'Cache-Control': 'no-store'}); r.enable_compression(); return r
async def h_asset(req):
    n = os.path.basename(req.match_info['n'])
    if not n.endswith(('.png', '.svg', '.ico', '.webmanifest')) and n != 'manifest.json': raise web.HTTPNotFound()
    p = os.path.join(WEB, n)
    if not os.path.isfile(p): raise web.HTTPNotFound()
    return web.FileResponse(p)
async def h_index(req):
    if not authed(req): raise web.HTTPFound('/login')
    v = os.path.join(REPO, 'server', 'viewer.html')   # the pulled repo copy => viewer fixes arrive with the 20-min update
    return web.FileResponse(v if os.path.exists(v) else os.path.join(HERE, 'viewer.html'), headers={'Cache-Control': 'no-store'})
async def h_ws(req):
    if not authed(req): raise web.HTTPForbidden()
    ws = web.WebSocketResponse(heartbeat=25, max_msg_size=8 * 1024 * 1024); await ws.prepare(req)
    BRAIN.viewers.add(ws); log(f'viewer connected ({len(BRAIN.viewers)})')
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try: await BRAIN.input(json.loads(msg.data))
                except Exception as e: log(f'ws msg: {e}')
    finally:
        BRAIN.viewers.discard(ws); log(f'viewer left ({len(BRAIN.viewers)})')
        if not BRAIN.viewers: await BRAIN.cast(False)
    return ws
async def h_upload(req):
    if not authed(req): raise web.HTTPForbidden()
    name = os.path.basename(req.query.get('name', 'file'))[:120] or 'file'
    name = f'{int(time.time())}-{name}'
    with open(os.path.join(UPL, name), 'wb') as fh:
        n = 0
        async for chunk in req.content.iter_chunked(65536):
            n += len(chunk)
            if n > 50 * 1024 * 1024: raise web.HTTPRequestEntityTooLarge(max_size=50 * 1024 * 1024, actual_size=n)
            fh.write(chunk)
    return web.json_response({'name': name})
def latest(prefix):
    L = sorted(glob.glob(os.path.join(OUT, prefix + '*')), key=os.path.getmtime)
    return L[-1] if L else None
async def h_file(req):
    if not (authed(req) or can_read(req)): raise web.HTTPFound('/login')
    k = req.match_info['k']
    if k == 'errors': p = os.path.join(LOGS, 'console.log')
    elif k == 'screen': p = latest('screen')
    else: p = latest({'diagnosis': 'Sonar-diagnosis', 'learning': 'Sonar-learning', 'all': 'Sonar-all'}.get(k, '§'))
    if not p or not os.path.exists(p): return web.Response(text='لسه مفيش', status=404)
    hd = {} if k in ('errors', 'screen') else {'Content-Disposition': f'attachment; filename="{os.path.basename(p)}"'}
    return web.FileResponse(p, headers=hd)
async def h_dl(req):
    if not authed(req): raise web.HTTPFound('/login')
    p = os.path.join(OUT, os.path.basename(req.match_info['n']))
    if not os.path.exists(p): raise web.HTTPNotFound()
    return web.FileResponse(p, headers={'Content-Disposition': f'attachment; filename="{os.path.basename(p)}"'})
def status():
    cut = time.time() - 3600; ST['errors_1h'] = [t for t in ST['errors_1h'] if t > cut]
    f = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime('%H:%M UTC') if t else '—'
    return {'alive': time.time() - ST['last_beat'] < 180, 'نسخة Sonar': ST['version'] or web_version(),
            'شغال من': f'{(time.time() - ST["started"]) / 3600:.1f} ساعة', 'آخر نبضة': f'{time.time() - ST["last_beat"]:.0f} ث' if ST['last_beat'] else '—',
            'إعادة تشغيل المتصفح': ST['browser_starts'], 'أخطاء آخر ساعة': len(ST['errors_1h']), 'فاتحين دلوقتي': len(BRAIN.viewers),
            'آخر ملفات': f(ST['last_snap']), 'آخر سحب تحديث': f(ST['last_pull']), 'net': ST['net'], 'notes': list(ST['notes'])}
async def h_status(req):
    if not (authed(req) or (can_read(req) and req.path.endswith('.json'))): raise web.HTTPFound('/login')
    s = status()
    if req.path.endswith('.json'): return web.json_response(s, dumps=lambda o: json.dumps(o, ensure_ascii=False, indent=1))
    rows = ''.join(f'<tr><td>{html.escape(k)}</td><td>{html.escape(str(v))}</td></tr>' for k, v in s.items() if k not in ('notes', 'net'))
    net = ''.join(f'<li>{html.escape(h)}: {"✅" if v.get("ok") else "❌ " + html.escape(str(v.get("err", "")))}</li>' for h, v in s['net'].items())
    notes = ''.join(f'<li>{html.escape(n)}</li>' for n in s['notes'])
    page = f'''<!doctype html><html dir="rtl" lang="ar"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60"><title>Sonar · حالة السيرفر</title><style>body{{font-family:system-ui;background:#0b1220;color:#e6edf3;margin:16px}}
a.b{{display:inline-block;background:#1f6feb;color:#fff;padding:10px 14px;border-radius:10px;margin:4px;text-decoration:none}}
table{{border-collapse:collapse}}td{{border-bottom:1px solid #223;padding:4px 8px}}</style>
<h2>{"🟢" if s["alive"] else "🔴"} Sonar على السيرفر</h2><a class="b" href="/">📡 افتح Sonar</a>
<a class="b" href="/f/diagnosis">🩺 ملف التشخيص</a><a class="b" href="/f/learning">📓 ملف التعلم</a><a class="b" href="/f/all">📦 كل البيانات</a><a class="b" href="/f/errors">⚠️ الأخطاء</a>
<table>{rows}</table><h3>المنصات من السيرفر</h3><ul>{net}</ul><h3>آخر الأحداث</h3><ul>{notes}</ul></html>'''
    return web.Response(text=page, content_type='text/html')
async def h_health(req):
    ok = time.time() - ST['last_beat'] < 180
    return web.Response(text='ok' if ok else 'stale', status=200 if ok else 503)

async def main():
    if not PASS: log('WARNING: SONAR_PASS is empty — anyone who finds the address can open Sonar')
    # internal server for web/ (fixed origin => saved data persists across updates)
    inner = web.Application()
    async def stat(req):
        n = req.match_info.get('n') or 'index.html'; p = os.path.realpath(os.path.join(WEB, n))
        if not p.startswith(os.path.realpath(WEB)) or not os.path.isfile(p): raise web.HTTPNotFound()
        return web.FileResponse(p, headers={'Cache-Control': 'no-store'})
    inner.router.add_get('/{n:.*}', stat)
    r1 = web.AppRunner(inner); await r1.setup(); await web.TCPSite(r1, '127.0.0.1', APP_PORT).start()
    app = web.Application(client_max_size=60 * 1024 * 1024)
    app.router.add_route('*', '/login', h_login); app.router.add_get('/', h_fast); app.router.add_get('/live', h_index); app.router.add_get('/ws', h_ws)
    app.router.add_get('/state', h_state)
    app.router.add_post('/upload', h_upload); app.router.add_get('/f/{k}', h_file); app.router.add_get('/dl/{n}', h_dl)
    app.router.add_get('/status', h_status); app.router.add_get('/status.json', h_status); app.router.add_get('/health', h_health)
    app.router.add_get('/{n}', h_asset)
    r2 = web.AppRunner(app); await r2.setup(); await web.TCPSite(r2, '0.0.0.0', DASH_PORT).start()
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, pull); await loop.run_in_executor(None, check_net)
    async def net_every():
        while True:
            await asyncio.sleep(6 * 3600); await loop.run_in_executor(None, check_net)
    asyncio.ensure_future(net_every())
    async with async_playwright() as pw:
        await BRAIN.loop(pw)

if __name__ == '__main__': asyncio.run(main())
