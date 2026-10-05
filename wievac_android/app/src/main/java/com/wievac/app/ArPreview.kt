package com.wievac.app

import android.opengl.GLES11Ext
import android.opengl.GLES20
import com.google.ar.core.Coordinates2d
import com.google.ar.core.Frame
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.FloatBuffer
import kotlin.math.atan2
import kotlin.math.hypot

/** Draws the ARCore camera image as a full-screen quad. */
class ArPreview {
    var texture = 0
        private set
    private var program = 0
    private var posAttr = 0
    private var uvAttr = 0
    private val quad = floats(floatArrayOf(-1f, -1f, 1f, -1f, -1f, 1f, 1f, 1f))
    private val uv = floats(FloatArray(8))

    fun create() {
        val ids = IntArray(1)
        GLES20.glGenTextures(1, ids, 0)
        texture = ids[0]
        val target = GLES11Ext.GL_TEXTURE_EXTERNAL_OES
        GLES20.glBindTexture(target, texture)
        GLES20.glTexParameteri(target, GLES20.GL_TEXTURE_WRAP_S, GLES20.GL_CLAMP_TO_EDGE)
        GLES20.glTexParameteri(target, GLES20.GL_TEXTURE_WRAP_T, GLES20.GL_CLAMP_TO_EDGE)
        GLES20.glTexParameteri(target, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR)
        GLES20.glTexParameteri(target, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR)
        program = GLES20.glCreateProgram()
        GLES20.glAttachShader(program, shader(GLES20.GL_VERTEX_SHADER, VERTEX))
        GLES20.glAttachShader(program, shader(GLES20.GL_FRAGMENT_SHADER, FRAGMENT))
        GLES20.glLinkProgram(program)
        posAttr = GLES20.glGetAttribLocation(program, "a_pos")
        uvAttr = GLES20.glGetAttribLocation(program, "a_uv")
    }

    fun draw(frame: Frame) {
        if (frame.hasDisplayGeometryChanged()) {
            frame.transformCoordinates2d(
                Coordinates2d.OPENGL_NORMALIZED_DEVICE_COORDINATES, quad,
                Coordinates2d.TEXTURE_NORMALIZED, uv,
            )
        }
        if (frame.timestamp == 0L) {
            return
        }
        GLES20.glDisable(GLES20.GL_DEPTH_TEST)
        GLES20.glDepthMask(false)
        GLES20.glUseProgram(program)
        GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, texture)
        GLES20.glVertexAttribPointer(posAttr, 2, GLES20.GL_FLOAT, false, 0, quad)
        GLES20.glVertexAttribPointer(uvAttr, 2, GLES20.GL_FLOAT, false, 0, uv)
        GLES20.glEnableVertexAttribArray(posAttr)
        GLES20.glEnableVertexAttribArray(uvAttr)
        GLES20.glDrawArrays(GLES20.GL_TRIANGLE_STRIP, 0, 4)
        GLES20.glDisableVertexAttribArray(posAttr)
        GLES20.glDisableVertexAttribArray(uvAttr)
        GLES20.glDepthMask(true)
        GLES20.glEnable(GLES20.GL_DEPTH_TEST)
    }

    private fun shader(type: Int, source: String): Int {
        return GLES20.glCreateShader(type).also {
            GLES20.glShaderSource(it, source)
            GLES20.glCompileShader(it)
        }
    }

    companion object {
        private const val VERTEX =
            "attribute vec4 a_pos; attribute vec2 a_uv; varying vec2 v_uv;" +
                "void main() { gl_Position = a_pos; v_uv = a_uv; }"
        private const val FRAGMENT =
            "#extension GL_OES_EGL_image_external : require\n" +
                "precision mediump float; varying vec2 v_uv; uniform samplerExternalOES u_tex;" +
                "void main() { gl_FragColor = texture2D(u_tex, v_uv); }"

        private fun floats(values: FloatArray): FloatBuffer {
            return ByteBuffer.allocateDirect(values.size * 4).order(ByteOrder.nativeOrder())
                .asFloatBuffer().apply {
                    put(values)
                    position(0)
                }
        }

        /** Walking direction on the floor from an ARCore camera pose, radians from +Y. */
        fun heading(frame: Frame): Float? {
            val display = frame.camera.displayOrientedPose
            val up = display.yAxis
            val back = display.zAxis
            val fx = up[0] - back[0]
            val fy = -(up[2] - back[2])
            return if (hypot(fx, fy) > 0.5f) atan2(fx, fy) else null
        }

        fun pose(frame: Frame): Se2? {
            if (frame.camera.trackingState != com.google.ar.core.TrackingState.TRACKING) {
                return null
            }
            val heading = heading(frame) ?: return null
            val p = frame.camera.pose
            return Se2(p.tx(), -p.tz(), heading)
        }
    }
}
