#!/usr/bin/env bash
# 在 Linux 上交叉编译 iOS 版 FLA 手机端 (无需 Mac / Xcode)
#   编译器: PyPI ziglang==0.13.0 (clang, 目标 aarch64-ios; 0.17.x 目前会破坏该 SDK 的链接)
#   SDK   : github.com/theos/sdks 的 iPhoneOS16.5.sdk (稀疏克隆)
# 产物: desktop/bin/FLA-ios.ipa  —— 未签名, 需要用户用 爱思助手/全能签/Sideloadly(Apple ID) 或 TrollStore 签名安装
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
SDKDIR="${SDKDIR:-/tmp/tsdk}"
S="$SDKDIR/iPhoneOS16.5.sdk"
VER=$(grep -o 'kVersion = @"[^"]*"' "$HERE/App.m" | cut -d'"' -f2)
APPID="top.clrv.fla.app"
ZIG_VERSION="${ZIG_VERSION:-0.13.0}"
if [ "$(python3 -m ziglang version 2>/dev/null || true)" != "$ZIG_VERSION" ]; then
  python3 -m pip install -q --break-system-packages --upgrade "ziglang==$ZIG_VERSION"
fi
python3 -c "import PIL" 2>/dev/null || python3 -m pip install -q --break-system-packages Pillow
if [ ! -d "$S/System/Library/Frameworks/ReplayKit.framework" ]; then
  rm -rf "$SDKDIR"
  git clone -q --filter=blob:none --no-checkout --depth 1 https://github.com/theos/sdks.git "$SDKDIR"
  (cd "$SDKDIR" && git sparse-checkout set iPhoneOS16.5.sdk && git checkout -q)
fi
CC="python3 -m ziglang cc -target aarch64-ios.14.0 -O2 -fno-sanitize=all -isysroot $S -isystem $S/usr/include -iframework $S/System/Library/Frameworks -F$S/System/Library/Frameworks -L$S/usr/lib -fobjc-arc -Wno-property-attribute-mismatch"
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
A="$W/Payload/FLA.app"; X="$A/PlugIns/FLABroadcast.appex"
mkdir -p "$X"
echo ">> FLA iOS $VER"

$CC -framework UIKit -framework WebKit -framework AVFoundation -framework ReplayKit -framework CoreGraphics -framework QuartzCore -framework Foundation \
    -o "$A/FLA" "$HERE/App.m" -lSystem -lobjc
$CC -fapplication-extension -Wl,-e,_NSExtensionMain -framework ReplayKit -framework CoreImage -framework CoreMedia \
    -framework CoreVideo -framework CoreGraphics -framework QuartzCore -framework ImageIO -framework UIKit -framework Foundation -o "$X/FLABroadcast" "$HERE/Broadcast.m" -lSystem -lobjc

python3 - "$ROOT/desktop/assets/fla.ico" "$A" <<'PY'
import sys
from PIL import Image
im = Image.open(sys.argv[1]); im = im.convert("RGBA")
bg = Image.new("RGBA", im.size, (255, 255, 255, 255)); bg.alpha_composite(im); im = bg.convert("RGB")   # iOS 图标不能透明
for name, px in [("AppIcon60x60@2x.png", 120), ("AppIcon60x60@3x.png", 180),
                 ("AppIcon76x76@2x~ipad.png", 152), ("AppIcon83.5x83.5@2x~ipad.png", 167)]:
    im.resize((px, px), Image.LANCZOS).save(f"{sys.argv[2]}/{name}")
PY

python3 - "$A/Info.plist" "$X/Info.plist" "$VER" "$APPID" <<'PY'
import plistlib, sys
app, ext, ver, appid = sys.argv[1:]
common = {"CFBundleDevelopmentRegion": "zh_CN", "CFBundleInfoDictionaryVersion": "6.0",
          "CFBundleShortVersionString": ver, "CFBundleVersion": ver, "MinimumOSVersion": "14.0",
          "CFBundleSupportedPlatforms": ["iPhoneOS"], "DTPlatformName": "iphoneos", "DTSDKName": "iphoneos16.5",
          "DTPlatformVersion": "16.5", "UIDeviceFamily": [1, 2], "UIRequiredDeviceCapabilities": ["arm64"]}
plistlib.dump({**common,
    "CFBundleIdentifier": appid, "CFBundleExecutable": "FLA", "CFBundleName": "FLA", "CFBundleDisplayName": "FLA 手机端",
    "CFBundlePackageType": "APPL",
    "CFBundleIcons": {"CFBundlePrimaryIcon": {"CFBundleIconFiles": ["AppIcon60x60"], "CFBundleIconName": "AppIcon"}},
    "CFBundleIcons~ipad": {"CFBundlePrimaryIcon": {"CFBundleIconFiles": ["AppIcon60x60", "AppIcon76x76", "AppIcon83.5x83.5"]}},
    "UILaunchScreen": {},
    "UIStatusBarStyle": "UIStatusBarStyleLightContent", "UIViewControllerBasedStatusBarAppearance": True,
    "UISupportedInterfaceOrientations": ["UIInterfaceOrientationPortrait", "UIInterfaceOrientationLandscapeLeft", "UIInterfaceOrientationLandscapeRight"],
    "UISupportedInterfaceOrientations~ipad": ["UIInterfaceOrientationPortrait", "UIInterfaceOrientationPortraitUpsideDown", "UIInterfaceOrientationLandscapeLeft", "UIInterfaceOrientationLandscapeRight"],
    "NSCameraUsageDescription": "扫描电脑上的二维码、用摄像头投屏到大屏",
    "NSPhotoLibraryUsageDescription": "选择照片投到大屏",
    "NSLocalNetworkUsageDescription": "同一 Wi-Fi 下直连电脑，降低投屏延迟",
    "NSAppTransportSecurity": {"NSAllowsArbitraryLoads": True},
    "LSRequiresIPhoneOS": True,
}, open(app, "wb"))
plistlib.dump({**common,
    "CFBundleIdentifier": appid + ".broadcast", "CFBundleExecutable": "FLABroadcast", "CFBundleName": "FLABroadcast",
    "CFBundleDisplayName": "FLA 投屏", "CFBundlePackageType": "XPC!",
    "NSExtension": {"NSExtensionPointIdentifier": "com.apple.broadcast-services-upload",
                    "NSExtensionPrincipalClass": "SampleHandler",
                    "NSExtensionAttributes": {"RPBroadcastProcessMode": "RPBroadcastProcessModeSampleBuffer"}},
}, open(ext, "wb"))
PY
printf 'APPL????' > "$A/PkgInfo"
# 个人 Apple ID/免费描述文件通常没有 App Group 能力。本 App 不声明硬编码 Team ID 或 App Group entitlement；
# ReplayKit 扩展通过 /api/remote/ios/session?vid=... 从服务器取回配对状态。签名时请让签名器按 profile 自动注入权限。
mkdir -p "$ROOT/desktop/bin"
rm -f "$ROOT/desktop/bin/FLA-ios.ipa"
(cd "$W" && python3 -c "
import zipfile,os
z=zipfile.ZipFile('$ROOT/desktop/bin/FLA-ios.ipa','w',zipfile.ZIP_DEFLATED)
for d,_,fs in os.walk('Payload'):
    for f in fs:
        p=os.path.join(d,f); zi=zipfile.ZipInfo.from_file(p,p); zi.compress_type=zipfile.ZIP_DEFLATED
        if os.access(p,os.X_OK) and f in ('FLA','FLABroadcast'): zi.external_attr=(0o100755<<16)
        z.writestr(zi,open(p,'rb').read())
z.close()")
ls -la "$ROOT/desktop/bin/FLA-ios.ipa"
python3 "$HERE/validate_ipa.py" "$ROOT/desktop/bin/FLA-ios.ipa"
