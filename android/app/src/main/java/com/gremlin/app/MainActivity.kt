package com.gremlin.app

import android.content.Intent
import android.content.SharedPreferences
import android.os.Bundle
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.method.ScrollingMovementMethod
import android.text.style.ForegroundColorSpan
import android.text.style.RelativeSizeSpan
import android.view.View
import android.webkit.JavascriptInterface
import android.webkit.WebView
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.google.zxing.integration.android.IntentIntegrator
import com.google.zxing.integration.android.IntentResult
import java.io.File
import java.net.URI

class MainActivity : AppCompatActivity() {

    private lateinit var prefs: SharedPreferences
    private lateinit var gremlinClient: GremlinClient

    private lateinit var connectionLabel: TextView
    private lateinit var chatLog: TextView
    private lateinit var thinkingStatus: TextView
    private lateinit var messageInput: EditText
    private lateinit var hologramView: WebView

    // Internal app storage -- no permission needed on any Android
    // version, unlike shared/external storage.
    private val historyFile: File by lazy { File(filesDir, "chat_history.txt") }

    // Must be registered as a property, not inside onCreate's body --
    // ActivityResultContracts requires registration before the activity
    // reaches STARTED. Storage Access Framework: writes wherever the
    // user picks via the system file dialog, no manifest permission
    // needed on any Android version.
    private val exportLauncher = registerForActivityResult(ActivityResultContracts.CreateDocument("text/plain")) { uri ->
        if (uri == null) return@registerForActivityResult
        try {
            contentResolver.openOutputStream(uri)?.use { it.write(chatLog.text.toString().toByteArray()) }
            Toast.makeText(this, "Chat exported", Toast.LENGTH_SHORT).show()
        } catch (e: Exception) {
            Toast.makeText(this, "Export failed: ${e.message}", Toast.LENGTH_LONG).show()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        prefs = getSharedPreferences("gremlin_prefs", MODE_PRIVATE)
        gremlinClient = GremlinClient(prefs, applicationContext)

        connectionLabel = findViewById(R.id.connection_label)
        chatLog = findViewById(R.id.chat_log)
        chatLog.movementMethod = ScrollingMovementMethod()
        if (historyFile.exists()) {
            chatLog.text = historyFile.readText()
        }
        thinkingStatus = findViewById(R.id.thinking_status)
        messageInput = findViewById(R.id.message_input)

        hologramView = findViewById(R.id.hologram_view)
        hologramView.settings.javaScriptEnabled = true
        hologramView.addJavascriptInterface(JsBridge(), "Android")
        hologramView.loadUrl("file:///android_asset/hologram.html")

        findViewById<Button>(R.id.scan_button).setOnClickListener { startQrScan() }
        findViewById<Button>(R.id.send_button).setOnClickListener { sendMessage() }
        findViewById<Button>(R.id.export_button).setOnClickListener {
            exportLauncher.launch("gremlin-chat-${System.currentTimeMillis()}.txt")
        }
    }

    override fun onResume() {
        super.onResume()
        updateConnectionLabel()
        // Best-effort refresh so the hologram's head-slot labels (and
        // ModelSettingsActivity, opened from one of them) have something
        // reasonably fresh cached without the WebView itself needing to
        // do any networking -- see JsBridge.getStatusJson() below.
        Thread { gremlinClient.fetchStatusRaw() }.start()
    }

    private inner class JsBridge {
        @JavascriptInterface
        fun openSettings() {
            runOnUiThread {
                val intent = Intent(this@MainActivity, SettingsActivity::class.java)
                intent.putExtra("host", prefs.getString("host", null))
                intent.putExtra("port", prefs.getInt("port", 0))
                intent.putExtra("token", prefs.getString("token", null))
                startActivity(intent)
            }
        }

        @JavascriptInterface
        fun openModelSettings(name: String) {
            runOnUiThread {
                val intent = Intent(this@MainActivity, ModelSettingsActivity::class.java)
                intent.putExtra("modelName", name)
                intent.putExtra("host", prefs.getString("host", null))
                intent.putExtra("port", prefs.getInt("port", 0))
                intent.putExtra("token", prefs.getString("token", null))
                intent.putExtra("adminToken", prefs.getString("admin_token", null))
                startActivity(intent)
            }
        }

        // Synchronous by necessity -- addJavascriptInterface calls block
        // the WebView's JS thread for their return value, there's no way
        // to hand back a Promise here. Whatever was last fetched in
        // onResume (or an empty string if nothing's been paired/fetched
        // yet) is what the hologram gets; hologram.html already retries
        // once shortly after load to cover that empty-first-call case.
        @JavascriptInterface
        fun getStatusJson(): String {
            return prefs.getString("cached_status_json", "") ?: ""
        }

        @JavascriptInterface
        fun quit() {
            // No-op on Android -- back/home already covers this. Kept so
            // the shared hologram.html doesn't need Android-specific logic
            // beyond hiding the button, which it already does in JS.
        }
    }

    /** Reflects configuration, not the outcome of the last message --
     * see appendToLog for what actually answered each specific message. */
    private fun updateConnectionLabel() {
        val host = prefs.getString("host", null)
        val port = prefs.getInt("port", 0)
        val hasAnthropicKey = !prefs.getString("anthropic_key", null).isNullOrBlank()
        val hasGeminiKey = !prefs.getString("gemini_key", null).isNullOrBlank()

        connectionLabel.text = when {
            host != null && port != 0 -> "Paired with $host:$port (falls back to direct API away from home)"
            hasAnthropicKey || hasGeminiKey -> "Standalone mode -- not paired with a desktop"
            else -> "Not set up yet -- tap the hologram for Settings, or pair with a desktop below"
        }
    }

    private fun startQrScan() {
        IntentIntegrator(this)
            .setDesiredBarcodeFormats(listOf(IntentIntegrator.QR_CODE))
            .setPrompt("Scan the pairing code shown by `gremlin serve`")
            .setBeepEnabled(false)
            .initiateScan()
    }

    // onActivityResult is deprecated in favor of the newer Activity Result
    // API, but it's still the standard, documented integration path for
    // zxing-android-embedded's classic IntentIntegrator flow. Android
    // Studio will likely show a deprecation lint warning here -- expected,
    // safe to ignore for this specific library usage.
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: android.content.Intent?) {
        val result: IntentResult? = IntentIntegrator.parseActivityResult(requestCode, resultCode, data)
        if (result?.contents == null) {
            super.onActivityResult(requestCode, resultCode, data)
            return
        }
        handleScannedPairingUrl(result.contents)
    }

    private fun handleScannedPairingUrl(scanned: String) {
        try {
            val uri = URI(scanned)
            val scannedToken = uri.query
                ?.split("&")
                ?.map { it.split("=", limit = 2) }
                ?.firstOrNull { it.getOrNull(0) == "token" }
                ?.getOrNull(1)

            if (uri.host == null || scannedToken == null) {
                Toast.makeText(this, "That QR code doesn't look like a Gremlin pairing code", Toast.LENGTH_LONG).show()
                return
            }

            val resolvedPort = if (uri.port != -1) uri.port else 8765
            prefs.edit()
                .putString("host", uri.host)
                .putInt("port", resolvedPort)
                .putString("token", scannedToken)
                .apply()

            updateConnectionLabel()
            appendToLog("(paired with ${uri.host}:$resolvedPort)")
        } catch (e: Exception) {
            Toast.makeText(this, "Couldn't read that QR code: ${e.message}", Toast.LENGTH_LONG).show()
        }
    }

    /** For plain/system lines (e.g. "(paired with ...)") -- unstyled,
     * same as always. Chat turns use appendUserTurn/appendAssistantTurn
     * below instead, which build a styled fragment and pass it through
     * this same append+persist path. */
    private fun appendToLog(line: String) {
        appendStyled(line)
    }

    private fun appendStyled(fragment: CharSequence) {
        if (chatLog.text.isNotEmpty()) chatLog.append("\n\n")
        chatLog.append(fragment)
        try {
            historyFile.writeText(chatLog.text.toString())
        } catch (e: Exception) {
            // Non-fatal -- chat still works in-memory for this session
            // even if persistence fails for some reason (e.g. full disk).
        }
    }

    // Claude-Code-style turn blocks: a `›` prompt glyph in the accent
    // color for what you typed, plain text for gremlin's reply, and any
    // source/consult status as a small dim sub-line rather than an
    // inline suffix -- same visual language as the desktop chat panel
    // (gui/assets/main.html), reusing the same Theme.Gremlin colors so
    // both platforms actually look identical, not just similar.
    private fun appendUserTurn(message: String) {
        val accent = ContextCompat.getColor(this, R.color.gremlin_accent)
        val builder = SpannableStringBuilder()
        val promptStart = builder.length
        builder.append("› ")
        builder.setSpan(ForegroundColorSpan(accent), promptStart, builder.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
        builder.append(message)
        appendStyled(builder)
    }

    private fun appendAssistantTurn(answer: String, subStatus: String?) {
        val secondary = ContextCompat.getColor(this, R.color.gremlin_text_secondary)
        val builder = SpannableStringBuilder()
        builder.append(answer)
        if (!subStatus.isNullOrEmpty()) {
            builder.append("\n")
            val subStart = builder.length
            builder.append(subStatus)
            builder.setSpan(ForegroundColorSpan(secondary), subStart, builder.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            builder.setSpan(RelativeSizeSpan(0.85f), subStart, builder.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
        }
        appendStyled(builder)
    }

    private fun sendMessage() {
        val message = messageInput.text.toString().trim()
        if (message.isEmpty()) return

        if (message.startsWith("/")) {
            messageInput.setText("")
            handleSlashCommand(message)
            return
        }

        appendUserTurn(message)
        messageInput.setText("")

        // Pushed directly rather than polled -- unlike the desktop
        // window (a separate process from wherever chat actually
        // happens), this WebView lives in the same activity as the
        // chat call itself, so there's no need for the file-based
        // signal gremlin_core.consult uses for the desktop case.
        hologramView.evaluateJavascript("setTalking(true)", null)
        thinkingStatus.visibility = View.VISIBLE

        Thread {
            val result = gremlinClient.chat(message)
            runOnUiThread {
                val subStatus = when (result.source) {
                    "claude" -> "(standalone, via Claude)"
                    "gemini" -> "(standalone, via Gemini)"
                    else -> null
                }
                appendAssistantTurn(result.answer, subStatus)
                hologramView.evaluateJavascript("setTalking(false)", null)
                thinkingStatus.visibility = View.GONE
            }
        }.start()
    }

    /** Slash commands are intercepted before the normal chat path -- all
     * of them talk to the desktop's admin-token-gated routes via
     * GremlinClient, rendered as a visually distinct "system" turn (see
     * appendSystemTurn) right in this same chat, no need to go into
     * Settings. Same split as the desktop chat panel
     * (gui/assets/main.html's handleSlashCommand), plus /desktop and
     * /reboot here since those are specifically about controlling the
     * desktop *from the phone* -- redundant on the desktop's own chat,
     * where you're already sitting at it. */
    private fun handleSlashCommand(message: String) {
        appendUserTurn(message)
        val parts = message.trim().split(Regex("\\s+"))
        val cmd = parts.getOrNull(0) ?: ""

        when (cmd) {
            "/desktop" -> {
                val command = message.removePrefix("/desktop").trim()
                if (command.isEmpty()) {
                    appendSystemTurn("Usage: /desktop <command>", true)
                } else {
                    runAdminSlash { gremlinClient.runCommand(command, asRoot = false) }
                }
            }

            "/root" -> {
                val command = message.removePrefix("/root").trim()
                if (command.isEmpty()) {
                    appendSystemTurn("Usage: /root <command>", true)
                } else {
                    runAdminSlash { gremlinClient.runCommand(command, asRoot = true) }
                }
            }

            "/reboot" -> {
                val confirmed = parts.getOrNull(1) == "confirm"
                if (!confirmed) {
                    appendSystemTurn("This reboots the desktop right now. Type \"/reboot confirm\" to proceed.", false)
                } else {
                    runAdminSlash { gremlinClient.reboot() }
                }
            }

            "/edit" -> {
                val rest = message.removePrefix("/edit").trim()
                val confirmed = rest.endsWith(" confirm")
                val goal = (if (confirmed) rest.removeSuffix("confirm") else rest).trim()
                if (goal.isEmpty()) {
                    appendSystemTurn("Usage: /edit <what to change>  (then /edit <what to change> confirm to actually apply it)", true)
                } else if (!confirmed) {
                    appendSystemTurn(
                        "This asks Gremlin to propose a code change for itself, reviewed by claude+gemini, " +
                            "and commits it to git if both approve. Type \"/edit $goal confirm\" to proceed.",
                        false,
                    )
                } else {
                    runAdminSlash { gremlinClient.selfEdit(goal, runTests = true) }
                }
            }

            "/snapshots" -> runAdminSlash { gremlinClient.listSnapshots() }

            "/rollback" -> {
                val number = parts.getOrNull(1)
                val confirmed = parts.getOrNull(2) == "confirm"
                if (number == null) {
                    appendSystemTurn("Usage: /rollback <number>  (then /rollback <number> confirm to actually do it)", true)
                } else if (!confirmed) {
                    appendSystemTurn(
                        "This rolls back to snapshot $number AND REBOOTS the desktop. " +
                            "Type \"/rollback $number confirm\" to proceed.",
                        false,
                    )
                } else {
                    runAdminSlash { gremlinClient.rollback(number) }
                }
            }

            else -> appendSystemTurn(
                "Unknown command: $cmd\nAvailable: /desktop <command>, /root <command>, /edit <goal>, /reboot, /snapshots, /rollback <number>",
                true,
            )
        }
    }

    private fun runAdminSlash(call: () -> AdminResult) {
        thinkingStatus.visibility = View.VISIBLE
        Thread {
            val result = call()
            runOnUiThread {
                appendSystemTurn(result.message, !result.ok)
                thinkingStatus.visibility = View.GONE
            }
        }.start()
    }

    private fun appendSystemTurn(text: String, isError: Boolean) {
        val color = ContextCompat.getColor(this, if (isError) R.color.gremlin_error else R.color.gremlin_system)
        val builder = SpannableStringBuilder()
        builder.append(text)
        builder.setSpan(ForegroundColorSpan(color), 0, builder.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
        appendStyled(builder)
    }
}
