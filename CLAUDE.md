# Sonar — working rules for Claude

Owner: Ashraf (Arabic, Egyptian dialect in replies; code comments in English). He dislikes the command line: updates must arrive by themselves.

## How changes reach Ashraf (the update gate)
- **Push to `dev`, never straight to `main`.** `.github/workflows/test.yml` tests `dev`; only if it passes is `dev` fast-forwarded to `main`.
  The Android/Mac apps (web/version.json every 3 h) and the server (`server/runner.py`, every 20 min) all update from `main`.
- Exception: a commit that changes `.github/workflows/*` cannot be promoted by the workflow token — after the test passes, push it to `main` yourself.
- Every page change = a new baked block `/* ===== baked update X.Y.Z ===== */` before `</script>` + `window.SONAR_WEB` + `web/version.json` `web`.
- Before pushing: `python server/smoke_test.py web` (needs playwright + chromium).
- App code changes (`android/`, `mac/`) trigger `build.yml` via the gate. After a build, set `apk`/`mac` in `web/version.json` to the new build number so installed apps offer the update.
- Never run `server/runner.py` locally with `REPO_DIR` pointing at your working clone: it does `git reset --hard origin/main`.

## The server (live since 18 Nov 2026)
- https://ashraf.zedshub.net — one Sonar running 24/7 in headless Chromium; Ashraf opens it from a browser or the apps (🌐 السيرفر).
- Read-only checks: `/status.json?k=<READ_TOKEN>`, `/f/diagnosis?k=…` (also `/f/learning`, `/f/all`, `/f/errors`). Ashraf gives the token in chat; never store it in the repo.
- The sandbox shell cannot reach it; WebFetch can.

## Research discipline (agreed 18 Nov 2026)
1. **No new live engines/filters until the historical test has a verdict.** New ideas go into `research/rules.json` first.
2. Rules are **pre-registered**: add a new id, never edit a rule that already has results. `research/backtest.py` runs weekly on GitHub
   (`backtest.yml`) and publishes to the `research-data` branch; the app shows it in the brain tab (📚 الاختبار على 8 سنين).
3. «مثبت» = positive after costs over 2018→now, t ≥ 2 on daily sums, positive in the unseen period (2023+) and in both halves.
4. Judge every idea by market state (DOWN / REBOUND / RANGE / UP) — keep engines that fail in one state, re-test when the market turns.
5. Goals: 30–40 real decisions while winning; the second half as good as the first; survive a regime change.
