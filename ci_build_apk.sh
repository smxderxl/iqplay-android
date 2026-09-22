#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 打安卓 APK 的构建包装脚本（**直接在 runner / Linux 主机上跑**，不用容器）。
#   GitHub Actions 里由 workflow 调：bash ci_build_apk.sh
#
# 它负责三件事：
#   1. 准备 p4a：按 spec 的 p4a.branch 克隆到 p4a.source_dir，并打 pip 加固补丁
#      （委托给 prepare_p4a.sh + patch_p4a_pip.py）
#   2. 用 PIP_CONSTRAINT 把 pip 钉到 PIP_PIN
#   3. 跑 buildozer；失败则清掉 venv 与半成品 dist 后重试一次
#
# 症状（同一根因的多种表现，都在构建早期炸掉）
#   ImportError: cannot import name 'open_rich_spinner' from 'pip._internal.cli.spinners'
#   ImportError: cannot import name 'RequirementInformation' from 'pip._vendor.resolvelib.structs'
#   两者的共同点：venv 里的 pip 相关模块来自**不同版本**。
#
# 根因（读 p4a 源码定位）
#   pythonforandroid/build.py，创建 build/venv 之后：
#       base_env["PYTHONPATH"] = ctx.get_site_packages_dir(arch)   # 目标 site-packages
#       shprint(sh.bash, '-c', "source venv/bin/activate && pip install -U pip")
#   在带着"指向目标 site-packages"的 PYTHONPATH 下做 pip 自升级，留下混装 pip。
#   → 我们直接用补丁把那条命令换成"先删干净 pip 目录，再 ensurepip 重建，
#     再按 PIP_CONSTRAINT 升级"，从根上消除混装（见 patch_p4a_pip.py）。
#
# 注意：那批 "No matching distribution found for numpy==2.2.6 / pyjnius==1.7.0 /
#      kivy==2.3.0" 是**探针噪音，不是失败原因**：
#      build.py 用 `pip install ... --dry-run --only-binary=:all: --report` 试探
#      有没有可用的 Android wheel，异常被 `except Exception -> warning` 吞掉；
#      recipe.py 的 lookup_prebuilt() 同样把失败当"没有预编译包"正常返回 False。
#      另外 pyjnius==1.7.0 是 p4a recipe 自己的默认版本，不是我们的 pin。
# ---------------------------------------------------------------------------
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIP_PIN="${PIP_PIN:-25.2}"
APP_DIR="${APP_DIR:-_apk_stage}"
CONSTRAINTS="${PIP_CONSTRAINTS_FILE:-/tmp/ci_pip_constraints.txt}"
P4A_REL="${P4A_REL:-.buildozer/android/platform/python-for-android}"

if [ ! -d "$APP_DIR" ]; then
  echo "FATAL: 构建目录不存在: $APP_DIR（workflow 里应先用'准备干净构建目录'步骤生成）"
  exit 1
fi

# ---- 0) 准备 p4a（克隆到指定 tag + 打 pip 加固补丁） ----------------------
echo "===== [0] 准备 p4a ====="
if [ ! -x "$SCRIPT_DIR/prepare_p4a.sh" ] && [ ! -f "$SCRIPT_DIR/prepare_p4a.sh" ]; then
  echo "FATAL: 找不到 $SCRIPT_DIR/prepare_p4a.sh"
  exit 1
fi
APP_DIR="$APP_DIR" bash "$SCRIPT_DIR/prepare_p4a.sh" || { echo "FATAL: 准备 p4a 失败"; exit 1; }

cd "$APP_DIR" || exit 1
echo "===== 构建目录: $PWD ====="

# 校验补丁确实打上了（没有它 pip 混装问题会复现）
if ! grep -q "p4a-pip-hardening" "$P4A_REL/pythonforandroid/build.py" 2>/dev/null; then
  echo "FATAL: p4a 的 build.py 里没有找到 pip 加固补丁标记，构建会重蹈 pip 混装的坑"
  exit 1
fi
echo "===== p4a 补丁校验通过 ====="

# ---- 1) 准备约束文件，并立刻校验它真的生效（不是设了就算） ----------------
printf 'pip==%s\n' "$PIP_PIN" > "$CONSTRAINTS"
export PIP_CONSTRAINT="$CONSTRAINTS"

: "${PIP_CONSTRAINT:?PIP_CONSTRAINT 未设置，pip 版本将不受控}"
if [ ! -s "$PIP_CONSTRAINT" ]; then
  echo "FATAL: 约束文件不存在或为空: $PIP_CONSTRAINT"
  exit 1
fi
if ! grep -q '^pip==' "$PIP_CONSTRAINT"; then
  echo "FATAL: 约束文件里没有 pip== 钉版行: $PIP_CONSTRAINT"
  exit 1
fi
echo "===== 生效的 pip 约束（$PIP_CONSTRAINT）====="
cat "$PIP_CONSTRAINT"
echo "===== 宿主 python: $(python3 --version 2>&1) ====="
echo "===== 宿主 pip: $(python3 -m pip --version 2>&1) ====="
echo "===== buildozer: $(command -v buildozer || echo '未找到！') ====="

# ---- 2) 清理 p4a 遗留的 venv ---------------------------------------------
# 只删**目录名恰好是 venv** 的目录。
# ⚠️ 绝不能按名字删 `build`：$NDK/build/ 里是 android.toolchain.cmake 与
#    build/core/*.mk（p4a 有 9 个 recipe 靠 join(ndk_dir,'build','cmake',...) 工作），
#    删掉 NDK 就废了，而且 ~/.buildozer 是缓存的，损坏会一直带下去。
clean_venvs() {
  local n=0 v ver
  while IFS= read -r v; do
    [ -n "$v" ] || continue
    if [ -e "$v/bin/python" ]; then
      ver="$("$v/bin/python" -m pip --version 2>/dev/null || echo '<pip 已损坏，无法执行>')"
      echo "    - 删除 $v   (其中 pip: $ver)"
    else
      echo "    - 删除 $v"
    fi
    if rm -rf "$v"; then n=$((n + 1)); fi
  done < <(find "$PWD/.buildozer" "$HOME/.buildozer" \
             -type d -name venv -prune -print 2>/dev/null || true)
  echo "    共删除 $n 个 venv"
}

# ---- 3) 清掉半成品的 dist（保留 recipe 编译产物，那个很贵） ----------------
# 半成品 dist 会让 p4a 在重试时行为异常；recipe 的 build-* 目录可安全复用。
reset_dist() {
  local d n=0
  for d in "$PWD/.buildozer/android/platform/"build-*/dists \
           "$PWD/.buildozer/android/platform/dists"; do
    [ -e "$d" ] || continue
    echo "    - 删除 $d"
    rm -rf "$d" && n=$((n + 1))
  done
  echo "    共删除 $n 项（recipe 编译产物、SDK/NDK、p4a 克隆保留）"
}

echo "===== [清理] 构建前删除遗留 venv ====="
clean_venvs

# ---- 4) 第一次构建 -------------------------------------------------------
echo "===== [attempt 1] buildozer android debug ====="
buildozer android debug
st1=$?
echo "===== attempt 1 exit code: $st1 ====="

if [ "$st1" -eq 0 ]; then
  echo "===== 第一次就成功，无需重试 ====="
  exit 0
fi

# ---- 5) 清理后重试 -------------------------------------------------------
echo "===== [清理] 删除 venv 与半成品 dist（不原地修 pip）====="
clean_venvs
reset_dist

echo "===== [attempt 2] buildozer android debug ====="
buildozer android debug
st2=$?
echo "===== attempt 2 exit code: $st2 ====="
exit "$st2"
