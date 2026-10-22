# FLA Android

The launcher, home screen, bottom navigation, profile entry, pairing form, and cast controls are native Android views. Screen capture/streaming remains the native `MediaProjection` + `CastService` implementation. WebView is created only for individual server-backed modules (courseware, AI, chat, forum) and as the no-dependency QR-scan fallback; it is no longer the app's home/navigation shell.

## Build

```bash
bash mobile/android/build_apk.sh
```

With an Android SDK and Gradle installed, the script uses the standard Gradle release build, including the open-source Material Components for Android (Material 3 theme) and ZXing Android Embedded QR scanner. In stripped Linux environments without an SDK/Maven access, it uses the repository's standalone APK builder; that fallback uses the Android platform's native Material-themed controls and existing `jsQR` camera scan page.

Release version is `1.4.1` / version code `7`. Keep `server/routers/mobile.py`'s Android version fields in sync with `app/build.gradle`; `MainActivity` checks `BuildConfig.VERSION_CODE` when downloading updates.
