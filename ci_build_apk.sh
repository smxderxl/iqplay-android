#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 在 kivy/buildozer 容器里以 root 执行（由 workflow 通过
#   --entrypoint /bin/bash -c "bash /ci_build_apk.sh" 调起）。
#
# 目标：解决 p4a 自建的 build/venv 里 pip 被升级成"模块混装"的问题。
#
# 症状（同一根因的多种表现，都会在构建早期炸掉）：
#   ImportError: cannot import name 'open_rich_spinner' from 'pip._internal.cli.spinners'
#   ImportError: cannot import name 'RequirementInformation' from 'pip._vendor.resolvelib.structs'
# 前者是 pip 25.2 才新增的 API；后者是 pip._vendor.resolvelib 版本对不上。
# 共同点：venv 里的 pip 相关模块来自**不同版本**。
#
# 根因（读 p4a 源码定位，不是猜）：
#   pythonforandroid/build.py 第 868-879 行，创建 build/venv 之后：
#       base_env["PYTHONPATH"] = ctx.get_site_packages_dir(arch)   # 目标 site-packages
#       shprint(sh.bash, '-c', "source venv/bin/activate && pip install -U pip")
#   在带着"指向目标 site-packages"的 PYTHONPATH 下执行 pip 自升级，
#   结果 venv 里的 pip 散成多个版本，import 时自相矛盾。
#
# 处理思路（预防 + 兜底）：
#   1) 预防：用 PIP_CONSTRAINT 把 pip 钉到 PIP_PIN，让 p4a 内部那次
#      `pip install -U pip` 装出完整同一份 wheel（PIP_CONSTRAINT 会经
#      entrypoint.sh 的 `sudo --preserve-env` 透传到 p4a 的子进程）；
#   2) 兜底：失败时**直接删掉** p4a 的 venv，再重试一次。
#
#   为什么兜底要"删"而不是"原地修 pip"：
#     `pip install --force-reinstall pip==X` 只用新 wheel **覆盖同名文件**，
#     **不会删除新版本里已不存在的旧文件**。pip 24.0 的 _internal / _vendor
#     里有不少 25.2 已移除的模块，残留下来照样能被 import 到 —— "混装"依旧。
#     删掉整个 venv 让 p4a 重建，才能根除这一类问题。
# ---------------------------------------------------------------------------
set -uo pipefail

PIP_PIN="${PIP_PIN:-25.2}"
ENTRY=/usr/local/bin/entrypoint.sh
CONSTRAINTS=/ci_pip_constraints.txt

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
echo "===== 容器自带 pip: $(python3 -m pip --version 2>/dev/null || echo '?') ====="
echo "===== PIP_CONSTRAINT=$PIP_CONSTRAINT ====="

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
  done < <(find /home/user/hostcwd/.buildozer /home/user/.buildozer \
             -type d -name venv -prune -print 2>/dev/null)
  echo "    共删除 $n 个 venv"
}

echo "===== [清理] 构建前删除遗留 venv ====="
clean_venvs

# ---- 2) 第一次构建 -------------------------------------------------------
echo "===== [attempt 1] $ENTRY android debug ====="
"$ENTRY" android debug
st1=$?
echo "===== attempt 1 exit code: $st1 ====="

if [ "$st1" -eq 0 ]; then
  echo "===== 第一次就成功，无需重试 ====="
  exit 0
fi

# ---- 3) 删掉可能已被弄坏的 venv 后重试 -----------------------------------
echo "===== [清理] 删除 p4a 的 venv（不原地修 pip）后重试 ====="
clean_venvs

echo "===== [attempt 2] $ENTRY android debug ====="
"$ENTRY" android debug
st2=$?
echo "===== attempt 2 exit code: $st2 ====="
exit "$st2"
