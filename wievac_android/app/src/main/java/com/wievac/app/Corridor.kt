package com.wievac.app

enum class CorridorState {
    PASSABLE,
    DEGRADED,
    BLOCKED,
    UNKNOWN,
}

data class Corridor(
    val id: String,
    val name: String,
    val state: CorridorState,
    val score: Int?,
    val quality: Double?,
    val uncertainty: Double?,
    val fresh: Boolean,
) {
    fun canEnter(): Boolean {
        return fresh && (state == CorridorState.PASSABLE || state == CorridorState.DEGRADED)
    }
}

fun sampleCorridors(): List<Corridor> {
    return listOf(
        Corridor("link-1", "Sảnh", CorridorState.PASSABLE, 91, 0.94, 0.08, true),
        Corridor("link-2", "Hành lang đông", CorridorState.PASSABLE, 84, 0.90, 0.12, true),
        Corridor("link-3", "Hành lang tây", CorridorState.DEGRADED, 62, 0.81, 0.22, true),
        Corridor("link-4", "Cầu thang A", CorridorState.PASSABLE, 77, 0.88, 0.14, true),
        Corridor("link-5", "Cầu thang B", CorridorState.BLOCKED, 18, 0.86, 0.17, true),
        Corridor("link-6", "Hành lang bắc", CorridorState.BLOCKED, 24, 0.83, 0.21, true),
        Corridor("link-7", "Hành lang nam", CorridorState.DEGRADED, 58, 0.79, 0.24, true),
        Corridor("link-8", "Tầng 2", CorridorState.UNKNOWN, null, 0.41, 0.63, true),
        Corridor("link-9", "Cửa sau", CorridorState.PASSABLE, 80, 0.70, 0.28, false),
        Corridor("link-10", "Tầng hầm", CorridorState.UNKNOWN, null, null, null, false),
    )
}
