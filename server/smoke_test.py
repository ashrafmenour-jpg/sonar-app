"""Smoke test for web/index.html — run before every push (and by GitHub Actions on every push).
Fails if the page throws, if the version in the page and in version.json differ, if a core module is missing,
or if the diagnosis report can't be built. Network failures are ignored (CI has no exchange access guarantees)."""
import json, os, sys
from playwright.sync_api import sync_playwright
WEB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), '..', 'web')
WEB = os.path.abspath(WEB)
want = json.load(open(os.path.join(WEB, 'version.json'), encoding='utf-8'))['web']
errs, problems = [], []
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get('CHROME_PATH') or None, args=['--no-sandbox'])
    pg = b.new_page()
    pg.on('pageerror', lambda e: errs.append(str(e)[:300]))
    pg.on('dialog', lambda d: d.dismiss())
    pg.goto('file://' + os.path.join(WEB, 'index.html'), wait_until='load', timeout=120000)
    pg.wait_for_timeout(int(os.environ.get('SMOKE_WAIT_MS', '20000')))
    got = pg.evaluate('window.SONAR_WEB')
    if got != want: problems.append(f'version mismatch: page {got} vs version.json {want}')
    missing = pg.evaluate("['S','TRN','PICK','HC','UPD','RVW','NET','XCH','store'].filter(k => { try { return typeof eval(k) === 'undefined'; } catch (e) { return true; } })")
    if missing: problems.append('missing modules: ' + ', '.join(missing))
    try:
        r = pg.evaluate('JSON.stringify(HC.report()).length')
        if not r or r < 1000: problems.append(f'diagnosis report too small ({r})')
    except Exception as e: problems.append(f'diagnosis report failed: {e}')
    baked = pg.evaluate("[...document.scripts].map(s => s.textContent).join('').match(/baked update [0-9.]+/g) || []")
    b.close()
for e in errs: problems.append('page error: ' + e)
print(f'Sonar {want} · baked: {", ".join(x.split()[-1] for x in baked[-3:])}')
if problems:
    print('FAILED'); [print(' -', x) for x in problems]; sys.exit(1)
print('OK')
