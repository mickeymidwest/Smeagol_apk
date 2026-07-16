package com.smeagol.app

import android.content.Intent
import android.content.SharedPreferences
import android.os.Bundle
import android.text.method.ScrollingMovementMethod
import android.webkit.JavascriptInterface
import android.webkit.WebView
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import com.google.zxing.integration.android.IntentIntegrator
import com.google.zxing.integration.android.IntentResult
import java.io.File
import java.net.URI

class MainActivity : AppCompatActivity() {

    private lateinit var prefs: SharedPreferences
    private lateinit var smeagolClient: SmeagolClient

    private lateinit var connectionLabel: TextView
    private lateinit var chatLog: TextView
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

        prefs = getSharedPreferences("smeagol_prefs", MODE_PRIVATE)
        smeagolClient = SmeagolClient(prefs, applicationContext)

        connectionLabel = findViewById(R.id.connection_label)
        chatLog = findViewById(R.id.chat_log)
        chatLog.movementMethod = ScrollingMovementMethod()
        if (historyFile.exists()) {
            chatLog.text = historyFile.readText()
        }
        messageInput = findViewById(R.id.message_input)

        hologramView = findViewById(R.id.hologram_view)
        hologramView.settings.javaScriptEnabled = true
        hologramView.addJavascriptInterface(JsBridge(), "Android")
        hologramView.loadUrl("file:///android_asset/hologram.html")

        findViewById<Button>(R.id.scan_button).setOnClickListener { startQrScan() }
        findViewById<Button>(R.id.send_button).setOnClickListener { sendMessage() }
        findViewById<Button>(R.id.export_button).setOnClickListener {
            exportLauncher.launch("smeagol-chat-${System.currentTimeMillis()}.txt")
        }
    }

    override fun onResume() {
        super.onResume()
        updateConnectionLabel()
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
            .setPrompt("Scan the pairing code shown by `smeagol serve`")
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
                Toast.makeText(this, "That QR code doesn't look like a Smeagol pairing code", Toast.LENGTH_LONG).show()
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

    private fun appendToLog(line: String) {
        chatLog.append(if (chatLog.text.isEmpty()) line else "\n\n$line")
        try {
            historyFile.writeText(chatLog.text.toString())
        } catch (e: Exception) {
            // Non-fatal -- chat still works in-memory for this session
            // even if persistence fails for some reason (e.g. full disk).
        }
    }

    private fun sendMessage() {
        val message = messageInput.text.toString().trim()
        if (message.isEmpty()) return

        appendToLog("you: $message")
        messageInput.setText("")

        Thread {
            val result = smeagolClient.chat(message)
            runOnUiThread {
                val sourceTag = when (result.source) {
                    "desktop" -> ""
                    "claude" -> "  (standalone, via Claude)"
                    "gemini" -> "  (standalone, via Gemini)"
                    else -> ""
                }
                appendToLog("smeagol: ${result.answer}$sourceTag")
            }
        }.start()
    }
}
