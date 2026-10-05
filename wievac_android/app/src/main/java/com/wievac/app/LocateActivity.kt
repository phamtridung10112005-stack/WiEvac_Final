package com.wievac.app

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.BitmapFactory
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.opengl.GLES20
import android.opengl.GLSurfaceView
import android.os.Build
import android.os.Bundle
import android.view.Surface
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.google.ar.core.ArCoreApk
import com.google.ar.core.Config
import com.google.ar.core.Session
import com.google.ar.core.TrackingState
import com.wievac.app.databinding.ActivityLocateBinding
import java.util.concurrent.Executors
import javax.microedition.khronos.egl.EGLConfig
import javax.microedition.khronos.opengles.GL10
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.sin

/**
 * Cold-start: match the camera still against survey photos (ORB, not pixels).
 * After a lock, ARCore of THIS session updates the red dot every frame via a frozen SE2.
 * If tracking drops, steps fill at most 5 s; then the fix is cleared, never guessed.
 */
class LocateActivity : AppCompatActivity(), GLSurfaceView.Renderer, SensorEventListener {
    private lateinit var binding: ActivityLocateBinding
    private lateinit var building: Building
    private lateinit var locator: Locator
    private lateinit var landmarks: List<Landmark>
    private val corridors = sampleCorridors()
    private val background = ArPreview()
    private val pool = Executors.newSingleThreadExecutor()

    @Volatile private var session: Session? = null
    @Volatile private var geometryDirty = false
    private var textureBound = false
    private var installAsked = false
    private var viewW = 0
    private var viewH = 0
    private var asked = false

    private var align: Se2? = null
    private var lastAr: Se2? = null
    private var lastMatchAt = 0L
    private var lostAt = 0L
    private var placing = false
    @Volatile private var matching = false
    @Volatile private var wantMatch = true

    private var sensorsOn = false
    private val rotation = FloatArray(9)
    private var heading = 0f
    private var stepM = DEFAULT_STEP_M

    private val permissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        if (!granted(Manifest.permission.CAMERA)) {
            Toast.makeText(this, R.string.locate_no_camera, Toast.LENGTH_LONG).show()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityLocateBinding.inflate(layoutInflater)
        setContentView(binding.root)
        building = Building.load(Building.file(filesDir))
        locator = Locator(building)
        landmarks = building.loadLandmarks(filesDir)
        stepM = getSharedPreferences(MAP_PREFS, MODE_PRIVATE).getFloat(PREF_STEP, DEFAULT_STEP_M)
        binding.plan.allSpaces = false
        binding.plan.building = building
        binding.plan.edgeColor = { edge ->
            val ids = edge.sensorIds
            if (ids.isEmpty()) ContextCompat.getColor(this, R.color.muted)
            else {
                val severity = listOf(CorridorState.PASSABLE, CorridorState.DEGRADED, CorridorState.UNKNOWN, CorridorState.BLOCKED)
                val state = ids.map { id -> corridors.firstOrNull { it.id == id }?.takeIf { it.fresh }?.state ?: CorridorState.UNKNOWN }
                    .maxBy { severity.indexOf(it) }
                ContextCompat.getColor(this, when (state) {
                    CorridorState.PASSABLE -> R.color.passable
                    CorridorState.DEGRADED -> R.color.degraded
                    CorridorState.BLOCKED -> R.color.blocked
                    CorridorState.UNKNOWN -> R.color.unknown
                })
            }
        }
        binding.plan.onTapNode = { node -> if (placing) pickFacing(node) }
        binding.manual.setOnClickListener {
            placing = !placing
            if (placing) {
                align = null
                locator.fix = null
                Toast.makeText(this, R.string.locate_place_hint, Toast.LENGTH_LONG).show()
            }
            paint()
        }
        binding.done.setOnClickListener { finish() }
        binding.surface.preserveEGLContextOnPause = true
        binding.surface.setEGLContextClientVersion(2)
        binding.surface.setEGLConfigChooser(8, 8, 8, 8, 16, 0)
        binding.surface.setRenderer(this)
        binding.surface.renderMode = GLSurfaceView.RENDERMODE_CONTINUOUSLY
        paint()
    }

    override fun onResume() {
        super.onResume()
        val need = listOfNotNull(
            Manifest.permission.CAMERA,
            if (Build.VERSION.SDK_INT >= 29) Manifest.permission.ACTIVITY_RECOGNITION else null,
        ).filterNot(::granted).toTypedArray()
        if (!asked && need.isNotEmpty()) {
            asked = true
            permissions.launch(need)
            return
        }
        resumeAr()
        startSensors()
    }

    override fun onPause() {
        super.onPause()
        binding.surface.onPause()
        session?.pause()
        stopSensors()
    }

    override fun onDestroy() {
        session?.close()
        session = null
        pool.shutdownNow()
        super.onDestroy()
    }

    private fun granted(permission: String) =
        ContextCompat.checkSelfPermission(this, permission) == PackageManager.PERMISSION_GRANTED

    private fun resumeAr() {
        if (!granted(Manifest.permission.CAMERA)) {
            return
        }
        try {
            if (session == null) {
                if (ArCoreApk.getInstance().requestInstall(this, !installAsked) ==
                    ArCoreApk.InstallStatus.INSTALL_REQUESTED
                ) {
                    installAsked = true
                    return
                }
                session = Session(this).apply {
                    configure(Config(this).apply {
                        planeFindingMode = Config.PlaneFindingMode.HORIZONTAL
                        focusMode = Config.FocusMode.AUTO
                        updateMode = Config.UpdateMode.LATEST_CAMERA_IMAGE
                    })
                }
            }
            session?.resume()
        } catch (_: Exception) {
            session?.close()
            session = null
            Toast.makeText(this, R.string.survey_ar_failed, Toast.LENGTH_LONG).show()
            return
        }
        binding.surface.onResume()
    }

    private fun pickFacing(node: Node) {
        val neighbors = building.edges.filter { it.from == node.id || it.to == node.id }
            .mapNotNull { building.node(it.other(node.id)) }
        if (neighbors.isEmpty()) {
            lockManual(node.at.x, node.at.y, 0f, node.room, node.level)
            return
        }
        val labels = neighbors.map { getString(R.string.locate_facing, it.name) }.toTypedArray()
        AlertDialog.Builder(this)
            .setTitle(node.name)
            .setItems(labels) { _, which ->
                val look = neighbors[which]
                val heading = atan2(look.at.x - node.at.x, look.at.y - node.at.y)
                lockManual(node.at.x, node.at.y, heading, node.room, node.level)
            }
            .show()
    }

    private fun lockManual(x: Float, y: Float, heading: Float, room: String?, level: Int) {
        val ar = lastAr
        if (ar != null) {
            align = Se2.align(Se2(x, y, heading), ar)
        }
        placing = false
        applyMap(Se2(x, y, heading), room, FixSource.MANUAL, 1f, level)
        paint()
    }

    private var lastMapHeading = 0f

    private fun applyMap(map: Se2, room: String?, source: FixSource, accuracy: Float, levelHint: Int? = null) {
        lastMapHeading = map.heading
        val now = System.currentTimeMillis()
        val level = levelHint ?: locator.fix?.level ?: building.levels().first()
        val snap = locator.spot(level, map.x, map.y, 3f, nodes = true) { building.visible(it, room) }
            ?: locator.spot(level, map.x, map.y, 3f, nodes = true)
        val space = snap?.room ?: room
        locator.fix = Fix(snap?.node?.level ?: level, snap?.x ?: map.x, snap?.y ?: map.y, space, snap?.edge, snap?.node, source, accuracy, now)
    }

    private fun paint() {
        val fix = locator.fix
        val level = fix?.level ?: building.levels().first()
        val space = fix?.room
        val open: (Edge) -> Boolean = { edge ->
            if (edge.sensorIds.isEmpty()) true
            else edge.sensorIds.any { id ->
                val c = corridors.firstOrNull { it.id == id }
                c != null && c.fresh && (c.state == CorridorState.PASSABLE || c.state == CorridorState.DEGRADED)
            }
        }
        val route = locator.route(open)
        binding.plan.level = level
        binding.plan.space = space
        binding.plan.me = fix?.let { P(it.x, it.y) }
        binding.plan.meRadiusM = if (fix?.source == FixSource.ARCORE || fix?.source == FixSource.VISION) 1.5f else (fix?.accuracyM ?: 0f)
        binding.plan.meFaded = false
        binding.plan.route = route?.edges.orEmpty()
        binding.plan.invalidate()
        val place = building.room(space)?.name ?: getString(R.string.view_corridor)
        binding.where.text = when {
            placing -> getString(R.string.locate_place_title)
            fix != null -> getString(R.string.view_where, place, levelName(this, level))
            else -> getString(R.string.view_unknown)
        }
        binding.how.text = when {
            placing -> getString(R.string.locate_place_hint)
            landmarks.isEmpty() -> getString(R.string.locate_no_photos)
            fix?.source == FixSource.ARCORE -> getString(R.string.locate_how_ar)
            fix?.source == FixSource.VISION -> getString(R.string.locate_how_vision)
            fix?.source == FixSource.MANUAL -> getString(R.string.view_how_manual)
            fix?.source == FixSource.STEPS -> getString(R.string.locate_how_steps)
            matching -> getString(R.string.locate_searching)
            else -> getString(R.string.locate_searching)
        }
        binding.route.text = when {
            placing || fix == null -> ""
            route == null -> getString(R.string.map_no_route)
            route.edges.isEmpty() -> getString(R.string.map_at_exit)
            space != null -> getString(R.string.view_route_room, route.metres, route.exit.name)
            else -> getString(R.string.view_route, route.metres, route.exit.name)
        }
        binding.manual.setText(if (placing) android.R.string.cancel else R.string.view_manual)
    }

    private fun startSensors() {
        if (sensorsOn) return
        val manager = getSystemService(SensorManager::class.java)
        val step = manager.getDefaultSensor(Sensor.TYPE_STEP_DETECTOR)
        val turn = manager.getDefaultSensor(Sensor.TYPE_GAME_ROTATION_VECTOR)
            ?: manager.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR)
        listOfNotNull(step, turn).forEach {
            manager.registerListener(this, it, SensorManager.SENSOR_DELAY_GAME)
        }
        sensorsOn = true
    }

    private fun stopSensors() {
        if (sensorsOn) {
            getSystemService(SensorManager::class.java).unregisterListener(this)
            sensorsOn = false
        }
    }

    override fun onSensorChanged(event: SensorEvent) {
        when (event.sensor.type) {
            Sensor.TYPE_GAME_ROTATION_VECTOR, Sensor.TYPE_ROTATION_VECTOR -> {
                SensorManager.getRotationMatrixFromVector(rotation, event.values)
                Locator.forwardHeading(rotation)?.let { heading = it }
            }
            Sensor.TYPE_STEP_DETECTOR -> {
                if (lostAt == 0L) {
                    return
                }
                val fix = locator.fix ?: return
                if (System.currentTimeMillis() - lostAt > PDR_MS) {
                    return
                }
                val h = lastMapHeading
                val nx = fix.x + stepM * sin(h)
                val ny = fix.y + stepM * cos(h)
                applyMap(Se2(nx, ny, h), fix.room, FixSource.STEPS, fix.accuracyM + 0.4f, fix.level)
                paint()
            }
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit

    override fun onSurfaceCreated(gl: GL10?, config: EGLConfig?) {
        GLES20.glClearColor(0f, 0f, 0f, 1f)
        background.create()
        textureBound = false
    }

    override fun onSurfaceChanged(gl: GL10?, width: Int, height: Int) {
        GLES20.glViewport(0, 0, width, height)
        viewW = width
        viewH = height
        geometryDirty = true
    }

    override fun onDrawFrame(gl: GL10?) {
        GLES20.glClear(GLES20.GL_COLOR_BUFFER_BIT or GLES20.GL_DEPTH_BUFFER_BIT)
        val s = session ?: return
        if (!textureBound) {
            s.setCameraTextureName(background.texture)
            textureBound = true
        }
        if (geometryDirty) {
            s.setDisplayGeometry(Surface.ROTATION_0, viewW, viewH)
            geometryDirty = false
        }
        val frame = try {
            s.update()
        } catch (_: Exception) {
            return
        }
        background.draw(frame)
        val ok = frame.camera.trackingState == TrackingState.TRACKING
        val ar = ArPreview.pose(frame)
        val now = System.currentTimeMillis()
        val locked = align
        if (ok && ar != null && locked != null) {
            lostAt = 0L
            lastAr = ar
            val map = locked.compose(ar)
            val due = now - lastMatchAt > RELOC_MS
            if (due) {
                wantMatch = true
            }
            runOnUiThread {
                    applyMap(map, locator.fix?.room, FixSource.ARCORE, 1.5f, locator.fix?.level)
                paint()
            }
        } else if (locked != null) {
            if (lostAt == 0L) {
                lostAt = now
            } else if (now - lostAt > PDR_MS) {
                align = null
                runOnUiThread {
                    locator.fix = null
                    wantMatch = true
                    paint()
                }
            }
        }
        if (ok && ar != null && !placing && !matching && (align == null || wantMatch) && landmarks.isNotEmpty()) {
            matching = true
            wantMatch = false
            val image = try {
                frame.acquireCameraImage()
            } catch (_: Exception) {
                null
            }
            val jpeg = image?.let { img ->
                try {
                    Vision.jpeg(img)
                } finally {
                    img.close()
                }
            }
            val pose = ar
            pool.execute { recognize(jpeg, pose) }
        }
    }

    private fun recognize(jpeg: ByteArray?, ar: Se2) {
        val bitmap = jpeg?.let { BitmapFactory.decodeByteArray(it, 0, it.size) }
        val query = bitmap?.let { Vision.features(it) }
        bitmap?.recycle()
        val hit = if (query != null) {
            Vision.match(query, building.landmarksDir(filesDir), landmarks)
        } else {
            null
        }
        query?.first?.release()
        query?.second?.release()
        runOnUiThread {
            matching = false
            if (hit != null) {
                lastMatchAt = System.currentTimeMillis()
                val map = Se2(hit.x, hit.y, hit.heading)
                val next = Se2.align(map, ar)
                val current = locator.fix
                val jump = current?.let { hypot(it.x - hit.x, it.y - hit.y) } ?: 0f
                if (align == null || current == null || jump < JUMP_M) {
                    align = next
                    applyMap(map, hit.room, FixSource.VISION, 2f, hit.level)
                    paint()
                }
            } else if (align == null) {
                paint()
            }
        }
    }

    companion object {
        private const val RELOC_MS = 5_000L
        private const val PDR_MS = 5_000L
        private const val JUMP_M = 4f
    }
}
