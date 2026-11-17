/* Sonar fast view (served by the server at /). Runs BEFORE Sonar: loads the server's saved learning into this browser and
   turns saving off, so this page is a fast local copy for looking around (scrolling, tabs, charts) — the server stays the
   one brain. Real trades are sent to the live mode (/live) so they are recorded on the server. */
(function () {
  var M = window.SONAR_MIRROR = { at: 0, ok: false };
  try {
    var x = new XMLHttpRequest(); x.open('GET', '/state', false); x.send(null);
    if (x.status === 200) {
      var o = JSON.parse(x.responseText);
      for (var i = localStorage.length - 1; i >= 0; i--) { var k = localStorage.key(i); if (k && k.indexOf('sonarpro.') === 0) localStorage.removeItem(k); }
      for (var key in o.keys) if (key !== 'sonarpro.inst') { try { localStorage.setItem(key, o.keys[key]); } catch (e) {} }
      M.at = o.at; M.ok = true; M.n = Object.keys(o.keys).length;
    } else if (x.status === 401 || x.status === 403) { location.href = '/login'; }
  } catch (e) { M.err = String(e); }
  window.__SONAR_PASSIVE = true;   // nothing this page does is saved here; the server keeps the record
  try { var tab = sessionStorage.getItem('mirror.tab'); if (tab) localStorage.setItem('sonarpro.v21.tab', JSON.stringify(tab)); } catch (e) {}

  addEventListener('load', function () { setTimeout(function () {
    // real trades go to the live mode so they are recorded by the server
    ['startTrade', 'startSpotTrade', 'startFastSpot'].forEach(function (fn) {
      var f0 = window[fn]; if (typeof f0 !== 'function') return;
      window[fn] = function () { var mode = arguments[fn === 'startFastSpot' ? 1 : 2] || 'real';
        if (mode === 'sim') return f0.apply(this, arguments);
        if (confirm('الصفقة الحقيقية لازم تتسجّل على السيرفر.\nأفتح الوضع المباشر وتفتحها من هناك؟')) location.href = '/live'; };
    });
    // the bar: data age · refresh · live mode
    var b = document.createElement('div');
    b.style.cssText = 'position:fixed;left:8px;bottom:8px;z-index:9998;background:#0b1220e6;border:1px solid #22304a;border-radius:999px;padding:4px 10px;font:12px system-ui;color:#9fb0c3;display:flex;gap:8px;align-items:center;direction:rtl';
    var age = function () { return M.ok ? Math.max(0, Math.round((Date.now() - M.at) / 60000)) + ' د' : 'مش متصل'; };
    b.innerHTML = '<span title="الصفحة دي نسخة سريعة — السيرفر هو اللي بيتعلم ويسجّل">⚡ عرض سريع · بيانات السيرفر من <b id="mAge"></b></span>' +
      '<a href="#" id="mRf" title="هات آخر بيانات من السيرفر" style="color:#9fb0c3;text-decoration:none">🔄</a>' +
      '<a href="/live" title="الوضع المباشر: تتحكم في Sonar اللي على السيرفر (صفقات حقيقية، إعدادات)" style="color:#9fb0c3;text-decoration:none">🖥 مباشر</a>';
    document.body.appendChild(b);
    var paint = function () { var e = document.getElementById('mAge'); if (e) e.textContent = age(); }; paint(); setInterval(paint, 30000);
    var reload = function () { try { if (window.V21 && V21.tab) sessionStorage.setItem('mirror.tab', V21.tab); sessionStorage.setItem('mirror.y', String(scrollY)); } catch (e) {} location.reload(); };
    document.getElementById('mRf').onclick = function (e) { e.preventDefault(); reload(); };
    try { var y = +sessionStorage.getItem('mirror.y'); if (y) setTimeout(function () { scrollTo(0, y); }, 1500); sessionStorage.removeItem('mirror.y'); } catch (e) {}
    // fresh server data: when you come back after 10+ minutes away, and every 30 minutes while untouched
    var hidAt = 0, lastInput = Date.now();
    ['pointerdown', 'keydown', 'wheel', 'touchstart'].forEach(function (ev) { addEventListener(ev, function () { lastInput = Date.now(); }, { passive: true }); });
    document.addEventListener('visibilitychange', function () { if (document.hidden) hidAt = Date.now(); else if (hidAt && Date.now() - hidAt > 10 * 60000) reload(); });
    setInterval(function () { if (Date.now() - M.at > 30 * 60000 && Date.now() - lastInput > 2 * 60000 && !document.hidden) reload(); }, 60000);
    if (!M.ok) try { toast('⚠️ مقدرتش أجيب بيانات السيرفر — ده Sonar فاضي. جرّب 🔄 أو افتح 🖥 مباشر', 'err'); } catch (e) {}
  }, 4500); });
})();
