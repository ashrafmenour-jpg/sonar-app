package app.sonar

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.BatteryManager
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.provider.MediaStore
import android.provider.Settings
import android.view.WindowManager
import android.webkit.JavascriptInterface
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.core.content.FileProvider
import androidx.webkit.WebViewAssetLoader
import org.json.JSONObject
import java.io.File
import java.io.FileInputStream

/**
 * Sonar for Android: one WebView that runs the Sonar page from the app's own storage.
 *  - The page always lives at https://appassets.androidplatform.net/ → its saved data never depends on a file name.
 *  - The newest page (downloaded by [Updater]) is served from the app's files; the bundled one is the fallback.
 *  - [KeepService] keeps the process and the CPU alive when the screen is off, so training never stops.
 *  - window.SonarApp: save / share files, notifications, battery, keep-awake, updates.
 */
class MainActivity : Activity() {
    companion object { const val HOST = "appassets.androidplatform.net"; const val CH = "sonar_alerts"; var self: MainActivity? = null }

    lateinit var web: WebView
    private var fileCb: ValueCallback<Array<Uri>>? = null
    private val main = Handler(Looper.getMainLooper())

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(b: Bundle?) {
        super.onCreate(b)
        self = this
        WebView.setWebContentsDebuggingEnabled(true)
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CH, "تنبيهات Sonar", NotificationManager.IMPORTANCE_HIGH))
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 7)

        val www = File(filesDir, "www")
        val loader = WebViewAssetLoader.Builder().setDomain(HOST)
            .addPathHandler("/", object : WebViewAssetLoader.PathHandler {
                override fun handle(path: String): WebResourceResponse? {
                    val p = if (path.isEmpty() || path == "/") "index.html" else path.trimStart('/')
                    if (p == "state") return mirrorState()
                    val f = File(www, p)
                    return try {
                        val stream = if (f.isFile) FileInputStream(f) else assets.open("www/$p")
                        val mime = when { p.endsWith(".html") -> "text/html"; p.endsWith(".json") -> "application/json"; p.endsWith(".png") -> "image/png"; else -> "application/octet-stream" }
                        WebResourceResponse(mime, "utf-8", stream).apply { responseHeaders = mapOf("Cache-Control" to "no-store") }
                    } catch (e: Exception) { null }
                }
            }).build()

        web = WebView(this)
        setContentView(web)
        with(web.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            mediaPlaybackRequiresUserGesture = false
            allowFileAccess = false
            loadWithOverviewMode = true
            useWideViewPort = true
            setSupportZoom(false)
            userAgentString = "$userAgentString SonarApp/${BuildConfig.VERSION_NAME}"
        }
        web.addJavascriptInterface(Bridge(this), "SonarApp")
        web.webViewClient = object : WebViewClient() {
            override fun shouldInterceptRequest(v: WebView, r: WebResourceRequest): WebResourceResponse? = loader.shouldInterceptRequest(r.url)
            override fun shouldOverrideUrlLoading(v: WebView, r: WebResourceRequest): Boolean {
                if (r.url.host == HOST) return false
                if (serverHost().isNotEmpty() && r.url.host == serverHost()) return false   // Sonar running on our server
                if (r.isForMainFrame) { try { startActivity(Intent(Intent.ACTION_VIEW, r.url)) } catch (e: Exception) {}; return true }
                return false
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(v: WebView, cb: ValueCallback<Array<Uri>>, p: WebChromeClient.FileChooserParams): Boolean {
                fileCb?.onReceiveValue(null); fileCb = cb
                val i = Intent(Intent.ACTION_GET_CONTENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*")
                return try { startActivityForResult(Intent.createChooser(i, "اختار الملف"), 42); true } catch (e: Exception) { fileCb = null; false }
            }
        }
        web.loadUrl(server().ifEmpty { "https://$HOST/index.html" })
        KeepService.start(this)
        Updater.schedule(this)
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(req: Int, res: Int, data: Intent?) {
        super.onActivityResult(req, res, data)
        if (req == 42) { fileCb?.onReceiveValue(if (res == RESULT_OK && data?.data != null) arrayOf(data.data!!) else null); fileCb = null }
    }

    // keep JavaScript running in the background: the WebView is NOT paused when the screen goes off
    override fun onPause() { super.onPause() }
    override fun onResume() { super.onResume(); js("try{window.__appResume&&window.__appResume()}catch(e){}") }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() { if (web.canGoBack()) web.goBack() else moveTaskToBack(true) }
    override fun onDestroy() { if (self === this) self = null; super.onDestroy() }

    fun js(code: String) = main.post { try { web.evaluateJavascript(code, null) } catch (e: Exception) {} }
    fun reloadPage() = main.post { web.reload() }

    /** Fast view: the server's saved Sonar data (its /f/all export, read key), served to the page from its own origin. */
    fun mirrorState(): WebResourceResponse? {
        val pr = getSharedPreferences("sonar", MODE_PRIVATE); val u = pr.getString("mirror", "") ?: ""; val k = pr.getString("mirrorKey", "") ?: ""
        if (u.isEmpty()) return null
        return try {
            val c = java.net.URL(u.trimEnd('/') + "/f/all?k=" + java.net.URLEncoder.encode(k, "UTF-8")).openConnection() as java.net.HttpURLConnection
            c.connectTimeout = 15000; c.readTimeout = 40000
            if (c.responseCode != 200) WebResourceResponse("text/plain", "utf-8", 502, "Bad Gateway", mapOf(), "no server data".byteInputStream())
            else WebResourceResponse("application/json", "utf-8", c.inputStream).apply { responseHeaders = mapOf("Cache-Control" to "no-store") }
        } catch (e: Exception) { WebResourceResponse("text/plain", "utf-8", 502, "Bad Gateway", mapOf(), "no server data".byteInputStream()) }
    }

    /** Address of the Sonar server ("" = run Sonar on this device). Set from the page via SonarApp.setServer(). */
    fun server(): String = getSharedPreferences("sonar", MODE_PRIVATE).getString("server", "") ?: ""
    fun serverHost(): String = try { Uri.parse(server()).host ?: "" } catch (e: Exception) { "" }

    class Bridge(private val a: MainActivity) {
        private fun ui(f: () -> Unit) = a.main.post(f)
        @JavascriptInterface fun getServer(): String = a.server()
        @JavascriptInterface fun getMirror(): String = if ((a.getSharedPreferences("sonar", MODE_PRIVATE).getString("mirror", "") ?: "").isEmpty()) "" else "1"
        /** Fast view on: Sonar runs here with the server's data (url + read key). "" = off. */
        @JavascriptInterface fun setMirror(url: String, key: String): String {
            val u = url.trim().let { if (it.isEmpty() || it.startsWith("https://") || it.startsWith("http://")) it else "https://$it" }
            a.getSharedPreferences("sonar", MODE_PRIVATE).edit().putString("mirror", u).putString("mirrorKey", key.trim()).putString("server", "").apply()
            ui { a.web.loadUrl("https://$HOST/index.html") }; return "ok"
        }
        /** Switch to the server (url) or back to this device (""). */
        @JavascriptInterface fun setServer(url: String): String {
            val u = url.trim().let { if (it.isEmpty() || it.startsWith("https://") || it.startsWith("http://")) it else "https://$it" }
            a.getSharedPreferences("sonar", MODE_PRIVATE).edit().putString("server", u).apply()
            ui { a.web.loadUrl(u.ifEmpty { "https://$HOST/index.html" }) }; return "ok"
        }
        @JavascriptInterface fun platform(): String = "android"
        @JavascriptInterface fun version(): String = JSONObject().put("app", BuildConfig.VERSION_NAME).put("code", BuildConfig.VERSION_CODE).put("web", Updater.webVersion(a)).toString()

        /** Saves a text file to Downloads (Android 10+ via MediaStore). */
        @JavascriptInterface fun saveFile(name: String, text: String): String {
            return try {
                if (Build.VERSION.SDK_INT >= 29) {
                    val v = ContentValues().apply { put(MediaStore.Downloads.DISPLAY_NAME, name); put(MediaStore.Downloads.MIME_TYPE, "application/json"); put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/Sonar") }
                    val uri = a.contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v)!!
                    a.contentResolver.openOutputStream(uri)!!.use { it.write(text.toByteArray()) }
                } else {
                    val d = File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), "Sonar"); d.mkdirs(); File(d, name).writeText(text)
                }
                ui { Toast.makeText(a, "اتحفظ في التنزيلات ← Sonar: $name", Toast.LENGTH_LONG).show() }; "ok"
            } catch (e: Exception) { ui { Toast.makeText(a, "مقدرتش أحفظ الملف: ${e.message}", Toast.LENGTH_LONG).show() }; "err:" + e.message }
        }

        /** Opens Android's share sheet with the file (choose Claude). */
        @JavascriptInterface fun shareFile(name: String, text: String): String {
            return try {
                val d = File(a.cacheDir, "share"); d.mkdirs(); val f = File(d, name); f.writeText(text)
                val uri = FileProvider.getUriForFile(a, "app.sonar.trader.files", f)
                val i = Intent(Intent.ACTION_SEND).setType("application/json").putExtra(Intent.EXTRA_STREAM, uri).putExtra(Intent.EXTRA_SUBJECT, name).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                ui { a.startActivity(Intent.createChooser(i, "ابعت $name")) }; "ok"
            } catch (e: Exception) { saveFile(name, text) }
        }

        @JavascriptInterface fun notify(title: String, body: String) {
            try {
                val pi = android.app.PendingIntent.getActivity(a, 0, Intent(a, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP), android.app.PendingIntent.FLAG_IMMUTABLE)
                val n = android.app.Notification.Builder(a, CH).setSmallIcon(R.drawable.ic_stat).setContentTitle(title).setContentText(body)
                    .setStyle(android.app.Notification.BigTextStyle().bigText(body)).setAutoCancel(true).setContentIntent(pi).build()
                a.getSystemService(NotificationManager::class.java).notify((System.currentTimeMillis() % 100000).toInt(), n)
            } catch (e: Exception) {}
        }

        @JavascriptInterface fun battery(): String {
            val bm = a.getSystemService(Context.BATTERY_SERVICE) as BatteryManager
            return JSONObject().put("level", bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)).put("charging", bm.isCharging).toString()
        }

        @JavascriptInterface fun keepAwake(on: Boolean) { if (on) KeepService.start(a) else KeepService.stop(a) }
        @JavascriptInterface fun awake(): Boolean = KeepService.running
        @JavascriptInterface fun screenOn(on: Boolean) = ui { if (on) a.window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON) else a.window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON) }

        @JavascriptInterface fun batteryOptimized(): Boolean = !(a.getSystemService(Context.POWER_SERVICE) as PowerManager).isIgnoringBatteryOptimizations(a.packageName)
        @SuppressLint("BatteryLife")
        @JavascriptInterface fun askNoBatteryOpt() = ui {
            try { a.startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + a.packageName))) }
            catch (e: Exception) { try { a.startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)) } catch (x: Exception) {} }
        }

        @JavascriptInterface fun checkUpdate() { Updater.check(a, true) }
        @JavascriptInterface fun openUrl(u: String) = ui { try { a.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(u))) } catch (e: Exception) {} }
        @JavascriptInterface fun reload() = a.reloadPage()
    }
}
