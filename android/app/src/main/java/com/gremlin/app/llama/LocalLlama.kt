package com.gremlin.app.llama

import java.util.concurrent.Executors

/**
 * Thin Kotlin wrapper around the JNI bridge in
 * android/app/src/main/cpp/gremlin_llama.cpp -- fully offline, on-device
 * inference for a small (~1GB) local GGUF model, used by
 * GremlinClient.kt's away-mode "local" provider when the desktop isn't
 * reachable.
 *
 * A singleton (object), not a class: there's only ever one on-device
 * model resident at a time, same as the desktop's own primary model.
 * All native calls are serialized through a dedicated single-thread
 * executor, since the underlying llama.cpp context/sampler state isn't
 * safe to touch from more than one thread at once -- this mirrors the
 * reference example's own "dedicated single-threaded dispatcher" note,
 * just without a coroutine dependency this app doesn't otherwise use
 * (see MainActivity's plain Thread {} pattern for GremlinClient calls).
 */
object LocalLlama {

    private external fun init()
    private external fun load(modelPath: String): Int
    private external fun prepare(): Int
    private external fun processSystemPrompt(systemPrompt: String): Int
    private external fun generate(userPrompt: String, nPredict: Int): String
    private external fun unload()
    private external fun shutdown()

    private val nativeThread = Executors.newSingleThreadExecutor { r ->
        Thread(r, "gremlin-local-llama").apply { isDaemon = true }
    }

    @Volatile private var libraryLoaded = false
    @Volatile private var modelReady = false
    @Volatile private var lastSystemPrompt: String? = null

    private fun ensureLibraryLoaded() {
        if (libraryLoaded) return
        synchronized(this) {
            if (libraryLoaded) return
            System.loadLibrary("gremlin_llama")
            nativeThread.submit { init() }.get()
            libraryLoaded = true
        }
    }

    /** True once a model is loaded and ready to generate. Cheap, no JNI call. */
    fun isReady(): Boolean = modelReady

    /**
     * Loads `modelPath` and prepares a context for it. Safe to call again
     * with the same path (no-ops if already ready) or a different one
     * (unloads the old model first). Returns false on any failure --
     * a bad/missing/corrupt GGUF file should degrade to "local model
     * unavailable," never crash the app.
     */
    @Synchronized
    fun loadModel(modelPath: String): Boolean {
        try {
            ensureLibraryLoaded()
            if (modelReady) return true

            val loadResult = nativeThread.submit<Int> { load(modelPath) }.get()
            if (loadResult != 0) return false

            val prepareResult = nativeThread.submit<Int> { prepare() }.get()
            if (prepareResult != 0) return false

            modelReady = true
            lastSystemPrompt = null // force the next generate() call to (re-)apply it
            return true
        } catch (e: Exception) {
            modelReady = false
            return false
        }
    }

    /**
     * Runs one chat turn against the loaded model, applying `systemPrompt`
     * (Gremlin's persona voice, same string cached from the desktop for
     * the Claude/Gemini away-mode calls -- see GremlinClient's
     * cached_persona_prompt) only when it actually changed since the last
     * call, so a run of messages in the same conversation doesn't re-pay
     * that cost each time. Returns null if the model isn't loaded or
     * generation fails -- callers should treat that as "local unavailable
     * right now," the same way a failed API call is already handled.
     */
    fun generateReply(systemPrompt: String, userPrompt: String, maxTokens: Int = 512): String? {
        if (!modelReady) return null
        return try {
            nativeThread.submit<String> {
                if (systemPrompt != lastSystemPrompt) {
                    if (processSystemPrompt(systemPrompt) != 0) {
                        throw IllegalStateException("processSystemPrompt failed")
                    }
                    lastSystemPrompt = systemPrompt
                }
                generate(userPrompt, maxTokens)
            }.get()
        } catch (e: Exception) {
            null
        }
    }

    /** Frees the model's memory without shutting down the backend -- a
     * later loadModel() call reloads cleanly. Used when the user disables
     * the offline model in Settings, or to free RAM under pressure. */
    @Synchronized
    fun unloadModel() {
        if (!modelReady) return
        try {
            nativeThread.submit { unload() }.get()
        } catch (e: Exception) {
            // best-effort -- an unload failure shouldn't wedge the app
        }
        modelReady = false
        lastSystemPrompt = null
    }
}
