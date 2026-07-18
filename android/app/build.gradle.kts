plugins {
    id("com.android.application")
}

android {
    namespace = "com.gremlin.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.gremlin.app"
        minSdk = 24
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"
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
