# -*- coding: utf-8 -*-
"""
给 python-for-android 的 build.py 打一个 "pip 加固" 补丁。

为什么需要（读 p4a 源码定位，不是猜）
  p4a 在创建 build/venv 之后执行：
      base_env["PYTHONPATH"] = ctx.get_site_packages_dir(arch)   # 目标 site-packages
      shprint(sh.bash, '-c', "source venv/bin/activate && pip install -U pip")
  即在 `PYTHONPATH` 指向"目标 site-packages"的环境里做 pip 自升级，结果 venv 里
  的 pip 相关模块来自不同版本，之后 import 就自相矛盾，报的是这类错：
      ImportError: cannot import name 'open_rich_spinner' from 'pip._internal.cli.spinners'
      ImportError: cannot import name 'RequirementInformation' from 'pip._vendor.resolvelib.structs'
  两种报错是同一根因的两种表现（一个露在 _internal，一个露在 _vendor）。

补丁做的事
  1. 先把 venv 里**整个 pip 目录删掉**；
  2. 用 `ensurepip` 恢复一个干净的 pip（它不 import pip，所以 pip 已经坏了也能跑）；
  3. 再升级到目标版本（版本由环境变量 PIP_CONSTRAINT 钉住）。

为什么必须"先删再装"
  `pip install -U pip` 与 `--force-reinstall` 都只用新 wheel **覆盖同名文件**，
  **不会删除新版本里已不存在的旧文件**。旧 pip 的 _internal / _vendor 里残留的
  模块照样能被 import 到 —— "混装"依旧。只有先删干净再装才能根除。

用法
    python patch_p4a_pip.py <p4a 的 pythonforandroid/build.py 路径>

退出码
    0 = 已打上补丁（或本来就已打过）
    1 = 找不到可打补丁的位置（说明 p4a 版本变了，需要更新本脚本的匹配串）
"""
import os
import sys

# p4a build.py 里那句 shell 命令的原文（v2026.05.09 中只出现 1 次）
OLD = "source venv/bin/activate && pip install -U pip"

# 替换后的命令。注意：它会被插进 build.py 的**双引号字符串字面量**里，
# 所以这里不能出现双引号或反斜杠。
MARKER = "# p4a-pip-hardening"
NEW = (
    "source venv/bin/activate"
    " && rm -rf $VIRTUAL_ENV/lib/python*/site-packages/pip"
    " $VIRTUAL_ENV/lib/python*/site-packages/pip-*.dist-info"
    " && python -m ensurepip --default-pip"
    " && python -m pip install --no-cache-dir --disable-pip-version-check --upgrade pip"
    " " + MARKER
)

DEFAULT_BUILD_PY = os.path.join(
    ".buildozer", "android", "platform", "python-for-android",
    "pythonforandroid", "build.py",
)


def main(argv):
    path = argv[1] if len(argv) > 1 else DEFAULT_BUILD_PY

    if not os.path.isfile(path):
        print(f"[patch_p4a_pip] FATAL: 找不到 {path}")
        print("                 （先克隆 p4a 到该位置，或把路径作为参数传入）")
        return 1

    with open(path, encoding="utf-8") as f:
        src = f.read()

    if MARKER in src:
        print(f"[patch_p4a_pip] 已经打过补丁，跳过：{path}")
        return 0

    if OLD not in src:
        print(f"[patch_p4a_pip] FATAL: 在 {path} 里找不到要替换的命令：")
        print(f"                 {OLD!r}")
        print("                 说明 p4a 版本变了，需要更新本脚本的 OLD/NEW。")
        return 1

    n = src.count(OLD)
    patched = src.replace(OLD, NEW, 1)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(patched)

    with open(path, encoding="utf-8") as f:
        check = f.read()
    if MARKER not in check or OLD in check:
        print(f"[patch_p4a_pip] FATAL: 写入后校验失败：{path}")
        return 1

    print(f"[patch_p4a_pip] 补丁已应用（原文出现 {n} 次，替换第 1 次）：{path}")
    print(f"[patch_p4a_pip] 新命令：")
    print(f"    {NEW}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
