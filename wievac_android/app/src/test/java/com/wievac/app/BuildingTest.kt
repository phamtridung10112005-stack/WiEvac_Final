package com.wievac.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class BuildingTest {
    @Test
    fun routeTakesNearestOpenExit() {
        val b = Building()
        listOf("s" to NodeKind.JUNCTION, "a" to NodeKind.EXIT, "b" to NodeKind.EXIT).forEach { (id, kind) ->
            b.nodes.add(Node(id, 0, P(0f, 0f), kind, id))
        }
        b.edges.add(Edge("near", "s", "a", 5f, "test", mutableListOf("link-5")))
        b.edges.add(Edge("far", "s", "b", 20f, "test"))

        assertEquals(listOf("near"), b.route("s") { true }!!.map { it.id })
        assertEquals(listOf("far"), b.route("s") { "link-5" !in it.sensorIds }!!.map { it.id })
        assertNull(b.route("s") { false })
    }

    @Test
    fun loopClosureSpreadsDriftAndSnapsNearNodes() {
        val b = Building()
        val r = SurveyRecorder(b, "test")
        r.moveTo(0f, 0f, 0f)
        val home = r.mark(NodeKind.JUNCTION, "home")
        r.moveTo(10f, 0f, 0f)
        r.mark(NodeKind.DOOR, "door")
        // Walked back to home but raw position drifted to (2, 0).
        r.moveTo(2f, 0f, 0f)
        r.closeAt(home)

        assertEquals(0f, r.x, 1e-4f)
        val door = b.nodes.first { it.name == "door" }
        assertTrue("door pulled toward the fix", door.at.x < 10f && door.at.x > 8f)
        assertEquals(1, b.edges.size)

        r.moveTo(2.5f, 0.3f, 0f)
        assertEquals(home, r.mark(NodeKind.JUNCTION, "dup"))
        assertEquals(2, b.nodes.size)
    }

    /** Corridor exit(0,0) — door(10,0); room node inside at (10,5). */
    private fun hallWithRoom(): Building {
        val b = Building()
        val r = SurveyRecorder(b, "test")
        r.moveTo(0f, 0f, 0f)
        r.mark(NodeKind.EXIT, "exit")
        r.moveTo(10f, 0f, 0f)
        val door = r.mark(NodeKind.DOOR, "door")
        r.fingerprint(mapOf("a" to -40, "b" to -70, "c" to -80))
        r.enterRoom(door, "Lab")
        r.moveTo(10f, 5f, 0f)
        r.mark(NodeKind.JUNCTION, "desk")
        r.fingerprint(mapOf("a" to -60, "b" to -45, "c" to -75))
        r.moveTo(10f, 0.3f, 0f)
        r.leaveRoom()
        r.moveTo(0f, 0f, 0f)
        r.fingerprint(mapOf("a" to -75, "b" to -85, "c" to -40))
        return b
    }

    @Test
    fun roomsSplitTheViews() {
        val b = hallWithRoom()
        val lab = b.rooms.single()
        val desk = b.nodes.first { it.name == "desk" }
        assertEquals(lab.id, desk.room)
        assertTrue(b.edges.none { b.visible(it, null) && (it.from == desk.id || it.to == desk.id) })
        assertTrue(b.edges.any { b.visible(it, lab.id) && (it.from == desk.id || it.to == desk.id) })
        assertTrue(b.visible(b.node(lab.door)!!, null))
        assertTrue(b.visible(b.node(lab.door)!!, lab.id))
    }

    @Test
    fun wifiAndStepsPlaceTheUser() {
        val b = hallWithRoom()
        val loc = Locator(b)
        val fix = loc.wifi(mapOf("a" to -62, "b" to -47, "c" to -74), 0L)!!
        assertEquals(b.rooms.single().id, fix.room)
        assertNull(loc.wifi(mapOf("x" to -50, "y" to -60, "z" to -70), 0L))

        loc.manual(0, 5f, 0.4f, null, 0L)
        assertEquals(5f, loc.fix!!.x, 0.01f)
        assertEquals(0f, loc.fix!!.y, 0.01f)
        val route = loc.route { true }!!
        assertEquals("exit", route.exit.name)
        assertEquals(5f, route.metres, 0.1f)

        // Walk east to the door, then north into the room: the fix follows the room edge.
        repeat(7) { loc.step(0.7f, (Math.PI / 2).toFloat(), 0L) }
        repeat(6) { loc.step(0.7f, 0f, 0L) }
        assertEquals(b.rooms.single().id, loc.fix!!.room)
    }

    @Test
    fun levelChangeAddsStairsEdge() {
        val b = Building()
        val r = SurveyRecorder(b, "test")
        r.moveTo(0f, 0f, 0f)
        val bottom = r.mark(NodeKind.STAIRS, "bottom")
        r.moveTo(3f, 0f, 3f)
        val top = r.changeLevel(1, "top")

        assertEquals(1, top.level)
        val edge = b.edges.single()
        assertEquals(setOf(bottom.id, top.id), setOf(edge.from, edge.to))
        assertTrue(edge.lengthM > 4f)
        assertEquals(listOf(0, 1), b.levels())
    }

    @Test
    fun se2LocksANewArSessionOntoTheSurveyMap() {
        val map = Se2(10f, 4f, 1.2f)
        val ar = Se2(1f, 2f, 0.3f)
        val lock = Se2.align(map, ar)
        val back = lock.compose(ar)
        assertEquals(map.x, back.x, 1e-4f)
        assertEquals(map.y, back.y, 1e-4f)
        assertEquals(map.heading, back.heading, 1e-4f)
        val p = lock.apply(ar.x, ar.y)
        assertEquals(map.x, p.x, 1e-4f)
        assertEquals(map.y, p.y, 1e-4f)
    }
}
