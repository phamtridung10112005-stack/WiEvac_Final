package com.wievac.app

import android.app.Activity
import android.opengl.GLES11Ext
import android.opengl.GLES20
import android.opengl.GLSurfaceView
import android.view.Surface
import com.google.ar.core.ArCoreApk
import com.google.ar.core.Config
import com.google.ar.core.Coordinates2d
import com.google.ar.core.Frame
import com.google.ar.core.Pose
import com.google.ar.core.Session
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.FloatBuffer
import javax.microedition.khronos.egl.EGLConfig
import javax.microedition.khronos.opengles.GL10
import kotlin.math.atan2
import kotlin.math.hypot

/** One ARCore session drawn into [surface]; [onFrame] runs on the GL thread for every camera frame. */
class ArCamera(
    private val activity: Activity,
    private val surface: GLSurfaceView,
    private val planes: Boolean,
    private val onFrame: (Frame) -> Unit,
) : GLSurfaceView.Renderer {
    @Volatile var session: Session? = null
        private set
    @Volatile private var geometryDirty = false
    private var installAsked = false
    private var textureBound = false
    private var viewW = 0
    private var viewH = 0
    private var running = false
    private val background = CameraBackground()

    init {
        surface.preserveEGLContextOnPause = true
        surface.setEGLContextClientVersion(2)
        surface.setEGLConfigChooser(8, 8, 8, 8, 16, 0)
        surface.setRenderer(this)
        surface.renderMode = GLSurfaceView.RENDERMODE_CONTINUOUSLY
        surface.onPause()
    }

    /** False = ARCore unusable on this phone; true = running, or waiting for the ARCore install screen. */
    fun resume(): Boolean {
        try {
            if (session == null) {
                if (ArCoreApk.getInstance().requestInstall(activity, !installAsked) ==
                    ArCoreApk.InstallStatus.INSTALL_REQUESTED
                ) {
                    installAsked = true
                    return true
                }
                session = Session(activity).apply {
                    configure(Config(this).apply {
                        planeFindingMode = if (planes) Config.PlaneFindingMode.HORIZONTAL else Config.PlaneFindingMode.DISABLED
                        focusMode = Config.FocusMode.AUTO
                        updateMode = Config.UpdateMode.LATEST_CAMERA_IMAGE
                    })
                }
            }
            session?.resume()
        } catch (_: Exception) {
            close()
            return false
        }
        surface.onResume()
        running = true
        return true
    }

    fun pause() {
        if (running) {
            surface.onPause()
            session?.pause()
            running = false
        }
    }

    fun close() {
        pause()
        session?.close()
        session = null
    }

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
        onFrame(frame)
    }

    val width get() = viewW
    val height get() = viewH

    companion object {
        /**
         * Floor-plan heading (radians clockwise from map +y) of the direction the camera looks,
         * from a display-oriented pose. Map axes are ARCore (x, −z). Null when looking at floor/ceiling.
         */
        fun lookHeading(pose: Pose): Float? {
            val z = pose.zAxis
            val fx = -z[0]
            val fy = z[2]
            return if (hypot(fx, fy) > LEVEL_LOOK) atan2(fx, fy) else null
        }

        /** Same "screen-up minus screen-normal" walking direction as [Locator.forwardHeading], in map axes. */
        fun walkHeading(pose: Pose): Float? {
            val up = pose.yAxis
            val back = pose.zAxis
            val fx = up[0] - back[0]
            val fy = -(up[2] - back[2])
            return if (hypot(fx, fy) > 0.5f) atan2(fx, fy) else null
        }

        /** Horizontal part of the view axis must exceed this, i.e. camera within ~50° of level. */
        private const val LEVEL_LOOK = 0.64f
    }
}

/** Draws the ARCore camera image as a full-screen quad. */
private class CameraBackground {
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
    }
}
