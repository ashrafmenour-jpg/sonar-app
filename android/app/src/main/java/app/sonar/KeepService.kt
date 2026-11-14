package app.sonar

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.IBinder
import android.os.PowerManager

/** Keeps Sonar alive with the screen off: a foreground service (so Android doesn't kill the app) + a CPU wake lock. */
class KeepService : Service() {
    companion object {
        @Volatile var running = false
        private const val CH = "sonar_keep"
        fun start(c: Context) { try { c.startForegroundService(Intent(c, KeepService::class.java)) } catch (e: Exception) {} }
        fun stop(c: Context) { try { c.stopService(Intent(c, KeepService::class.java)) } catch (e: Exception) {} }
    }
    private var wl: PowerManager.WakeLock? = null
    private var wf: WifiManager.WifiLock? = null

    override fun onBind(i: Intent?): IBinder? = null

    override fun onStartCommand(i: Intent?, flags: Int, id: Int): Int {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CH, "Sonar شغال", NotificationManager.IMPORTANCE_MIN))
        val pi = PendingIntent.getActivity(this, 1, Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP), PendingIntent.FLAG_IMMUTABLE)
        val n: Notification = Notification.Builder(this, CH).setSmallIcon(R.drawable.ic_stat).setContentTitle("Sonar شغال")
            .setContentText("بيتابع السوق والتدريب حتى والشاشة مقفولة").setOngoing(true).setContentIntent(pi).build()
        if (Build.VERSION.SDK_INT >= 29) startForeground(11, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC) else startForeground(11, n)
        if (wl == null) wl = (getSystemService(POWER_SERVICE) as PowerManager).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "sonar:keep").apply { setReferenceCounted(false); acquire() }
        if (wf == null) try {
            @Suppress("DEPRECATION")
            wf = (applicationContext.getSystemService(WIFI_SERVICE) as WifiManager).createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "sonar:wifi").apply { setReferenceCounted(false); acquire() }
        } catch (e: Exception) {}
        running = true
        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        try { wl?.release() } catch (e: Exception) {}; wl = null
        try { wf?.release() } catch (e: Exception) {}; wf = null
        super.onDestroy()
    }
}
