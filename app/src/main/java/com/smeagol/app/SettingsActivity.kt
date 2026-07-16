package com.smeagol.app

import android.os.Bundle
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.RadioGroup
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

class SettingsActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)

        val prefs = getSharedPreferences("smeagol_prefs", MODE_PRIVATE)
        val host = intent.getStringExtra("host") ?: ""
        val port = intent.getIntExtra("port", 0)
        val token = intent.getStringExtra("token") ?: ""

        findViewById<TextView>(R.id.settings_connection_label).text =
            if (host.isNotEmpty()) "Paired with $host:$port" else "Not paired with a desktop"

        findViewById<Button>(R.id.settings_repair_button).setOnClickListener {
            prefs.edit().remove("host").remove("port").remove("token").apply()
            startQrScan()
        }

        // Away-mode API key fields, pre-filled with whatever's already saved
        val anthropicInput = findViewById<EditText>(R.id.anthropic_key_input)
        val geminiInput = findViewById<EditText>(R.id.gemini_key_input)
        val providerGroup = findViewById<RadioGroup>(R.id.preferred_provider_group)
        val claudeModelSpinner = findViewById<Spinner>(R.id.claude_model_spinner)
        val geminiModelSpinner = findViewById<Spinner>(R.id.gemini_model_spinner)

        anthropicInput.setText(prefs.getString("anthropic_key", ""))
        geminiInput.setText(prefs.getString("gemini_key", ""))
        providerGroup.check(
            if (prefs.getString("away_preferred", "claude") == "gemini") R.id.prefer_gemini_radio
            else R.id.prefer_claude_radio
        )

        setUpModelSpinner(claudeModelSpinner, R.array.claude_model_choices, prefs.getString("claude_model_id", null))
        setUpModelSpinner(geminiModelSpinner, R.array.gemini_model_choices, prefs.getString("gemini_model_id", null))

        findViewById<Button>(R.id.save_keys_button).setOnClickListener {
            val preferred = if (providerGroup.checkedRadioButtonId == R.id.prefer_gemini_radio) "gemini" else "claude"
            prefs.edit()
                .putString("anthropic_key", anthropicInput.text.toString().trim())
                .putString("gemini_key", geminiInput.text.toString().trim())
                .putString("away_preferred", preferred)
                .putString("claude_model_id", claudeModelSpinner.selectedItem as String)
                .putString("gemini_model_id", geminiModelSpinner.selectedItem as String)
                .apply()
            Toast.makeText(this, "Saved", Toast.LENGTH_SHORT).show()
        }

        if (host.isNotEmpty()) {
            loadStatus(host, port, token)
        } else {
            findViewById<TextView>(R.id.settings_persona_info).text = "(not paired -- pair with a desktop to see this)"
            findViewById<TextView>(R.id.settings_models_list).text = ""
        }
    }

    private fun setUpModelSpinner(spinner: Spinner, choicesArrayRes: Int, savedValue: String?) {
        val adapter = ArrayAdapter.createFromResource(this, choicesArrayRes, android.R.layout.simple_spinner_item)
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
        spinner.adapter = adapter

        if (savedValue != null) {
            val choices = resources.getStringArray(choicesArrayRes)
            val index = choices.indexOf(savedValue)
            if (index >= 0) spinner.setSelection(index)
            // if savedValue isn't one of the current choices (e.g. an
            // older saved model id), just leaves the default (index 0)
            // selected rather than crashing on a bad index
        }
    }

    private fun startQrScan() {
        // Re-pairing from Settings hands off to the same scan flow
        // MainActivity uses -- simplest way to reuse it without
        // duplicating the zxing integration is to just finish back to
        // MainActivity and let the user tap "Pair with Desktop" there.
        Toast.makeText(this, "Tap \"Pair with Desktop\" on the main screen to scan a new code", Toast.LENGTH_LONG).show()
        finish()
    }

    private fun loadStatus(host: String, port: Int, token: String) {
        Thread {
            try {
                val json = fetchStatus(host, port, token)
                runOnUiThread { renderStatus(json) }
            } catch (e: Exception) {
                runOnUiThread {
                    findViewById<TextView>(R.id.settings_persona_info).text = "Couldn't reach smeagol: ${e.message}"
                }
            }
        }.start()
    }

    private fun fetchStatus(host: String, port: Int, token: String): JSONObject {
        val url = URL("http://$host:$port/status")
        val connection = url.openConnection() as HttpURLConnection
        connection.requestMethod = "GET"
        connection.setRequestProperty("Authorization", "Bearer $token")
        connection.connectTimeout = 8_000
        connection.readTimeout = 8_000

        val responseCode = connection.responseCode
        val stream = if (responseCode in 200..299) connection.inputStream else connection.errorStream
        val text = stream.bufferedReader().use { it.readText() }
        return JSONObject(text)
    }

    private fun renderStatus(json: JSONObject) {
        val fallback = json.optJSONArray("fallback_models")
        val consult = json.optJSONArray("consult_models")

        val personaText = buildString {
            append("Primary: ").append(json.optString("primary_model", "(none)")).append("\n")
            append("Fallback: ").append(joinOrNone(fallback)).append("\n")
            append("Consult group: ").append(joinOrNone(consult)).append("\n")
            append("Last resort: ").append(json.optString("last_resort_model", "(none)"))
        }
        findViewById<TextView>(R.id.settings_persona_info).text = personaText

        val models = json.optJSONArray("models")
        val modelsText = buildString {
            if (models == null || models.length() == 0) {
                append("(none)")
            } else {
                for (i in 0 until models.length()) {
                    val m = models.getJSONObject(i)
                    append("• ").append(m.optString("name")).append("  (").append(m.optString("type")).append(")")
                    if (i != models.length() - 1) append("\n")
                }
            }
        }
        findViewById<TextView>(R.id.settings_models_list).text = modelsText
    }

    private fun joinOrNone(arr: org.json.JSONArray?): String {
        if (arr == null || arr.length() == 0) return "(none)"
        return (0 until arr.length()).joinToString(", ") { arr.getString(it) }
    }
}
