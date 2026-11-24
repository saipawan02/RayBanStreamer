import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.serialization)
}

// Read Meta Wearables credentials from local.properties
val localProperties = Properties()
val localPropertiesFile = rootProject.file("local.properties")
if (localPropertiesFile.exists()) {
    localPropertiesFile.inputStream().use { localProperties.load(it) }
}

android {
    namespace = "com.glassesstream"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.glassesstream.debug"
        minSdk = 29
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"

        // Set Meta Wearables credentials
        manifestPlaceholders["mwdat_application_id"] =
            localProperties.getProperty("mwdat_application_id", "0")
        manifestPlaceholders["mwdat_client_token"] =
            localProperties.getProperty("mwdat_client_token", "0")
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

    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation(libs.mwdat.core)
    implementation(libs.mwdat.camera)
    implementation(libs.mwdat.motion)
    implementation(libs.androidx.core)
    implementation(libs.androidx.appcompat)
    implementation(libs.androidx.lifecycle)
    implementation(libs.androidx.datastore)
    implementation(libs.material)
    implementation(libs.kotlinx.coroutines)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.okhttp)
}
