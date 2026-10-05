package com.wievac.app

import android.Manifest
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import androidx.core.content.ContextCompat
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.sin
import kotlin.math.sqrt

const val MAP_PREFS = "wievac_map"
const val PREF_STEP = "step_m"
const val DEFAULT_STEP_M = 0.7f

enum class FixSource { WIFI, STEPS, MANUAL, LAST, VISION, ARCORE }

/** Where the user is believed to be. [room] null = corridor. [accuracyM] is a rough radius, not calibrated. */
class Fix(
    val level: Int,
    val x: Float,
    val y: Float,
    val room: String?,
    val edge: Edge?,
    val node: Node?,
    val source: FixSource,
    val accuracyM: Float,
    val time: Long,
)

class Route(val edges: List<Edge>, val metres: Float, val exit: Node)

/**
 * Places the user on a surveyed [Building]: Wi-Fi fingerprint kNN for an absolute fix,
 * steps + compass for motion between fixes (map-matched onto edges), manual override.
 */
class Locator(val building: Building) {
    var fix: Fix? = null

    fun wifi(scan: Map<String, Int>, now: Long): Fix? {
        if (scan.size < SurveyRecorder.MIN_APS) {
            return null
        }
        val scored = building.fingerprints.mapNotNull { print ->
            val common = print.rssi.keys.count { it in scan }
            if (common < SurveyRecorder.MIN_APS) null else print to rssiDistance(print.rssi, scan)
        }.sortedBy { it.second }
        val best = scored.firstOrNull()?.takeIf { it.second <= MAX_DB } ?: return null
        val top = scored.take(K).filter { it.first.level == best.first.level && it.second <= MAX_DB }
        val weights = top.map { 1f / (it.second + 1f) }
        val x = top.indices.sumOf { (top[it].first.at.x * weights[it]).toDouble() }.toFloat() / weights.sum()
        val y = top.indices.sumOf { (top[it].first.at.y * weights[it]).toDouble() }.toFloat() / weights.sum()
        val spread = top.maxOf { hypot(it.first.at.x - x, it.first.at.y - y) }
        val level = best.first.level
        val spot = spot(level, x, y, WIFI_SNAP_M, nodes = true) { building.visible(it, best.first.room) }
            ?: spot(level, x, y, WIFI_SNAP_M, nodes = true)
        return Fix(
            level, spot?.x ?: x, spot?.y ?: y, spot?.room ?: best.first.room, spot?.edge, spot?.node,
            FixSource.WIFI, maxOf(MIN_WIFI_ACCURACY_M, spread), now,
        )
    }

    fun step(lengthM: Float, heading: Float, now: Long) {
        val f = fix ?: return
        val nx = f.x + lengthM * sin(heading)
        val ny = f.y + lengthM * cos(heading)
        val spot = spot(f.level, nx, ny, STEP_SNAP_M, nodes = false)
        fix = Fix(
            f.level, spot?.x ?: nx, spot?.y ?: ny, spot?.room ?: f.room, spot?.edge, spot?.node,
            FixSource.STEPS, minOf(MAX_ACCURACY_M, f.accuracyM + STEP_GROWTH_M), now,
        )
    }

    /** User tapped the map. [space] is the room (or corridor) they were looking at. */
    fun manual(level: Int, x: Float, y: Float, space: String?, now: Long) {
        val spot = spot(level, x, y, MANUAL_SNAP_M, nodes = true) { building.visible(it, space) }
        fix = Fix(level, spot?.x ?: x, spot?.y ?: y, spot?.room ?: space, spot?.edge, spot?.node,
            FixSource.MANUAL, 1f, now)
    }

    fun route(open: (Edge) -> Boolean): Route? {
        val f = fix ?: return null
        val starts = when {
            f.node != null -> listOf(f.node to 0f)
            f.edge != null -> listOfNotNull(building.node(f.edge.from), building.node(f.edge.to))
                .map { it to hypot(it.at.x - f.x, it.at.y - f.y) }
            else -> listOfNotNull(
                building.nodes.filter { it.level == f.level }.minByOrNull { hypot(it.at.x - f.x, it.at.y - f.y) },
            ).map { it to hypot(it.at.x - f.x, it.at.y - f.y) }
        }
        return starts.mapNotNull { (start, lead) ->
            val path = building.route(start.id, open) ?: return@mapNotNull null
            var end = start.id
            path.forEach { end = it.other(end) }
            val edges = if (f.edge != null && f.node == null) listOf(f.edge) + path else path
            Route(edges, lead + path.sumOf { it.lengthM.toDouble() }.toFloat(), building.node(end)!!)
        }.minByOrNull { it.metres }
    }

    class Spot(val x: Float, val y: Float, val room: String?, val edge: Edge?, val node: Node?)

    /** Nearest node (if [nodes]) or point on an edge within [limitM] on [level]. */
    fun spot(level: Int, x: Float, y: Float, limitM: Float, nodes: Boolean, keep: (Node) -> Boolean = { true }): Spot? {
        if (nodes) {
            building.nodes.filter { it.level == level && keep(it) }
                .minByOrNull { hypot(it.at.x - x, it.at.y - y) }
                ?.takeIf { hypot(it.at.x - x, it.at.y - y) <= NODE_SNAP_M }
                ?.let { return Spot(it.at.x, it.at.y, it.room, null, it) }
        }
        var best: Spot? = null
        var bestD = limitM
        building.edges.forEach { edge ->
            val a = building.node(edge.from) ?: return@forEach
            val b = building.node(edge.to) ?: return@forEach
            if (a.level != level || b.level != level || !keep(a) || !keep(b)) return@forEach
            val dx = b.at.x - a.at.x
            val dy = b.at.y - a.at.y
            val len = dx * dx + dy * dy
            val t = if (len == 0f) 0f else (((x - a.at.x) * dx + (y - a.at.y) * dy) / len).coerceIn(0f, 1f)
            val px = a.at.x + t * dx
            val py = a.at.y + t * dy
            val d = hypot(x - px, y - py)
            if (d <= bestD) {
                bestD = d
                best = Spot(px, py, building.spaceOf(edge), edge, null)
            }
        }
        return best
    }

    fun save(prefs: SharedPreferences) {
        val f = fix ?: return
        prefs.edit()
            .putString("fix_map", building.id)
            .putInt("fix_level", f.level).putFloat("fix_x", f.x).putFloat("fix_y", f.y)
            .putString("fix_room", f.room).putFloat("fix_acc", f.accuracyM).putLong("fix_time", f.time)
            .apply()
    }

    fun restore(prefs: SharedPreferences) {
        if (prefs.getString("fix_map", null) != building.id) {
            return
        }
        val level = prefs.getInt("fix_level", 0)
        val x = prefs.getFloat("fix_x", 0f)
        val y = prefs.getFloat("fix_y", 0f)
        val room = prefs.getString("fix_room", null)
        val spot = spot(level, x, y, MANUAL_SNAP_M, nodes = true) { building.visible(it, room) }
        fix = Fix(level, spot?.x ?: x, spot?.y ?: y, room, spot?.edge, spot?.node,
            FixSource.LAST, prefs.getFloat("fix_acc", 5f), prefs.getLong("fix_time", 0L))
    }

    companion object {
        private const val K = 3
        private const val MAX_DB = 18f
        private const val MISSING_DBM = -100
        private const val MIN_WIFI_ACCURACY_M = 3f
        private const val MAX_ACCURACY_M = 15f
        private const val STEP_GROWTH_M = 0.05f
        private const val WIFI_SNAP_M = 6f
        private const val STEP_SNAP_M = 3f
        private const val MANUAL_SNAP_M = 2f
        private const val NODE_SNAP_M = 1.5f

        /** RMS dB difference over the union of access points; missing ones count as −100 dBm. */
        fun rssiDistance(a: Map<String, Int>, b: Map<String, Int>): Float {
            val keys = a.keys + b.keys
            val sum = keys.sumOf { key ->
                val d = (a[key] ?: MISSING_DBM) - (b[key] ?: MISSING_DBM)
                (d * d).toDouble()
            }
            return sqrt(sum / keys.size).toFloat()
        }

        /** Heading of the phone's walking direction (screen-up blended with camera-forward), radians from +Y. */
        fun forwardHeading(rotation: FloatArray): Float? {
            val fx = rotation[1] - rotation[2]
            val fy = rotation[4] - rotation[5]
            return if (hypot(fx, fy) > 0.2f) atan2(fx, fy) else null
        }
    }
}

/** Phone sensors for locating: magnetic heading, steps and Wi-Fi scans. */
class Tracker(private val context: Context, private val steps: Boolean, private val listener: Listener) :
    SensorEventListener {
    interface Listener {
        fun onStep() = Unit
        fun onScan(scan: Map<String, Int>) = Unit
    }

    /** Magnetic heading of the walking direction, radians clockwise from north; NaN until known. */
    var heading = Float.NaN
        private set
    private val rotation = FloatArray(9)
    private val sensors = context.getSystemService(SensorManager::class.java)
    private val wifi = context.applicationContext.getSystemService(WifiManager::class.java)
    private val handler = Handler(Looper.getMainLooper())
    private var running = false
    private val tick = object : Runnable {
        override fun run() {
            scan()
            handler.postDelayed(this, SCAN_EVERY_MS)
        }
    }
    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            if (intent?.getBooleanExtra(WifiManager.EXTRA_RESULTS_UPDATED, false) == true) {
                results(FRESH_MS)?.let(listener::onScan)
            }
        }
    }

    fun start() {
        if (running) {
            return
        }
        running = true
        listOfNotNull(
            sensors.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR),
            if (steps) sensors.getDefaultSensor(Sensor.TYPE_STEP_DETECTOR) else null,
        ).forEach { sensors.registerListener(this, it, SensorManager.SENSOR_DELAY_GAME) }
        ContextCompat.registerReceiver(
            context, receiver, IntentFilter(WifiManager.SCAN_RESULTS_AVAILABLE_ACTION),
            ContextCompat.RECEIVER_NOT_EXPORTED,
        )
        handler.post(tick)
    }

    fun stop() {
        if (!running) {
            return
        }
        running = false
        sensors.unregisterListener(this)
        context.unregisterReceiver(receiver)
        handler.removeCallbacks(tick)
    }

    fun canScan(): Boolean {
        val fine = ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED
        val nearby = Build.VERSION.SDK_INT < 33 ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.NEARBY_WIFI_DEVICES) ==
            PackageManager.PERMISSION_GRANTED
        return fine && nearby
    }

    /** Android throttles foreground apps to 4 scans per 2 minutes; extra calls just return false. */
    @Suppress("DEPRECATION")
    fun scan(): Boolean {
        return canScan() && try {
            wifi.startScan()
        } catch (_: SecurityException) {
            false
        }
    }

    /** Latest system scan results no older than [maxAgeMs]; null when too few or not permitted. */
    fun results(maxAgeMs: Long): Map<String, Int>? {
        if (!canScan()) {
            return null
        }
        val list = try {
            wifi.scanResults
        } catch (_: SecurityException) {
            return null
        }
        val nowUs = SystemClock.elapsedRealtime() * 1000
        return list.filter { nowUs - it.timestamp <= maxAgeMs * 1000 }
            .associate { it.BSSID to it.level }
            .takeIf { it.size >= SurveyRecorder.MIN_APS }
    }

    override fun onSensorChanged(event: SensorEvent) {
        when (event.sensor.type) {
            Sensor.TYPE_ROTATION_VECTOR -> {
                SensorManager.getRotationMatrixFromVector(rotation, event.values)
                Locator.forwardHeading(rotation)?.let { heading = it }
            }
            Sensor.TYPE_STEP_DETECTOR -> listener.onStep()
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit

    companion object {
        private const val SCAN_EVERY_MS = 10_000L
        const val FRESH_MS = 15_000L
        const val CACHED_MS = 60_000L
    }
}
