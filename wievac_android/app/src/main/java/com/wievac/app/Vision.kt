package com.wievac.app

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageFormat
import android.graphics.Rect
import android.graphics.YuvImage
import android.media.Image
import org.json.JSONArray
import org.json.JSONObject
import org.opencv.android.Utils
import org.opencv.calib3d.Calib3d
import org.opencv.core.DMatch
import org.opencv.core.Mat
import org.opencv.core.MatOfDMatch
import org.opencv.core.MatOfKeyPoint
import org.opencv.core.MatOfPoint2f
import org.opencv.core.Point
import org.opencv.features2d.BFMatcher
import org.opencv.features2d.ORB
import java.io.ByteArrayOutputStream
import java.io.File
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.sin

/** Rigid transform on the floor: map = R(heading) * ar + (x, y). Heading is radians, CCW from +Y. */
class Se2(val x: Float, val y: Float, val heading: Float) {
    fun apply(px: Float, py: Float): P {
        val c = cos(heading)
        val s = sin(heading)
        return P(x + c * px - s * py, y + s * px + c * py)
    }

    fun compose(ar: Se2): Se2 {
        val p = apply(ar.x, ar.y)
        return Se2(p.x, p.y, heading + ar.heading)
    }

    companion object {
        /** Unique SE2 sending [ar] to [map] (used to lock a new ARCore session onto the old map). */
        fun align(map: Se2, ar: Se2): Se2 {
            val heading = map.heading - ar.heading
            val c = cos(heading)
            val s = sin(heading)
            return Se2(map.x - (c * ar.x - s * ar.y), map.y - (s * ar.x + c * ar.y), heading)
        }
    }
}

class Landmark(
    val id: String,
    val file: String,
    val level: Int,
    val x: Float,
    val y: Float,
    val heading: Float,
    val room: String?,
    @Transient var desc: Mat? = null,
    @Transient var keys: MatOfKeyPoint? = null,
)

fun Building.landmarksDir(root: File) = File(root, "vision/$id")

fun Building.loadLandmarks(root: File): List<Landmark> {
    val index = File(landmarksDir(root), "index.json")
    if (!index.exists()) {
        return emptyList()
    }
    return try {
        val array = JSONArray(index.readText())
        (0 until array.length()).map { i ->
            val o = array.getJSONObject(i)
            Landmark(
                o.getString("id"), o.getString("file"), o.getInt("level"),
                o.getDouble("x").toFloat(), o.getDouble("y").toFloat(),
                o.getDouble("heading").toFloat(),
                if (o.isNull("room")) null else o.optString("room").ifEmpty { null },
            )
        }
    } catch (_: Exception) {
        emptyList()
    }
}

fun Building.saveLandmarks(root: File, list: List<Landmark>) {
    val dir = landmarksDir(root)
    dir.mkdirs()
    val json = JSONArray().apply {
        list.forEach {
            put(JSONObject().put("id", it.id).put("file", it.file).put("level", it.level)
                .put("x", (it.x * 100).roundToInt() / 100.0).put("y", (it.y * 100).roundToInt() / 100.0)
                .put("heading", (it.heading * 1000).roundToInt() / 1000.0).put("room", it.room))
        }
    }
    File(dir, "index.json").writeText(json.toString(1))
}

fun Building.clearLandmarks(root: File) {
    landmarksDir(root).deleteRecursively()
}

/**
 * ORB keyframe matching. Compares the current camera still against survey photos of the
 * same corridor; a homography with enough inliers means "this is that place", not a pixel
 * clone. People walking through or a slightly different phone angle still match if the
 * architecture (door frames, corners) stays.
 */
object Vision {
    private val orb: ORB by lazy { ORB.create(800) }
    private val matcher: BFMatcher by lazy { BFMatcher.create(org.opencv.core.Core.NORM_HAMMING, false) }

    fun features(bitmap: Bitmap): Pair<MatOfKeyPoint, Mat>? {
        val rgba = Mat()
        Utils.bitmapToMat(bitmap, rgba)
        val keys = MatOfKeyPoint()
        val desc = Mat()
        orb.detectAndCompute(rgba, Mat(), keys, desc)
        rgba.release()
        if (desc.empty() || keys.toArray().size < MIN_KEYS) {
            keys.release()
            desc.release()
            return null
        }
        return keys to desc
    }

    fun load(file: File): Pair<MatOfKeyPoint, Mat>? {
        val bitmap = BitmapFactory.decodeFile(file.absolutePath) ?: return null
        return features(bitmap)
    }

    /** Best landmark whose photo overlaps the query; null if the scene is unknown. */
    fun match(query: Pair<MatOfKeyPoint, Mat>, dir: File, landmarks: List<Landmark>): Landmark? {
        var best: Landmark? = null
        var bestInliers = MIN_INLIERS
        landmarks.forEach { lm ->
            val feat = lm.desc?.let { lm.keys!! to it } ?: load(File(dir, lm.file))?.also {
                lm.keys = it.first
                lm.desc = it.second
            } ?: return@forEach
            val n = inliers(query.second, feat.second, query.first, feat.first)
            if (n > bestInliers) {
                bestInliers = n
                best = lm
            }
        }
        return best
    }

    fun jpeg(image: Image): ByteArray? {
        val nv21 = nv21(image) ?: return null
        val yuv = YuvImage(nv21, ImageFormat.NV21, image.width, image.height, null)
        val out = ByteArrayOutputStream()
        val scale = IMAGE_W.toFloat() / image.width
        val h = (image.height * scale).toInt().coerceAtLeast(1)
        yuv.compressToJpeg(Rect(0, 0, image.width, image.height), 70, out)
        val full = BitmapFactory.decodeByteArray(out.toByteArray(), 0, out.size()) ?: return null
        val small = Bitmap.createScaledBitmap(full, IMAGE_W, h, true)
        if (small != full) {
            full.recycle()
        }
        val jpeg = ByteArrayOutputStream()
        small.compress(Bitmap.CompressFormat.JPEG, 80, jpeg)
        small.recycle()
        return jpeg.toByteArray()
    }

    private fun inliers(qDesc: Mat, tDesc: Mat, qKeys: MatOfKeyPoint, tKeys: MatOfKeyPoint): Int {
        val knn = ArrayList<MatOfDMatch>()
        matcher.knnMatch(qDesc, tDesc, knn, 2)
        val good = ArrayList<DMatch>()
        knn.forEach { mat ->
            val pair = mat.toArray()
            if (pair.size == 2 && pair[0].distance < 0.75f * pair[1].distance) {
                good.add(pair[0])
            }
            mat.release()
        }
        if (good.size < MIN_INLIERS) {
            return 0
        }
        val qPts = MatOfPoint2f(*good.map { Point(qKeys.toArray()[it.queryIdx].pt.x, qKeys.toArray()[it.queryIdx].pt.y) }.toTypedArray())
        val tPts = MatOfPoint2f(*good.map { Point(tKeys.toArray()[it.trainIdx].pt.x, tKeys.toArray()[it.trainIdx].pt.y) }.toTypedArray())
        val mask = Mat()
        val homography = Calib3d.findHomography(qPts, tPts, Calib3d.RANSAC, 8.0, mask)
        val count = if (homography.empty()) 0 else (0 until mask.rows()).count { mask.get(it, 0)[0] != 0.0 }
        homography.release()
        mask.release()
        qPts.release()
        tPts.release()
        return count
    }

    /** ARCore camera images are YUV_420_888; NV21 is what Android's JPEG compressor accepts. */
    private fun nv21(image: Image): ByteArray? {
        if (image.format != ImageFormat.YUV_420_888) {
            return null
        }
        val y = image.planes[0]
        val u = image.planes[1]
        val v = image.planes[2]
        val width = image.width
        val height = image.height
        val ySize = width * height
        val out = ByteArray(ySize + ySize / 2)
        val yRow = y.rowStride
        val yBuf = y.buffer
        var dst = 0
        for (row in 0 until height) {
            yBuf.position(row * yRow)
            yBuf.get(out, dst, width)
            dst += width
        }
        val vRow = v.rowStride
        val vPix = v.pixelStride
        val uRow = u.rowStride
        val uPix = u.pixelStride
        val vBuf = v.buffer
        val uBuf = u.buffer
        dst = ySize
        for (row in 0 until height / 2) {
            for (col in 0 until width / 2) {
                out[dst++] = vBuf.get(row * vRow + col * vPix)
                out[dst++] = uBuf.get(row * uRow + col * uPix)
            }
        }
        return out
    }

    const val IMAGE_W = 640
    const val MIN_KEYS = 40
    const val MIN_INLIERS = 12
    const val SPACING_M = 2.5f
}
