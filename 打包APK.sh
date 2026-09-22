#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 打安卓 APK（在 Linux / WSL / macOS 里跑；Windows 原生 buildozer 不支持）
#
# 用法：
#     bash 打包APK.sh            # 构建，产物在 _apk_stage/bin/*.apk
#     bash 打包APK.sh logcat     # 看真机日志（需先用 USB 连上手机）
#
# 前置依赖：
#     python3.11  、  pip install "cython<3" "buildozer==1.6.0"  、  git
#     （git 是必需的：p4a 由 prepare_p4a.sh 克隆，不是 buildozer 自己拉）
#
# 为什么要建 _apk_stage/：buildozer 会把 source.dir **整个**打包，而本项目
# 目录里有几十个无关的 .py / cs16 / 截图，直接打会把 APK 撑大还可能混进
# 同名模块。这里先把"只该进 APK 的三个文件 + spec"拷进干净目录再构建。
#
# 为什么构建要交给 ci_build_apk.sh、而不是直接跑 buildozer：
#   buildozer.spec 里设了 p4a.source_dir（为了给 p4a 打 pip 加固补丁，而补丁
#   必须由我们自己克隆的 p4a 提供）。**若该目录不存在，buildozer 会直接报错
#   退出："Path for p4a.source_dir does not exist"。**
#   ci_build_apk.sh 会先调 prepare_p4a.sh（按 p4a.branch 克隆到该位置 → 打补丁
#   → 校验补丁标记），再设 PIP_CONSTRAINT 跑 buildozer，失败还会清理并重试一次。
# ---------------------------------------------------------------------------
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
STAGE="$HERE/_apk_stage"
NEED="main.py iqplay_android.py iqdsp.py buildozer.spec"

if ! command -v buildozer >/dev/null 2>&1; then
    echo "找不到 buildozer，请先： python3.11 -m pip install \"cython<3\" \"buildozer==1.6.0\""
    exit 1
fi
if ! command -v git >/dev/null 2>&1; then
    echo "找不到 git —— 需要它来克隆 python-for-android（prepare_p4a.sh 会用到）"
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

echo "== 暂存目录内容 =="
ls -la "$STAGE"

cd "$HERE"

if [ "${1:-}" = "logcat" ]; then
    cd "$STAGE"
    buildozer -v android logcat
else
    echo "== 开始构建（首次会下载 Android SDK/NDK，约 3~5 GB）=="
    APP_DIR="$STAGE" bash "$HERE/ci_build_apk.sh"
    echo
    echo "== 完成。APK 在：$STAGE/bin/ =="
    ls -la "$STAGE/bin/" 2>/dev/null || true
fi
