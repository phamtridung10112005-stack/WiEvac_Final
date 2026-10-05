package com.wievac.app

import android.content.Context
import android.graphics.Canvas
import android.graphics.DashPathEffect
import android.graphics.Paint
import android.text.SpannableStringBuilder
import android.text.style.ForegroundColorSpan
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.View
import androidx.core.content.ContextCompat
import kotlin.math.abs
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min

/**
 * 2D plan of one level: track, walls, graph, route and the walker. Always fits to content.
 * With [allSpaces] off only one space is drawn: the corridor ([space] null) or one room.
 */
class PlanView(context: Context, attrs: AttributeSet?) : View(context, attrs) {
    // ponytail: auto-fit only, no pinch zoom; add ScaleGestureDetector when floors get too big to tap.
    var building: Building? = null
    var level = 0
    var allSpaces = true
    var space: String? = null
    var me: P? = null
    var meRadiusM = 0f
    var meFaded = false
    var route: List<Edge> = emptyList()
    var edgeColor: (Edge) -> Int = { color(R.color.muted) }
    var onTapNode: ((Node) -> Unit)? = null
    var onTapEdge: ((Edge) -> Unit)? = null
    var onTapMap: ((Float, Float) -> Unit)? = null

    private val density = resources.displayMetrics.density
    private val stroke = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
    }
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    private val text = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textSize = 11 * density
        color = color(R.color.text)
    }
    private val dash = DashPathEffect(floatArrayOf(6 * density, 6 * density), 0f)
    private var scale = 1f
    private var originX = 0f
    private var originY = 0f
    private var downX = 0f
    private var downY = 0f

    private fun color(id: Int) = ContextCompat.getColor(context, id)

    private fun sx(x: Float) = originX + x * scale

    private fun sy(y: Float) = originY - y * scale

    private fun shows(line: Line) = line.level == level && (allSpaces || line.room == space)

    private fun shows(b: Building, node: Node) = node.level == level && (allSpaces || b.visible(node, space))

    private fun shows(b: Building, edge: Edge): Boolean {
        val a = b.node(edge.from) ?: return false
        val c = b.node(edge.to) ?: return false
        return a.level == level && c.level == level && (allSpaces || b.visible(edge, space))
    }

    private fun fit(b: Building) {
        val points = b.tracks.filter(::shows).flatMap { it.points } +
            b.walls.filter(::shows).flatMap { it.points } +
            b.nodes.filter { shows(b, it) }.map { it.at } + listOfNotNull(me)
        val pad = 24 * density
        val minX = points.minOfOrNull { it.x } ?: 0f
        val maxX = points.maxOfOrNull { it.x } ?: 0f
        val minY = points.minOfOrNull { it.y } ?: 0f
        val maxY = points.maxOfOrNull { it.y } ?: 0f
        val spanX = max(maxX - minX, MIN_SPAN_M)
        val spanY = max(maxY - minY, MIN_SPAN_M)
        scale = min((width - 2 * pad) / spanX, (height - 2 * pad) / spanY)
        originX = width / 2f - (minX + maxX) / 2f * scale
        originY = height / 2f + (minY + maxY) / 2f * scale
    }

    override fun onDraw(canvas: Canvas) {
        val b = building ?: return
        fit(b)
        stroke.pathEffect = null
        stroke.strokeWidth = 2 * density
        stroke.color = color(R.color.line)
        b.tracks.filter(::shows).forEach { drawLine(canvas, it) }
        stroke.strokeWidth = 4 * density
        stroke.color = color(R.color.text)
        b.walls.filter(::shows).forEach { drawLine(canvas, it) }

        val onRoute = route.toHashSet()
        b.edges.filter { shows(b, it) }.forEach { edge ->
            val a = b.node(edge.from)!!
            val c = b.node(edge.to)!!
            stroke.pathEffect = if (edge.sensorIds.isEmpty()) dash else null
            stroke.strokeWidth = (if (edge in onRoute) 8 else 4) * density
            stroke.color = if (edge in onRoute) color(R.color.passable) else edgeColor(edge)
            canvas.drawLine(sx(a.at.x), sy(a.at.y), sx(c.at.x), sy(c.at.y), stroke)
        }
        stroke.pathEffect = null

        b.nodes.filter { shows(b, it) }.forEach { node ->
            fill.color = color(node.kind.color)
            val cx = sx(node.at.x)
            val cy = sy(node.at.y)
            canvas.drawCircle(cx, cy, 7 * density, fill)
            val label = b.rooms.firstOrNull { it.door == node.id && allSpaces.not() && space == null }
                ?.let { "${node.name} → ${it.name}" } ?: node.name
            canvas.drawText(label, cx + 10 * density, cy - 8 * density, text)
        }

        me?.let {
            fill.color = color(R.color.alarm)
            if (meRadiusM > 0f) {
                fill.alpha = 50
                canvas.drawCircle(sx(it.x), sy(it.y), max(meRadiusM * scale, 12 * density), fill)
            }
            fill.alpha = if (meFaded) 110 else 255
            canvas.drawCircle(sx(it.x), sy(it.y), 9 * density, fill)
            fill.alpha = 255
        }
    }

    private fun drawLine(canvas: Canvas, line: Line) {
        line.points.zipWithNext { a, c -> canvas.drawLine(sx(a.x), sy(a.y), sx(c.x), sy(c.y), stroke) }
        if (line.points.size == 1) {
            val p = line.points[0]
            canvas.drawPoint(sx(p.x), sy(p.y), stroke)
        }
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                downX = event.x
                downY = event.y
            }
            MotionEvent.ACTION_UP -> {
                if (abs(event.x - downX) < 12 * density && abs(event.y - downY) < 12 * density) {
                    performClick()
                    tap(event.x, event.y)
                }
            }
        }
        return onTapNode != null || onTapEdge != null || onTapMap != null
    }

    override fun performClick(): Boolean {
        super.performClick()
        return true
    }

    private fun tap(px: Float, py: Float) {
        val b = building ?: return
        val node = b.nodes.filter { shows(b, it) }
            .minByOrNull { hypot(sx(it.at.x) - px, sy(it.at.y) - py) }
            ?.takeIf { hypot(sx(it.at.x) - px, sy(it.at.y) - py) <= 24 * density }
        if (node != null && onTapNode != null) {
            onTapNode?.invoke(node)
            return
        }
        val edge = b.edges.filter { shows(b, it) }.map { edge ->
            val a = b.node(edge.from)!!
            val c = b.node(edge.to)!!
            edge to segment(px, py, sx(a.at.x), sy(a.at.y), sx(c.at.x), sy(c.at.y))
        }.minByOrNull { it.second }?.takeIf { it.second <= 16 * density }?.first
        if (edge != null && onTapEdge != null) {
            onTapEdge?.invoke(edge)
            return
        }
        onTapMap?.invoke((px - originX) / scale, (originY - py) / scale)
    }

    private fun segment(px: Float, py: Float, ax: Float, ay: Float, bx: Float, by: Float): Float {
        val dx = bx - ax
        val dy = by - ay
        val len = dx * dx + dy * dy
        val t = if (len == 0f) 0f else (((px - ax) * dx + (py - ay) * dy) / len).coerceIn(0f, 1f)
        return hypot(px - (ax + t * dx), py - (ay + t * dy))
    }

    companion object {
        private const val MIN_SPAN_M = 6f

        fun legend(context: Context, withMe: Boolean): CharSequence {
            val items = NodeKind.entries.map { it.color to context.getString(it.label) } +
                if (withMe) listOf(R.color.alarm to context.getString(R.string.legend_me)) else emptyList()
            val out = SpannableStringBuilder()
            items.forEach { (color, name) ->
                val at = out.length
                out.append("● ")
                out.setSpan(ForegroundColorSpan(ContextCompat.getColor(context, color)), at, at + 1, 0)
                out.append(name).append("    ")
            }
            return out
        }
    }
}

fun levelName(context: Context, level: Int): String {
    return if (level >= 0) {
        context.getString(R.string.level_name, level + 1)
    } else {
        context.getString(R.string.level_basement, -level)
    }
}
