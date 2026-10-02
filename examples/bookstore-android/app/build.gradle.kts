plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.example.bookstore"
    compileSdk = 35
}

dependencies {
    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("androidx.navigation:navigation-compose:2.8.0")
    implementation("androidx.work:work-runtime-ktx:2.9.1")
}
