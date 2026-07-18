package com.gremlin.app

import android.os.Bundle
import android.view.Gravity
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONObject
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL

/**
 * Opened from a hologram head-slot (JsBridge.openModelSettings) -- shows
 * and edits one of the 4 local consult models' tunable fields. Unlike
 * SettingsActivity's free-form admin command box, saves here go to the
 * dedicated /admin/model-edit endpoint (JSON body, no shell involved),
 * so there's no quoting/escaping to get right and no need for this app
 * to know the desktop's project directory.
 */
class ModelSettingsActivity : AppCompatActivity() {

    // Same allowlist as gremlin_core.model_scan.EDITABLE_FIELDS --
    // model_path is deliberately not editable here, swapping the actual
    // file stays a `models --hf` job on the desktop.
    private val editableFields = listOf("display_name", "chat_format", "n_gpu_layers", "n_ctx")

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_model_settings)

        val prefs = getSharedPreferences("gremlin_prefs", MODE_PRIVATE)
        val modelName = intent.getStringExtra("modelName") ?: ""
        val host = intent.getStringExtra("host") ?: ""
        val port = intent.getIntExtra("port", 0)
        val token = intent.getStringExtra("token") ?: ""

        findViewById<TextView>(R.id.model_title).text = modelName.ifEmpty { "(unknown model)" }

        val adminTokenInput = findViewById<EditText>(R.id.model_admin_token_input)
        adminTokenInput.setText(prefs.getString("admin_token", ""))

        val infoView = findViewById<TextView>(R.id.model_info)
        val statusView = findViewById<TextView>(R.id.model_status_output)
        val fieldsContainer = findViewById<LinearLayout>(R.id.model_fields_container)

        if (host.isEmpty() || modelName.isEmpty()) {
            infoView.text = "Not paired with a desktop"
            return
        }

        fun saveField(field: String, value: String) {
            val adminToken = adminTokenInput.text.toString().trim()
            if (adminToken.isEmpty()) {
                Toast.makeText(this, "Enter the admin token first", Toast.LENGTH_SHORT).show()
                return
            }
            prefs.edit().putString("admin_token", adminToken).apply()
            statusView.text = "Saving..."
            Thread {
                val result = editModelField(host, port, adminToken, modelName, field, value)
                runOnUiThread { statusView.text = result }
            }.start()
        }

        fun renderFields(model: JSONObject) {
            infoView.text = "Type: ${model.optString("type", "?")}"
            fieldsContainer.removeAllViews()
            for (field in editableFields) {
                if (!model.has(field)) continue

                val row = LinearLayout(this)
                row.orientation = LinearLayout.HORIZONTAL
                row.gravity = Gravity.CENTER_VERTICAL
                row.setPadding(0, 8, 0, 8)

                val label = TextView(this)
                label.text = field
                label.textSize = 12f
                label.layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)

                val input = EditText(this)
                input.setText(model.opt(field)?.toString() ?: "")
                input.layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 2f)

                val button = Button(this)
                button.text = "Save"
                button.setOnClickListener { saveField(field, input.text.toString().trim()) }

                row.addView(label)
                row.addView(input)
                row.addView(button)
                fieldsContainer.addView(row)
            }
        }

        statusView.text = "Loading..."
        Thread {
            try {
                val status = fetchStatus(host, port, token)
                val models = status.optJSONArray("models")
                var found: JSONObject? = null
                if (models != null) {
                    for (i in 0 until models.length()) {
                        val m = models.getJSONObject(i)
                        if (m.optString("name") == modelName) {
                            found = m
                            break
                        }
                    }
                }
                runOnUiThread {
                    statusView.text = ""
                    if (found != null) renderFields(found) else infoView.text = "Couldn't find '$modelName' in /status"
                }
            } catch (e: Exception) {
                runOnUiThread { infoView.text = "Couldn't reach desktop: ${e.message}" }
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
        if (responseCode !in 200..299) throw RuntimeException("HTTP $responseCode")
        return JSONObject(text)
    }

    private fun editModelField(host: String, port: Int, adminToken: String, name: String, field: String, value: String): String {
        return try {
            val url = URL("http://$host:$port/admin/model-edit")
            val connection = url.openConnection() as HttpURLConnection
            connection.requestMethod = "POST"
            connection.setRequestProperty("Content-Type", "application/json")
            connection.setRequestProperty("X-Admin-Token", adminToken)
            connection.doOutput = true
            connection.connectTimeout = 8_000
            connection.readTimeout = 15_000

            val body = JSONObject().apply {
                put("name", name)
                put("field", field)
                put("value", value)
            }
            OutputStreamWriter(connection.outputStream).use { it.write(body.toString()) }

            val responseCode = connection.responseCode
            val stream = if (responseCode in 200..299) connection.inputStream else connection.errorStream
            val json = JSONObject(stream.bufferedReader().use { it.readText() })

            if (responseCode in 200..299 && json.optBoolean("ok", false)) {
                "Saved"
            } else {
                "Failed: ${json.optString("error", "HTTP $responseCode")}"
            }
        } catch (e: Exception) {
            "Couldn't reach desktop: ${e.message}"
        }
    }
}
