# -*- coding: utf-8 -*-
"""
buildozer.spec 静态检查 —— 专查本项目在云端构建时真实踩过的坑。

用法：
    python _check_spec.py            # 检查当前目录的 buildozer.spec

背景（每一个检查项都对应一次真实的构建失败）：
  [1] 行尾注释：INI 不支持行尾 '#'，取值时整串 "24   # 注释" 会被当成值，
      导致 p4a 收到 --ndk-api='24   # 覆盖到 Android 7.0' -> exit code 2
  [2] python3 与 hostpython3 版本必须一致且都要锁，
      否则 "python3 should have same version as hostpython3, X != Y"
  [3] numpy 版本写法必须带 'v'（git tag），python3 不能带 'v'（tarball）
  [4] requirements 里不能写裸的 'android'
  [5] workflow 里的 docker 镜像必须锁 digest（不能用 :latest，它会静默换
      掉镜像内的 python/pip 组合，同一个 commit 今天能过、明天就挂）
  [6] 修复 venv 的 find 表达式：venv 的 bin/python 是**符号链接**，
      用 `-type f -path '*/bin/python'` 会命中 0 个，必须按目录名找；
      并禁止按名字删 `build`（会删掉 $NDK/build）、禁止只做原地修 pip
  [7] workflow 里对缓存目录（~/.buildozer）用 find 必须加 `|| true`：
      冷缓存时目录不存在，find 返回非 0 会让该步骤直接失败
"""
import configparser
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(_HERE, "buildozer.spec")
WORKFLOW = os.path.join(_HERE, ".github", "workflows", "build-apk.yml")
CI_SCRIPT = os.path.join(_HERE, "ci_build_apk.sh")


def main():
    if not os.path.exists(SPEC):
        print("找不到 buildozer.spec")
        return 1

    raw = open(SPEC, encoding="utf-8").read()
    fails = []

    # ---- [1] 行尾注释 ----
    print("[1] 行尾注释检查（INI 不支持 '#' 写在值后面）")
    eol = []
    for i, ln in enumerate(raw.splitlines(), 1):
        s = ln.strip()
        if not s or s.startswith("#") or s.startswith("[") or "=" not in s:
            continue
        _, _, val = s.partition("=")
        # 颜色值（整串就是 #xxxxxx）是合法的，不算注释
        if "#" in val and not re.fullmatch(r"\s*#[0-9a-fA-F]{3,8}\s*", val):
            eol.append((i, s))
    if eol:
        for i, s in eol:
            print(f"    [FAIL] 第{i}行: {s}")
        fails.append("行尾注释")
    else:
        print("    OK")

    # ---- 解析 ----
    p = configparser.ConfigParser()
    p.read_string(raw)
    app = p["app"]
    reqs = {}
    for item in app.get("requirements", "").split(","):
        item = item.strip()
        if "==" in item:
            n, v = item.split("==", 1)
            reqs[n.strip()] = v.strip()
        elif item:
            reqs[item] = None

    # ---- [2] python3 / hostpython3 ----
    print("\n[2] python3 与 hostpython3 版本一致性")
    py, hp = reqs.get("python3"), reqs.get("hostpython3")
    if "python3" not in reqs:
        print("    [FAIL] requirements 里没有 python3")
        fails.append("缺 python3")
    elif py is None:
        print("    [FAIL] python3 没锁版本，会用 recipe 默认 3.14.2")
        fails.append("python3 未锁")
    elif "hostpython3" not in reqs:
        print("    [FAIL] 锁了 python3 但没锁 hostpython3，构建必失败")
        fails.append("hostpython3 缺失")
    elif hp != py:
        print(f"    [FAIL] python3={py} 与 hostpython3={hp} 不一致")
        fails.append("python 版本不一致")
    else:
        print(f"    OK（两者同为 {py}）")

    # ---- [3] 版本写法 ----
    print("\n[3] 版本号写法（numpy 用 git 要带 v，python3 用 tarball 不带 v）")
    if py and py.startswith("v"):
        print(f"    [FAIL] python3={py} 不应带 v（tarball url 已含 v{{version}}）")
        fails.append("python3 写法")
    else:
        print(f"    OK  python3={py}（不带 v）")
    npv = reqs.get("numpy")
    if npv and not npv.startswith("v"):
        print(f"    [FAIL] numpy={npv} 必须写成 v{npv}（recipe 是 git checkout tag）")
        fails.append("numpy 写法")
    elif npv:
        print(f"    OK  numpy={npv}（带 v）")
    else:
        print("    [FAIL] numpy 未锁版本（会取 recipe 默认 v2.3.0）")
        fails.append("numpy 未锁")

    # ---- [4] 裸 android ----
    print("\n[4] requirements 里不能有裸的 android")
    if "android" in reqs:
        print("    [FAIL] 写了裸 android，它在 p4a 里不是独立 recipe 名")
        fails.append("裸 android")
    else:
        print("    OK")

    # ---- [5] workflow 镜像锁 digest ----
    print("\n[5] workflow 的 docker 镜像是否锁 digest（不能用 :latest）")
    if not os.path.exists(WORKFLOW):
        print("    [FAIL] 找不到 .github/workflows/build-apk.yml")
        fails.append("workflow 缺失")
    else:
        wf = open(WORKFLOW, encoding="utf-8").read()
        # 去掉注释行再检查：注释里会写 "ghcr.io/kivy/buildozer:latest" 这种
        # 升级示例，不剔除就会把示例当成真实引用（这个坑本脚本自己踩过一次）
        code = "\n".join(l for l in wf.splitlines()
                         if not l.strip().startswith("#"))
        pinned = re.findall(r"[\w./-]+@sha256:[0-9a-f]{64}", code)
        mut = re.findall(r"(?:ghcr\.io/)?kivy/buildozer:([a-zA-Z0-9._-]+)", code)
        if mut:
            print(f"    [FAIL] 用了可变标签: {sorted(set(mut))} -- 锁到 @sha256:... 才行")
            fails.append("镜像未锁 digest")
        elif pinned:
            print(f"    OK  已锁 digest: {pinned[0][:64]}...")
        else:
            print("    [FAIL] workflow 里没找到任何 @sha256: 形式的镜像引用")
            fails.append("镜像未锁 digest")
        if "PIP_CONSTRAINT" not in wf and "PIP_CONSTRAINT" not in (
                open(CI_SCRIPT, encoding="utf-8").read() if os.path.exists(CI_SCRIPT) else ""):
            print("    [FAIL] 没有任何 PIP_CONSTRAINT 兜住 p4a 内部的 pip 升级")
            fails.append("缺 PIP_CONSTRAINT")
        else:
            print("    OK  有 PIP_CONSTRAINT 钉住 pip 版本")

    # ---- [6] venv 修复用的 find 写法 ----
    print("\n[6] 修复脚本的 venv 清理写法与约束校验")
    if not os.path.exists(CI_SCRIPT):
        print("    [FAIL] 找不到 ci_build_apk.sh")
        fails.append("修复脚本缺失")
    else:
        ci = open(CI_SCRIPT, encoding="utf-8").read()
        # 去掉注释行再检查：注释里会举例写 `--force-reinstall`、`-name build`、
        # `pip install -U pip` 等，全文匹配会把注释误判成真实代码
        # （本脚本已经在 workflow 检查上踩过一次同类坑）
        ci_code = "\n".join(l for l in ci.splitlines()
                            if not l.lstrip().startswith("#"))

        # venv 的 bin/python 是符号链接，-type f 命中不到
        if re.search(r"-type\s+f[^\n]*-path[^\n]*bin/python", ci_code):
            print("    [FAIL] 用了 -type f -path '*/bin/python'：venv 的 bin/python 是"
                  "符号链接，会命中 0 个")
            fails.append("find 写法会漏掉 venv")
        elif re.search(r"-type\s+d\s+-name\s+venv", ci_code):
            print("    OK  按目录名 (-type d -name venv) 查找，能正确命中")
        else:
            print("    [FAIL] ci_build_apk.sh 里没有可靠的 venv 查找逻辑")
            fails.append("缺 venv 查找")

        # 只删 venv，绝不能按名字删 build（会删掉 $NDK/build）
        if re.search(r"-name\s+[\"']?build[\"']?", ci_code):
            print("    [FAIL] 出现按名字删 build 的写法：会删掉 $NDK/build/"
                  "（android.toolchain.cmake / build/core），把缓存的 NDK 弄废")
            fails.append("会误删 NDK/build")
        else:
            print("    OK  没有按名字删 build 的危险写法")

        # 原地修 pip 修不干净：force-reinstall 不删新版本已移除的旧文件
        if "--force-reinstall" in ci_code:
            print("    [FAIL] 只做原地修 pip：--force-reinstall 不会删掉新版本已移除的"
                  "旧文件，混装依旧；应直接删 venv 让 p4a 重建")
            fails.append("只原地修 pip")
        else:
            print("    OK  走的是「删 venv 重建」而非原地修 pip")

        # 约束文件要能被校验（设了不等于生效）
        if re.search(r"PIP_CONSTRAINT", ci_code) and \
                re.search(r"-s\s+[\"']?\$PIP_CONSTRAINT", ci_code):
            print("    OK  有 PIP_CONSTRAINT 约束文件存在性/非空校验")
        else:
            print("    [FAIL] 没有校验 PIP_CONSTRAINT 对应的约束文件真的存在且非空")
            fails.append("缺约束文件校验")

    # 注：ci_build_apk.sh 刻意不开 `set -e`（第一轮失败后要接着重试），
    # 所以 process substitution 里的 find 失败不会中断；真正需要 `|| true`
    # 的是 workflow 里那步（那里出错会让整个 job 失败）。
    print("\n[7] workflow 清理步骤对缓存目录 find 的容错")
    if not os.path.exists(WORKFLOW):
        print("    [FAIL] 找不到 workflow")
        fails.append("workflow 缺失")
    else:
        bad = [l.strip() for l in open(WORKFLOW, encoding="utf-8").read().splitlines()
               if "find" in l and ".buildozer" in l and "|| true" not in l
               and not l.strip().startswith("#")]
        if bad:
            print("    [FAIL] 这些 find 没加 '|| true'：冷缓存时目录不存在，"
                  "find 返回非 0 会让该步骤失败")
            for l in bad:
                print(f"      {l}")
            fails.append("workflow find 缺 || true")
        else:
            print("    OK  workflow 里的 find 都有容错")

    # ---- 汇总 ----
    print("\n" + "=" * 46)
    if fails:
        print(f"检查未通过：{len(fails)} 项 -> {', '.join(fails)}")
        return 1
    print("全部检查通过")
    print(f"  requirements = {app.get('requirements')}")
    print(f"  api={app.get('android.api')} minapi={app.get('android.minapi')} "
          f"ndk={app.get('android.ndk')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
