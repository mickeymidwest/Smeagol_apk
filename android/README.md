# Gremlin (Android)

Talks to a Gremlin desktop instance over your home Wi-Fi when it's
reachable, and falls back to calling Claude or Gemini directly (using
your own API keys, entered in Settings) when it's not. Same hologram
widget as the desktop version.

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
