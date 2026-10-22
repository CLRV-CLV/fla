#!/usr/bin/env bash
# FLA Desktop 2.0 — 纯 Win32 原生客户端构建脚本 (零依赖单文件 exe)
# 工具链: zig cc (pip install ziglang) 交叉编译 x86_64-windows-gnu, CRT 静态链接。
# 产物: ../downloads/FLA.exe  (web 端下载页 + 自动更新链路直接分发)
set -euo pipefail
cd "$(dirname "$0")"

SRC="part1_base.c part2_main.c part3_dock.c part4_overlay.c"
OUT_ALL="$(mktemp -d)/FLA_all.c"
OUT_EXE="../bin/FLA.exe"

VER=$(grep -o '#define FLA_VERSION L"[^"]*"' part1_base.c | sed 's/.*L"\(.*\)"/\1/')
echo "==> FLA Desktop v${VER} (native Win32)"

echo "==> 拼接源文件..."
cat $SRC > "$OUT_ALL"

echo "==> 编译 (zig cc, x86_64-windows-gnu, static)..."
python3 -m ziglang cc -target x86_64-windows-gnu \
    -municode -O2 -g0 -s \
    -Wall -Wno-unknown-pragmas \
    -o "$OUT_EXE" "$OUT_ALL" \
    -luser32 -lgdi32 -lshell32 -lwinhttp -lws2_32 -lcomctl32 -ladvapi32 -lole32 \
    -static

# 同步到全部分发位 (server/desktop_dist.py 先找 bin/ 再找 web/downloads)
cp -f "$OUT_EXE" ../dist/FLA.exe
cp -f "$OUT_EXE" ../../web/downloads/FLA.exe
ls -l "$OUT_EXE" ../dist/FLA.exe ../../web/downloads/FLA.exe
echo "==> 完成: v${VER}"
