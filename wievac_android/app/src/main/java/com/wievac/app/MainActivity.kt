package com.wievac.app

import android.Manifest
import android.app.NotificationManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.net.Uri
import android.location.Location
import android.location.LocationManager
import android.os.Build
import android.os.Bundle
import android.os.Looper
import android.os.PowerManager
import android.telephony.SmsManager
import android.provider.Settings
import android.view.View
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.EditText
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import com.google.android.material.chip.Chip
import com.google.ar.core.ArCoreApk
import com.wievac.app.databinding.ActivityMainBinding
import com.wievac.app.databinding.ViewCorridorBinding
import com.wievac.app.databinding.ViewSkillBinding
import com.wievac.app.databinding.ViewSosRowBinding
import org.json.JSONArray
import org.json.JSONObject
import kotlin.random.Random

class MainActivity : AppCompatActivity() {
    private lateinit var binding: ActivityMainBinding
    private val sosRows = ArrayList<ViewSosRowBinding>()
    private var pendingRow: ViewSosRowBinding? = null
    private var armSeconds = -1
    private var askedFsi = false
    private var askedOverlay = false
    private var askedBattery = false

    private val notificationPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) {
            advanceArm()
        } else {
            armSeconds = -1
            Toast.makeText(this, R.string.notification_denied, Toast.LENGTH_LONG).show()
        }
    }

    private val sosPermissions = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) { granted ->
        val sms = granted[Manifest.permission.SEND_SMS] == true
        val location = granted[Manifest.permission.ACCESS_FINE_LOCATION] == true ||
            granted[Manifest.permission.ACCESS_COARSE_LOCATION] == true
        if (sms && location) {
            sendSos()
        } else {
            pendingRow = null
            Toast.makeText(this, R.string.sos_need_permission, Toast.LENGTH_LONG).show()
        }
    }

    private var building = Building()
    private var mapLevel = 0

    private val exportMap = registerForActivityResult(
        ActivityResultContracts.CreateDocument("application/json"),
    ) { uri ->
        if (uri != null) {
            try {
                contentResolver.openOutputStream(uri)!!.use { it.write(building.toJson().toByteArray()) }
                Toast.makeText(this, R.string.map_saved, Toast.LENGTH_SHORT).show()
            } catch (_: Exception) {
                Toast.makeText(this, R.string.map_io_failed, Toast.LENGTH_LONG).show()
            }
        }
    }

    private val importMap = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) {
            try {
                val text = contentResolver.openInputStream(uri)!!.use { it.readBytes().decodeToString() }
                building = Building.fromJson(text)
                building.save(Building.file(filesDir))
                mapLevel = building.levels().first()
                showMap()
                showView()
            } catch (_: Exception) {
                Toast.makeText(this, R.string.map_bad_file, Toast.LENGTH_LONG).show()
            }
        }
    }

    private val settingsGate = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) {
        advanceArm()
    }

    private val alarmReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            render()
        }
    }

    private val tick = Runnable { render() }
    private val corridors = sampleCorridors().toMutableList()
    private val rows = ArrayList<ViewCorridorBinding>()
    private val demoTick: Runnable = Runnable {
        for (index in corridors.indices) {
            corridors[index] = roll(corridors[index])
        }
        paint()
        binding.root.postDelayed(demoTick, 3_000)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        armSeconds = savedInstanceState?.getInt(STATE_ARM, -1) ?: -1
        askedFsi = savedInstanceState?.getBoolean(STATE_FSI) ?: false
        askedOverlay = savedInstanceState?.getBoolean(STATE_OVERLAY) ?: false
        askedBattery = savedInstanceState?.getBoolean(STATE_BATTERY) ?: false

        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.seconds.background = null
        WindowCompat.setDecorFitsSystemWindows(window, false)
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { _, insets ->
            val bars = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout(),
            )
            binding.rail.setPadding(bars.left, bars.top, 0, bars.bottom)
            binding.railHandle.setPadding(0, bars.top, 0, bars.bottom)
            binding.scroll.setPadding(0, bars.top, bars.right, 0)
            val density = resources.displayMetrics.density
            val side = (20 * density).toInt()
            binding.bottom.setPadding(
                side,
                (14 * density).toInt(),
                bars.right + side,
                bars.bottom + (14 * density).toInt(),
            )
            val pageSide = (16 * density).toInt()
            binding.pageSituation.setPadding(pageSide, bars.top + pageSide, bars.right + pageSide, bars.bottom + pageSide)
            binding.pageSos.setPadding(side, bars.top + side, bars.right + side, bars.bottom + side)
            binding.pageMap.setPadding(side, bars.top + side, bars.right + side, bars.bottom + side)
            binding.pageView.setPadding(side, bars.top + side, bars.right + side, bars.bottom + side)
            WindowInsetsCompat.CONSUMED
        }
        ViewCompat.requestApplyInsets(binding.root)

        corridors.forEach { corridor ->
            val row = ViewCorridorBinding.inflate(layoutInflater, binding.corridorList, true)
            rows.add(row)
            bindCorridor(row, corridor)
        }
        showBoard(corridors)

        binding.action.setOnClickListener {
            when {
                AlarmService.isRunning -> AlarmService.stop(this)
                AlarmService.scheduledAt(this) > System.currentTimeMillis() -> AlarmService.cancelSchedule(this)
                else -> beginArm()
            }
        }
        binding.tabGuide.setOnClickListener { showPage(0) }
        binding.tabSituation.setOnClickListener { showPage(1) }
        binding.tabSos.setOnClickListener { showPage(2) }
        binding.tabMap.setOnClickListener { showPage(3) }
        binding.tabView.setOnClickListener {
            showPage(4)
            if (building.nodes.isNotEmpty()) {
                startActivity(Intent(this, LocateActivity::class.java))
            }
        }
        setupMap()
        setupView()
        binding.tabHide.setOnClickListener {
            binding.rail.visibility = View.GONE
            binding.railHandle.visibility = View.VISIBLE
        }
        binding.railHandle.setOnClickListener {
            binding.rail.visibility = View.VISIBLE
            binding.railHandle.visibility = View.GONE
        }
        showPage(0)
        binding.pickFire.setOnClickListener { openSection(0) }
        binding.pickQuake.setOnClickListener { openSection(1) }
        binding.pickFlood.setOnClickListener { openSection(2) }
        binding.skillBack.setOnClickListener { showSkillChoices() }
        loadSos()
        binding.sosAdd.setOnClickListener { addSosRow("", getString(R.string.sos_default)) }
        binding.sosSend.setOnClickListener {
            pendingRow = null
            beginSos()
        }
        binding.seconds.setOnEditorActionListener { _, actionId, _ ->
            val idle = !AlarmService.isRunning &&
                AlarmService.scheduledAt(this) <= System.currentTimeMillis()
            if (actionId == EditorInfo.IME_ACTION_DONE && idle) {
                beginArm()
                true
            } else {
                false
            }
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putInt(STATE_ARM, armSeconds)
        outState.putBoolean(STATE_FSI, askedFsi)
        outState.putBoolean(STATE_OVERLAY, askedOverlay)
        outState.putBoolean(STATE_BATTERY, askedBattery)
    }

    override fun onStart() {
        super.onStart()
        ContextCompat.registerReceiver(
            this,
            alarmReceiver,
            IntentFilter(AlarmService.ACTION_CHANGED),
            ContextCompat.RECEIVER_NOT_EXPORTED,
        )
        render()
        building = Building.load(Building.file(filesDir))
        showMap()
        showView()
        binding.root.removeCallbacks(demoTick)
        binding.root.postDelayed(demoTick, 3_000)
    }

    override fun onStop() {
        saveSos()
        binding.root.removeCallbacks(tick)
        binding.root.removeCallbacks(demoTick)
        unregisterReceiver(alarmReceiver)
        super.onStop()
    }

    private fun showPage(page: Int) {
        binding.pageGuide.visibility = if (page == 0) View.VISIBLE else View.GONE
        binding.pageSituation.visibility = if (page == 1) View.VISIBLE else View.GONE
        binding.pageSos.visibility = if (page == 2) View.VISIBLE else View.GONE
        binding.pageMap.visibility = if (page == 3) View.VISIBLE else View.GONE
        binding.pageView.visibility = if (page == 4) View.VISIBLE else View.GONE
        val tabs = listOf(binding.tabGuide, binding.tabSituation, binding.tabSos, binding.tabMap, binding.tabView)
        val on = ContextCompat.getColor(this, R.color.passable)
        val off = ContextCompat.getColor(this, R.color.card)
        val inkOn = ContextCompat.getColor(this, R.color.on_passable)
        val inkOff = ContextCompat.getColor(this, R.color.muted)
        tabs.forEachIndexed { index, tab ->
            tab.setBackgroundColor(if (index == page) on else off)
            tab.setTextColor(if (index == page) inkOn else inkOff)
        }
    }

    private fun skillSections(): List<Pair<Int, List<Pair<Int, Int>>>> {
        return listOf(
            R.string.fire to listOf(
                R.drawable.fire_low to R.string.fire_1,
                R.drawable.fire_door to R.string.fire_2,
                R.drawable.fire_feel to R.string.fire_3,
                R.drawable.fire_stairs to R.string.fire_4,
                R.drawable.fire_leave to R.string.fire_5,
                R.drawable.fire_out to R.string.fire_6,
            ),
            R.string.quake to listOf(
                R.drawable.quake_table to R.string.quake_1,
                R.drawable.quake_glass to R.string.quake_2,
                R.drawable.quake_still to R.string.quake_3,
                R.drawable.quake_frame to R.string.quake_6,
                R.drawable.quake_open to R.string.quake_4,
                R.drawable.quake_wire to R.string.quake_5,
            ),
            R.string.flood to listOf(
                R.drawable.flood_power to R.string.flood_1,
                R.drawable.flood_up to R.string.flood_2,
                R.drawable.flood_current to R.string.flood_3,
                R.drawable.flood_electric to R.string.flood_4,
                R.drawable.flood_high to R.string.flood_5,
                R.drawable.flood_roof to R.string.flood_6,
            ),
        )
    }

    private fun showSkillChoices() {
        binding.skillFeed.removeAllViews()
        binding.sitChoices.visibility = View.VISIBLE
        binding.skillBack.visibility = View.GONE
    }

    private fun openSection(index: Int) {
        binding.sitChoices.visibility = View.GONE
        binding.skillBack.visibility = View.VISIBLE
        binding.skillFeed.removeAllViews()
        skillSections()[index].second.forEach { (image, caption) ->
            val card = ViewSkillBinding.inflate(layoutInflater, binding.skillFeed, true)
            card.photo.setImageResource(image)
            card.caption.setText(caption)
        }
    }

    private fun loadSos() {
        val prefs = getSharedPreferences(SOS_PREFS, MODE_PRIVATE)
        val stored = prefs.getString("rows", null)
        val array = if (stored.isNullOrBlank()) {
            val legacyNumber = prefs.getString("number", "").orEmpty()
            val legacyMessage = prefs.getString("message", "").orEmpty()
            JSONArray().put(
                JSONObject()
                    .put("n", legacyNumber)
                    .put("m", legacyMessage.ifBlank { getString(R.string.sos_default) }),
            )
        } else {
            JSONArray(stored)
        }
        for (index in 0 until array.length()) {
            val item = array.getJSONObject(index)
            addSosRow(item.optString("n"), item.optString("m"))
        }
        if (sosRows.isEmpty()) {
            addSosRow("", getString(R.string.sos_default))
        }
    }

    private fun addSosRow(number: String, message: String) {
        val row = ViewSosRowBinding.inflate(layoutInflater, binding.sosList, true)
        row.number.setText(number)
        row.message.setText(message)
        row.remove.setOnClickListener {
            binding.sosList.removeView(row.root)
            sosRows.remove(row)
            if (pendingRow == row) {
                pendingRow = null
            }
            if (sosRows.isEmpty()) {
                addSosRow("", getString(R.string.sos_default))
            }
        }
        row.send.setOnClickListener {
            pendingRow = row
            beginSos()
        }
        sosRows.add(row)
    }

    private fun contacts(): List<Pair<String, String>> {
        val rows = pendingRow?.let { listOf(it) } ?: sosRows
        return rows.map { row ->
            row.number.text.toString().trim() to
                row.message.text.toString().trim().ifBlank { getString(R.string.sos_default) }
        }.filter { it.first.length >= 8 }
    }

    private fun saveSos() {
        val array = JSONArray()
        sosRows.forEach { row ->
            array.put(
                JSONObject()
                    .put("n", row.number.text.toString().trim())
                    .put("m", row.message.text.toString().trim()),
            )
        }
        getSharedPreferences(SOS_PREFS, MODE_PRIVATE).edit().putString("rows", array.toString()).apply()
    }

    private fun beginSos() {
        saveSos()
        if (contacts().isEmpty()) {
            pendingRow = null
            Toast.makeText(this, R.string.sos_need_number, Toast.LENGTH_SHORT).show()
            return
        }
        val smsOk = ContextCompat.checkSelfPermission(this, Manifest.permission.SEND_SMS) ==
            PackageManager.PERMISSION_GRANTED
        val locOk = ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED ||
            ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_COARSE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED
        if (!smsOk || !locOk) {
            sosPermissions.launch(
                arrayOf(
                    Manifest.permission.SEND_SMS,
                    Manifest.permission.ACCESS_FINE_LOCATION,
                    Manifest.permission.ACCESS_COARSE_LOCATION,
                ),
            )
            return
        }
        sendSos()
    }

    private fun sendSos() {
        val location = lastLocation()
        if (location == null) {
            askFix()
            return
        }
        deliver(location)
    }

    private fun lastLocation(): Location? {
        val manager = getSystemService(LocationManager::class.java)
        return listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER).firstNotNullOfOrNull { provider ->
            try {
                manager.getLastKnownLocation(provider)
            } catch (_: SecurityException) {
                null
            }
        }
    }

    private fun askFix() {
        val manager = getSystemService(LocationManager::class.java)
        val provider = when {
            manager.isProviderEnabled(LocationManager.GPS_PROVIDER) -> LocationManager.GPS_PROVIDER
            manager.isProviderEnabled(LocationManager.NETWORK_PROVIDER) -> LocationManager.NETWORK_PROVIDER
            else -> null
        }
        if (provider == null) {
            pendingRow = null
            Toast.makeText(this, R.string.sos_no_location, Toast.LENGTH_LONG).show()
            return
        }
        if (Build.VERSION.SDK_INT >= 30) {
            manager.getCurrentLocation(provider, null, mainExecutor) { location ->
                if (location == null) {
                    pendingRow = null
                    Toast.makeText(this, R.string.sos_no_location, Toast.LENGTH_LONG).show()
                } else {
                    deliver(location)
                }
            }
        } else {
            @Suppress("DEPRECATION")
            manager.requestSingleUpdate(provider, { location -> deliver(location) }, Looper.getMainLooper())
        }
    }

    private fun deliver(location: Location) {
        val link = "https://maps.google.com/?q=${location.latitude},${location.longitude}"
        val sms = if (Build.VERSION.SDK_INT >= 31) {
            getSystemService(SmsManager::class.java)
        } else {
            @Suppress("DEPRECATION")
            SmsManager.getDefault()
        }
        var sent = 0
        contacts().forEach { (number, note) ->
            try {
                val text = "$note\n$link"
                val parts = sms.divideMessage(text)
                if (parts.size <= 1) {
                    sms.sendTextMessage(number, null, text, null, null)
                } else {
                    sms.sendMultipartTextMessage(number, null, parts, null, null)
                }
                sent += 1
            } catch (_: Exception) {
            }
        }
        pendingRow = null
        val message = if (sent == 0) R.string.sos_failed else R.string.sos_sent
        Toast.makeText(this, message, Toast.LENGTH_LONG).show()
    }

    private fun beginArm() {
        val seconds = binding.seconds.text.toString().trim().toIntOrNull()
        if (seconds == null || seconds !in 1..3600) {
            Toast.makeText(this, R.string.seconds_invalid, Toast.LENGTH_SHORT).show()
            return
        }
        getSystemService(InputMethodManager::class.java)
            .hideSoftInputFromWindow(binding.seconds.windowToken, 0)
        armSeconds = seconds
        askedFsi = false
        askedOverlay = false
        askedBattery = false
        advanceArm()
    }

    private fun advanceArm() {
        val seconds = armSeconds
        if (seconds < 1) {
            return
        }
        if (needsNotification()) {
            notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
            return
        }
        if (needsFullScreen() && !askedFsi) {
            askedFsi = true
            Toast.makeText(this, R.string.need_fullscreen, Toast.LENGTH_LONG).show()
            if (openSettings(Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT)) {
                return
            }
        }
        if (!Settings.canDrawOverlays(this) && !askedOverlay) {
            askedOverlay = true
            Toast.makeText(this, R.string.need_overlay, Toast.LENGTH_LONG).show()
            if (openSettings(Settings.ACTION_MANAGE_OVERLAY_PERMISSION)) {
                return
            }
        }
        if (needsBattery() && !askedBattery) {
            askedBattery = true
            if (openSettings(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)) {
                return
            }
        }
        armSeconds = -1
        if (!AlarmService.schedule(this, seconds)) {
            Toast.makeText(this, R.string.alarm_failed, Toast.LENGTH_LONG).show()
        }
        render()
    }

    private fun openSettings(action: String): Boolean {
        return try {
            settingsGate.launch(Intent(action, Uri.parse("package:$packageName")))
            true
        } catch (_: Exception) {
            false
        }
    }

    private fun needsNotification(): Boolean {
        return Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
    }

    private fun needsFullScreen(): Boolean {
        return Build.VERSION.SDK_INT >= 34 &&
            !getSystemService(NotificationManager::class.java).canUseFullScreenIntent()
    }

    private fun needsBattery(): Boolean {
        return !getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(packageName)
    }

    private fun render() {
        val running = AlarmService.isRunning
        val at = AlarmService.scheduledAt(this)
        val leftMs = at - System.currentTimeMillis()
        val scheduled = !running && leftMs > 0
        binding.seconds.isEnabled = !running && !scheduled
        binding.action.setText(
            when {
                running -> R.string.stop_test
                scheduled -> R.string.cancel_schedule
                else -> R.string.arm_alarm
            },
        )
        if (scheduled) {
            val left = ((leftMs + 999) / 1000).toInt().coerceAtLeast(1)
            binding.hint.text = getString(R.string.scheduled_hint, left)
        } else {
            binding.hint.setText(if (running) R.string.running_hint else R.string.test_hint)
        }
        binding.root.removeCallbacks(tick)
        if (scheduled) {
            binding.root.postDelayed(tick, 500)
        }
    }

    private fun paint() {
        showBoard(corridors)
        corridors.forEachIndexed { index, corridor -> bindCorridor(rows[index], corridor) }
        showMap()
        showView()
    }

    private fun setupView() {
        binding.viewPlan.allSpaces = true
        binding.viewPlan.edgeColor = { edge ->
            edgeState(edge)?.let(::stateColor) ?: ContextCompat.getColor(this, R.color.muted)
        }
        binding.viewLegend.text = PlanView.legend(this, withMe = false)
        binding.viewAuto.setOnClickListener {
            startActivity(Intent(this, LocateActivity::class.java))
        }
        binding.viewManual.setOnClickListener {
            startActivity(Intent(this, LocateActivity::class.java))
        }
    }

    private fun showView() {
        val shots = building.loadLandmarks(filesDir).size
        binding.viewPlan.building = building
        binding.viewPlan.level = building.levels().first()
        binding.viewPlan.me = null
        binding.viewPlan.invalidate()
        binding.viewWhere.text = if (building.nodes.isEmpty()) {
            getString(R.string.view_no_map_title)
        } else {
            getString(R.string.locate_ready_title)
        }
        binding.viewHow.text = when {
            building.nodes.isEmpty() -> getString(R.string.view_no_map)
            shots == 0 -> getString(R.string.locate_no_photos)
            else -> getString(R.string.locate_ready_body, shots)
        }
        binding.viewPlace.visibility = View.GONE
        binding.viewRoute.text = ""
        binding.viewAuto.setText(R.string.locate_open)
        binding.viewManual.visibility = View.GONE
    }

    private fun setupMap() {
        binding.plan.edgeColor = { edge -> edgeState(edge)?.let(::stateColor) ?: ContextCompat.getColor(this, R.color.muted) }
        binding.mapLegend.text = PlanView.legend(this, withMe = false)
        binding.plan.onTapNode = ::nodeMenu
        binding.plan.onTapEdge = ::edgeMenu
        binding.levelDown.setOnClickListener { stepLevel(-1) }
        binding.levelUp.setOnClickListener { stepLevel(1) }
        binding.surveyAr.isEnabled = ArCoreApk.getInstance().checkAvailability(this) !=
            ArCoreApk.Availability.UNSUPPORTED_DEVICE_NOT_CAPABLE
        binding.surveyAr.setOnClickListener { startSurvey(true) }
        binding.surveyWalk.setOnClickListener { startSurvey(false) }
        binding.mapExport.setOnClickListener { exportMap.launch("wievac-building.json") }
        binding.mapImport.setOnClickListener { importMap.launch(arrayOf("*/*")) }
    }

    /** Worst live state among the corridors linked to this edge; null = edge not monitored. */
    private fun edgeState(edge: Edge): CorridorState? {
        if (edge.sensorIds.isEmpty()) {
            return null
        }
        val severity = listOf(CorridorState.PASSABLE, CorridorState.DEGRADED, CorridorState.UNKNOWN, CorridorState.BLOCKED)
        return edge.sensorIds.map { id ->
            corridors.firstOrNull { it.id == id }?.takeIf { it.fresh }?.state ?: CorridorState.UNKNOWN
        }.maxBy { severity.indexOf(it) }
    }

    private fun edgeOpen(edge: Edge): Boolean {
        val state = edgeState(edge)
        return state == null || state == CorridorState.PASSABLE || state == CorridorState.DEGRADED
    }

    private fun showMap() {
        val levels = building.levels()
        if (mapLevel !in levels) {
            mapLevel = levels.first()
        }
        binding.levelName.text = levelName(this, mapLevel)
        binding.plan.building = building
        binding.plan.level = mapLevel
        binding.plan.invalidate()
        binding.mapHint.text = if (building.nodes.isEmpty()) {
            getString(R.string.map_empty)
        } else {
            getString(R.string.map_edit_hint, building.nodes.size, building.rooms.size, building.fingerprints.size)
        }
    }

    private fun stepLevel(delta: Int) {
        val levels = building.levels()
        val index = levels.indexOf(mapLevel) + delta
        if (index in levels.indices) {
            mapLevel = levels[index]
            showMap()
        }
    }

    private fun saveMap() {
        building.save(Building.file(filesDir))
        showMap()
    }

    private fun startSurvey(ar: Boolean) {
        val go = {
            startActivity(Intent(this, SurveyActivity::class.java).putExtra(SurveyActivity.EXTRA_AR, ar))
        }
        if (building.nodes.isEmpty()) {
            go()
            return
        }
        AlertDialog.Builder(this)
            .setMessage(R.string.map_replace)
            .setPositiveButton(R.string.map_replace_ok) { _, _ -> go() }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun nodeMenu(node: Node) {
        val items = arrayOf(getString(R.string.map_rename), getString(R.string.map_delete))
        AlertDialog.Builder(this)
            .setTitle(node.name)
            .setItems(items) { _, which ->
                when (which) {
                    0 -> rename(node)
                    else -> {
                        building.removeNode(node.id)
                        saveMap()
                    }
                }
            }
            .show()
    }

    private fun rename(node: Node) {
        val field = EditText(this).apply { setText(node.name) }
        AlertDialog.Builder(this)
            .setTitle(R.string.map_rename)
            .setView(field)
            .setPositiveButton(android.R.string.ok) { _, _ ->
                node.name = field.text.toString().trim().ifBlank { node.name }
                saveMap()
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun edgeMenu(edge: Edge) {
        AlertDialog.Builder(this)
            .setTitle(getString(R.string.map_edge_title, edge.lengthM))
            .setItems(arrayOf(getString(R.string.map_link), getString(R.string.map_delete))) { _, which ->
                if (which == 0) {
                    linkEdge(edge)
                } else {
                    building.edges.remove(edge)
                    saveMap()
                }
            }
            .show()
    }

    private fun linkEdge(edge: Edge) {
        val labels = corridors.map { "${it.name} (${it.id})" }.toTypedArray()
        val checked = corridors.map { it.id in edge.sensorIds }.toBooleanArray()
        AlertDialog.Builder(this)
            .setTitle(R.string.map_link)
            .setMultiChoiceItems(labels, checked) { _, which, on -> checked[which] = on }
            .setPositiveButton(android.R.string.ok) { _, _ ->
                edge.sensorIds.clear()
                corridors.forEachIndexed { index, corridor -> if (checked[index]) edge.sensorIds.add(corridor.id) }
                saveMap()
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun roll(corridor: Corridor): Corridor {
        val state = CorridorState.entries.random()
        val fresh = Random.nextInt(5) != 0
        val score = when {
            !fresh || state == CorridorState.UNKNOWN -> null
            state == CorridorState.PASSABLE -> Random.nextInt(70, 96)
            state == CorridorState.DEGRADED -> Random.nextInt(40, 70)
            else -> Random.nextInt(5, 40)
        }
        return corridor.copy(state = state, score = score, fresh = fresh)
    }

    private fun showBoard(corridors: List<Corridor>) {
        val go = corridors.filter { it.canEnter() }
        val avoid = corridors.filter { !it.canEnter() }
        val open = go.isNotEmpty()
        binding.hero.setBackgroundResource(if (open) R.drawable.bg_hero_go else R.drawable.bg_hero_stop)
        binding.noWay.visibility = if (open) View.GONE else View.VISIBLE
        fillChips(binding.goChips, go, R.color.passable, R.color.on_passable)
        fillChips(binding.avoidChips, avoid, R.color.blocked, R.color.white)
        binding.avoidChips.visibility = if (avoid.isEmpty()) View.GONE else View.VISIBLE
    }

    private fun fillChips(group: com.google.android.material.chip.ChipGroup, lanes: List<Corridor>, background: Int, ink: Int) {
        group.removeAllViews()
        val bg = ContextCompat.getColor(this, background)
        val fg = ContextCompat.getColor(this, ink)
        lanes.forEach { lane ->
            group.addView(Chip(this).apply {
                text = lane.name
                isClickable = false
                isCheckable = false
                chipBackgroundColor = ColorStateList.valueOf(bg)
                setTextColor(fg)
                chipStrokeWidth = 0f
                chipCornerRadius = 14f * resources.displayMetrics.density
                chipMinHeight = 44f * resources.displayMetrics.density
                textSize = 16f
            })
        }
    }

    private fun bindCorridor(row: ViewCorridorBinding, corridor: Corridor) {
        val live = corridor.fresh
        val color = stateColor(if (live) corridor.state else CorridorState.UNKNOWN)
        val ink = when {
            !live || corridor.state == CorridorState.UNKNOWN -> ContextCompat.getColor(this, R.color.text)
            corridor.state == CorridorState.PASSABLE -> ContextCompat.getColor(this, R.color.on_passable)
            corridor.state == CorridorState.DEGRADED -> ContextCompat.getColor(this, R.color.on_degraded)
            else -> ContextCompat.getColor(this, R.color.white)
        }
        row.name.text = corridor.name
        row.name.alpha = if (live) 1f else 0.4f
        row.name.backgroundTintList = ColorStateList.valueOf(color)
        row.name.setTextColor(ink)
    }

    private fun stateColor(state: CorridorState): Int {
        val id = when (state) {
            CorridorState.PASSABLE -> R.color.passable
            CorridorState.DEGRADED -> R.color.degraded
            CorridorState.BLOCKED -> R.color.blocked
            CorridorState.UNKNOWN -> R.color.unknown
        }
        return ContextCompat.getColor(this, id)
    }

    companion object {
        private const val STATE_ARM = "arm"
        private const val STATE_FSI = "asked_fsi"
        private const val STATE_OVERLAY = "asked_overlay"
        private const val STATE_BATTERY = "asked_battery"
        private const val SOS_PREFS = "wievac_sos"
        private const val MANUAL_HOLD_MS = 60_000L
        private const val REFIX_M = 8f
    }
}
