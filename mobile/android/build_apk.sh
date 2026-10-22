#!/usr/bin/env bash
# APK 构建：优先使用标准 Gradle (含 Material Components + ZXing)；无 SDK/Maven 网络时退回独立原生控件构建。
# fallback 工具链: 先运行 mobile/android/prep_tools.sh (只需 PyPI + npm)
# 用法: bash mobile/android/build_apk.sh   产物: desktop/bin/FLA-cast.apk
# FLA_ANDROID_MANUAL=1 可强制使用无需 Gradle 的 fallback 构建。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
ANDROID_SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
if [ "${FLA_ANDROID_MANUAL:-0}" != "1" ] && [ -n "$ANDROID_SDK" ] && command -v gradle >/dev/null 2>&1; then
  echo ">> Gradle release build (Material Components + ZXing)"
  (cd "$HERE" && gradle --no-daemon :app:assembleRelease)
  cp "$HERE/app/build/outputs/apk/release/app-release.apk" "$ROOT/desktop/bin/FLA-cast.apk"
  ls -la "$ROOT/desktop/bin/FLA-cast.apk"
  exit 0
fi
T="${T:-/tmp/apktools}"
[ -f "$T/d8.jar" ] || bash "$HERE/prep_tools.sh"
JAVA="$T/jre/bin/java"
JC="$JAVA -jar $T/ecj.jar"
D8="$JAVA -cp $T/d8.jar com.android.tools.r8.D8"
SIGN="$JAVA --enable-native-access=ALL-UNNAMED -jar $T/apksigner.jar"
A34="$T/android.jar"

APP="$HERE/app/src/main"
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
VC=$(grep -o 'versionCode [0-9]*' "$HERE/app/build.gradle" | awk '{print $2}')
VN=$(grep -o 'versionName "[^"]*"' "$HERE/app/build.gradle" | cut -d'"' -f2)
echo ">> FLA 投屏 $VN ($VC)"

# 1. 资源 + 清单 (Gradle 使用 Material 3/ZXing; 独立构建不拉 Maven 依赖, 使用原生卡片/扫码页降级)
mkdir -p "$W/res"
cp -R "$APP/res/." "$W/res/"
rm -f "$W/res/values/styles.xml"   # Material3 parent 由 Gradle 依赖提供, 本地 aapt2 fallback 用系统主题
sed -e 's#<manifest xmlns:android="http://schemas.android.com/apk/res/android">#<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="top.clrv.fla">#' \
    -e 's#android:theme="@style/Theme.FLA"#android:theme="@android:style/Theme.Material.NoActionBar"#' \
    "$APP/AndroidManifest.xml" > "$W/AndroidManifest.xml"
"$T/aapt2" compile --dir "$W/res" -o "$W/res.zip"
cp "$ROOT/web/js/jsqr.min.js" "$APP/assets/jsqr.min.js"
"$T/aapt2" link -I "$A34" -A "$APP/assets" --manifest "$W/AndroidManifest.xml" "$W/res.zip" \
  --min-sdk-version 24 --target-sdk-version 34 --version-code "$VC" --version-name "$VN" \
  -o "$W/base.apk"

# 2. Java → class → dex (BuildConfig由Gradle生成，这里按build.gradle的版本字段补齐)
mkdir -p "$W/cls" "$W/dex" "$W/gen/top/clrv/fla"
cat > "$W/gen/top/clrv/fla/BuildConfig.java" <<EOF
package top.clrv.fla;
public final class BuildConfig {
    public static final int VERSION_CODE = $VC;
    public static final String VERSION_NAME = "$VN";
    private BuildConfig() {}
}
EOF
$JC -nowarn -source 1.8 -target 1.8 -encoding UTF-8 -bootclasspath "$A34" -d "$W/cls" $(find "$APP/java" -name "*.java") "$W/gen/top/clrv/fla/BuildConfig.java"
$D8 --release --lib "$A34" --min-api 24 --output "$W/dex" $(find "$W/cls" -name "*.class")

# 3. 合并 + 4 字节对齐 (resources.arsc 必须不压缩且对齐)
python3 - "$W/base.apk" "$W/dex/classes.dex" "$W/unsigned.apk" <<'PY'
import sys, zipfile
src, dex, dst = sys.argv[1:]
zin = zipfile.ZipFile(src)
entries = [(i.filename, zin.read(i.filename)) for i in zin.infolist()] + [("classes.dex", open(dex, "rb").read())]
with open(dst, "wb") as f:
    z = zipfile.ZipFile(f, "w")
    for name, data in entries:
        stored = name == "resources.arsc" or name.endswith(".png")
        zi = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
        if stored:   # 对齐: 本地文件头 30 + 文件名 + extra
            off = f.tell() + 30 + len(name.encode())
            pad = (-off) % 4
            zi.extra = b"\0" * pad
        z.writestr(zi, data)
    z.close()
PY

# 4. 签名 (v1 + v2 + v3) 并校验
mkdir -p "$ROOT/desktop/bin"
$SIGN sign --ks "$HERE/keystore/fla.p12" --ks-type PKCS12 --ks-pass pass:flafla --ks-key-alias fla --key-pass pass:flafla \
  --min-sdk-version 24 --v4-signing-enabled false --out "$ROOT/desktop/bin/FLA-cast.apk" "$W/unsigned.apk"
$SIGN verify --verbose --min-sdk-version 24 "$ROOT/desktop/bin/FLA-cast.apk" | head -6
ls -la "$ROOT/desktop/bin/FLA-cast.apk"
