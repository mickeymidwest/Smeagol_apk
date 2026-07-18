package com.gremlin.app

import android.content.Context
import android.content.SharedPreferences
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL

/**
 * Full-app behavior: at home, the desktop's whole orchestrator (all
 * local models, consult, everything) is one fast LAN hop away, so use
 * it. Away from home, that's not reachable at all -- fall back to
 * calling Claude or Gemini directly with the phone's own stored API
 * keys, in the same persona voice cached from the last time the
 * desktop was reachable. This deliberately does NOT reimplement the
 * router/persona/consult machinery in Kotlin -- that logic stays in
 * one place (gremlin_core), and the phone either borrows it over the
 * network or falls back to a much simpler direct call.
 */
data class ChatResult(val answer: String, val source: String)

class GremlinClient(private val prefs: SharedPreferences, private val appContext: Context) {

    // Short connect timeout for the desktop attempt -- on the home LAN
    // this connects almost instantly, so it costs nothing there. Away
    // from home it means falling back quickly instead of hanging.
    private val desktopConnectTimeoutMs = 4_000
    private val desktopReadTimeoutMs = 120_000 // consult/synthesis can take a while, once connected

    // Away-mode exchanges the desktop doesn't know about yet -- queued
    // here, sent along with the next message that actually reaches the
    // desktop, then cleared only once the desktop confirms it got them.
    // Never cleared on a failed send, so a dropped connection mid-sync
    // just means it tries again next time rather than losing anything.
    private val pendingSyncFile: File by lazy { File(appContext.filesDir, "pending_sync.jsonl") }

    private fun appendPendingSync(prompt: String, answer: String, source: String) {
        try {
            val entry = JSONObject().apply {
                put("prompt", prompt)
                put("answer", answer)
                put("source", source)
                put("timestamp", System.currentTimeMillis() / 1000.0)
            }
            pendingSyncFile.appendText(entry.toString() + "\n")
        } catch (e: Exception) {
            // Best-effort -- losing a queued sync entry isn't fatal, the
            // away-mode answer itself already succeeded and was shown.
        }
    }

    private fun readPendingSync(): JSONArray {
        val arr = JSONArray()
        if (!pendingSyncFile.exists()) return arr
        try {
            pendingSyncFile.readLines().forEach { line ->
                if (line.isNotBlank()) arr.put(JSONObject(line))
            }
        } catch (e: Exception) {
            // Malformed queue file -- better to skip syncing this round
            // than crash the whole chat call over stale local data.
        }
        return arr
    }

    private fun clearPendingSync() {
        try {
            pendingSyncFile.delete()
        } catch (e: Exception) {
        }
    }

    private fun hasAnyNetwork(): Boolean {
        val cm = appContext.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager ?: return true
        val network = cm.activeNetwork ?: return false
        val capabilities = cm.getNetworkCapabilities(network) ?: return false
        return capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }

    fun chat(message: String): ChatResult {
        val host = prefs.getString("host", null)
        val port = prefs.getInt("port", 0)
        val token = prefs.getString("token", null)

        // No network at all (e.g. airplane mode) -- don't waste time on
        // a desktop attempt that can't possibly succeed, and don't
        // bother trying direct API calls either, just say so plainly.
        if (!hasAnyNetwork()) {
            return ChatResult("No network connection right now.", "no-network")
        }

        if (host != null && port != 0 && token != null) {
            try {
                val pending = readPendingSync()
                val answer = postToDesktop(host, port, token, message, pending)
                if (pending.length() > 0) clearPendingSync() // only after the server actually got them
                refreshCachedPersonaVoice(host, port, token) // best-effort, keeps away-mode voice current
                return ChatResult(answer, "desktop")
            } catch (e: Exception) {
                // Desktop configured but unreachable -- fall through to direct API calls.
            }
        }

        val result = chatAway(message)
        if (result.source == "claude" || result.source == "gemini") {
            appendPendingSync(message, result.answer, result.source)
        }
        return result
    }

    private fun chatAway(message: String): ChatResult {
        val anthropicKey = prefs.getString("anthropic_key", null)
        val geminiKey = prefs.getString("gemini_key", null)
        val preferred = prefs.getString("away_preferred", "claude")
        val personaPrompt = prefs.getString("cached_persona_prompt", "") ?: ""

        val order = if (preferred == "gemini") listOf("gemini", "claude") else listOf("claude", "gemini")
        val errors = mutableListOf<String>()

        for (provider in order) {
            try {
                when (provider) {
                    "claude" -> if (!anthropicKey.isNullOrBlank()) {
                        return ChatResult(callClaude(anthropicKey, personaPrompt, message), "claude")
                    }
                    "gemini" -> if (!geminiKey.isNullOrBlank()) {
                        return ChatResult(callGemini(geminiKey, personaPrompt, message), "gemini")
                    }
                }
            } catch (e: Exception) {
                errors.add("$provider: ${e.message}")
            }
        }

        return if (anthropicKey.isNullOrBlank() && geminiKey.isNullOrBlank()) {
            ChatResult(
                "Can't reach the desktop and no API keys are set up. " +
                "Connect to your home Wi-Fi, or add a Claude/Gemini API key in Settings.",
                "none-configured",
            )
        } else {
            ChatResult("Couldn't get an answer from anything: ${errors.joinToString("; ")}", "error")
        }
    }

    /**
     * Fetches the full /status body (not just system_prompt, unlike
     * refreshCachedPersonaVoice) and caches it in prefs -- this is what
     * the hologram's getStatusJson() bridge call reads to label its 4
     * head-slots, and what ModelSettingsActivity reads to show a
     * model's current field values. Best-effort: returns null and
     * leaves any previously cached value in place on failure, same
     * "stale is better than blank" approach as the persona-voice cache.
     */
    fun fetchStatusRaw(): String? {
        val host = prefs.getString("host", null)
        val port = prefs.getInt("port", 0)
        val token = prefs.getString("token", null)
        if (host == null || port == 0 || token == null) return null

        return try {
            val url = URL("http://$host:$port/status")
            val connection = url.openConnection() as HttpURLConnection
            connection.requestMethod = "GET"
            connection.setRequestProperty("Authorization", "Bearer $token")
            connection.connectTimeout = 4_000
            connection.readTimeout = 8_000
            val text = connection.inputStream.bufferedReader().use { it.readText() }
            prefs.edit().putString("cached_status_json", text).apply()
            text
        } catch (e: Exception) {
            null
        }
    }

    private fun refreshCachedPersonaVoice(host: String, port: Int, token: String) {
        try {
            val url = URL("http://$host:$port/status")
            val connection = url.openConnection() as HttpURLConnection
            connection.requestMethod = "GET"
            connection.setRequestProperty("Authorization", "Bearer $token")
            connection.connectTimeout = 3_000
            connection.readTimeout = 5_000
            val text = connection.inputStream.bufferedReader().use { it.readText() }
            val prompt = JSONObject(text).optString("system_prompt", "")
            prefs.edit().putString("cached_persona_prompt", prompt).apply()
        } catch (e: Exception) {
            // best-effort only -- an away-mode chat still works with
            // whatever was cached last, or with no persona flavor at all
        }
    }

    private fun postToDesktop(host: String, port: Int, token: String, message: String, pendingSync: JSONArray? = null): String {
        val url = URL("http://$host:$port/chat")
        val connection = url.openConnection() as HttpURLConnection
        connection.requestMethod = "POST"
        connection.setRequestProperty("Content-Type", "application/json")
        connection.setRequestProperty("Authorization", "Bearer $token")
        connection.doOutput = true
        connection.connectTimeout = desktopConnectTimeoutMs
        connection.readTimeout = desktopReadTimeoutMs

        val body = JSONObject().apply {
            put("message", message)
            put("token", token)
            if (pendingSync != null && pendingSync.length() > 0) {
                put("pending_sync", pendingSync)
            }
        }
        OutputStreamWriter(connection.outputStream).use { it.write(body.toString()) }

        val responseCode = connection.responseCode
        val stream = if (responseCode in 200..299) connection.inputStream else connection.errorStream
        val json = JSONObject(stream.bufferedReader().use { it.readText() })

        if (responseCode !in 200..299) {
            throw RuntimeException(json.optString("error", "HTTP $responseCode"))
        }
        return json.optString("answer", "[empty response]")
    }

    private fun callClaude(apiKey: String, systemPrompt: String, message: String): String {
        val modelId = prefs.getString("claude_model_id", null) ?: "claude-sonnet-5"
        val url = URL("https://api.anthropic.com/v1/messages")
        val connection = url.openConnection() as HttpURLConnection
        connection.requestMethod = "POST"
        connection.setRequestProperty("Content-Type", "application/json")
        connection.setRequestProperty("x-api-key", apiKey)
        connection.setRequestProperty("anthropic-version", "2023-06-01")
        connection.doOutput = true
        connection.connectTimeout = 10_000
        connection.readTimeout = 60_000

        val body = JSONObject().apply {
            put("model", modelId)
            put("max_tokens", 1024)
            if (systemPrompt.isNotBlank()) put("system", systemPrompt)
            put("messages", org.json.JSONArray().put(
                JSONObject().apply { put("role", "user"); put("content", message) }
            ))
        }
        OutputStreamWriter(connection.outputStream).use { it.write(body.toString()) }

        val responseCode = connection.responseCode
        val stream = if (responseCode in 200..299) connection.inputStream else connection.errorStream
        val json = JSONObject(stream.bufferedReader().use { it.readText() })

        if (responseCode !in 200..299) {
            val errMsg = json.optJSONObject("error")?.optString("message") ?: "HTTP $responseCode"
            throw RuntimeException(errMsg)
        }

        val contentArray = json.optJSONArray("content") ?: return "[empty response]"
        val textParts = mutableListOf<String>()
        for (i in 0 until contentArray.length()) {
            val block = contentArray.getJSONObject(i)
            if (block.optString("type") == "text") textParts.add(block.optString("text"))
        }
        return if (textParts.isEmpty()) "[empty response]" else textParts.joinToString("")
    }

    private fun callGemini(apiKey: String, systemPrompt: String, message: String): String {
        val modelId = prefs.getString("gemini_model_id", null) ?: "gemini-2.5-flash"
        val url = URL("https://generativelanguage.googleapis.com/v1beta/models/$modelId:generateContent?key=$apiKey")
        val connection = url.openConnection() as HttpURLConnection
        connection.requestMethod = "POST"
        connection.setRequestProperty("Content-Type", "application/json")
        connection.doOutput = true
        connection.connectTimeout = 10_000
        connection.readTimeout = 60_000

        val body = JSONObject().apply {
            put("contents", org.json.JSONArray().put(
                JSONObject().apply {
                    put("parts", org.json.JSONArray().put(JSONObject().apply { put("text", message) }))
                }
            ))
            if (systemPrompt.isNotBlank()) {
                put("systemInstruction", JSONObject().apply {
                    put("parts", org.json.JSONArray().put(JSONObject().apply { put("text", systemPrompt) }))
                })
            }
            put("generationConfig", JSONObject().apply { put("maxOutputTokens", 1024) })
        }
        OutputStreamWriter(connection.outputStream).use { it.write(body.toString()) }

        val responseCode = connection.responseCode
        val stream = if (responseCode in 200..299) connection.inputStream else connection.errorStream
        val json = JSONObject(stream.bufferedReader().use { it.readText() })

        if (responseCode !in 200..299) {
            val errMsg = json.optJSONObject("error")?.optString("message") ?: "HTTP $responseCode"
            throw RuntimeException(errMsg)
        }

        val candidates = json.optJSONArray("candidates") ?: return "[empty response]"
        if (candidates.length() == 0) return "[empty response]"
        val parts = candidates.getJSONObject(0).optJSONObject("content")?.optJSONArray("parts")
        if (parts == null || parts.length() == 0) return "[empty response]"
        return parts.getJSONObject(0).optString("text", "[empty response]")
    }
}
