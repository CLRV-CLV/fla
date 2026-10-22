#!/usr/bin/env bash
# 准备无 Gradle 的 APK 工具链 (只需 PyPI + npm), 放到 ${T:-/tmp/apktools}
#   java : PyPI jdk4py (JRE)
#   ecj / d8 / apksigner / android.jar : npm @drxiaozhi/minapk
#   aapt2 (linux x64) : npm aaptjs3
set -euo pipefail
T="${T:-/tmp/apktools}"; mkdir -p "$T"; cd "$T"
if [ ! -x jre/bin/java ]; then
  pip download -q jdk4py --no-deps -d dl && rm -rf jx && mkdir jx && (cd jx && unzip -qo ../dl/jdk4py-*.whl)
  ln -sfn "$T/jx/jdk4py/java-runtime" jre
fi
if [ ! -f d8.jar ]; then
  f=$(npm pack -q @drxiaozhi/minapk | tail -1); mkdir -p m && tar xzf "$f" -C m
  cp m/package/tools/{d8.jar,apksigner.jar,android.jar} . && cp m/package/tools/ecj-*.jar ecj.jar
fi
if [ ! -x aapt2 ]; then
  f=$(npm pack -q aaptjs3 | tail -1); mkdir -p a && tar xzf "$f" -C a
  cp a/package/bin/x64/linux/aapt2 . && chmod +x aapt2
fi
echo "工具链就绪: $T"
