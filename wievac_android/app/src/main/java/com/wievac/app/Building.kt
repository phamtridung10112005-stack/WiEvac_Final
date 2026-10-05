package com.wievac.app

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.UUID
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.roundToInt
import kotlin.math.sin

enum class NodeKind(val key: String, val label: Int, val hint: Int, val color: Int) {
    JUNCTION("junction", R.string.node_junction, R.string.hint_junction, R.color.text),
    DOOR("door", R.string.node_door, R.string.hint_door, R.color.water),
    STAIRS("stairs", R.string.node_stairs, R.string.hint_stairs, R.color.degraded),
    EXIT("exit", R.string.node_exit, R.string.hint_exit, R.color.passable),
}

class P(var x: Float, var y: Float)

/** [room] null = corridor. A room's door stays in the corridor so both views show it. */
class Node(val id: String, val level: Int, val at: P, val kind: NodeKind, var name: String, val room: String? = null)

class Edge(
    val id: String,
    val from: String,
    val to: String,
    val lengthM: Float,
    val source: String,
    val sensorIds: MutableList<String> = ArrayList(),
) {
    fun other(id: String) = if (from == id) to else from
}

class Line(val level: Int, val room: String? = null, val points: MutableList<P> = ArrayList())

class Room(val id: String, var name: String, val door: String)

/** Wi-Fi seen at one surveyed spot: BSSID -> RSSI dBm. */
class Fingerprint(val level: Int, val at: P, val room: String?, val rssi: Map<String, Int>)

class Building(val id: String = UUID.randomUUID().toString()) {
    val nodes = ArrayList<Node>()
    val edges = ArrayList<Edge>()
    val walls = ArrayList<Line>()
    val tracks = ArrayList<Line>()
    val rooms = ArrayList<Room>()
    val fingerprints = ArrayList<Fingerprint>()

    /** Magnetic heading minus map heading (radians); null = never measured. */
    var northOffset: Float? = null

    fun node(id: String?) = nodes.firstOrNull { it.id == id }

    fun room(id: String?) = rooms.firstOrNull { it.id == id }

    fun isEmpty() = nodes.isEmpty() && walls.isEmpty() && tracks.isEmpty()

    fun levels(): List<Int> {
        return (nodes.map { it.level } + walls.map { it.level } + tracks.map { it.level })
            .distinct().sorted().ifEmpty { listOf(0) }
    }

    /** Room an edge belongs to: the room of whichever end is inside one; null = corridor. */
    fun spaceOf(edge: Edge): String? = node(edge.from)?.room ?: node(edge.to)?.room

    fun visible(node: Node, space: String?): Boolean {
        return node.room == space || (space != null && room(space)?.door == node.id)
    }

    fun visible(edge: Edge, space: String?): Boolean {
        val a = node(edge.from) ?: return false
        val b = node(edge.to) ?: return false
        return visible(a, space) && visible(b, space) && spaceOf(edge) == space
    }

    fun freshId(prefix: String): String {
        val taken = (nodes.map { it.id } + edges.map { it.id } + rooms.map { it.id }).toHashSet()
        return generateSequence(1) { it + 1 }.map { "$prefix$it" }.first { it !in taken }
    }

    fun link(a: String, b: String, lengthM: Float, source: String) {
        if (a == b || edges.any { (it.from == a && it.to == b) || (it.from == b && it.to == a) }) {
            return
        }
        edges.add(Edge(freshId("e"), a, b, lengthM, source))
    }

    fun removeNode(id: String) {
        nodes.removeAll { it.id == id }
        edges.removeAll { it.from == id || it.to == id }
    }

    /** Shortest path to the nearest EXIT over edges that [open] allows; null = no route. */
    fun route(start: String, open: (Edge) -> Boolean): List<Edge>? {
        // ponytail: O(n²) Dijkstra, fine for a few hundred nodes per building.
        val dist = hashMapOf(start to 0f)
        val via = HashMap<String, Edge>()
        val done = HashSet<String>()
        while (true) {
            val here = dist.filterKeys { it !in done }.minByOrNull { it.value }?.key ?: return null
            if (node(here)?.kind == NodeKind.EXIT) {
                val path = ArrayList<Edge>()
                var at = here
                while (at != start) {
                    val edge = via.getValue(at)
                    path.add(edge)
                    at = edge.other(at)
                }
                return path.asReversed()
            }
            done.add(here)
            edges.filter { (it.from == here || it.to == here) && open(it) }.forEach { edge ->
                val next = edge.other(here)
                val cost = dist.getValue(here) + edge.lengthM
                if (next !in done && cost < (dist[next] ?: Float.MAX_VALUE)) {
                    dist[next] = cost
                    via[next] = edge
                }
            }
        }
    }

    fun toJson(): String {
        fun num(v: Float) = (v * 100).roundToInt() / 100.0
        fun lines(list: List<Line>) = JSONArray().apply {
            list.filter { it.points.isNotEmpty() }.forEach { line ->
                put(JSONObject().put("level", line.level).put("room", line.room).put("coords", JSONArray().apply {
                    line.points.forEach { put(JSONArray().put(num(it.x)).put(num(it.y))) }
                }))
            }
        }
        return JSONObject()
            .put("schema", SCHEMA)
            .put("buildingId", id)
            .put("crs", "local-meters")
            .put("northOffsetDeg", northOffset?.let { Math.toDegrees(it.toDouble()) })
            .put("nodes", JSONArray().apply {
                nodes.forEach {
                    put(JSONObject().put("id", it.id).put("level", it.level).put("x", num(it.at.x))
                        .put("y", num(it.at.y)).put("kind", it.kind.key).put("name", it.name).put("room", it.room))
                }
            })
            .put("edges", JSONArray().apply {
                edges.forEach {
                    val stairs = node(it.from)?.level != node(it.to)?.level
                    put(JSONObject().put("id", it.id).put("from", it.from).put("to", it.to)
                        .put("kind", if (stairs) "stairs" else "corridor")
                        .put("lengthM", num(it.lengthM)).put("lengthSource", it.source)
                        .put("bidirectional", true).put("sensorIds", JSONArray(it.sensorIds)))
                }
            })
            .put("rooms", JSONArray().apply {
                rooms.forEach { put(JSONObject().put("id", it.id).put("name", it.name).put("door", it.door)) }
            })
            .put("walls", lines(walls))
            .put("tracks", lines(tracks))
            .put("fingerprints", JSONArray().apply {
                fingerprints.forEach { fp ->
                    put(JSONObject().put("level", fp.level).put("x", num(fp.at.x)).put("y", num(fp.at.y))
                        .put("room", fp.room).put("ap", JSONObject(fp.rssi as Map<*, *>)))
                }
            })
            .toString(1)
    }

    fun save(file: File) {
        val part = File(file.parentFile, file.name + ".part")
        part.writeText(toJson())
        part.renameTo(file)
    }

    companion object {
        const val SCHEMA = "wievac-building-1"

        fun file(dir: File) = File(dir, "building.json")

        fun load(file: File): Building {
            return try {
                if (file.exists()) fromJson(file.readText()) else Building()
            } catch (_: Exception) {
                Building()
            }
        }

        private fun JSONObject.text(key: String): String? = if (isNull(key)) null else optString(key).ifEmpty { null }

        /** Throws on wrong schema so a bad import never silently replaces the map. */
        fun fromJson(text: String): Building {
            val root = JSONObject(text)
            require(root.optString("schema") == SCHEMA) { "schema" }
            val building = root.text("buildingId")?.let(::Building) ?: Building()
            if (!root.isNull("northOffsetDeg") && root.has("northOffsetDeg")) {
                building.northOffset = Math.toRadians(root.getDouble("northOffsetDeg")).toFloat()
            }
            val nodes = root.getJSONArray("nodes")
            for (i in 0 until nodes.length()) {
                val o = nodes.getJSONObject(i)
                val kind = NodeKind.entries.firstOrNull { it.key == o.getString("kind") } ?: NodeKind.JUNCTION
                building.nodes.add(
                    Node(o.getString("id"), o.getInt("level"),
                        P(o.getDouble("x").toFloat(), o.getDouble("y").toFloat()), kind, o.optString("name"),
                        o.text("room")),
                )
            }
            val edges = root.getJSONArray("edges")
            for (i in 0 until edges.length()) {
                val o = edges.getJSONObject(i)
                if (building.node(o.getString("from")) == null || building.node(o.getString("to")) == null) {
                    continue
                }
                val sensors = o.optJSONArray("sensorIds") ?: JSONArray()
                building.edges.add(
                    Edge(o.getString("id"), o.getString("from"), o.getString("to"),
                        o.getDouble("lengthM").toFloat(), o.optString("lengthSource"),
                        MutableList(sensors.length()) { sensors.getString(it) }),
                )
            }
            val rooms = root.optJSONArray("rooms") ?: JSONArray()
            for (i in 0 until rooms.length()) {
                val o = rooms.getJSONObject(i)
                building.rooms.add(Room(o.getString("id"), o.optString("name"), o.getString("door")))
            }
            fun lines(key: String, into: MutableList<Line>) {
                val array = root.optJSONArray(key) ?: return
                for (i in 0 until array.length()) {
                    val o = array.getJSONObject(i)
                    val coords = o.getJSONArray("coords")
                    into.add(Line(o.getInt("level"), o.text("room"), MutableList(coords.length()) {
                        val c = coords.getJSONArray(it)
                        P(c.getDouble(0).toFloat(), c.getDouble(1).toFloat())
                    }))
                }
            }
            lines("walls", building.walls)
            lines("tracks", building.tracks)
            val prints = root.optJSONArray("fingerprints") ?: JSONArray()
            for (i in 0 until prints.length()) {
                val o = prints.getJSONObject(i)
                val ap = o.getJSONObject("ap")
                building.fingerprints.add(
                    Fingerprint(o.getInt("level"), P(o.getDouble("x").toFloat(), o.getDouble("y").toFloat()),
                        o.text("room"), ap.keys().asSequence().associateWith { ap.getInt(it) }),
                )
            }
            return building
        }
    }
}

/**
 * Turns a stream of raw positions (ARCore world or PDR dead reckoning, metres) into
 * nodes, edges, walls, a track and Wi-Fi fingerprints. Raw positions drift; [closeAt]
 * snaps the walker back onto a known node and spreads the error along everything
 * recorded since the previous fix, weighted by walked distance.
 */
class SurveyRecorder(val building: Building, var source: String) {
    var level = 0
        private set
    var x = 0f
        private set
    var y = 0f
        private set
    var z = 0f
        private set
    var lastNode: String? = null
        private set
    var room: String? = null
        private set

    private var offX = 0f
    private var offY = 0f
    private var seen = false
    private var walked = 0f
    private var sinceFix = 0f
    private val loose = ArrayList<Pair<P, Float>>()
    private var countX = 0f
    private var countY = 0f
    private var countZ = 0f
    private var wall: Line? = null
    private var track: Line? = null
    private var northSin = 0.0
    private var northCos = 0.0
    private var lastVisionX = Float.NaN
    private var lastVisionY = Float.NaN

    fun visionDue(): Boolean {
        if (!seen) {
            return false
        }
        if (lastVisionX.isNaN() || hypot(x - lastVisionX, y - lastVisionY) >= Vision.SPACING_M) {
            lastVisionX = x
            lastVisionY = y
            return true
        }
        return false
    }

    fun moveTo(rawX: Float, rawY: Float, rawZ: Float) {
        x = rawX + offX
        y = rawY + offY
        z = rawZ
        if (!seen) {
            seen = true
            countX = x
            countY = y
            countZ = z
        }
        val step = hypot(hypot(x - countX, y - countY), z - countZ)
        if (step >= MIN_STEP_M) {
            walked += step
            sinceFix += step
            countX = x
            countY = y
            countZ = z
        }
        val line = track?.takeIf { it.level == level && it.room == room } ?: Line(level, room).also {
            building.tracks.add(it)
            track = it
        }
        val last = line.points.lastOrNull()
        if (last == null || hypot(last.x - x, last.y - y) >= TRACK_STEP_M) {
            line.points.add(P(x, y).also(::remember))
        }
    }

    /** Nodes the walker could be standing on again: same level, same room (or this room's door). */
    fun candidates(): List<Node> {
        val door = building.room(room)?.door
        return building.nodes.filter { it.level == level && (it.room == room || it.id == door) }
    }

    fun mark(kind: NodeKind, name: String): Node {
        val near = candidates()
            .minByOrNull { hypot(it.at.x - x, it.at.y - y) }
            ?.takeIf { hypot(it.at.x - x, it.at.y - y) <= SNAP_M }
        if (near != null) {
            closeAt(near)
            return near
        }
        val node = Node(building.freshId("n"), level, P(x, y), kind, name, room)
        building.nodes.add(node)
        remember(node.at)
        arrive(node)
        lastVisionX = Float.NaN
        return node
    }

    fun closeAt(node: Node) {
        val ex = node.at.x - x
        val ey = node.at.y - y
        if (sinceFix > 0f) {
            loose.forEach { (p, d) ->
                if (p !== node.at) {
                    p.x += ex * d / sinceFix
                    p.y += ey * d / sinceFix
                }
            }
        }
        offX += ex
        offY += ey
        x = node.at.x
        y = node.at.y
        countX = x
        countY = y
        if (node.level != level) {
            level = node.level
            wall = null
            track = null
        }
        loose.clear()
        sinceFix = 0f
        arrive(node)
    }

    fun changeLevel(delta: Int, stairsName: String): Node {
        level += delta
        wall = null
        track = null
        return mark(NodeKind.STAIRS, stairsName)
    }

    /** Everything recorded from now until [leaveRoom] belongs to the room behind [door]. */
    fun enterRoom(door: Node, name: String): Room {
        val created = Room(building.freshId("r"), name, door.id)
        building.rooms.add(created)
        room = created.id
        wall = null
        track = null
        return created
    }

    /** Walks back out through the door the room was entered by; null if not in a room. */
    fun leaveRoom(): Node? {
        val door = building.node(building.room(room)?.door) ?: return null
        room = null
        wall = null
        track = null
        closeAt(door)
        return door
    }

    /** Wall corner at a raw (uncorrected) position, e.g. an ARCore floor hit. */
    fun wallAtRaw(rawX: Float, rawY: Float) = wallAt(rawX + offX, rawY + offY)

    fun wallHere() = wallAt(x, y)

    fun newWall() {
        wall = null
    }

    fun fingerprint(rssi: Map<String, Int>) {
        if (!seen || rssi.size < MIN_APS) {
            return
        }
        val print = Fingerprint(level, P(x, y), room, rssi)
        building.fingerprints.add(print)
        remember(print.at)
    }

    /** One sample of (magnetic heading − map heading) for the same phone direction, radians. */
    fun north(sample: Float) {
        northSin += sin(sample)
        northCos += cos(sample)
        building.northOffset = atan2(northSin, northCos).toFloat()
    }

    private fun wallAt(px: Float, py: Float) {
        val line = wall?.takeIf { it.level == level && it.room == room } ?: Line(level, room).also {
            building.walls.add(it)
            wall = it
        }
        line.points.add(P(px, py).also(::remember))
    }

    private fun arrive(node: Node) {
        lastNode?.let { building.link(it, node.id, walked, source) }
        lastNode = node.id
        walked = 0f
    }

    private fun remember(p: P) {
        loose.add(p to sinceFix)
    }

    companion object {
        const val SNAP_M = 1.0f
        const val TRACK_STEP_M = 0.5f
        const val MIN_STEP_M = 0.1f
        const val MIN_APS = 3
    }
}
