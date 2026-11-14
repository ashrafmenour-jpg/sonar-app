package app.sonar

import android.content.Context
import android.os.Handler
import android.os.Looper
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Automatic updates. Every 3 hours (and on demand) it reads web/version.json from the GitHub repository.
 *  - A newer Sonar page → downloaded into the app's files, then the page is told (it offers to reopen).
 *  - A newer app (APK) → the page shows a link to the newest APK (Android needs you to tap «install»).
 */
object Updater {
    private const val RAW = "https://raw.githubusercontent.com/ashrafmenour-jpg/sonar-app/main/web/"
    const val APK = "https://github.com/ashrafmenour-jpg/sonar-app/releases/latest/download/Sonar.apk"
    private val main = Handler(Looper.getMainLooper())
    @Volatile private var busy = false

    fun webVersion(c: Context): String {
        val p = c.getSharedPreferences("upd", Context.MODE_PRIVATE).getString("web", null)
        if (p != null && File(c.filesDir, "www/index.html").isFile) return p
        return try { JSONObject(c.assets.open("www/version.json").bufferedReader().readText()).optString("web", "0") } catch (e: Exception) { "0" }
    }

    fun schedule(c: Context) {
        val r = object : Runnable { override fun run() { check(c, false); main.postDelayed(this, 3 * 3600_000L) } }
        main.postDelayed(r, 45_000L)
    }

    private fun get(u: String): ByteArray {
        val h = URL(u + (if (u.contains("?")) "&" else "?") + "t=" + System.currentTimeMillis()).openConnection() as HttpURLConnection
        h.connectTimeout = 20000; h.readTimeout = 60000; h.setRequestProperty("Cache-Control", "no-cache")
        try { if (h.responseCode != 200) throw Exception("HTTP " + h.responseCode); return h.inputStream.use { it.readBytes() } } finally { h.disconnect() }
    }

    private fun newer(a: String, b: String): Boolean {
        val x = a.split(".").map { it.toIntOrNull() ?: 0 }; val y = b.split(".").map { it.toIntOrNull() ?: 0 }
        for (i in 0 until maxOf(x.size, y.size)) { val p = x.getOrElse(i) { 0 }; val q = y.getOrElse(i) { 0 }; if (p != q) return p > q }
        return false
    }

    fun check(c: Context, manual: Boolean) {
        if (busy) return; busy = true
        Thread {
            try {
                val v = JSONObject(String(get(RAW + "version.json")))
                val web = v.optString("web", "0"); val apk = v.optInt("apk", 0); val notes = v.optString("notes", "")
                val cur = webVersion(c)
                if (newer(web, cur)) {
                    val html = get(RAW + "index.html")
                    if (html.size > 100_000 && String(html, 0, 400).contains("<html", true)) {
                        val d = File(c.filesDir, "www"); d.mkdirs()
                        val tmp = File(d, "index.new"); tmp.writeBytes(html); tmp.renameTo(File(d, "index.html"))
                        File(d, "version.json").writeText(v.toString())
                        c.getSharedPreferences("upd", Context.MODE_PRIVATE).edit().putString("web", web).apply()
                        MainActivity.self?.js("try{window.__appUpdated&&window.__appUpdated(${JSONObject.quote(web)},${JSONObject.quote(notes)})}catch(e){}")
                    }
                } else if (manual) MainActivity.self?.js("try{window.__appUpdated&&window.__appUpdated(null,'')}catch(e){}")
                if (apk > BuildConfig.VERSION_CODE) MainActivity.self?.js("try{window.__appApk&&window.__appApk(${apk},${JSONObject.quote(APK)})}catch(e){}")
            } catch (e: Exception) {
                if (manual) MainActivity.self?.js("try{window.__appUpdated&&window.__appUpdated('err',${JSONObject.quote(e.message ?: "")})}catch(e){}")
            } finally { busy = false }
        }.start()
    }
}
