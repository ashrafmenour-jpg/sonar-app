// Sonar for macOS — a native window around the Sonar page.
//  - The page is served by a tiny built-in web server at http://127.0.0.1:47817 → its saved data never depends on a file name.
//  - The newest page (auto-downloaded from GitHub) lives in ~/Library/Application Support/Sonar/www; the bundled one is the fallback.
//  - Closing the window only hides it; Sonar keeps working. The Mac is kept from napping/sleeping while it runs.
//  - window.SonarApp: save / share files (to Downloads), notifications, keep-awake, updates.
import Cocoa
import WebKit
import Network
import UserNotifications

let PORT: UInt16 = 47817
let RAW = "https://raw.githubusercontent.com/ashrafmenour-jpg/sonar-app/main/web/"
let APK = "https://github.com/ashrafmenour-jpg/sonar-app/releases/latest/download/Sonar-Mac.zip"
let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("Sonar")
let www = support.appendingPathComponent("www")

func pageData(_ name: String) -> Data? {
    let f = www.appendingPathComponent(name)
    if let d = try? Data(contentsOf: f) { return d }
    if let u = Bundle.main.url(forResource: (name as NSString).deletingPathExtension, withExtension: (name as NSString).pathExtension, subdirectory: "www") { return try? Data(contentsOf: u) }
    return nil
}

// ---------------------------------------------------------------- tiny local web server (GET only)
final class Server {
    var listener: NWListener?
    func start() {
        let p = NWParameters.tcp; p.allowLocalEndpointReuse = true; p.requiredLocalEndpoint = NWEndpoint.hostPort(host: .ipv4(.loopback), port: NWEndpoint.Port(rawValue: PORT)!)
        guard let l = try? NWListener(using: p) else { return }
        l.newConnectionHandler = { c in c.start(queue: .global()); self.read(c, Data()) }
        l.start(queue: .global()); listener = l
    }
    private func read(_ c: NWConnection, _ buf: Data) {
        c.receive(minimumIncompleteLength: 1, maximumLength: 65536) { data, _, done, err in
            var b = buf; if let d = data { b.append(d) }
            if let r = b.range(of: Data("\r\n\r\n".utf8)) {
                let head = String(decoding: b[..<r.lowerBound], as: UTF8.self)
                var path = head.split(separator: " ").dropFirst().first.map(String.init) ?? "/"
                if let q = path.firstIndex(of: "?") { path = String(path[..<q]) }
                if path == "/" { path = "/index.html" }
                let name = String(path.dropFirst()).replacingOccurrences(of: "..", with: "")
                self.reply(c, name)
            } else if done || err != nil { c.cancel() } else { self.read(c, b) }
        }
    }
    /** fast view: the server's saved Sonar data (its /f/all export), fetched here so the page gets it from its own origin */
    private func mirrorState() -> Data? {
        guard let u = UserDefaults.standard.string(forKey: "mirror"), !u.isEmpty, let k = UserDefaults.standard.string(forKey: "mirrorKey"),
              let url = URL(string: u.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + "/f/all?k=" + (k.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? "")) else { return nil }
        var out: Data?; let sem = DispatchSemaphore(value: 0)
        var rq = URLRequest(url: url); rq.timeoutInterval = 40; rq.cachePolicy = .reloadIgnoringLocalCacheData
        URLSession.shared.dataTask(with: rq) { d, r, _ in if let h = r as? HTTPURLResponse, h.statusCode == 200 { out = d }; sem.signal() }.resume()
        _ = sem.wait(timeout: .now() + 45); return out
    }
    private func reply(_ c: NWConnection, _ name: String) {
        var body = Data(), code = "200 OK", type = "application/octet-stream"
        if name == "state" { if let d = mirrorState() { body = d; type = "application/json" } else { code = "502 Bad Gateway"; body = Data("no server data".utf8) } }
        else if let d = pageData(name) { body = d } else { code = "404 Not Found"; body = Data("not found".utf8) }
        if name.hasSuffix(".html") { type = "text/html; charset=utf-8" } else if name.hasSuffix(".json") { type = "application/json" } else if name.hasSuffix(".png") { type = "image/png" }
        var h = Data("HTTP/1.1 \(code)\r\nContent-Type: \(type)\r\nContent-Length: \(body.count)\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n".utf8); h.append(body)
        c.send(content: h, completion: .contentProcessed { _ in c.cancel() })
    }
}

// ---------------------------------------------------------------- app
final class App: NSObject, NSApplicationDelegate, NSWindowDelegate, WKUIDelegate, WKNavigationDelegate, WKScriptMessageHandler {
    var win: NSWindow!
    var web: WKWebView!
    let server = Server()
    var activity: NSObjectProtocol?
    var timer: Timer?

    func applicationDidFinishLaunching(_ n: Notification) {
        try? FileManager.default.createDirectory(at: www, withIntermediateDirectories: true)
        server.start()
        buildMenu()
        let cfg = WKWebViewConfiguration()
        cfg.websiteDataStore = .default()
        cfg.preferences.javaScriptCanOpenWindowsAutomatically = true
        // keep the page at full speed when the window is hidden or behind others (macOS 14+): the 15 Nov diagnosis showed
        // timers firing ~1 s late on average (10 s at worst) while the window was closed to the menu bar
        if #available(macOS 14.0, *) { cfg.preferences.inactiveSchedulingPolicy = .none }
        cfg.mediaTypesRequiringUserActionForPlayback = []
        let js = """
        window.SonarApp = { platform: function(){ return 'mac'; },
          version: function(){ return JSON.stringify(window.__macVer || {}); },
          saveFile: function(n, t){ webkit.messageHandlers.sonar.postMessage({op:'save', name:n, text:t}); return 'ok'; },
          shareFile: function(n, t){ webkit.messageHandlers.sonar.postMessage({op:'share', name:n, text:t}); return 'ok'; },
          notify: function(a, b){ webkit.messageHandlers.sonar.postMessage({op:'notify', title:a, body:b}); },
          keepAwake: function(on){ webkit.messageHandlers.sonar.postMessage({op:'awake', on:!!on}); }, awake: function(){ return !!window.__macAwake; },
          checkUpdate: function(){ webkit.messageHandlers.sonar.postMessage({op:'update'}); },
          openUrl: function(u){ webkit.messageHandlers.sonar.postMessage({op:'open', url:u}); },
          reload: function(){ location.reload(); },
          getServer: function(){ return window.__macServer || ''; },
          getMirror: function(){ return window.__macMirror || ''; },
          setMirror: function(u, k){ webkit.messageHandlers.sonar.postMessage({op:'mirror', url:String(u||''), key:String(k||'')}); return 'ok'; },
          setServer: function(u){ webkit.messageHandlers.sonar.postMessage({op:'server', url:String(u||'')}); return 'ok'; } };
        window.__macServer = '\(UserDefaults.standard.string(forKey: "server") ?? "")';
        window.__macMirror = '\((UserDefaults.standard.string(forKey: "mirror") ?? "").isEmpty ? "" : "1")';
        """
        cfg.userContentController.addUserScript(WKUserScript(source: js, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        cfg.userContentController.add(self, name: "sonar")
        web = WKWebView(frame: .zero, configuration: cfg)
        web.uiDelegate = self; web.navigationDelegate = self
        web.customUserAgent = nil
        if #available(macOS 13.3, *) { web.isInspectable = true }
        win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1440, height: 920), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        win.title = "Sonar"; win.contentView = web; win.delegate = self; win.center(); win.setFrameAutosaveName("SonarMain"); win.isReleasedWhenClosed = false
        win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { self.web.load(URLRequest(url: self.startURL())) }
        setAwake(true)
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
        DispatchQueue.main.asyncAfter(deadline: .now() + 45) { self.checkUpdate(false) }
        timer = Timer.scheduledTimer(withTimeInterval: 3 * 3600, repeats: true) { _ in self.checkUpdate(false) }
    }

    func buildMenu() {
        let m = NSMenu()
        let a = NSMenuItem(); m.addItem(a); let am = NSMenu(); a.submenu = am
        am.addItem(withTitle: "دوّر على تحديث", action: #selector(menuUpdate), keyEquivalent: "")
        am.addItem(withTitle: "إعادة تحميل", action: #selector(menuReload), keyEquivalent: "r")
        am.addItem(NSMenuItem.separator())
        am.addItem(withTitle: "إخفاء Sonar", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        am.addItem(withTitle: "قفل Sonar نهائيًا", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let e = NSMenuItem(); m.addItem(e); let em = NSMenu(title: "Edit"); e.submenu = em
        em.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        em.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        em.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        em.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        em.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        let w = NSMenuItem(); m.addItem(w); let wm = NSMenu(title: "Window"); w.submenu = wm
        wm.addItem(withTitle: "Sonar", action: #selector(showWin), keyEquivalent: "1")
        wm.addItem(withTitle: "Minimize", action: #selector(NSWindow.miniaturize(_:)), keyEquivalent: "m")
        NSApp.mainMenu = m
    }
    @objc func menuUpdate() { checkUpdate(true) }
    @objc func menuReload() { web.reload() }
    @objc func showWin() { win.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true) }

    // closing the window hides it — Sonar keeps working in the background
    func windowShouldClose(_ s: NSWindow) -> Bool { win.orderOut(nil); return false }
    func applicationShouldHandleReopen(_ s: NSApplication, hasVisibleWindows f: Bool) -> Bool { showWin(); return true }
    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool { false }

    func setAwake(_ on: Bool) {
        if on, activity == nil { activity = ProcessInfo.processInfo.beginActivity(options: [.userInitiated, .idleSystemSleepDisabled, .latencyCritical], reason: "Sonar is following the market") }
        if !on, let a = activity { ProcessInfo.processInfo.endActivity(a); activity = nil }
        web?.evaluateJavaScript("window.__macAwake = \(on ? "true" : "false")")
    }

    // ---------------- bridge
    func userContentController(_ u: WKUserContentController, didReceive msg: WKScriptMessage) {
        guard let o = msg.body as? [String: Any], let op = o["op"] as? String else { return }
        switch op {
        case "save", "share":
            let name = ((o["name"] as? String) ?? "Sonar.json").replacingOccurrences(of: "/", with: "-"), text = (o["text"] as? String) ?? ""
            let dir = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]
            let f = dir.appendingPathComponent(name)
            do { try text.write(to: f, atomically: true, encoding: .utf8); NSWorkspace.shared.activateFileViewerSelecting([f])
                if op == "share" { toast("الملف في التنزيلات ومتحدد في Finder — اسحبه لمحادثة Claude") } }
            catch { toast("مقدرتش أحفظ الملف: \(error.localizedDescription)") }
        case "notify": notify((o["title"] as? String) ?? "Sonar", (o["body"] as? String) ?? "")
        case "awake": setAwake((o["on"] as? Bool) ?? true)
        case "update": checkUpdate(true)
        case "open": if let s = o["url"] as? String, let url = URL(string: s) { NSWorkspace.shared.open(url) }
        case "mirror":   // fast view: Sonar runs here with the server's data (url + read key), or "" to turn it off
            var u = ((o["url"] as? String) ?? "").trimmingCharacters(in: .whitespaces)
            if !u.isEmpty && !u.hasPrefix("http://") && !u.hasPrefix("https://") { u = "https://" + u }
            UserDefaults.standard.set(u, forKey: "mirror"); UserDefaults.standard.set((o["key"] as? String) ?? "", forKey: "mirrorKey")
            UserDefaults.standard.set("", forKey: "server")
            web.evaluateJavaScript("window.__macMirror = '\(u.isEmpty ? "" : "1")'; window.__macServer = ''") { _, _ in self.web.load(URLRequest(url: self.startURL())) }
        case "server":   // switch to the Sonar server (url) or back to this Mac ("")
            var u = ((o["url"] as? String) ?? "").trimmingCharacters(in: .whitespaces)
            if !u.isEmpty && !u.hasPrefix("http://") && !u.hasPrefix("https://") { u = "https://" + u }
            UserDefaults.standard.set(u, forKey: "server")
            web.load(URLRequest(url: startURL()))
        default: break
        }
    }
    func toast(_ s: String) { let q = s.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "'", with: "\\'"); web.evaluateJavaScript("try{toast('\(q)','info')}catch(e){}") }
    func notify(_ t: String, _ b: String) {
        let c = UNMutableNotificationContent(); c.title = t; c.body = b; c.sound = .default
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: UUID().uuidString, content: c, trigger: nil)) { err in
            if err != nil { DispatchQueue.main.async { let s = "display notification \"\(b.replacingOccurrences(of: "\"", with: "'"))\" with title \"\(t.replacingOccurrences(of: "\"", with: "'"))\""; NSAppleScript(source: s)?.executeAndReturnError(nil) } }
        }
    }

    // ---------------- updates
    func webVersion() -> String {
        if let d = try? Data(contentsOf: www.appendingPathComponent("version.json")), let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any], let v = j["web"] as? String, FileManager.default.fileExists(atPath: www.appendingPathComponent("index.html").path) { return v }
        if let d = pageData("version.json"), let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any], let v = j["web"] as? String { return v }
        return "0"
    }
    func newer(_ a: String, _ b: String) -> Bool { let x = a.split(separator: ".").map { Int($0) ?? 0 }, y = b.split(separator: ".").map { Int($0) ?? 0 }; for i in 0..<max(x.count, y.count) { let p = i < x.count ? x[i] : 0, q = i < y.count ? y[i] : 0; if p != q { return p > q } }; return false }
    func fetch(_ name: String, _ done: @escaping (Data?) -> Void) {
        var r = URLRequest(url: URL(string: RAW + name + "?t=\(Int(Date().timeIntervalSince1970))")!); r.cachePolicy = .reloadIgnoringLocalCacheData; r.timeoutInterval = 60
        URLSession.shared.dataTask(with: r) { d, resp, _ in done((resp as? HTTPURLResponse)?.statusCode == 200 ? d : nil) }.resume()
    }
    func checkUpdate(_ manual: Bool) {
        let ver = Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "0"
        DispatchQueue.main.async { self.web.evaluateJavaScript("window.__macVer = {app:'\(ver)', web:'\(self.webVersion())'}") }
        fetch("version.json") { d in
            guard let d = d, let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { if manual { DispatchQueue.main.async { self.web.evaluateJavaScript("try{window.__appUpdated&&window.__appUpdated('err','')}catch(e){}") } }; return }
            let web = j["web"] as? String ?? "0", notes = (j["notes"] as? String ?? "").replacingOccurrences(of: "'", with: "’"), mac = j["mac"] as? Int ?? 0
            if self.newer(web, self.webVersion()) {
                self.fetch("index.html") { h in
                    guard let h = h, h.count > 100_000 else { return }
                    try? FileManager.default.createDirectory(at: www, withIntermediateDirectories: true)
                    try? h.write(to: www.appendingPathComponent("index.html"), options: .atomic); try? d.write(to: www.appendingPathComponent("version.json"), options: .atomic)
                    DispatchQueue.main.async { self.web.evaluateJavaScript("try{window.__appUpdated&&window.__appUpdated('\(web)','\(notes)')}catch(e){}") }
                }
            } else if manual { DispatchQueue.main.async { self.web.evaluateJavaScript("try{window.__appUpdated&&window.__appUpdated(null,'')}catch(e){}") } }
            if mac > (Int(ver) ?? 0) { DispatchQueue.main.async { self.web.evaluateJavaScript("try{window.__appApk&&window.__appApk(\(mac),'\(APK)')}catch(e){}") } }
        }
    }

    // ---------------- page dialogs, file picker, links
    func webView(_ w: WKWebView, runJavaScriptAlertPanelWithMessage m: String, initiatedByFrame f: WKFrameInfo, completionHandler done: @escaping () -> Void) {
        let a = NSAlert(); a.messageText = "Sonar"; a.informativeText = m; a.addButton(withTitle: "تمام"); a.runModal(); done()
    }
    func webView(_ w: WKWebView, runJavaScriptConfirmPanelWithMessage m: String, initiatedByFrame f: WKFrameInfo, completionHandler done: @escaping (Bool) -> Void) {
        let a = NSAlert(); a.messageText = "Sonar"; a.informativeText = m; a.addButton(withTitle: "أيوه"); a.addButton(withTitle: "لأ"); done(a.runModal() == .alertFirstButtonReturn)
    }
    func webView(_ w: WKWebView, runJavaScriptTextInputPanelWithPrompt p: String, defaultText t: String?, initiatedByFrame f: WKFrameInfo, completionHandler done: @escaping (String?) -> Void) {
        let a = NSAlert(); a.messageText = "Sonar"; a.informativeText = p; let tf = NSTextField(frame: NSRect(x: 0, y: 0, width: 320, height: 24)); tf.stringValue = t ?? ""; a.accessoryView = tf
        a.addButton(withTitle: "تمام"); a.addButton(withTitle: "إلغاء"); done(a.runModal() == .alertFirstButtonReturn ? tf.stringValue : nil)
    }
    func webView(_ w: WKWebView, runOpenPanelWith p: WKOpenPanelParameters, initiatedByFrame f: WKFrameInfo, completionHandler done: @escaping ([URL]?) -> Void) {
        let o = NSOpenPanel(); o.allowsMultipleSelection = p.allowsMultipleSelection; o.canChooseDirectories = false; o.begin { r in done(r == .OK ? o.urls : nil) }
    }
    /** The Sonar server if one is saved, otherwise Sonar on this Mac. */
    func startURL() -> URL {
        if let s = UserDefaults.standard.string(forKey: "server"), !s.isEmpty, let u = URL(string: s) { return u }
        return URL(string: "http://127.0.0.1:\(PORT)/index.html")!
    }
    func serverHost() -> String { URL(string: UserDefaults.standard.string(forKey: "server") ?? "")?.host ?? "" }
    func webView(_ w: WKWebView, decidePolicyFor a: WKNavigationAction, decisionHandler done: @escaping (WKNavigationActionPolicy) -> Void) {
        if let h = a.request.url?.host, !serverHost().isEmpty, h == serverHost() { done(.allow); return }
        if let u = a.request.url, a.targetFrame?.isMainFrame ?? true, u.host != "127.0.0.1", ["http", "https"].contains(u.scheme ?? "") { NSWorkspace.shared.open(u); done(.cancel); return }
        done(.allow)
    }
    func webView(_ w: WKWebView, createWebViewWith c: WKWebViewConfiguration, for a: WKNavigationAction, windowFeatures f: WKWindowFeatures) -> WKWebView? {
        if let u = a.request.url { NSWorkspace.shared.open(u) }; return nil
    }
    func webViewWebContentProcessDidTerminate(_ w: WKWebView) { w.reload() }
}

let app = NSApplication.shared
let delegate = App()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
