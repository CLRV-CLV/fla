#!/usr/bin/env bash
# 在 Linux 上交叉打包 Windows 单文件 FLA.exe (无需 Windows / PyInstaller)
#   运行环境 = 官方签名 Python 3.11 embeddable + PyQt5 / qfluentwidgets / pywin32 的 win_amd64 wheel
#   用法: bash build_bundle.sh      产物: desktop/bin/FLA.exe (+ 同步 desktop/dist, web/downloads)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
VER=$(grep -oP '^VERSION = "\K[^"]+' "$HERE/fla_qt/core.py")
W=${FLA_BUILD_DIR:-/tmp/fla-qt-build}
mkdir -p "$W/whl" "$W/dl"
echo "== FLA $VER"

# 1. Python embeddable (官方 3.11.9, 经 PyPI 包 python-embed 分发; 校验 PSF 签名)
if [ ! -f "$W/dl/py.zip" ]; then
  url=$(curl -s https://pypi.org/pypi/python-embed/3.11.0/json | python3 -c "import json,sys;print(json.load(sys.stdin)['urls'][0]['url'])")
  curl -sSL -o "$W/dl/pe.tgz" "$url"
  tar xzf "$W/dl/pe.tgz" -C "$W/dl"
  cp "$W"/dl/python_embed-3.11.0/src3.11/python_embed/data.zip "$W/dl/py.zip"
fi
# 2. wheels
O="--no-deps --only-binary=:all: --platform win_amd64 --python-version 3.11 --implementation cp -d $W/whl -q"
for p in PyQt5==5.15.11 PyQt5-Qt5==5.15.2 PyQt5-sip==12.19.0 PyQt-Fluent-Widgets==1.11.3 \
         PyQt5-Frameless-Window==0.8.2 darkdetect==0.8.0 pywin32==312; do
  pip download $O "$p"
done

S="$W/stage"; rm -rf "$S"; mkdir -p "$S/python" "$S/app" "$S/assets"
( cd "$S/python" && unzip -q "$W/dl/py.zip" && mv cp311/* . && rmdir cp311 )
python3 - "$S/python" <<'PY'
import sys, pefile
for f in ("python311.dll", "pythonw.exe"):
    pe = pefile.PE(f"{sys.argv[1]}/{f}"); d = pe.OPTIONAL_HEADER.DATA_DIRECTORY[4]
    raw = open(f"{sys.argv[1]}/{f}", "rb").read()[d.VirtualAddress:d.VirtualAddress + d.Size]
    assert b"Python Software Foundation" in raw, f"{f} 未见 PSF 签名"
print("  python 签名校验通过")
PY
SP="$S/python/Lib/site-packages"; mkdir -p "$SP"
for w in "$W"/whl/*.whl; do unzip -q -o "$w" -d "$SP"; done
printf 'python311.zip\r\n.\r\nLib\\site-packages\r\nLib\\site-packages\\win32\r\nLib\\site-packages\\win32\\lib\r\n..\\app\r\nimport site\r\n' > "$S/python/python311._pth"
cp "$SP"/pywin32_system32/*.dll "$S/python/"

# 3. 瘦身: 只保留用到的 Qt 模块 / 插件
Q="$SP/PyQt5"
keep_pyd="QtCore QtGui QtWidgets QtSvg QtXml QtNetwork QtWebSockets"
for f in "$Q"/*.pyd; do b=$(basename "$f" .pyd); case " $keep_pyd " in *" $b "*) ;; *) [[ $b == sip* ]] || rm -f "$f";; esac; done
rm -f "$Q"/*.pyi; rm -rf "$Q"/uic "$Q"/bindings "$Q"/Qt5/qml "$Q"/Qt5/qsci "$Q"/Qt5/translations "$Q"/Qt5/lib
B="$Q/Qt5/bin"
for f in "$B"/*.dll; do
  case $(basename "$f") in
    Qt5Core.dll|Qt5Gui.dll|Qt5Widgets.dll|Qt5Svg.dll|Qt5Xml.dll|Qt5Network.dll|Qt5WebSockets.dll|libssl-1_1-x64.dll|libcrypto-1_1-x64.dll|msvcp140*.dll|vcruntime140*.dll|concrt140.dll|libEGL.dll|libGLESv2.dll|d3dcompiler_47.dll) ;;
    *) rm -f "$f";;
  esac
done
P="$Q/Qt5/plugins"
for d in "$P"/*; do case $(basename "$d") in platforms|styles|imageformats|iconengines) ;; *) rm -rf "$d";; esac; done
find "$P/platforms" -type f ! -name qwindows.dll -delete
find "$P/imageformats" -type f ! -name 'qico.dll' ! -name 'qsvg.dll' ! -name 'qjpeg.dll' ! -name 'qgif.dll' -delete
rm -rf "$SP"/pythonwin "$SP"/adodbapi "$SP"/isapi "$SP"/PyWin32.chm "$SP"/win32/Demos "$SP"/win32/test "$SP"/win32/scripts \
       "$SP"/win32com/demos "$SP"/win32com/test "$SP"/win32comext/*/demos "$SP"/win32comext/*/test "$SP"/*.dist-info/RECORD
find "$SP" -name "__pycache__" -prune -exec rm -rf {} +

# 4. FLA 程序 (预编译 .pyc 由 Windows 首次运行时生成, 这里只放源码)
cp -r "$HERE/fla_qt" "$S/app/"; cp "$HERE/fla_desktop.py" "$S/app/"
cp "$ROOT/web/cast.html" "$S/app/fla_qt/cast.html"            # 局域网极速模式: 电脑直接给手机提供页面
cp -r "$ROOT/server/vendor/qrcode" "$SP/qrcode"                # 本地生成局域网二维码
find "$S/app" -name "__pycache__" -prune -exec rm -rf {} +
cp "$ROOT/desktop/assets/fla.ico" "$S/app/fla.ico"
echo "  运行环境: $(du -sh "$S" | cut -f1)"

# 5. 打 zip (deflate-9)
python3 - "$S" "$W/payload.zip" <<'PY'
import os, sys, zipfile
src, dst = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for root, _, files in os.walk(src):
        for f in files:
            p = os.path.join(root, f)
            z.write(p, os.path.relpath(p, src).replace(os.sep, "/"))
print("  payload:", os.path.getsize(dst))
PY

# 6. 启动器 + 拼接
( cd "$HERE" && python3 -m ziglang cc -target x86_64-windows-gnu -municode -Wl,--subsystem,windows -O2 -s -Wall \
    -DFLA_VERSION="L\"$VER\"" -o "$W/launcher.exe" launcher.c launcher.rc -luser32 -lgdi32 -static )
python3 - "$W/launcher.exe" "$W/payload.zip" "$ROOT/desktop/bin/FLA.exe" <<'PY'
import struct, sys
l, p, out = sys.argv[1:]
a = open(l, "rb").read(); b = open(p, "rb").read()
open(out, "wb").write(a + b + struct.pack("<Q", len(a)) + b"FLAPAYLD")
print("  FLA.exe:", len(a) + len(b) + 16)
PY
mkdir -p "$ROOT/desktop/dist" "$ROOT/web/downloads"
cp "$ROOT/desktop/bin/FLA.exe" "$ROOT/desktop/dist/FLA.exe"
cp "$ROOT/desktop/bin/FLA.exe" "$ROOT/web/downloads/FLA.exe"
echo "== done"
