# Smeagol (Android)

Talks to a Smeagol desktop instance over your home Wi-Fi when it's
reachable, and falls back to calling Claude or Gemini directly (using
your own API keys, entered in Settings) when it's not. Same hologram
widget as the desktop version.

## Getting a built APK without installing Android Studio

Push this repo to GitHub and the included workflow
(`.github/workflows/android-build.yml`) builds a debug APK
automatically on every push, using a real Android SDK and a real
Gradle installation on GitHub's own runner -- not something checked by
inspection, an actual compile:

```bash
git init
git add .
git commit -m "initial"
git remote add origin <your-repo-url>
git push -u origin main
```

Then: your repo's **Actions** tab → the latest workflow run → scroll to
**Artifacts** → download `smeagol-debug-apk`. Unzip that, and you have
an installable APK -- copy it to your phone and open it (you'll need to
allow "install from unknown sources" the first time, standard for
anything not from the Play Store).

## Building locally in Android Studio instead

Open this folder in Android Studio and let it sync. One thing worth
knowing up front: **this repo doesn't include the Gradle wrapper jar**
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
rather than trusted from memory. The GitHub Actions workflow above is
the first time this project gets an actual compile check from a real
toolchain. If it fails, that's genuinely useful information -- paste me
the exact error from the Actions log and I'll fix it against something
real instead of guessing again.
