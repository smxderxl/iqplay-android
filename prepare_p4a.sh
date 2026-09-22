#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 准备 python-for-android：按 buildozer.spec 里的 p4a.branch（tag）克隆一份，
# 并给它打上 "pip 加固" 补丁（见 patch_p4a_pip.py）。
#
# 为什么我们自己克隆 p4a，而不是让 buildozer 去 clone：
#   buildozer 的 _install_p4a()（targets/android.py）在每次构建开始时都会检查
#   已存在的 p4a 目录，并且：
#     - `cur_branch` 取的是 `git branch -vv` 的第二个字段；用 tag 克隆时 HEAD 处于
#       detached 状态，解析出来是 "(HEAD"，与 p4a.branch 不等 -> 直接 rmdir 重克隆；
#     - 若分支名"恰好"匹配，它会 `git clean -dxf` + `git pull`，
#       而当 p4a.commit 不是 HEAD 时还会 `git reset --hard`。
#   这几种情况都会**抹掉我们打的补丁**，所以必须改用 p4a.source_dir：
#   一旦在 spec 里设了 p4a.source_dir，buildozer 只检查目录是否存在，绝不改动它。
#
# 顺带的好处：把 p4a 放在 .buildozer/ 下面 —— buildozer 打包时会跳过任何以 "."
# 开头的路径（buildozer/__init__.py:384-388 "avoid hidden directory"），
# 所以它不会被塞进 APK。
# ---------------------------------------------------------------------------
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-_apk_stage}"
SPEC="${SPEC:-$SCRIPT_DIR/buildozer.spec}"
# 与 buildozer.spec 里的 p4a.source_dir 保持一致（相对 APP_DIR）
P4A_REL="${P4A_REL:-.buildozer/android/platform/python-for-android}"
P4A_URL="${P4A_URL:-https://github.com/kivy/python-for-android.git}"

# ---- 从 spec 读出要检出的 tag -------------------------------------------
P4A_TAG="${P4A_TAG:-$(sed -n 's/^[[:space:]]*p4a\.branch[[:space:]]*=[[:space:]]*\(.*\)$/\1/p' "$SPEC" | tail -1 | tr -d '\r' | tr -d '[:space:]')}"
if [ -z "$P4A_TAG" ]; then
  echo "FATAL: 没法从 $SPEC 里读到 p4a.branch（准备脚本用它决定克隆哪个 tag）"
  exit 1
fi
echo "===== [prepare_p4a] tag = $P4A_TAG ====="

if [ ! -d "$APP_DIR" ]; then
  echo "FATAL: 构建目录不存在: $APP_DIR"
  exit 1
fi
cd "$APP_DIR" || exit 1
echo "===== [prepare_p4a] 工作目录 = $PWD ====="

# ---- 克隆（浅克隆即可：p4a 的版本号是 __init__.py 里硬编码的，不靠 git 历史）----
if [ -d "$P4A_REL/.git" ]; then
  echo "     p4a 已存在，跳过克隆：$P4A_REL"
else
  mkdir -p "$(dirname "$P4A_REL")"
  echo "     克隆 $P4A_URL @ $P4A_TAG ..."
  if ! git clone --quiet --depth 1 --branch "$P4A_TAG" --single-branch \
        "$P4A_URL" "$P4A_REL"; then
    echo "FATAL: 克隆 p4a 失败（tag=$P4A_TAG）"
    exit 1
  fi
fi

# 确认检出的确实是我们要的版本
if [ -f "$P4A_REL/pythonforandroid/__init__.py" ]; then
  echo "     实际版本: $(sed -n "s/^__version__ = '\(.*\)'/\1/p" "$P4A_REL/pythonforandroid/__init__.py" | head -1)"
fi
echo "     HEAD: $(git -C "$P4A_REL" rev-parse --short HEAD 2>/dev/null || echo '?')"

# ---- 打补丁 --------------------------------------------------------------
echo "===== [prepare_p4a] 给 p4a 打 pip 加固补丁 ====="
PY="${PYTHON:-python3}"
if ! "$PY" "$SCRIPT_DIR/patch_p4a_pip.py" "$P4A_REL/pythonforandroid/build.py"; then
  echo "FATAL: 打补丁失败"
  exit 1
fi
echo "===== [prepare_p4a] 完成 ====="
