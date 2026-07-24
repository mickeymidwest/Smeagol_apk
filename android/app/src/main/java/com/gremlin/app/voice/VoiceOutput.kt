package com.gremlin.app.voice

import android.content.Context
import android.content.SharedPreferences
import android.speech.tts.TextToSpeech
import java.util.Locale

/**
 * Thin wrapper around Android's built-in TextToSpeech -- speaks
 * Gremlin's replies aloud when enabled in Settings. No custom voice
 * model or accent: stock Android TTS has no way to pick a regional
 * accent or a character voice, only whatever system voices are
 * installed plus pitch/rate tuning. Pitch and rate default to a
 * slightly lower/faster setting than flat 1.0 (a rough "gravelly,
 * clipped" approximation), and are user-adjustable in Settings' "Voice"
 * section so you can dial it in to taste rather than being stuck with
 * one hardcoded guess.
 */
class VoiceOutput(context: Context, private val prefs: SharedPreferences) {

    private var tts: TextToSpeech? = null
    private var ready = false

    init {
        tts = TextToSpeech(context.applicationContext) { status ->
            if (status == TextToSpeech.SUCCESS) {
                tts?.language = Locale.US
                ready = true
            }
        }
    }

    fun isEnabled(): Boolean = prefs.getBoolean("voice_enabled", false)

    /** Speaks `text`, replacing anything currently being spoken -- a new
     * reply shouldn't queue up behind an old one still playing. No-op if
     * voice output is disabled in Settings or the engine never
     * initialized successfully (e.g. no TTS engine installed). */
    fun speak(text: String) {
        if (!isEnabled() || !ready || text.isBlank()) return
        val engine = tts ?: return
        engine.setPitch(prefs.getFloat("voice_pitch", DEFAULT_PITCH))
        engine.setSpeechRate(prefs.getFloat("voice_rate", DEFAULT_RATE))
        engine.speak(text, TextToSpeech.QUEUE_FLUSH, null, "gremlin-reply")
    }

    fun stop() {
        tts?.stop()
    }

    /** Call from onDestroy() -- leaking a TextToSpeech engine connection
     * is a real, easy-to-hit Android footgun if this isn't released. */
    fun shutdown() {
        tts?.stop()
        tts?.shutdown()
        tts = null
    }

    companion object {
        const val DEFAULT_PITCH = 0.8f
        const val DEFAULT_RATE = 1.05f
    }
}
