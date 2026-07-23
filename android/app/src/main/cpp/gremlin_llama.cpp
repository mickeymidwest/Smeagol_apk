// JNI bridge to llama.cpp for fully offline, on-device inference -- this
// is what backs GremlinClient.kt's "local" away-mode provider, used when
// the desktop isn't reachable and no cloud API key is configured (or the
// user just prefers not to send anything off-device). Adapted from
// llama.cpp's own examples/llama.android/lib/src/main/cpp/ai_chat.cpp
// (pinned at release b10091, see android/llama.cpp submodule) rather than
// written from scratch, so the actual llama.cpp API usage below is
// verified-working code, not a guess at function signatures that may
// have drifted across versions.
//
// One deliberate simplification vs. that reference: it exposes
// processUserPrompt() and generateNextToken() as separate JNI calls for
// token-by-token streaming to the UI. Gremlin's away-mode chat is already
// a single blocking call end to end (see GremlinClient.chat()), so this
// collapses both into one generate() call that loops internally in C++
// and returns the whole assembled reply -- far fewer JNI round-trips,
// and nothing upstream needs incremental tokens today.
#include <android/log.h>
#include <jni.h>
#include <cmath>
#include <string>
#include <sstream>
#include <unistd.h>
#include <sampling.h>

#include "chat.h"
#include "common.h"
#include "llama.h"

#define TAG "GremlinLlama"
#define LOGi(...) ((void)__android_log_print(ANDROID_LOG_INFO,  TAG, __VA_ARGS__))
#define LOGw(...) ((void)__android_log_print(ANDROID_LOG_WARN,  TAG, __VA_ARGS__))
#define LOGe(...) ((void)__android_log_print(ANDROID_LOG_ERROR, TAG, __VA_ARGS__))
#define LOGd(...) ((void)__android_log_print(ANDROID_LOG_DEBUG, TAG, __VA_ARGS__))

static void gremlin_log_callback(ggml_log_level level, const char *text, void * /*user_data*/) {
    int prio = ANDROID_LOG_INFO;
    switch (level) {
        case GGML_LOG_LEVEL_ERROR: prio = ANDROID_LOG_ERROR; break;
        case GGML_LOG_LEVEL_WARN:  prio = ANDROID_LOG_WARN;  break;
        case GGML_LOG_LEVEL_DEBUG: prio = ANDROID_LOG_DEBUG; break;
        default:                   prio = ANDROID_LOG_INFO;  break;
    }
    __android_log_print(prio, TAG, "%s", text);
}

// ---------------------------------------------------------------------
// Resources: context, model, batch, chat templates, sampler
// ---------------------------------------------------------------------
constexpr int   N_THREADS_MIN        = 2;
constexpr int   N_THREADS_MAX        = 4;
constexpr int   N_THREADS_HEADROOM   = 2;

// A ~1B-parameter model's own trained context is usually well under this;
// init_context() below caps it to whichever is smaller anyway.
constexpr int   DEFAULT_CONTEXT_SIZE = 4096;
constexpr int   OVERFLOW_HEADROOM    = 4;
constexpr int   BATCH_SIZE           = 512;
constexpr float DEFAULT_SAMPLER_TEMP = 0.7f; // matches the desktop persona's default generate() temperature

static llama_model             *g_model;
static llama_context           *g_context;
static llama_batch              g_batch;
static common_chat_templates_ptr g_chat_templates;
static common_sampler           *g_sampler;

extern "C"
JNIEXPORT void JNICALL
Java_com_gremlin_app_llama_LocalLlama_init(JNIEnv * /*env*/, jobject /*unused*/) {
    ggml_log_set(gremlin_log_callback, nullptr);
    llama_backend_init();
    LOGi("Backend initialized.");
}

extern "C"
JNIEXPORT jint JNICALL
Java_com_gremlin_app_llama_LocalLlama_load(JNIEnv *env, jobject /*unused*/, jstring jmodel_path) {
    llama_model_params model_params = llama_model_default_params();

    const auto *model_path = env->GetStringUTFChars(jmodel_path, nullptr);
    LOGd("%s: loading model from %s", __func__, model_path);
    auto *model = llama_model_load_from_file(model_path, model_params);
    env->ReleaseStringUTFChars(jmodel_path, model_path);

    if (!model) {
        LOGe("%s: llama_model_load_from_file() failed", __func__);
        return 1;
    }
    g_model = model;
    return 0;
}

static llama_context *init_context(llama_model *model, const int n_ctx = DEFAULT_CONTEXT_SIZE) {
    if (!model) {
        LOGe("%s: model cannot be null", __func__);
        return nullptr;
    }

    const int n_threads = std::max(N_THREADS_MIN, std::min(N_THREADS_MAX,
                                    (int) sysconf(_SC_NPROCESSORS_ONLN) - N_THREADS_HEADROOM));
    LOGi("%s: using %d threads", __func__, n_threads);

    llama_context_params ctx_params = llama_context_default_params();
    const int trained_context_size = llama_model_n_ctx_train(model);
    const int actual_n_ctx = std::min(n_ctx, trained_context_size);
    if (n_ctx > trained_context_size) {
        LOGw("%s: model trained with only %d context, using %d instead of requested %d",
             __func__, trained_context_size, actual_n_ctx, n_ctx);
    }
    ctx_params.n_ctx = actual_n_ctx;
    ctx_params.n_batch = BATCH_SIZE;
    ctx_params.n_ubatch = BATCH_SIZE;
    ctx_params.n_threads = n_threads;
    ctx_params.n_threads_batch = n_threads;

    auto *context = llama_init_from_model(model, ctx_params);
    if (context == nullptr) {
        LOGe("%s: llama_init_from_model() returned null", __func__);
    }
    return context;
}

extern "C"
JNIEXPORT jint JNICALL
Java_com_gremlin_app_llama_LocalLlama_prepare(JNIEnv * /*env*/, jobject /*unused*/) {
    auto *context = init_context(g_model);
    if (!context) { return 1; }
    g_context = context;
    g_batch = llama_batch_init(BATCH_SIZE, 0, 1);
    g_chat_templates = common_chat_templates_init(g_model, "");

    common_params_sampling sparams;
    sparams.temp = DEFAULT_SAMPLER_TEMP;
    g_sampler = common_sampler_init(g_model, sparams);
    return 0;
}

// ---------------------------------------------------------------------
// Chat state -- reset per persona-prompt change (long-term) and per
// generate() call (short-term). Same structure as the reference this
// was adapted from; see its comments for why context shifting anchors
// on system_prompt_position specifically.
// ---------------------------------------------------------------------
constexpr const char *ROLE_SYSTEM    = "system";
constexpr const char *ROLE_USER      = "user";
constexpr const char *ROLE_ASSISTANT = "assistant";

static std::vector<common_chat_msg> chat_msgs;
static llama_pos system_prompt_position;
static llama_pos current_position;

static void reset_long_term_state() {
    chat_msgs.clear();
    system_prompt_position = 0;
    current_position = 0;
    if (g_context) {
        llama_memory_clear(llama_get_memory(g_context), false);
    }
}

static void shift_context() {
    const int n_discard = (current_position - system_prompt_position) / 2;
    LOGi("%s: discarding %d tokens", __func__, n_discard);
    llama_memory_seq_rm(llama_get_memory(g_context), 0, system_prompt_position, system_prompt_position + n_discard);
    llama_memory_seq_add(llama_get_memory(g_context), 0, system_prompt_position + n_discard, current_position, -n_discard);
    current_position -= n_discard;
}

static std::string chat_add_and_format(const std::string &role, const std::string &content) {
    common_chat_msg new_msg;
    new_msg.role = role;
    new_msg.content = content;
    auto formatted = common_chat_format_single(
            g_chat_templates.get(), chat_msgs, new_msg, role == ROLE_USER, /* use_jinja */ false);
    chat_msgs.push_back(new_msg);
    return formatted;
}

static int decode_tokens_in_batches(const llama_tokens &tokens, const llama_pos start_pos,
                                     bool compute_last_logit = false) {
    for (int i = 0; i < (int) tokens.size(); i += BATCH_SIZE) {
        const int cur_batch_size = std::min((int) tokens.size() - i, BATCH_SIZE);
        common_batch_clear(g_batch);

        if (start_pos + i + cur_batch_size >= (int) llama_n_ctx(g_context) - OVERFLOW_HEADROOM) {
            LOGw("%s: batch won't fit in context, shifting", __func__);
            shift_context();
        }

        for (int j = 0; j < cur_batch_size; j++) {
            const llama_token token_id = tokens[i + j];
            const llama_pos position = start_pos + i + j;
            const bool want_logit = compute_last_logit && (i + j == (int) tokens.size() - 1);
            common_batch_add(g_batch, token_id, position, {0}, want_logit);
        }

        if (llama_decode(g_context, g_batch) != 0) {
            LOGe("%s: llama_decode() failed", __func__);
            return 1;
        }
    }
    return 0;
}

extern "C"
JNIEXPORT jint JNICALL
Java_com_gremlin_app_llama_LocalLlama_processSystemPrompt(JNIEnv *env, jobject /*unused*/, jstring jsystem_prompt) {
    reset_long_term_state();

    const auto *raw = env->GetStringUTFChars(jsystem_prompt, nullptr);
    std::string formatted(raw);
    const bool has_template = common_chat_templates_was_explicit(g_chat_templates.get());
    if (has_template) {
        formatted = chat_add_and_format(ROLE_SYSTEM, raw);
    }
    env->ReleaseStringUTFChars(jsystem_prompt, raw);

    const auto tokens = common_tokenize(g_context, formatted, has_template, has_template);
    const int max_tokens = (int) llama_n_ctx(g_context) - OVERFLOW_HEADROOM;
    if ((int) tokens.size() > max_tokens) {
        LOGe("%s: system prompt too long (%d tokens, max %d)", __func__, (int) tokens.size(), max_tokens);
        return 1;
    }

    if (decode_tokens_in_batches(tokens, current_position)) {
        return 2;
    }
    system_prompt_position = current_position = (int) tokens.size();
    return 0;
}

static bool is_valid_utf8(const std::string &s) {
    const auto *bytes = (const unsigned char *) s.c_str();
    int num;
    while (*bytes != 0x00) {
        if      ((*bytes & 0x80) == 0x00) { num = 1; }
        else if ((*bytes & 0xE0) == 0xC0) { num = 2; }
        else if ((*bytes & 0xF0) == 0xE0) { num = 3; }
        else if ((*bytes & 0xF8) == 0xF0) { num = 4; }
        else { return false; }
        bytes += 1;
        for (int i = 1; i < num; ++i) {
            if ((*bytes & 0xC0) != 0x80) { return false; }
            bytes += 1;
        }
    }
    return true;
}

// Tokenizes+decodes the user turn, then samples one token at a time until
// end-of-generation, a hard stop position, or n_predict is reached --
// exactly the reference's processUserPrompt()+generateNextToken() loop,
// just run to completion here instead of yielding through JNI per token.
extern "C"
JNIEXPORT jstring JNICALL
Java_com_gremlin_app_llama_LocalLlama_generate(JNIEnv *env, jobject /*unused*/, jstring juser_prompt, jint n_predict) {
    const auto *raw = env->GetStringUTFChars(juser_prompt, nullptr);
    std::string formatted(raw);
    const bool has_template = common_chat_templates_was_explicit(g_chat_templates.get());
    if (has_template) {
        formatted = chat_add_and_format(ROLE_USER, raw);
    }
    env->ReleaseStringUTFChars(juser_prompt, raw);

    auto user_tokens = common_tokenize(g_context, formatted, has_template, has_template);
    const int max_tokens = (int) llama_n_ctx(g_context) - OVERFLOW_HEADROOM;
    if ((int) user_tokens.size() > max_tokens) {
        LOGw("%s: user prompt too long, truncating", __func__);
        user_tokens.resize(max_tokens);
    }

    if (decode_tokens_in_batches(user_tokens, current_position, true)) {
        return env->NewStringUTF("");
    }
    current_position += (int) user_tokens.size();
    const llama_pos stop_position = current_position + n_predict;

    std::ostringstream assistant_ss;
    std::string cached_chars;

    while (current_position < stop_position) {
        if (current_position >= (int) llama_n_ctx(g_context) - OVERFLOW_HEADROOM) {
            shift_context();
        }

        const auto new_token_id = common_sampler_sample(g_sampler, g_context, -1);
        common_sampler_accept(g_sampler, new_token_id, true);

        common_batch_clear(g_batch);
        common_batch_add(g_batch, new_token_id, current_position, {0}, true);
        if (llama_decode(g_context, g_batch) != 0) {
            LOGe("%s: llama_decode() failed mid-generation", __func__);
            break;
        }
        current_position++;

        if (llama_vocab_is_eog(llama_model_get_vocab(g_model), new_token_id)) {
            break;
        }

        cached_chars += common_token_to_piece(g_context, new_token_id);
        if (is_valid_utf8(cached_chars)) {
            assistant_ss << cached_chars;
            cached_chars.clear();
        }
        // else: keep accumulating -- a multi-byte UTF-8 char got split
        // across sampled tokens, next iteration completes it.
    }

    const std::string reply = assistant_ss.str();
    chat_add_and_format(ROLE_ASSISTANT, reply);
    return env->NewStringUTF(reply.c_str());
}

extern "C"
JNIEXPORT void JNICALL
Java_com_gremlin_app_llama_LocalLlama_unload(JNIEnv * /*unused*/, jobject /*unused*/) {
    reset_long_term_state();
    if (g_sampler) { common_sampler_free(g_sampler); g_sampler = nullptr; }
    g_chat_templates.reset();
    if (g_batch.token) { llama_batch_free(g_batch); g_batch = {}; }
    if (g_context) { llama_free(g_context); g_context = nullptr; }
    if (g_model) { llama_model_free(g_model); g_model = nullptr; }
}

extern "C"
JNIEXPORT void JNICALL
Java_com_gremlin_app_llama_LocalLlama_shutdown(JNIEnv * /*unused*/, jobject /*unused*/) {
    llama_backend_free();
}
