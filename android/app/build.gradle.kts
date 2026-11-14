plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}
val run = (System.getenv("GITHUB_RUN_NUMBER") ?: "1").toInt()
android {
    namespace = "app.sonar"
    compileSdk = 35
    defaultConfig {
        applicationId = "app.sonar.trader"
        minSdk = 26
        targetSdk = 34
        versionCode = run
        versionName = "3.0.$run"
    }
    signingConfigs {
        create("rel") {
            storeFile = file("../keystore/sonar.jks")
            storePassword = "sonar-app-2026"
            keyAlias = "sonar"
            keyPassword = "sonar-app-2026"
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("rel")
        }
        debug { signingConfig = signingConfigs.getByName("rel") }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { buildConfig = true }
}
dependencies {
    implementation("androidx.webkit:webkit:1.12.1")
    implementation("androidx.core:core-ktx:1.13.1")
}
