package com.wievac.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.PixelFormat
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.os.SystemClock
import android.os.VibrationAttributes
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.provider.Settings
import android.util.Log
import android.view.ContextThemeWrapper
import android.view.LayoutInflater
import android.view.View
import android.view.WindowManager
import android.widget.Toast
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import com.wievac.app.databinding.ActivityAlarmBinding
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.PI
import kotlin.math.sin

class AlarmService : Service() {
    private var siren: Siren? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var overlay: View? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopAlarm()
            return START_NOT_STICKY
        }
        startAlarm()
        return START_STICKY
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        if (isRunning) {
            scheduleRestart()
        }
        super.onTaskRemoved(rootIntent)
    }

    override fun onDestroy() {
        releaseRuntime()
        super.onDestroy()
    }

    private fun startAlarm() {
        isRunning = true
        val notification = buildNotification()
        ServiceCompat.startForeground(
            this,
            NOTIFICATION_ID,
            notification,
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK,
        )
        acquireWakeLock()
        if (siren == null) {
            siren = Siren(applicationContext)
        }
        siren?.start()
        openScreen()
        showOverlay()
        publish()
    }

    private fun stopAlarm() {
        isRunning = false
        cancelRestart(applicationContext)
        releaseRuntime()
        enterApp()
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
        publish()
    }

    private fun enterApp() {
        val home = Intent(this, MainActivity::class.java).addFlags(
            Intent.FLAG_ACTIVITY_NEW_TASK or
                Intent.FLAG_ACTIVITY_CLEAR_TOP or
                Intent.FLAG_ACTIVITY_SINGLE_TOP,
        )
        try {
            startActivity(home)
        } catch (error: Exception) {
            Log.e(TAG, "enter app failed", error)
        }
    }

    private fun releaseRuntime() {
        hideOverlay()
        siren?.stop()
        val lock = wakeLock
        if (lock != null && lock.isHeld) {
            lock.release()
        }
        wakeLock = null
    }

    private fun acquireWakeLock() {
        if (wakeLock?.isHeld == true) {
            return
        }
        val power = getSystemService(PowerManager::class.java)
        wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "wievac:alarm-test").apply {
            setReferenceCounted(false)
            acquire()
        }
    }

    private fun openScreen() {
        wakeScreen(this)
        val screen = Intent(this, AlarmActivity::class.java)
            .putExtra(AlarmActivity.EXTRA_FIRE, true)
            .addFlags(
                Intent.FLAG_ACTIVITY_NEW_TASK or
                    Intent.FLAG_ACTIVITY_CLEAR_TOP or
                    Intent.FLAG_ACTIVITY_SINGLE_TOP,
            )
        try {
            startActivity(screen)
        } catch (error: Exception) {
            Log.e(TAG, "open screen failed", error)
        }
    }

    @Suppress("DEPRECATION")
    private fun showOverlay() {
        if (overlay != null || !Settings.canDrawOverlays(this)) {
            return
        }
        try {
            val themed = ContextThemeWrapper(this, R.style.Theme_WiEvac_Alarm)
            val views = ActivityAlarmBinding.inflate(LayoutInflater.from(themed))
            val density = resources.displayMetrics.density
            views.root.setPadding(
                (24 * density).toInt(),
                (72 * density).toInt(),
                (24 * density).toInt(),
                (32 * density).toInt(),
            )
            views.stop.setOnClickListener { stopAlarm() }
            val params = WindowManager.LayoutParams(
                WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                    WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON or
                    WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
                    WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON or
                    WindowManager.LayoutParams.FLAG_HARDWARE_ACCELERATED,
                PixelFormat.OPAQUE,
            )
            params.screenBrightness = 1f
            if (Build.VERSION.SDK_INT >= 27) {
                params.layoutInDisplayCutoutMode =
                    WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
            }
            getSystemService(WindowManager::class.java).addView(views.root, params)
            overlay = views.root
            pulseKicker(views.kicker)
        } catch (error: Exception) {
            Log.e(TAG, "overlay failed", error)
        }
    }

    private fun hideOverlay() {
        val view = overlay ?: return
        overlay = null
        view.findViewById<View>(R.id.kicker)?.animate()?.cancel()
        try {
            getSystemService(WindowManager::class.java).removeView(view)
        } catch (_: Exception) {
        }
    }

    private fun pulseKicker(view: View) {
        view.animate().alpha(0.25f).setDuration(420).withEndAction {
            if (!view.isAttachedToWindow) {
                return@withEndAction
            }
            view.animate().alpha(1f).setDuration(420).withEndAction {
                if (view.isAttachedToWindow) {
                    pulseKicker(view)
                }
            }.start()
        }.start()
    }

    private fun buildNotification(): Notification {
        val manager = getSystemService(NotificationManager::class.java)
        if (manager.getNotificationChannel(CHANNEL_ID) == null) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                getString(R.string.alarm_channel),
                NotificationManager.IMPORTANCE_HIGH,
            ).apply {
                description = getString(R.string.alarm_channel_desc)
                setSound(null, null)
                enableVibration(false)
                setBypassDnd(true)
                lockscreenVisibility = Notification.VISIBILITY_PUBLIC
            }
            manager.createNotificationChannel(channel)
        }

        val screen = screenPending()
        val stop = PendingIntent.getService(
            this,
            3,
            Intent(this, AlarmService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_alarm)
            .setContentTitle(getString(R.string.alarm_title))
            .setContentText(getString(R.string.alarm_text))
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setOngoing(true)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .setContentIntent(screen)
            .setFullScreenIntent(screen, true)
            .addAction(0, getString(R.string.stop_test), stop)
            .build()
    }

    private fun screenPending(): PendingIntent {
        val open = Intent(this, AlarmActivity::class.java)
            .putExtra(AlarmActivity.EXTRA_FIRE, true)
            .addFlags(
                Intent.FLAG_ACTIVITY_NEW_TASK or
                    Intent.FLAG_ACTIVITY_CLEAR_TOP or
                    Intent.FLAG_ACTIVITY_SINGLE_TOP,
            )
        return PendingIntent.getActivity(
            this,
            2,
            open,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    private fun scheduleRestart() {
        val alarmManager = getSystemService(android.app.AlarmManager::class.java)
        val pending = restartPending(applicationContext, PendingIntent.FLAG_UPDATE_CURRENT)
        val triggerAt = SystemClock.elapsedRealtime() + 1_000L
        try {
            alarmManager.setExactAndAllowWhileIdle(
                android.app.AlarmManager.ELAPSED_REALTIME_WAKEUP,
                triggerAt,
                pending,
            )
        } catch (error: SecurityException) {
            Log.w(TAG, "exact restart denied", error)
            alarmManager.setAndAllowWhileIdle(
                android.app.AlarmManager.ELAPSED_REALTIME_WAKEUP,
                triggerAt,
                pending,
            )
        }
    }

    private fun publish() {
        notifyChanged(this)
    }

    companion object {
        const val ACTION_START = "com.wievac.app.START_ALARM"
        const val ACTION_STOP = "com.wievac.app.STOP_ALARM"
        const val ACTION_CHANGED = "com.wievac.app.ALARM_CHANGED"
        private const val CHANNEL_ID = "wievac_alarm_v2"
        private const val NOTIFICATION_ID = 4101
        private const val PREFS = "wievac_alarm"
        private const val KEY_AT = "at"
        private const val TAG = "WiEvacAlarm"

        @Volatile
        var isRunning: Boolean = false
            private set

        fun start(context: Context) {
            val intent = Intent(context, AlarmService::class.java).setAction(ACTION_START)
            androidx.core.content.ContextCompat.startForegroundService(context, intent)
        }

        fun stop(context: Context) {
            context.startService(Intent(context, AlarmService::class.java).setAction(ACTION_STOP))
        }

        fun scheduledAt(context: Context): Long {
            return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getLong(KEY_AT, 0L)
        }

        fun schedule(context: Context, delaySeconds: Int): Boolean {
            val triggerAt = System.currentTimeMillis() + delaySeconds * 1000L
            val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            prefs.edit().putLong(KEY_AT, triggerAt).apply()
            return try {
                val alarmManager = context.getSystemService(android.app.AlarmManager::class.java)
                val show = PendingIntent.getActivity(
                    context,
                    12,
                    Intent(context, MainActivity::class.java),
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
                )
                val fire = firePending(context)
                try {
                    alarmManager.setAlarmClock(
                        android.app.AlarmManager.AlarmClockInfo(triggerAt, show),
                        fire,
                    )
                } catch (error: SecurityException) {
                    Log.w(TAG, "setAlarmClock denied", error)
                    alarmManager.setExactAndAllowWhileIdle(
                        android.app.AlarmManager.RTC_WAKEUP,
                        triggerAt,
                        fire,
                    )
                }
                notifyChanged(context)
                true
            } catch (error: Exception) {
                Log.e(TAG, "schedule failed", error)
                prefs.edit().remove(KEY_AT).apply()
                false
            }
        }

        fun cancelSchedule(context: Context) {
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().remove(KEY_AT).apply()
            val alarmManager = context.getSystemService(android.app.AlarmManager::class.java)
            alarmManager.cancel(firePending(context))
            notifyChanged(context)
        }

        fun clearScheduled(context: Context) {
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().remove(KEY_AT).apply()
        }

        private fun notifyChanged(context: Context) {
            context.sendBroadcast(Intent(ACTION_CHANGED).setPackage(context.packageName))
        }

        private fun firePending(context: Context): PendingIntent {
            return PendingIntent.getBroadcast(
                context,
                11,
                Intent(context, AlarmFireReceiver::class.java),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
        }

        private fun cancelRestart(context: Context) {
            val pending = PendingIntent.getBroadcast(
                context,
                7,
                Intent(context, AlarmRestartReceiver::class.java),
                PendingIntent.FLAG_NO_CREATE or PendingIntent.FLAG_IMMUTABLE,
            ) ?: return
            val alarmManager = context.getSystemService(android.app.AlarmManager::class.java)
            alarmManager.cancel(pending)
            pending.cancel()
        }

        private fun restartPending(context: Context, mutability: Int): PendingIntent {
            return PendingIntent.getBroadcast(
                context,
                7,
                Intent(context, AlarmRestartReceiver::class.java),
                mutability or PendingIntent.FLAG_IMMUTABLE,
            )
        }
    }
}

class AlarmRestartReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        try {
            AlarmService.start(context)
        } catch (error: Exception) {
            Log.e("WiEvacAlarm", "restart failed", error)
        }
    }
}

class AlarmFireReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        AlarmService.clearScheduled(context)
        wakeScreen(context)
        try {
            AlarmService.start(context)
        } catch (error: Exception) {
            Log.e("WiEvacAlarm", "fire failed", error)
            Toast.makeText(context, R.string.alarm_failed, Toast.LENGTH_LONG).show()
        }
    }
}

@Suppress("DEPRECATION") // ponytail: SCREEN_BRIGHT_WAKE_LOCK still wakes the panel from a background alarm
private fun wakeScreen(context: Context) {
    val power = context.getSystemService(PowerManager::class.java) ?: return
    val lock = power.newWakeLock(
        PowerManager.SCREEN_BRIGHT_WAKE_LOCK or
            PowerManager.ACQUIRE_CAUSES_WAKEUP or
            PowerManager.ON_AFTER_RELEASE,
        "wievac:alarm-screen",
    )
    lock.acquire(15_000L)
}

private class Siren(private val context: Context) {
    private val gate = Any()
    private var session: Session? = null

    fun start() {
        synchronized(gate) {
            if (session?.running?.get() == true) {
                return
            }
            val running = AtomicBoolean(true)
            val audio = context.getSystemService(AudioManager::class.java)
            val attributes = alarmAttributes()
            val focus = requestFocus(audio, attributes)
            val previousVolume = raiseAlarmVolume(audio)
            val track = buildTrack(attributes)
            preferSpeaker(track)
            try {
                track.setVolume(1f)
            } catch (_: Exception) {
            }
            val vibrator = vibrator()
            val created = Session(running, track, previousVolume, focus)
            session = created
            try {
                track.play()
            } catch (error: IllegalStateException) {
                Log.e(TAG, "siren play failed", error)
            }
            vibrate(vibrator)
            Thread({
                try {
                    loop(track, running, audio)
                } finally {
                    release(created, vibrator, audio)
                }
            }, "wievac-siren").start()
        }
    }

    fun stop() {
        val current: Session?
        synchronized(gate) {
            current = session
            current?.running?.set(false)
        }
        try {
            current?.track?.pause()
            current?.track?.flush()
        } catch (_: Exception) {
        }
    }

    private fun loop(track: AudioTrack, running: AtomicBoolean, audio: AudioManager) {
        val sampleRate = 22050
        val samples = ShortArray(2205)
        var phase = 0.0
        var index = 0L
        var nextForce = 0L
        val period = sampleRate.toLong()
        while (running.get()) {
            if (index >= nextForce) {
                // ponytail: re-pin alarm volume; drop if a hardware mute must win
                forceAlarmVolume(audio)
                if (track.routedDevice?.type != AudioDeviceInfo.TYPE_BUILTIN_SPEAKER) {
                    preferSpeaker(track)
                }
                nextForce = index + sampleRate
            }
            for (i in samples.indices) {
                val position = (index % period).toDouble() / sampleRate
                val frequency = if (position < 0.45) 960.0 else 680.0
                val value = sin(phase) * 0.9 * Short.MAX_VALUE
                samples[i] = value.toInt().toShort()
                phase += 2.0 * PI * frequency / sampleRate
                if (phase > 2.0 * PI) {
                    phase -= 2.0 * PI
                }
                index++
            }
            if (!running.get()) {
                break
            }
            val wrote = track.write(samples, 0, samples.size)
            if (wrote < 0) {
                break
            }
        }
    }

    private fun release(created: Session, vibrator: Vibrator, audio: AudioManager) {
        try {
            created.track.pause()
        } catch (_: Exception) {
        }
        try {
            created.track.flush()
        } catch (_: Exception) {
        }
        try {
            created.track.release()
        } catch (_: Exception) {
        }
        try {
            vibrator.cancel()
        } catch (_: Exception) {
        }
        synchronized(gate) {
            if (session === created) {
                session = null
                restoreVolume(audio, created.previousVolume)
                abandonFocus(audio, created.focus)
            }
        }
    }

    private fun alarmAttributes(): AudioAttributes {
        return AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_ALARM)
            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
            .setFlags(AudioAttributes.FLAG_AUDIBILITY_ENFORCED)
            .build()
    }

    private fun buildTrack(attributes: AudioAttributes): AudioTrack {
        val sampleRate = 22050
        val format = AudioFormat.Builder()
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .setSampleRate(sampleRate)
            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
            .build()
        val min = AudioTrack.getMinBufferSize(
            sampleRate,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        return AudioTrack.Builder()
            .setAudioAttributes(attributes)
            .setAudioFormat(format)
            .setBufferSizeInBytes(maxOf(min, 4096))
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()
    }

    private fun requestFocus(audio: AudioManager, attributes: AudioAttributes): AudioFocusRequest {
        val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_EXCLUSIVE)
            .setAudioAttributes(attributes)
            .setAcceptsDelayedFocusGain(false)
            .setOnAudioFocusChangeListener { }
            .build()
        audio.requestAudioFocus(request)
        return request
    }

    private fun abandonFocus(audio: AudioManager, request: AudioFocusRequest?) {
        if (request != null) {
            audio.abandonAudioFocusRequest(request)
        }
    }

    private fun raiseAlarmVolume(audio: AudioManager): Int? {
        return try {
            val current = audio.getStreamVolume(AudioManager.STREAM_ALARM)
            forceAlarmVolume(audio)
            current
        } catch (error: Exception) {
            Log.w(TAG, "volume raise denied", error)
            null
        }
    }

    private fun forceAlarmVolume(audio: AudioManager) {
        try {
            audio.adjustStreamVolume(AudioManager.STREAM_ALARM, AudioManager.ADJUST_UNMUTE, 0)
            val max = audio.getStreamMaxVolume(AudioManager.STREAM_ALARM)
            audio.setStreamVolume(AudioManager.STREAM_ALARM, max, 0)
        } catch (_: Exception) {
        }
    }

    private fun preferSpeaker(track: AudioTrack) {
        try {
            val speaker = context.getSystemService(AudioManager::class.java)
                .getDevices(AudioManager.GET_DEVICES_OUTPUTS)
                .firstOrNull { it.type == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER }
                ?: return
            track.setPreferredDevice(speaker)
        } catch (_: Exception) {
        }
    }

    private fun restoreVolume(audio: AudioManager, previous: Int?) {
        if (previous == null) {
            return
        }
        try {
            audio.setStreamVolume(AudioManager.STREAM_ALARM, previous, 0)
        } catch (_: SecurityException) {
        }
    }

    private fun vibrator(): Vibrator {
        return if (Build.VERSION.SDK_INT >= 31) {
            context.getSystemService(VibratorManager::class.java).defaultVibrator
        } else {
            @Suppress("DEPRECATION")
            context.getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
        }
    }

    private fun vibrate(vibrator: Vibrator) {
        if (!vibrator.hasVibrator()) {
            return
        }
        val effect = VibrationEffect.createWaveform(longArrayOf(0, 500, 150, 500, 150, 250, 400), 0)
        if (Build.VERSION.SDK_INT >= 33) {
            val attributes = VibrationAttributes.createForUsage(VibrationAttributes.USAGE_ALARM)
            vibrator.vibrate(effect, attributes)
        } else {
            @Suppress("DEPRECATION")
            vibrator.vibrate(effect)
        }
    }

    private class Session(
        val running: AtomicBoolean,
        val track: AudioTrack,
        val previousVolume: Int?,
        val focus: AudioFocusRequest?,
    )

    companion object {
        private const val TAG = "WiEvacAlarm"
    }
}
