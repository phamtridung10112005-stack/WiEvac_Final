package com.wievac.app

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import com.wievac.app.databinding.ActivityAlarmBinding

class AlarmActivity : AppCompatActivity() {
    private var binding: ActivityAlarmBinding? = null
    private var pulsing = false
    private var receiverOn = false

    private val alarmGone = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            if (!AlarmService.isRunning) {
                finish()
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applyWindow()
        val fire = intent.getBooleanExtra(EXTRA_FIRE, false)
        if (!fire && !AlarmService.isRunning) {
            finish()
            return
        }
        val views = ActivityAlarmBinding.inflate(layoutInflater)
        binding = views
        setContentView(views.root)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        ViewCompat.setOnApplyWindowInsetsListener(views.root) { view, insets ->
            val bars = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout(),
            )
            val side = (24 * resources.displayMetrics.density).toInt()
            view.setPadding(bars.left + side, bars.top + side, bars.right + side, bars.bottom + side)
            WindowInsetsCompat.CONSUMED
        }
        views.stop.setOnClickListener {
            startActivity(
                Intent(this, MainActivity::class.java).addFlags(
                    Intent.FLAG_ACTIVITY_NEW_TASK or
                        Intent.FLAG_ACTIVITY_CLEAR_TOP or
                        Intent.FLAG_ACTIVITY_SINGLE_TOP,
                ),
            )
            AlarmService.stop(this)
            finish()
        }
        onBackPressedDispatcher.addCallback(
            this,
            object : OnBackPressedCallback(true) {
                override fun handleOnBackPressed() = Unit
            },
        )
        ContextCompat.registerReceiver(
            this,
            alarmGone,
            IntentFilter(AlarmService.ACTION_CHANGED),
            ContextCompat.RECEIVER_NOT_EXPORTED,
        )
        receiverOn = true
        if (!AlarmService.isRunning) {
            try {
                AlarmService.start(this)
            } catch (_: Exception) {
                Toast.makeText(this, R.string.alarm_failed, Toast.LENGTH_LONG).show()
            }
        }
    }

    override fun onStart() {
        super.onStart()
        val views = binding ?: return
        if (AlarmService.isRunning) {
            intent.removeExtra(EXTRA_FIRE)
        } else if (!intent.getBooleanExtra(EXTRA_FIRE, false)) {
            finish()
            return
        }
        pulsing = true
        views.kicker.alpha = 1f
        pulse()
    }

    override fun onStop() {
        pulsing = false
        binding?.kicker?.animate()?.cancel()
        super.onStop()
    }

    override fun onDestroy() {
        if (receiverOn) {
            unregisterReceiver(alarmGone)
            receiverOn = false
        }
        super.onDestroy()
    }

    private fun pulse() {
        val kicker = binding?.kicker ?: return
        if (!pulsing) {
            return
        }
        kicker.animate().alpha(0.25f).setDuration(420).withEndAction {
            if (!pulsing) {
                return@withEndAction
            }
            kicker.animate().alpha(1f).setDuration(420).withEndAction { pulse() }.start()
        }.start()
    }

    private fun applyWindow() {
        if (Build.VERSION.SDK_INT >= 27) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        }
        @Suppress("DEPRECATION")
        window.addFlags(
            WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON or
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
                WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON,
        )
    }

    companion object {
        const val EXTRA_FIRE = "fire"
    }
}
