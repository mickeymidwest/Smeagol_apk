package com.gremlin.app

import android.content.SharedPreferences
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.view.View
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.ProgressBar
import android.widget.RadioGroup
import android.widget.SeekBar
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import com.gremlin.app.llama.LocalLlama
import com.gremlin.app.llama.LocalModelManager
import com.gremlin.app.voice.VoiceOutput
import org.json.JSONObject
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL
import java.util.Locale

class SettingsActivity : AppCompatActivity() {

    // Separate from MainActivity's VoiceOutput -- this one only exists to
    // back the "Test voice" button here, scoped to this activity's own
    // lifecycle rather than reaching into MainActivity's instance.
    private var testTts: TextToSpeech? = null

    override fun onDestroy() {
        super.onDestroy()
        testTts?.shutdown()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)

        val prefs = getSharedPreferences("gremlin_prefs", MODE_PRIVATE)
        val host = intent.getStringExtra("host") ?: ""
        val port = intent.getIntExtra("port", 0)
        val token = intent.getStringExtra("token") ?: ""

        findViewById<TextView>(R.id.settings_connection_label).text =
            if (host.isNotEmpty()) "Paired with $host:$port" else "Not paired with a desktop"

        findViewById<Button>(R.id.settings_repair_button).setOnClickListener {
            prefs.edit().remove("host").remove("port").remove("token").apply()
            startQrScan()
        }

        findViewById<Button>(R.id.show_commands_button).setOnClickListener { showCommandsReference() }

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

        // Admin token field lives further down this screen (see below),
        // but it's grabbed here too so the one "Save" button covers
        // everything on this screen -- it used to only get persisted as
        // a side effect of clicking "Run on Desktop"/"Reboot Desktop",
        // which meant typing a token (or a *new* token) and just
        // navigating away silently never saved it at all.
        val adminTokenInputForSave = findViewById<EditText>(R.id.admin_token_input)

        findViewById<Button>(R.id.save_keys_button).setOnClickListener {
            val preferred = if (providerGroup.checkedRadioButtonId == R.id.prefer_gemini_radio) "gemini" else "claude"
            prefs.edit()
                .putString("anthropic_key", anthropicInput.text.toString().trim())
                .putString("gemini_key", geminiInput.text.toString().trim())
                .putString("away_preferred", preferred)
                .putString("claude_model_id", claudeModelSpinner.selectedItem as String)
                .putString("gemini_model_id", geminiModelSpinner.selectedItem as String)
                .putString("admin_token", adminTokenInputForSave.text.toString().trim())
                .apply()
            Toast.makeText(this, "Saved", Toast.LENGTH_SHORT).show()
        }

        setUpLocalModelSection(prefs)
        setUpVoiceSection(prefs)

        if (host.isNotEmpty()) {
            loadStatus(host, port, token)
        } else {
            findViewById<TextView>(R.id.settings_persona_info).text = "(not paired -- pair with a desktop to see this)"
            findViewById<TextView>(R.id.settings_models_list).text = ""
        }

        // Admin section -- deliberately a separate token from the regular
        // pairing token above (see server.py's get_or_create_admin_token
        // for why). Nothing here is reachable without the user manually
        // entering this token themselves.
        val adminTokenInput = findViewById<EditText>(R.id.admin_token_input)
        val adminCommandInput = findViewById<EditText>(R.id.admin_command_input)
        val adminResultOutput = findViewById<TextView>(R.id.admin_result_output)
        adminTokenInput.setText(prefs.getString("admin_token", ""))

        findViewById<Button>(R.id.admin_run_button).setOnClickListener {
            val adminToken = adminTokenInput.text.toString().trim()
            val command = adminCommandInput.text.toString().trim()
            if (adminToken.isEmpty()) {
                Toast.makeText(this, "Enter the admin token first", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            if (command.isEmpty()) {
                Toast.makeText(this, "Enter a command to run", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            if (host.isEmpty()) {
                Toast.makeText(this, "Not paired with a desktop", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            prefs.edit().putString("admin_token", adminToken).apply()
            adminResultOutput.text = "Running..."
            runAdminCommand(host, port, adminToken, command, adminResultOutput)
        }

        findViewById<Button>(R.id.admin_reboot_button).setOnClickListener {
            val adminToken = adminTokenInput.text.toString().trim()
            if (adminToken.isEmpty()) {
                Toast.makeText(this, "Enter the admin token first", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            if (host.isEmpty()) {
                Toast.makeText(this, "Not paired with a desktop", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            prefs.edit().putString("admin_token", adminToken).apply()
            AlertDialog.Builder(this)
                .setTitle("Reboot desktop?")
                .setMessage("This will reboot $host right now. It should come back up and reconnect on its own if auto-start is set up.")
                .setPositiveButton("Reboot") { _, _ -> triggerReboot(host, port, adminToken) }
                .setNegativeButton("Cancel", null)
                .show()
        }
    }

    /** Just a reference -- these all run from the chat input on the main
     * screen (MainActivity.handleSlashCommand), not from here. Kept as
     * plain text in a dialog rather than a separate screen since it's
     * static content with nothing to interact with. */
    private fun showCommandsReference() {
        val text = """
            Type these directly in the chat box on the main screen -- no need to come back here.

            /desktop <command>
              Run a command on the desktop (sandboxed: confined directory, timeout, no root).
              e.g. /desktop ls -la
                   /desktop find /home -iname "*keyword*"

            /root <command>
              Same, but with sudo. Needs a sudo password cached first --
              run `gremlin set-sudo-password` on the desktop itself, once.
              e.g. /root pacman -S --noconfirm neovim
                   /root pacman -Syu --noconfirm

            /reboot
              Shows a confirmation, then /reboot confirm actually reboots the desktop.

            /snapshots
              List BTRFS snapshots (for rolling back if something breaks).

            /rollback <number>
              Shows a confirmation, then /rollback <number> confirm rolls the
              desktop back to that snapshot and reboots it.

            /updatecheck
              Checks pending pacman updates against Manjaro's own forum for
              known issues with those specific packages. Read-only, doesn't
              need the Admin token below -- just needs to be paired.

            /fix <path> <what's wrong>
              Shows a warning, then /fix <path> <what's wrong> confirm asks
              Gremlin's own registered models (not the separate claude CLI)
              to fix a specific file anywhere on the desktop -- your own
              scripts/configs, not system files. Backs up the file first,
              reverts automatically if the fix fails to compile.

            /claude <problem to fix>
              Shows a warning, then /claude <problem> confirm runs a full
              Claude Code session on the desktop with FULL autonomy -- it
              can read/write any file and run any command in the project
              to actually fix things, not just describe them. Uses the
              `claude` CLI already logged into your own account on the
              desktop, not a separate paid API key. Can take a while (up
              to 10 minutes) for anything nontrivial.

            Tips:
            - No shell, so wildcards (*), pipes (|), and && don't work -- just a
              program plus its own flags/arguments.
            - pacman -S / -Syu need --noconfirm added, since there's no way to
              answer its "Proceed? [Y/n]" prompt through this.
            - Not sure where a file is? /desktop find /home -iname "*name*"
              searches by partial name. Widening to /desktop find / ... searches
              everywhere but can be slow and may hit the timeout -- narrower is faster.

            All of the above need the Admin token entered below first.
        """.trimIndent()

        AlertDialog.Builder(this)
            .setTitle("Gremlin commands")
            .setMessage(text)
            .setPositiveButton("Close", null)
            .show()
    }

    private fun runAdminCommand(host: String, port: Int, adminToken: String, command: String, output: TextView) {
        Thread {
            try {
                val url = URL("http://$host:$port/admin/execute")
                val connection = url.openConnection() as HttpURLConnection
                connection.requestMethod = "POST"
                connection.setRequestProperty("Content-Type", "application/json")
                connection.setRequestProperty("X-Admin-Token", adminToken)
                connection.doOutput = true
                connection.connectTimeout = 8_000
                connection.readTimeout = 130_000

                val body = JSONObject().apply { put("command", command) }
                OutputStreamWriter(connection.outputStream).use { it.write(body.toString()) }

                val responseCode = connection.responseCode
                val stream = if (responseCode in 200..299) connection.inputStream else connection.errorStream
                val json = JSONObject(stream.bufferedReader().use { it.readText() })

                runOnUiThread {
                    output.text = if (responseCode in 200..299) {
                        "exit ${json.optInt("exit_code")}\n${json.optString("stdout")}\n${json.optString("stderr")}".trim()
                    } else {
                        "Error: ${json.optString("error", "HTTP $responseCode")}"
                    }
                }
            } catch (e: Exception) {
                runOnUiThread { output.text = "Couldn't reach desktop: ${e.message}" }
            }
        }.start()
    }

    private fun triggerReboot(host: String, port: Int, adminToken: String) {
        Thread {
            try {
                val url = URL("http://$host:$port/admin/reboot")
                val connection = url.openConnection() as HttpURLConnection
                connection.requestMethod = "POST"
                connection.setRequestProperty("Content-Type", "application/json")
                connection.setRequestProperty("X-Admin-Token", adminToken)
                connection.doOutput = true
                connection.connectTimeout = 8_000
                connection.readTimeout = 15_000
                OutputStreamWriter(connection.outputStream).use { it.write("{}") }
                val responseCode = connection.responseCode
                runOnUiThread {
                    Toast.makeText(
                        this,
                        if (responseCode in 200..299) "Reboot triggered" else "Reboot failed (HTTP $responseCode)",
                        Toast.LENGTH_LONG,
                    ).show()
                }
            } catch (e: Exception) {
                // A connection drop here is actually the expected/good
                // outcome once the reboot really starts -- don't treat
                // every exception as a failure worth alarming over.
                runOnUiThread { Toast.makeText(this, "Reboot request sent", Toast.LENGTH_LONG).show() }
            }
        }.start()
    }

    /** Offline on-device model: download into app-private storage, enable
     * it as the first away-mode provider (see GremlinClient.chatAway()),
     * or remove it to free the ~910MB back up. */
    private fun setUpLocalModelSection(prefs: SharedPreferences) {
        val statusText = findViewById<TextView>(R.id.local_model_status)
        val progressBar = findViewById<ProgressBar>(R.id.local_model_progress)
        val downloadButton = findViewById<Button>(R.id.local_model_download_button)
        val enabledCheckbox = findViewById<CheckBox>(R.id.local_model_enabled_checkbox)
        val removeButton = findViewById<Button>(R.id.local_model_remove_button)

        fun refresh() {
            val downloaded = LocalModelManager.isDownloaded(applicationContext)
            if (downloaded) {
                val sizeMb = LocalModelManager.modelFile(applicationContext).length() / (1024 * 1024)
                statusText.text = "Downloaded (${sizeMb}MB)"
                downloadButton.text = "Re-download offline model"
                enabledCheckbox.isEnabled = true
                removeButton.visibility = View.VISIBLE
                // Keep the path pref in sync with reality even if it was
                // somehow cleared without the file itself being removed.
                prefs.edit().putString("local_model_path", LocalModelManager.modelFile(applicationContext).absolutePath).apply()
            } else {
                statusText.text = "Not downloaded (~910MB, one-time download)"
                downloadButton.text = "Download offline model"
                enabledCheckbox.isEnabled = false
                removeButton.visibility = View.GONE
            }
            enabledCheckbox.isChecked = downloaded && prefs.getBoolean("local_model_enabled", false)
        }
        refresh()

        downloadButton.setOnClickListener {
            downloadButton.isEnabled = false
            progressBar.visibility = View.VISIBLE
            progressBar.progress = 0
            statusText.text = "Downloading..."
            Thread {
                val ok = LocalModelManager.download(applicationContext) { downloaded, total ->
                    runOnUiThread {
                        if (total > 0) {
                            progressBar.progress = ((downloaded * 100) / total).toInt()
                            statusText.text = "Downloading... ${downloaded / (1024 * 1024)}MB / ${total / (1024 * 1024)}MB"
                        }
                    }
                }
                runOnUiThread {
                    downloadButton.isEnabled = true
                    progressBar.visibility = View.GONE
                    Toast.makeText(
                        this,
                        if (ok) "Offline model downloaded" else "Download failed -- check connection and try again",
                        if (ok) Toast.LENGTH_SHORT else Toast.LENGTH_LONG,
                    ).show()
                    refresh()
                }
            }.start()
        }

        enabledCheckbox.setOnCheckedChangeListener { _, checked ->
            prefs.edit().putBoolean("local_model_enabled", checked).apply()
            if (!checked) {
                Thread { LocalLlama.unloadModel() }.start()
            }
        }

        removeButton.setOnClickListener {
            AlertDialog.Builder(this)
                .setTitle("Remove offline model?")
                .setMessage("Frees up ~910MB. You can download it again anytime.")
                .setPositiveButton("Remove") { _, _ ->
                    Thread {
                        LocalModelManager.delete(applicationContext, prefs)
                        runOnUiThread { refresh() }
                    }.start()
                }
                .setNegativeButton("Cancel", null)
                .show()
        }
    }

    /** Speaks replies aloud via Android's built-in TTS -- see
     * VoiceOutput.kt for the actual speak-on-reply logic (that lives in
     * MainActivity, since that's where replies actually arrive). This
     * just edits the prefs it reads (voice_enabled/voice_pitch/voice_rate)
     * and offers a way to preview them before leaving this screen. */
    private fun setUpVoiceSection(prefs: SharedPreferences) {
        val enabledCheckbox = findViewById<CheckBox>(R.id.voice_enabled_checkbox)
        val pitchSeekBar = findViewById<SeekBar>(R.id.voice_pitch_seekbar)
        val rateSeekBar = findViewById<SeekBar>(R.id.voice_rate_seekbar)
        val testButton = findViewById<Button>(R.id.voice_test_button)

        enabledCheckbox.isChecked = prefs.getBoolean("voice_enabled", false)
        pitchSeekBar.progress = (prefs.getFloat("voice_pitch", VoiceOutput.DEFAULT_PITCH) * 100).toInt()
        rateSeekBar.progress = (prefs.getFloat("voice_rate", VoiceOutput.DEFAULT_RATE) * 100).toInt()

        enabledCheckbox.setOnCheckedChangeListener { _, checked ->
            prefs.edit().putBoolean("voice_enabled", checked).apply()
        }

        // Saved on every move rather than only e.g. on stopTrackingTouch --
        // simpler, and a SeekBar drag is cheap enough that there's no
        // real cost to just always being up to date.
        pitchSeekBar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(seekBar: SeekBar, progress: Int, fromUser: Boolean) {
                prefs.edit().putFloat("voice_pitch", (progress.coerceAtLeast(10)) / 100f).apply()
            }
            override fun onStartTrackingTouch(seekBar: SeekBar) {}
            override fun onStopTrackingTouch(seekBar: SeekBar) {}
        })
        rateSeekBar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(seekBar: SeekBar, progress: Int, fromUser: Boolean) {
                prefs.edit().putFloat("voice_rate", (progress.coerceAtLeast(10)) / 100f).apply()
            }
            override fun onStartTrackingTouch(seekBar: SeekBar) {}
            override fun onStopTrackingTouch(seekBar: SeekBar) {}
        })

        testButton.setOnClickListener {
            if (testTts == null) {
                testTts = TextToSpeech(applicationContext) { status ->
                    if (status == TextToSpeech.SUCCESS) {
                        testTts?.language = Locale.US
                        speakTest(prefs)
                    } else {
                        Toast.makeText(this, "No text-to-speech engine available", Toast.LENGTH_LONG).show()
                    }
                }
            } else {
                speakTest(prefs)
            }
        }
    }

    private fun speakTest(prefs: SharedPreferences) {
        val engine = testTts ?: return
        engine.setPitch(prefs.getFloat("voice_pitch", VoiceOutput.DEFAULT_PITCH))
        engine.setSpeechRate(prefs.getFloat("voice_rate", VoiceOutput.DEFAULT_RATE))
        engine.speak("This is what I sound like.", TextToSpeech.QUEUE_FLUSH, null, "voice-test")
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
                    findViewById<TextView>(R.id.settings_persona_info).text = "Couldn't reach gremlin: ${e.message}"
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
