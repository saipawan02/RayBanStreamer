#!/usr/bin/env bash
# Build Android APK and install to connected phone via ADB
# TECH-02 section 11: Build order verification script

set -euo pipefail

echo "=== Glasses Streamer Build & Install ==="
echo

# Check prerequisites
echo "Checking prerequisites..."

# Check if adb is available
if ! command -v adb &> /dev/null; then
    echo "ERROR: adb not found. Install Android SDK platform-tools."
    exit 1
fi

# Check if phone is connected
DEVICES=$(adb devices | grep -v "List" | grep -w "device" | wc -l)
if [ "$DEVICES" -eq 0 ]; then
    echo "ERROR: No Android device connected via USB."
    echo "Connect your phone and enable USB debugging."
    exit 1
fi

echo "✓ adb found"
echo "✓ $DEVICES device(s) connected"
echo

# Navigate to android directory
cd "$(dirname "$0")/android"

echo "Building APK..."
echo

# Build debug APK
if [ -f "./gradlew" ]; then
    ./gradlew assembleDebug
else
    # Fallback to system gradle
    gradle assembleDebug
fi

echo
echo "✓ Build successful"
echo

# Find the APK
APK_PATH="app/build/outputs/apk/debug/app-debug.apk"

if [ ! -f "$APK_PATH" ]; then
    echo "ERROR: APK not found at $APK_PATH"
    exit 1
fi

echo "Installing APK to device..."
adb install -r "$APK_PATH"

echo
echo "✓ Installation successful"
echo

# Launch the app
PACKAGE="com.glassesstream.debug"
ACTIVITY="com.glassesstream.ui.MainActivity"

echo "Launching app..."
adb shell am start -n "$PACKAGE/$ACTIVITY"

echo
echo "=== Done! ==="
echo
echo "The app should now be running on your phone."
echo
echo "To view logs:"
echo "  adb logcat | grep GlassesStream"
echo
echo "To pull session logs:"
echo "  adb exec-out run-as $PACKAGE cat files/logs/<session_id>.jsonl"
echo
