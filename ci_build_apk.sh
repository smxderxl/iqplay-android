#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 打安卓 APK 的构建包装脚本（**直接在 runner / Linux 主机上跑**，不用容器）。
#   GitHub Actions 里由 workflow 调：bash ci_build_apk.sh
#
# 目标：解决 p4a 自建的 build/venv 里 pip 变成"模块混装"的问题。
#
# 症状（同一根因的多种表现，都在构建早期炸掉）：
#   ImportError: cannot import name 'open_rich_spinner' from 'pip._internal.cli.spinners'
#   ImportError: cannot import name 'RequirementInformation' from 'pip._vendor.resolvelib.structs'
# 前者是 pip 25.2 才新增的 API；后者是 pip._vendor.resolvelib 版本对不上。
# 共同点：venv 里的 pip 相关模块来自**不同版本**。
#
# 根因（读 p4a 源码定位，不是猜）：
#   pythonforandroid/build.py 里创建 build/venv 之后：
#       base_env["PYTHONPATH"] = ctx.get_site_packages_dir(arch)   # 目标 site-packages
#       shprint(sh.bash, '-c', "source venv/bin/activate && pip install -U pip")
#   在带着"指向目标 site-packages"的 PYTHONPATH 下执行 pip 自升级，
#   结果 venv 里的 pip 散成多个版本，import 时自相矛盾。
#
# 处理思路（预防 + 兜底）：
#   1) 预防：用 PIP_CONSTRAINT 把 pip 钉到 PIP_PIN，让 p4a 内部那次
#      `pip install -U pip` 装出完整同一份 wheel。
#      直接在宿主 shell 里 export 即可 —— p4a 的 pip 子进程是它的后代，
#      会自然继承（比走容器 entrypoint 的 sudo --preserve-env 更简单可靠）。
#   2) 兜底：失败时**删掉** p4a 的 venv 与它已产出的构建目录/dists
#      （半成品状态会让 p4a 行为异常，例如报 "No module named venv"），
#      再重试一次。
#
#   为什么兜底要"删"而不是"原地修 pip"：
#     `pip install --force-reinstall pip==X` 只用新 wheel **覆盖同名文件**，
#     **不会删除新版本里已不存在的旧文件**。旧 pip 的 _internal / _vendor
#     里有不少新版本已移除的模块，残留下来照样能被 import 到 —— "混装"依旧。
# ---------------------------------------------------------------------------
set -uo pipefail

PIP_PIN="${PIP_PIN:-25.2}"
APP_DIR="${APP_DIR:-_apk_stage}"
CONSTRAINTS="${PIP_CONSTRAINTS_FILE:-/tmp/ci_pip_constraints.txt}"

if [ ! -d "$APP_DIR" ]; then
  echo "FATAL: 构建目录不存在: $APP_DIR（workflow 里应先用'准备干净构建目录'步骤生成）"
  exit 1
fi
cd "$APP_DIR"
echo "===== 构建目录: $PWD ====="

# ---- 0) 准备约束文件，并立刻校验它真的生效（不是设了就算） ----------------
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

# ---- 1) 清理 p4a 遗留的 venv ---------------------------------------------
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

# ---- 2) 重置 p4a 的构建产物（保留 p4a 自己的 git 克隆与 SDK/NDK）----------
reset_p4a_build() {
  local d n=0
  for d in "$PWD/.buildozer/android/platform/"build-* \
           "$PWD/.buildozer/android/platform/dists"; do
    [ -e "$d" ] || continue
    echo "    - 删除 $d"
    rm -rf "$d" && n=$((n + 1))
  done
  echo "    共删除 $n 项（SDK/NDK 与 p4a 克隆保留）"
}

echo "===== [清理] 构建前删除遗留 venv ====="
clean_venvs

# ---- 3) 第一次构建 -------------------------------------------------------
echo "===== [attempt 1] buildozer android debug ====="
buildozer android debug
st1=$?
echo "===== attempt 1 exit code: $st1 ====="

if [ "$st1" -eq 0 ]; then
  echo "===== 第一次就成功，无需重试 ====="
  exit 0
fi

# ---- 4) 彻底重置后重试 ---------------------------------------------------
echo "===== [清理] 删除 p4a 的 venv 与半成品构建目录（不原地修 pip）====="
clean_venvs
reset_p4a_build

echo "===== [attempt 2] buildozer android debug ====="
buildozer android debug
st2=$?
echo "===== attempt 2 exit code: $st2 ====="
exit "$st2"
