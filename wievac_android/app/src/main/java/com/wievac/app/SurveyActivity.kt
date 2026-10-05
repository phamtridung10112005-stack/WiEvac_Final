package com.wievac.app

import android.Manifest
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.opengl.GLES20
import android.opengl.GLSurfaceView
import android.os.Build
import android.os.Bundle
import android.text.InputType
import android.text.SpannableStringBuilder
import android.text.style.ForegroundColorSpan
import android.text.style.RelativeSizeSpan
import android.view.Gravity
import android.view.Surface
import android.view.View
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.addCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.google.android.material.button.MaterialButton
import com.google.ar.core.ArCoreApk
import com.google.ar.core.Config
import com.google.ar.core.Plane
import com.google.ar.core.Session
import com.google.ar.core.TrackingState
import com.wievac.app.databinding.ActivitySurveyBinding
import java.io.File
import javax.microedition.khronos.egl.EGLConfig
import javax.microedition.khronos.opengles.GL10
import kotlin.math.abs
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.sin

/**
 * Walk the building once and record a 2D plan + graph. ARCore gives the phone pose when
 * available; otherwise steps + heading (PDR). One continuous session per building: a new
 * session has its own origin and heading, so it cannot be merged into an old map.
 */
class SurveyActivity : AppCompatActivity(), GLSurfaceView.Renderer, SensorEventListener {
    // ponytail: no multi-session merge; add two-node alignment when one walk can't cover a building.
    private lateinit var binding: ActivitySurveyBinding
    private lateinit var building: Building
    private lateinit var recorder: SurveyRecorder
    private var ar = false
    private var note = ""
    private var wallMode = false
    private var levelAsk = 0

    @Volatile private var session: Session? = null
    @Volatile private var wantHit = false
    @Volatile private var geometryDirty = false
    private var installAsked = false
    private var textureBound = false
    private var viewW = 0
    private var viewH = 0
    private var tracking = false
    private val background = ArPreview()
    private val vision = ArrayList<Landmark>()
    private val visionPool = java.util.concurrent.Executors.newSingleThreadExecutor()
    @Volatile private var wantVision = false
    @Volatile private var snapping = false

    private var sensorsOn = false
    private var asked = false
    private val rotation = FloatArray(9)
    private var heading = 0f
    private var rawX = 0f
    private var rawY = 0f
    private var altitude = Float.NaN
    private var levelBase = Float.NaN
    private var stepM = DEFAULT_STEP_M
    private lateinit var tracker: Tracker

    private val permissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        if (ar && !granted(Manifest.permission.CAMERA)) {
            Toast.makeText(this, R.string.survey_no_camera, Toast.LENGTH_LONG).show()
            toPdr()
            asked = false
        } else if (!tracker.canScan()) {
            Toast.makeText(this, R.string.survey_no_wifi, Toast.LENGTH_LONG).show()
        }
    }

    private fun granted(permission: String) =
        ContextCompat.checkSelfPermission(this, permission) == PackageManager.PERMISSION_GRANTED

    private fun missing(): Array<String> {
        return listOfNotNull(
            if (ar) Manifest.permission.CAMERA else null,
            if (!ar && Build.VERSION.SDK_INT >= 29) Manifest.permission.ACTIVITY_RECOGNITION else null,
            Manifest.permission.ACCESS_FINE_LOCATION,
            if (Build.VERSION.SDK_INT >= 33) Manifest.permission.NEARBY_WIFI_DEVICES else null,
        ).filterNot(::granted).toTypedArray()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySurveyBinding.inflate(layoutInflater)
        setContentView(binding.root)
        building = if (savedInstanceState == null) {
            Building().also { it.clearLandmarks(filesDir) }
        } else {
            Building.load(Building.file(filesDir))
        }
        ar = intent.getBooleanExtra(EXTRA_AR, false)
        recorder = SurveyRecorder(building, SOURCE_AR)
        stepM = getSharedPreferences(MAP_PREFS, MODE_PRIVATE).getFloat(PREF_STEP, DEFAULT_STEP_M)
        tracker = Tracker(this, steps = false, object : Tracker.Listener {
            override fun onScan(scan: Map<String, Int>) {
                if (!ar || tracking) {
                    recorder.fingerprint(scan)
                    refresh()
                }
            }
        })
        binding.plan.building = building
        binding.legend.text = PlanView.legend(this, withMe = true)
        binding.primary.setOnClickListener { if (wallMode) wallCorner() else pick() }
        binding.wallToggle.setOnClickListener {
            wallMode = !wallMode
            recorder.newWall()
            refresh()
        }
        binding.levelButton.setOnClickListener { askLevel() }
        binding.stepButton.setOnClickListener { editStep() }
        binding.done.setOnClickListener { askFinish() }
        binding.bannerYes.setOnClickListener { answerLevel(true) }
        binding.bannerNo.setOnClickListener { answerLevel(false) }
        onBackPressedDispatcher.addCallback(this) { askFinish() }
        if (ar) {
            binding.surface.preserveEGLContextOnPause = true
            binding.surface.setEGLContextClientVersion(2)
            binding.surface.setEGLConfigChooser(8, 8, 8, 8, 16, 0)
            binding.surface.setRenderer(this)
            binding.surface.renderMode = GLSurfaceView.RENDERMODE_CONTINUOUSLY
        } else {
            toPdr()
        }
        refresh()
    }

    override fun onResume() {
        super.onResume()
        val need = missing()
        if (!asked && need.isNotEmpty()) {
            asked = true
            permissions.launch(need)
            return
        }
        tracker.start()
        if (ar) resumeAr() else startSensors()
    }

    override fun onPause() {
        super.onPause()
        if (ar) {
            binding.surface.onPause()
            session?.pause()
        }
        stopSensors()
        tracker.stop()
        building.save(Building.file(filesDir))
        building.saveLandmarks(filesDir, vision)
    }

    override fun onDestroy() {
        session?.close()
        session = null
        visionPool.shutdownNow()
        super.onDestroy()
    }

    private fun resumeAr() {
        if (!granted(Manifest.permission.CAMERA)) {
            toPdr()
            startSensors()
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
            toPdr()
            startSensors()
            return
        }
        binding.surface.onResume()
    }

    private fun toPdr() {
        if (ar) {
            binding.surface.onPause()
        }
        ar = false
        recorder.source = SOURCE_PDR
        binding.surface.visibility = View.GONE
        binding.spacer.visibility = View.GONE
        binding.stepButton.visibility = View.VISIBLE
        binding.mapCard.layoutParams = (binding.mapCard.layoutParams as LinearLayout.LayoutParams).apply {
            height = 0
            weight = 1f
        }
        binding.plan.layoutParams = (binding.plan.layoutParams as LinearLayout.LayoutParams).apply {
            height = 0
            weight = 1f
        }
        recorder.moveTo(rawX, rawY, 0f)
        refresh()
    }

    private fun label(kind: NodeKind) = getString(kind.label) + " " + (building.nodes.count { it.kind == kind } + 1)

    private fun distance(node: Node) = hypot(node.at.x - recorder.x, node.at.y - recorder.y)

    /** One sheet for every mark: "I'm back at X" options first (closes the loop), then new place types. */
    private fun pick() {
        val sheet = BottomSheetDialog(this)
        val density = resources.displayMetrics.density
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            val pad = (20 * density).toInt()
            setPadding(pad, pad, pad, pad)
        }
        fun heading(text: Int) {
            box.addView(TextView(this).apply {
                setText(text)
                setTextColor(ContextCompat.getColor(context, R.color.text))
                textSize = 17f
                setPadding(0, (8 * density).toInt(), 0, (8 * density).toInt())
            })
        }
        fun option(color: Int, title: String, detail: String, run: () -> Unit) {
            val text = SpannableStringBuilder(title).append("\n")
            val at = text.length
            text.append(detail)
            text.setSpan(RelativeSizeSpan(0.8f), at, text.length, 0)
            text.setSpan(ForegroundColorSpan(ContextCompat.getColor(this, R.color.muted)), at, text.length, 0)
            box.addView(MaterialButton(this, null, com.google.android.material.R.attr.materialButtonOutlinedStyle).apply {
                this.text = text
                isAllCaps = false
                gravity = Gravity.START or Gravity.CENTER_VERTICAL
                textAlignment = View.TEXT_ALIGNMENT_VIEW_START
                setTextColor(ContextCompat.getColor(context, R.color.text))
                textSize = 17f
                cornerRadius = (14 * density).toInt()
                strokeColor = ColorStateList.valueOf(ContextCompat.getColor(context, R.color.line))
                setIconResource(R.drawable.bg_circle)
                iconTint = ColorStateList.valueOf(ContextCompat.getColor(context, color))
                iconSize = (16 * density).toInt()
                iconPadding = (14 * density).toInt()
                minHeight = (64 * density).toInt()
                layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
                setOnClickListener {
                    sheet.dismiss()
                    run()
                    building.save(Building.file(filesDir))
                    refresh()
                }
            })
        }
        val started = recorder.lastNode != null
        val inside = building.room(recorder.room)
        if (inside != null) {
            heading(R.string.pick_room_title)
            option(NodeKind.DOOR.color, getString(R.string.pick_leave, inside.name), getString(R.string.pick_leave_detail)) {
                val door = recorder.leaveRoom()
                note = getString(R.string.survey_left, inside.name, door?.name.orEmpty())
            }
        }
        val old = recorder.candidates().sortedBy(::distance).take(3)
        if (started && old.isNotEmpty()) {
            heading(R.string.pick_old_title)
            old.forEach { node ->
                option(node.kind.color, getString(R.string.pick_old, node.name), getString(R.string.pick_old_detail, distance(node))) {
                    recorder.closeAt(node)
                    note = getString(R.string.survey_joined, node.name)
                }
            }
        }
        heading(if (started) R.string.pick_new_title else R.string.pick_first_title)
        NodeKind.entries.forEach { kind ->
            option(kind.color, getString(kind.label), getString(kind.hint)) {
                val before = building.nodes.size
                val node = recorder.mark(kind, label(kind))
                wantVision = true
                note = getString(if (building.nodes.size > before) R.string.survey_marked else R.string.survey_joined, node.name)
                if (kind == NodeKind.DOOR && recorder.room == null) {
                    askRoom(node)
                }
            }
        }
        sheet.setContentView(ScrollView(this).apply { addView(box) })
        sheet.show()
    }

    private fun askRoom(door: Node) {
        val field = EditText(this).apply {
            setText(getString(R.string.room_default, building.rooms.size + 1))
            setSelectAllOnFocus(true)
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.room_ask)
            .setMessage(R.string.room_ask_body)
            .setView(field)
            .setPositiveButton(R.string.room_enter) { _, _ ->
                val name = field.text.toString().trim().ifBlank { getString(R.string.room_default, building.rooms.size + 1) }
                recorder.enterRoom(door, name)
                note = ""
                building.save(Building.file(filesDir))
                refresh()
            }
            .setNegativeButton(R.string.room_skip, null)
            .show()
    }

    private fun wallCorner() {
        if (ar) {
            wantHit = true
        } else {
            recorder.wallHere()
            building.save(Building.file(filesDir))
            Toast.makeText(this, R.string.wall_added, Toast.LENGTH_SHORT).show()
            refresh()
        }
    }

    private fun askLevel() {
        val up = recorder.level + 1
        val down = recorder.level - 1
        val items = arrayOf(
            getString(R.string.level_go_up, levelName(this, up)),
            getString(R.string.level_go_down, levelName(this, down)),
        )
        AlertDialog.Builder(this)
            .setTitle(getString(R.string.level_now, levelName(this, recorder.level)))
            .setItems(items) { _, which -> moveLevel(if (which == 0) 1 else -1) }
            .show()
    }

    private fun moveLevel(delta: Int) {
        val node = recorder.changeLevel(delta, label(NodeKind.STAIRS))
        levelBase = height()
        levelAsk = 0
        note = getString(R.string.survey_level_done, levelName(this, recorder.level), node.name)
        building.save(Building.file(filesDir))
        refresh()
    }

    private fun answerLevel(yes: Boolean) {
        if (yes) {
            moveLevel(levelAsk)
        } else {
            levelBase = height()
            levelAsk = 0
            refresh()
        }
    }

    private fun askFinish() {
        if (building.nodes.isEmpty()) {
            finish()
            return
        }
        var message = getString(
            R.string.done_summary,
            building.nodes.size,
            building.edges.size,
            building.levels().size,
        )
        if (building.nodes.none { it.kind == NodeKind.EXIT }) {
            message += "\n\n" + getString(R.string.done_no_exit)
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.done_title)
            .setMessage(message)
            .setPositiveButton(R.string.done_save) { _, _ ->
                building.save(Building.file(filesDir))
                finish()
            }
            .setNegativeButton(R.string.done_continue, null)
            .show()
    }

    private fun editStep() {
        val field = EditText(this).apply {
            inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL
            setText(stepM.toString())
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.survey_step)
            .setMessage(R.string.survey_step_help)
            .setView(field)
            .setPositiveButton(android.R.string.ok) { _, _ ->
                val value = field.text.toString().toFloatOrNull()
                if (value != null && value in 0.3f..1.2f) {
                    stepM = value
                    getSharedPreferences(MAP_PREFS, MODE_PRIVATE).edit().putFloat(PREF_STEP, value).apply()
                    refresh()
                }
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun height() = if (ar) recorder.z else altitude

    private fun refresh() {
        val started = recorder.lastNode != null
        val inside = building.room(recorder.room)
        binding.title.text = when {
            wallMode -> getString(R.string.step_wall_title)
            !started -> getString(R.string.step_start_title)
            inside != null -> getString(R.string.step_room_title, inside.name)
            else -> getString(R.string.step_walk_title)
        }
        binding.body.text = when {
            wallMode -> getString(if (ar) R.string.step_wall_body_ar else R.string.step_wall_body_pdr)
            !started -> getString(R.string.step_start_body)
            note.isNotEmpty() -> note
            inside != null -> getString(R.string.step_room_body)
            else -> getString(R.string.step_walk_body)
        }
        val metres = building.edges.sumOf { it.lengthM.toDouble() }
        binding.tally.text = getString(
            R.string.survey_tally,
            levelName(this, recorder.level),
            building.nodes.size,
            metres,
            building.fingerprints.size,
        ) + getString(R.string.survey_tally_vision, vision.size) + if (ar) "" else getString(R.string.survey_tally_step, stepM)

        if (levelBase.isNaN()) {
            levelBase = height()
        }
        val rise = height() - levelBase
        if (levelAsk == 0 && started && !rise.isNaN() && abs(rise) > LEVEL_RISE_M) {
            levelAsk = if (rise > 0) 1 else -1
        }
        val lost = ar && !tracking
        binding.banner.visibility = if (levelAsk != 0 || lost) View.VISIBLE else View.GONE
        binding.bannerActions.visibility = if (levelAsk != 0) View.VISIBLE else View.GONE
        if (levelAsk != 0) {
            binding.bannerText.setText(if (levelAsk > 0) R.string.level_ask_up else R.string.level_ask_down)
            binding.bannerYes.text = getString(R.string.level_yes, levelName(this, recorder.level + levelAsk))
        } else if (lost) {
            binding.bannerText.setText(R.string.survey_ar_lost)
        }

        binding.primary.setText(
            when {
                wallMode -> R.string.primary_wall
                !started -> R.string.primary_start
                else -> R.string.primary_mark
            },
        )
        binding.primary.isEnabled = !lost
        binding.primary.alpha = if (lost) 0.4f else 1f
        binding.wallToggle.setText(if (wallMode) R.string.wall_stop else R.string.wall_start)
        binding.wallToggle.isEnabled = started
        binding.levelButton.isEnabled = started
        binding.crosshair.visibility = if (ar && wallMode) View.VISIBLE else View.GONE
        binding.mapCard.visibility = if (ar && wallMode) View.GONE else View.VISIBLE

        binding.plan.level = recorder.level
        binding.plan.me = P(recorder.x, recorder.y)
        binding.plan.invalidate()
    }

    private fun startSensors() {
        if (sensorsOn) {
            return
        }
        val manager = getSystemService(SensorManager::class.java)
        val step = manager.getDefaultSensor(Sensor.TYPE_STEP_DETECTOR)
        if (step == null) {
            Toast.makeText(this, R.string.survey_no_steps, Toast.LENGTH_LONG).show()
        }
        val turn = manager.getDefaultSensor(Sensor.TYPE_GAME_ROTATION_VECTOR)
            ?: manager.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR)
        listOfNotNull(step, turn, manager.getDefaultSensor(Sensor.TYPE_PRESSURE)).forEach {
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
                if (ar) return
                rawX += stepM * sin(heading)
                rawY += stepM * cos(heading)
                recorder.moveTo(rawX, rawY, 0f)
                if (!tracker.heading.isNaN()) {
                    recorder.north(tracker.heading - heading)
                }
                refresh()
            }
            Sensor.TYPE_PRESSURE -> {
                val metres = SensorManager.getAltitude(SensorManager.PRESSURE_STANDARD_ATMOSPHERE, event.values[0])
                altitude = if (altitude.isNaN()) metres else altitude * 0.9f + metres * 0.1f
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
        val pose = ArPreview.pose(frame)
        val px = pose?.x ?: 0f
        val py = pose?.y ?: 0f
        val pz = if (ok) frame.camera.pose.ty() else 0f
        val facing = pose?.heading
        val grab = wantVision && !snapping && pose != null
        wantVision = false
        if (grab) {
            snapping = true
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
            val heading = pose.heading
            runOnUiThread { keepVision(jpeg, heading) }
        }
        val askedHit = wantHit
        wantHit = false
        val hit = if (askedHit) {
            frame.hitTest(viewW / 2f, viewH / 2f).firstOrNull {
                val plane = it.trackable
                plane is Plane && plane.type == Plane.Type.HORIZONTAL_UPWARD_FACING && plane.isPoseInPolygon(it.hitPose)
            }?.hitPose?.let { floatArrayOf(it.tx(), -it.tz()) }
        } else {
            null
        }
        runOnUiThread {
            if (pose != null) {
                recorder.moveTo(px, py, pz)
            }
            if (facing != null && !tracker.heading.isNaN()) {
                recorder.north(tracker.heading - facing)
            }
            tracking = ok
            if (recorder.visionDue()) {
                wantVision = true
            }
            if (askedHit) {
                if (hit == null) {
                    Toast.makeText(this, R.string.survey_no_floor, Toast.LENGTH_SHORT).show()
                } else {
                    recorder.wallAtRaw(hit[0], hit[1])
                    building.save(Building.file(filesDir))
                    Toast.makeText(this, R.string.wall_added, Toast.LENGTH_SHORT).show()
                }
            }
            refresh()
        }
    }

    private fun keepVision(jpeg: ByteArray?, heading: Float) {
        snapping = false
        if (jpeg == null || recorder.lastNode == null) {
            return
        }
        val dir = building.landmarksDir(filesDir)
        dir.mkdirs()
        val id = "v${vision.size + 1}"
        val file = "$id.jpg"
        File(dir, file).writeBytes(jpeg)
        vision.add(Landmark(id, file, recorder.level, recorder.x, recorder.y, heading, recorder.room))
        building.saveLandmarks(filesDir, vision)
        refresh()
    }

    companion object {
        const val EXTRA_AR = "ar"
        private const val SOURCE_AR = "arcore"
        private const val SOURCE_PDR = "pdr"
        private const val LEVEL_RISE_M = 2.2f
    }
}
