#!/usr/bin/env bash
# Build the Ember Android APK using the AOSP command-line tools (aapt, dx, zipalign,
# apksigner) — NO Android Studio and NO Google SDK download required. This is the
# path used when Google's SDK host (dl.google.com) is unreachable; the standard
# Gradle build (see README) is used on CI where the full SDK is available.
#
# Requirements (Debian/Ubuntu): aapt apksigner zipalign dalvik-exchange  + a JDK.
#   sudo apt-get install -y aapt apksigner zipalign dalvik-exchange
# The framework android.jar (API 34) is fetched once if missing.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/build"
SRC="$HERE/app/src/main"
PKG_DIR="com/ember/ai"
API_JAR="${ANDROID_JAR:-$OUT/android-34.jar}"
KEYSTORE="$OUT/debug.keystore"
APK="$OUT/ember-debug.apk"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing tool: $1"; MISSING=1; }; }
MISSING=0
for t in aapt zipalign apksigner javac keytool; do need "$t"; done
if ! command -v dalvik-exchange >/dev/null 2>&1 && ! command -v dx >/dev/null 2>&1; then
  echo "Missing dexer: install 'dalvik-exchange' (provides dx)"; MISSING=1
fi
[ "$MISSING" = 1 ] && { echo "Install the tools above and retry."; exit 1; }
DX="$(command -v dalvik-exchange || command -v dx)"

rm -rf "$OUT"; mkdir -p "$OUT/gen" "$OUT/classes"

if [ ! -f "$API_JAR" ]; then
  echo "• Fetching framework android.jar (API 34)…"
  curl -fsSL -o "$API_JAR" \
    "https://raw.githubusercontent.com/Sable/android-platforms/master/android-34/android.jar"
fi

echo "• aapt: compiling resources + generating R.java"
aapt package -f -m \
  -J "$OUT/gen" \
  -M "$SRC/AndroidManifest.xml" \
  -S "$SRC/res" \
  -I "$API_JAR" >/dev/null

echo "• javac: compiling Java sources"
javac -source 8 -target 8 -encoding UTF-8 \
  -bootclasspath "$API_JAR" -cp "$API_JAR" \
  -d "$OUT/classes" \
  $(find "$SRC/java" "$OUT/gen" -name '*.java') 2>&1 | grep -viE "bootstrap class path|warning:|^Note:" || true

echo "• dx: dexing to classes.dex"
"$DX" --dex --output="$OUT/classes.dex" "$OUT/classes" >/dev/null 2>&1

echo "• aapt: packaging base APK (resources + manifest)"
aapt package -f \
  -M "$SRC/AndroidManifest.xml" \
  -S "$SRC/res" \
  -I "$API_JAR" \
  -F "$OUT/app.unaligned.apk" >/dev/null

echo "• adding classes.dex"
( cd "$OUT" && aapt add app.unaligned.apk classes.dex >/dev/null )

echo "• zipalign"
zipalign -f 4 "$OUT/app.unaligned.apk" "$OUT/app.aligned.apk"

if [ ! -f "$KEYSTORE" ]; then
  echo "• generating debug keystore"
  keytool -genkeypair -keystore "$KEYSTORE" -storepass android -keypass android \
    -alias androiddebugkey -keyalg RSA -keysize 2048 -validity 10000 \
    -dname "CN=Ember Debug,O=Ember,C=US" >/dev/null 2>&1
fi

echo "• apksigner: signing (v1+v2+v3)"
apksigner sign --ks "$KEYSTORE" --ks-pass pass:android --key-pass pass:android \
  --v1-signing-enabled true --v2-signing-enabled true \
  --out "$APK" "$OUT/app.aligned.apk"

apksigner verify "$APK" && echo ""
echo "✓ Built: $APK"
ls -la "$APK"
