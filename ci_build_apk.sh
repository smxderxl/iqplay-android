#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 在 kivy/buildozer 容器里以 root 执行（由 workflow 通过
#   --entrypoint /bin/bash -c "bash /ci_build_apk.sh" 调起）。
#
# 干三件事：
#   1) 设 PIP_CONSTRAINT，把所有 pip 调用（含 p4a 内部那次 `pip install -U pip`）
#      钉到同一个 pip 版本，避免装出"模块混装"的 pip；
#   2) 跑第一次 buildozer；
#   3) 若失败，逐个修复 .buildozer 里 p4a 创建的所有 venv（用 venv 自己的
#      python 执行 pip，而不是容器里无关的系统 pip），再跑第二次。
#
# 背景（2026-09 云端构建实测）：
#   p4a 的 build.py 在创建 build/venv 后执行
#       base_env["PYTHONPATH"] = ctx.get_site_packages_dir(arch)   # 目标 site-packages
#       shprint(sh.bash, '-c', "source venv/bin/activate && pip install -U pip")
#   即在带着"目标 site-packages"的 PYTHONPATH 下升级 pip，结果 venv 里的
#   pip 相关模块来自不同版本（新版 main.py + 旧版 spinners.py），报
#       ImportError: cannot import name 'open_rich_spinner'
#       from 'pip._internal.cli.spinners'
#   open_rich_spinner 是 pip 25.2 才新增的 API。
# ---------------------------------------------------------------------------
set -u

PIP_PIN="${PIP_PIN:-25.2}"
ENTRY=/usr/local/bin/entrypoint.sh
CONSTRAINTS=/ci_pip_constraints.txt

# ---- 1) 预防：把 pip 版本钉死 -------------------------------------------
printf 'pip==%s\n' "$PIP_PIN" > "$CONSTRAINTS"
export PIP_CONSTRAINT="$CONSTRAINTS"
echo "===== PIP_CONSTRAINT=$PIP_CONSTRAINT (pip==$PIP_PIN) ====="
echo "===== 容器自带 pip: $(python3 -m pip --version 2>/dev/null || echo '?') ====="

# ---- 2) 第一次构建 -------------------------------------------------------
echo "===== [attempt 1] $ENTRY android debug ====="
"$ENTRY" android debug
st1=$?
echo "===== attempt 1 exit code: $st1 ====="

if [ "$st1" -eq 0 ]; then
  echo "===== 第一次就成功，跳过修复 ====="
  exit 0
fi

# ---- 3) 修复 p4a 建出来的 venv -------------------------------------------
echo "===== [repair] 扫描 .buildozer 下的 venv ====="
found=0
fixed=0
removed=0

while IFS= read -r venv; do
  [ -n "$venv" ] || continue
  py="$venv/bin/python"
  [ -e "$py" ] || continue
  found=$((found + 1))
  echo "--- venv: $venv"

  # 3a) 先用 ensurepip 恢复一个"一定能用"的 pip：
  #     它不 import 已损坏的 pip 模块，所以即使 `python -m pip` 已经崩了也能跑。
  "$py" -m ensurepip --upgrade >/dev/null 2>&1 || true

  # 3b) 再强制重装钉死版本的 pip，保证所有模块来自同一份 wheel。
  if "$py" -m pip install --disable-pip-version-check --no-cache-dir \
        --force-reinstall "pip==${PIP_PIN}" >/dev/null 2>&1; then
    echo "    OK -> $("$py" -m pip --version 2>/dev/null || echo '?')"
    fixed=$((fixed + 1))
  else
    # 3c) 兜底：直接删掉整个 venv，让 p4a 下次重建（重建时会再走一次
    #     `pip install -U pip`，此时有 PIP_CONSTRAINT 兜着，能装干净的）。
    echo "    pip 修不好 -> 删除该 venv，交给 p4a 重建"
    rm -rf "$venv"
    removed=$((removed + 1))
  fi
done < <(find /home/user/hostcwd/.buildozer /home/user/.buildozer \
           -type d -name venv -prune -print 2>/dev/null)

echo "===== venv 统计: 找到 $found / 修复 $fixed / 删除重建 $removed ====="

# ---- 4) 第二次构建 -------------------------------------------------------
echo "===== [attempt 2] $ENTRY android debug ====="
"$ENTRY" android debug
st2=$?
echo "===== attempt 2 exit code: $st2 ====="
exit "$st2"
