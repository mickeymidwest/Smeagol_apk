plugins {
    id("com.android.application")
}

android {
    namespace = "com.gremlin.app"
    compileSdk = 35

    // Pinned so a local build and the CI runner (android-build.yml)
    // install the exact same NDK -- "whichever sdkmanager feels like
    // giving you today" is how native-build mismatches turn into a CI
    // failure that doesn't reproduce locally.
    ndkVersion = "27.0.12077973"

    defaultConfig {
        applicationId = "com.gremlin.app"
        minSdk = 24
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"

        // arm64-v8a only -- that's every real phone this app will ever
        // run on. Skipping x86_64 keeps the from-source llama.cpp build
        // (see src/main/cpp/CMakeLists.txt) to one ABI instead of two,
        // which matters a lot for CI build time since there's no
        // prebuilt llama.cpp Android artifact to depend on instead.
        ndk {
            abiFilters += "arm64-v8a"
        }

        externalNativeBuild {
            cmake {
                arguments += "-DCMAKE_BUILD_TYPE=Release"
            }
        }
    }

    externalNativeBuild {
        cmake {
            path("src/main/cpp/CMakeLists.txt")
            version = "3.22.1"
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation("com.google.android.material:material:1.12.0")
    // QR scanning without needing Google Play Services -- opens its own
    // camera activity and hands back the scanned text.
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")
}
