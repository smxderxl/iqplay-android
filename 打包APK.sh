#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 打安卓 APK（在 Linux / WSL / macOS 里跑；Windows 原生 buildozer 不支持）
#
#   1) 装依赖：pip install buildozer cython
#   2) 执行：  bash 打包APK.sh          # 产物在 _apk_stage/bin/*.apk
#             bash 打包APK.sh logcat   # 真机日志
#
# 为什么要建 _apk_stage/：buildozer 会把 source.dir **整个**打包，而本项目
# 目录里有几十个无关的 .py / cs16 / 截图，直接打会把 APK 撑大还可能混进
# 同名模块。这里先把"只该进 APK 的三个文件 + spec"拷进干净目录再构建。
# ---------------------------------------------------------------------------
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
STAGE="$HERE/_apk_stage"
NEED="main.py iqplay_android.py iqdsp.py buildozer.spec"

if ! command -v buildozer >/dev/null 2>&1; then
    echo "找不到 buildozer，请先： pip install buildozer cython"
    exit 1
fi

rm -rf "$STAGE"
mkdir -p "$STAGE"
for f in $NEED; do
    if [ ! -f "$HERE/$f" ]; then
        echo "缺少源文件：$f"
        exit 1
    fi
    cp "$HERE/$f" "$STAGE/$f"
done
# 中文字体兜底：本机放了 font.ttf / 字体.ttf 就一起带上
for f in font.ttf 字体.ttf; do
    [ -f "$HERE/$f" ] && cp "$HERE/$f" "$STAGE/$f"
done

cd "$STAGE"
echo "== 暂存目录内容 =="
ls -la

if [ "${1:-}" = "logcat" ]; then
    buildozer -v android logcat
else
    echo "== 开始构建（首次会下载 Android SDK/NDK，约 3~5 GB）=="
    buildozer -v android debug
    echo
    echo "== 完成。APK 在：$STAGE/bin/ =="
    ls -la "$STAGE/bin/" 2>/dev/null || true
fi
