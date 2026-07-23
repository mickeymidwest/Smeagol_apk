# Gremlin (Android)

Talks to a Gremlin desktop instance over your home Wi-Fi when it's
reachable, and falls back -- in order -- to a fully offline on-device
model, then Claude, then Gemini (using your own API keys, entered in
Settings) when it's not. Same hologram widget as the desktop version.

## Offline on-device model

Settings → "Offline (fully local)" downloads a small (~910MB,
`mradermacher/Llama-3.2-1B-Instruct-abliterated-GGUF`, `Q4_K_M`) model
straight to the phone and runs it with llama.cpp compiled from source
via JNI (see `app/src/main/cpp/`). This is what actually keeps "talking
to Gremlin" working with zero connectivity at all -- no desktop, no
Wi-Fi, no cell signal, no API key. Once you get back in range of the
desktop (or just get signal again), whatever was said in offline mode
rides along with your next message and gets folded into the desktop's
own `data/away_session_log.jsonl` the same way an away-mode Claude/Gemini
exchange already does (see `gremlin_core/away_sync.py`) -- nothing new
needed server-side, that sync path already existed.

`android/llama.cpp` is a **git submodule** (pinned to release `b10091`),
not vendored source -- llama.cpp has no published Maven artifact, only
its own CMake project, so building it from source via
`add_subdirectory()` is the actual supported way to embed it (same
approach as llama.cpp's own `examples/llama.android` reference app,
which `app/src/main/cpp/gremlin_llama.cpp` was adapted from). A fresh
clone needs `git clone --recurse-submodules`, or `git submodule update
--init` after a plain clone -- the CI workflow already does this
(`submodules: recursive` in the checkout step).

**This folder is part of the combined `gremlin` repo, not its own
separate repo** -- see the root `README.md`'s "Putting this on GitHub"
section for the actual push instructions. The short version: one
`git init` / `git push` at the repo root pushes both this and the
desktop project together; `.github/workflows/android-build.yml` (at
the repo root, not in here -- GitHub Actions only looks there) builds
this specifically whenever something under `android/` changes.

## Getting a built APK without installing Android Studio

Once the combined repo is pushed (see the root README), check your
repo's **Actions** tab → the `Android Build` workflow run → scroll to
**Artifacts** → download `gremlin-debug-apk`. Unzip that, and you have
an installable APK -- copy it to your phone and open it (you'll need to
allow "install from unknown sources" the first time, standard for
anything not from the Play Store).

## Building locally in Android Studio instead

Open this `android/` folder specifically in Android Studio (not the
repo root) and let it sync. One thing worth knowing up front: **this
repo doesn't include the Gradle wrapper jar**
(`gradle/wrapper/gradle-wrapper.jar`). That file is a small compiled
binary, and I don't consider it safe to hand-produce without a working
Gradle installation to generate it correctly -- a subtly wrong or
corrupted jar would fail in a much more confusing way than just not
having one. Android Studio handles this fine on its own (it can
generate the wrapper automatically on import), or generate it yourself
once if you have Gradle installed:

```bash
gradle wrapper --gradle-version 8.13
```

After that, `./gradlew assembleDebug` works locally too, and you could
switch the CI workflow to use `./gradlew` instead of `gradle` if you'd
rather it use your committed wrapper version specifically.

## Honesty note

I (Claude) wrote this app without ever being able to compile or run it
myself -- no Android SDK, emulator, or Kotlin compiler available in my
environment. Everything I could check without a real build was
checked: XML validity, Kotlin brace/paren balance, every `R.id.*` and
`R.array.*` reference matched against what's actually declared in the
layouts, current library/plugin versions verified via live search
rather than trusted from memory. The GitHub Actions workflow is the
first time this project gets an actual compile check from a real
toolchain. If it fails, that's genuinely useful information -- paste me
the exact error from the Actions log and I'll fix it against something
real instead of guessing again.

The native/JNI piece (`app/src/main/cpp/gremlin_llama.cpp`,
`LocalLlama.kt`) carries the same caveat but more so -- it's adapted
line-by-line from the real, working `ai_chat.cpp` in the pinned
llama.cpp submodule (read directly off disk, not reconstructed from
memory or a lossy web summary, specifically to avoid guessing at C API
signatures that drift across llama.cpp versions), with
`processUserPrompt()` + `generateNextToken()`'s per-token JNI loop
collapsed into one blocking `generate()` call. The CMake/NDK wiring in
`app/build.gradle.kts` and `app/src/main/cpp/CMakeLists.txt` is new
territory for this project's CI (`android-build.yml` now installs NDK
27 and checks out the submodule) and has never actually run yet as of
this writing -- expect at least one round of real build errors on the
first push, most likely in the CMake config rather than the C++ itself.
