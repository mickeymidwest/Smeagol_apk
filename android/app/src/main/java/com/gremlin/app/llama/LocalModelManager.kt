package com.gremlin.app.llama

import android.content.Context
import android.content.SharedPreferences
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Downloads and manages the offline on-device model file that
 * [LocalLlama] loads. Not bundled into the APK/repo -- same convention
 * the desktop side already follows (see the main README's "Confirmed
 * model sources": models are downloaded separately, never committed) --
 * so this fetches it into app-private storage on demand from Settings.
 */
object LocalModelManager {

    // mradermacher's GGUF quantization of huihui-ai/Llama-3.2-1B-Instruct-abliterated --
    // the same abliteration author (huihui-ai) and quantizer (mradermacher) the desktop's
    // own config/models.yaml already uses for its consult models, just small enough
    // (~910MB Q4_K_M) to actually live on a phone. Verified with a real HEAD request
    // before picking this repo/file, not guessed -- Content-Length was 955,445,792 bytes
    // at the time this was added; same "don't cite a repo without checking it's real"
    // rule the desktop README's model-sourcing section already follows.
    const val MODEL_URL =
        "https://huggingface.co/mradermacher/Llama-3.2-1B-Instruct-abliterated-GGUF/resolve/main/Llama-3.2-1B-Instruct-abliterated.Q4_K_M.gguf"
    const val MODEL_FILENAME = "gremlin-local-llama-3.2-1b-abliterated.Q4_K_M.gguf"
    const val EXPECTED_SIZE_BYTES = 955_445_792L

    fun modelFile(context: Context): File = File(context.filesDir, MODEL_FILENAME)

    fun isDownloaded(context: Context): Boolean {
        val f = modelFile(context)
        return f.exists() && f.length() > 0
    }

    /**
     * Downloads the model file synchronously -- callers run this off the
     * main thread (matches the rest of the app's plain-Thread pattern,
     * see MainActivity's other background calls) and marshal onProgress
     * callbacks to the UI thread themselves.
     *
     * Downloads to a `.part` file and only renames to the final name on
     * success, so a killed/interrupted download never leaves a corrupt
     * file that LocalLlama.loadModel() would try to load later.
     */
    fun download(context: Context, onProgress: (downloaded: Long, total: Long) -> Unit): Boolean {
        val dest = modelFile(context)
        val partial = File(context.filesDir, "$MODEL_FILENAME.part")
        var connection: HttpURLConnection? = null
        try {
            connection = (URL(MODEL_URL).openConnection() as HttpURLConnection).apply {
                instanceFollowRedirects = true
                connectTimeout = 15_000
                readTimeout = 30_000
            }
            val total = connection.contentLengthLong.takeIf { it > 0 } ?: EXPECTED_SIZE_BYTES

            connection.inputStream.use { input ->
                partial.outputStream().use { output ->
                    val buffer = ByteArray(64 * 1024)
                    var downloaded = 0L
                    while (true) {
                        val read = input.read(buffer)
                        if (read == -1) break
                        output.write(buffer, 0, read)
                        downloaded += read
                        onProgress(downloaded, total)
                    }
                }
            }

            if (!partial.renameTo(dest)) {
                partial.delete()
                return false
            }
            return true
        } catch (e: Exception) {
            partial.delete()
            return false
        } finally {
            connection?.disconnect()
        }
    }

    /** Removes the downloaded model and disables it in prefs -- used by
     * Settings' "Remove offline model" action to free up ~1GB of storage. */
    fun delete(context: Context, prefs: SharedPreferences) {
        LocalLlama.unloadModel()
        modelFile(context).delete()
        prefs.edit()
            .putBoolean("local_model_enabled", false)
            .remove("local_model_path")
            .apply()
    }
}
