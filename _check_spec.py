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
  [5] workflow 的构建环境：必须用 actions/setup-python 固定 3.11（runner 默认
      python 太新，p4a 交叉编译会炸）；apt 列表不能含 libtinfo5（ubuntu 24.04
      已无此包）；不要 pip install python-for-android —— buildozer 是
      `p4a.url` + `p4a.branch` 去 git clone p4a，pip 那个包根本不会被用到
  [6] p4a 的取材方式：p4a.branch 必须锁 release tag；必须设 p4a.source_dir
      （否则 buildozer 会自己 clone/clean/pull/reset p4a，把我们的 pip 加固补丁
      抹掉），且该路径必须含以 "." 开头的目录段（buildozer 打包只自动跳过隐藏
      目录，否则整个 p4a 会被塞进 APK）；prepare_p4a.sh 的路径要与 spec 一致、
      要调用 patch_p4a_pip.py；ci_build_apk.sh 构建前要校验补丁标记
  [7] 修复脚本：venv 的 bin/python 是**符号链接**，
      用 `-type f -path '*/bin/python'` 会命中 0 个，必须按目录名找；
      并禁止按名字删 `build`（会删掉 $NDK/build）、禁止只做原地修 pip
      （--force-reinstall 不会删除新版本里已不存在的旧文件，混装依旧）
  [8] workflow 里对缓存目录（~/.buildozer）用 find 必须加 `|| true`：
      冷缓存时目录不存在，find 返回非 0 会让该步骤直接失败
  [9] 收集产物必须只取 bin/ 下那一份，不能 `find . -name "*.apk"` 全盘搜：
      一次构建会在三个路径各留一份**完全相同**的 apk（gradle 原始输出 /
      p4a 的 _finish_package 复制到它工作目录的带版本副本 / bin 下的最终产物），
      artifact 解压后出现 3 个 apk，用户不知道装哪个
  [10] 并发与行尾：workflow 要有 concurrency（否则连续 push 会排队跑多次
      20~40 分钟的构建）；.gitattributes 必须存在、必须含 eol=lf，
      **且必须被 .gitignore 放行**（"默认忽略一切"的写法会把它自己吞掉，
      于是锁 LF 等于没做 —— 本项目真中过）
"""
import configparser
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(_HERE, "buildozer.spec")
WORKFLOW = os.path.join(_HERE, ".github", "workflows", "build-apk.yml")
CI_SCRIPT = os.path.join(_HERE, "ci_build_apk.sh")
PREPARE_SCRIPT = os.path.join(_HERE, "prepare_p4a.sh")
PATCH_SCRIPT = os.path.join(_HERE, "patch_p4a_pip.py")


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

    # ---- [5] workflow 的构建环境 ----
    print("\n[5] workflow 的构建环境（Python 版本 / 依赖 / 不装 p4a）")
    if not os.path.exists(WORKFLOW):
        print("    [FAIL] 找不到 .github/workflows/build-apk.yml")
        fails.append("workflow 缺失")
    else:
        wf = open(WORKFLOW, encoding="utf-8").read()
        # 去掉注释行再检查：注释里会写 "ghcr.io/kivy/buildozer:latest"、
        # "libtinfo5" 之类的反例说明，不剔除会把示例当成真实引用
        # （本脚本自己踩过两次）
        code = "\n".join(l for l in wf.splitlines()
                         if not l.strip().startswith("#"))

        # 必须显式用 setup-python 指定 3.11，不能用 runner 默认 python
        if "actions/setup-python" not in code:
            print("    [FAIL] 没有 actions/setup-python：会用 runner 默认 python"
                  "（版本过新，p4a 交叉编译会炸）")
            fails.append("未固定 python 版本")
        elif re.search(r"python-version:\s*[\"']?3\.11", code):
            print("    OK  用 actions/setup-python 固定了 3.11")
        else:
            print("    [FAIL] setup-python 里没写 python-version: '3.11'")
            fails.append("未固定 3.11")

        # 不应再依赖可变 docker 镜像
        if re.search(r"(?:ghcr\.io/)?kivy/buildozer", code):
            print("    [FAIL] 又用上 kivy/buildozer 镜像了（该镜像底层环境出过"
                  "一连串问题；如确实要用，必须锁 @sha256 digest）")
            fails.append("用了未受控的 docker 镜像")

        # 不要 pip install python-for-android：buildozer 自己 git clone p4a
        if re.search(r"pip install[^\n]*python-for-android", code):
            print("    [FAIL] pip install python-for-android 是无效的：buildozer 用"
                  " p4a.url/p4a.branch 自己 git clone，pip 那个包不会被用到。"
                  "要锁 p4a 版本请改 buildozer.spec 的 p4a.branch")
            fails.append("无效的 pip install p4a")

        # apt 依赖：libtinfo5 在 ubuntu 24.04 已不存在。
        # 注意要按「词边界」匹配——apt 列表是把包名内联写在一行里的
        # （如 `g++ gcc git lbzip2`），只匹配独占一行会漏掉。
        if re.search(r"(?<![\w.-])libtinfo5(?![\w.-])", code):
            print("    [FAIL] apt 列表里有 libtinfo5：ubuntu 24.04(noble) 已无此包，"
                  "apt 会直接失败（该用 libtinfo6 或不需要）")
            fails.append("apt 含 libtinfo5")
        else:
            print("    OK  apt 列表没有 libtinfo5")

        # python3-venv 缺失会让 p4a 建 venv 时报 "No module named venv"
        if not re.search(r"(?<![\w.-])python3-venv(?![\w.-])", code):
            print("    [FAIL] apt 列表里没有 python3-venv：p4a 建 venv 时会报"
                  " \"No module named venv\"")
            fails.append("apt 缺 python3-venv")
        else:
            print("    OK  apt 列表含 python3-venv")

        if "PIP_CONSTRAINT" not in wf and "PIP_CONSTRAINT" not in (
                open(CI_SCRIPT, encoding="utf-8").read() if os.path.exists(CI_SCRIPT) else ""):
            print("    [FAIL] 没有任何 PIP_CONSTRAINT 兜住 p4a 内部的 pip 升级")
            fails.append("缺 PIP_CONSTRAINT")
        else:
            print("    OK  有 PIP_CONSTRAINT 钉住 pip 版本")

    # ---- [6] p4a 的取材方式（锁 tag + 用 source_dir 自管 + 补丁校验）----
    print("\n[6] p4a 的取材方式")
    branch = (app.get("p4a.branch") or "").strip()
    if not branch:
        print("    [FAIL] 没写 p4a.branch：prepare_p4a.sh 靠它决定克隆哪个 tag")
        fails.append("缺 p4a.branch")
    elif branch in ("master", "develop", "stable", "main"):
        print(f"    [FAIL] p4a.branch = {branch} 是会漂移的分支；recipe 默认值会变"
              "（python3 曾默认 3.14.2、numpy 曾默认 v2.3.0），同一个 commit"
              "今天能过明天可能挂。锁到具体 tag（如 v2026.05.09）")
        fails.append("p4a.branch 未锁 tag")
    elif not re.match(r"^v?\d{4}\.\d{2}\.\d{2}", branch):
        print(f"    [WARN] p4a.branch = {branch} 看着不像 p4a 的 release tag"
              "（形如 v2026.05.09）")
    else:
        print(f"    OK  已锁到 tag: {branch}")

    src_dir = (app.get("p4a.source_dir") or "").strip()
    if not src_dir:
        print("    [FAIL] 没设 p4a.source_dir：buildozer 会自己 clone/clean/pull/reset"
              " p4a，把我们打好的 pip 加固补丁抹掉")
        fails.append("缺 p4a.source_dir")
    else:
        print(f"    OK  已设 p4a.source_dir = {src_dir}")
        # buildozer 打包只自动跳过以 "." 开头的路径，其它一律会被塞进 APK
        if not any(part.startswith(".") for part in src_dir.replace("\\", "/").split("/")):
            print("    [FAIL] p4a.source_dir 不以 '.' 开头：buildozer 打包时**不会**"
                  "自动跳过它，整个 p4a 源码会被塞进 APK。放到 .buildozer/ 下面"
                  "（buildozer/__init__.py 里 'avoid hidden directory'）")
            fails.append("p4a 会被打进 APK")
        else:
            print("    OK  路径含以 '.' 开头的目录，打包时会被自动跳过")

        if os.path.exists(PREPARE_SCRIPT):
            prep = open(PREPARE_SCRIPT, encoding="utf-8").read()
            m = re.search(r'P4A_REL="\$\{P4A_REL:-([^}]*)\}"', prep)
            rel = m.group(1) if m else None
            if rel != src_dir:
                print(f"    [FAIL] prepare_p4a.sh 的 P4A_REL={rel!r} 与 spec 的"
                      f" p4a.source_dir={src_dir!r} 不一致，克隆位置和 buildozer 找的"
                      "位置就对不上了")
                fails.append("p4a 路径不一致")
            else:
                print(f"    OK  prepare_p4a.sh 与 spec 路径一致：{rel}")
            if "patch_p4a_pip.py" not in prep:
                print("    [FAIL] prepare_p4a.sh 里没有调用 patch_p4a_pip.py")
                fails.append("准备脚本没打补丁")
        else:
            print("    [FAIL] 找不到 prepare_p4a.sh")
            fails.append("准备脚本缺失")

    if os.path.exists(CI_SCRIPT):
        ci = open(CI_SCRIPT, encoding="utf-8").read()
        if "p4a-pip-hardening" not in ci:
            print("    [FAIL] ci_build_apk.sh 构建前没校验 p4a 补丁标记：补丁没打上时"
                  "会照常构建，然后又栽在 pip 混装上")
            fails.append("缺补丁校验")
        else:
            print("    OK  构建前会校验 p4a 补丁标记")

    # 设了 p4a.source_dir 之后，任何"直接跑 buildozer"的入口都会在
    # "Path for p4a.source_dir does not exist" 上直接失败。
    # 本项目的本地入口是 打包APK.sh —— 它必须转交 ci_build_apk.sh（或至少
    # 先调 prepare_p4a.sh）。
    if src_dir:
        # 先按原名找，找不到再试全小写（Windows 上两个名字会指向同一文件，
        # 不去重会打两条一样的消息）
        entries = []
        for entry in ("打包APK.sh", "打包apk.sh"):
            p = os.path.join(_HERE, entry)
            if os.path.exists(p) and not any(
                    os.path.normcase(os.path.realpath(p)) ==
                    os.path.normcase(os.path.realpath(q)) for q in entries):
                entries.append(p)
        for p in entries:
            entry = os.path.basename(p)
            # ⚠️ 必须按"真正的调用形式"判断，不能全文匹配名字：
            #   脚本头注释里解释着 prepare_p4a.sh，echo 的错误提示里也提到它 ——
            #   这两种都会被误判成"已调用"（本脚本已第三次踩同类坑）。
            # 只认行首带 bash / sh / source / . 的调用行。
            body = open(p, encoding="utf-8").read()
            invoked = re.search(
                r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S+\s+)*"
                r"(?:bash|sh|source|\.)\s+\S*(?:prepare_p4a|ci_build_apk)",
                body, re.M,
            )
            if invoked:
                print(f"    OK  {entry}（本地构建入口）会准备 p4a："
                      f"{invoked.group(0).strip()}")
            else:
                print(f"    [FAIL] {entry} 直接跑 buildozer 却没准备 p4a：spec 设了"
                      " p4a.source_dir，目录不存在时 buildozer 会报"
                      " \"Path for p4a.source_dir does not exist\" 直接失败")
                fails.append("本地入口缺 p4a 准备")

    # ---- [7] venv 修复用的 find 写法 ----
    print("\n[7] 修复脚本的 venv 清理写法与约束校验")
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
    print("\n[8] workflow 清理步骤对缓存目录 find 的容错")
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

    # ---- [9] 收集产物只取 bin/ 下那一份 ----
    print("\n[9] workflow 收集 apk 的方式")
    if not os.path.exists(WORKFLOW):
        print("    [FAIL] workflow 缺失")
        fails.append("workflow 缺失")
    else:
        wf = open(WORKFLOW, encoding="utf-8").read()
        code = "\n".join(l for l in wf.splitlines()
                         if not l.lstrip().startswith("#"))
        # find 全盘收 apk 的两种常见写法，都算命中
        broad = (re.search(r"find[^\n]*\.apk[^\n]*cp", code)
                 or re.search(r"find[^\n]*-name\s*[\"']\*\.apk[\"'][^\n]*"
                              r"(-exec|-print|>|\|)", code))
        if broad:
            print("    [FAIL] 用 find 全盘收集 *.apk：一次构建会在三个路径各留一份"
                  "**完全相同**的 apk（gradle 原始输出 / p4a 的 _finish_package 复制"
                  "到 p4a 工作目录的带版本副本 / bin 下的最终产物），artifact 解压后"
                  "会出现 3 个 apk，用户不知道装哪个。只 cp bin/*.apk")
            fails.append("全盘收集 apk")
        elif re.search(r"cp\s+[^\n]*bin/\*\.apk", code):
            print("    OK  只收集 bin/ 下的最终产物")
        else:
            print("    [WARN] 没看到 `cp .../bin/*.apk` 形式的收集步骤，请确认产物来源")
        if "SHORT_SHA" in code:
            print("    OK  artifact 名带提交短哈希，不同次构建可区分")
        else:
            print("    [WARN] artifact 名里没有短哈希：多次构建下载下来文件名相同，"
                  "不好分辨是哪次的")

    # ---- [10] 并发控制与行尾策略 ----
    print("\n[10] 并发控制与行尾（CRLF）策略")
    if os.path.exists(WORKFLOW):
        wf = open(WORKFLOW, encoding="utf-8").read()
        code = "\n".join(l for l in wf.splitlines()
                         if not l.lstrip().startswith("#"))
        if re.search(r"^concurrency:", code, re.M):
            print("    OK  有 concurrency 控制（连续 push 时只跑最新一次构建）")
        else:
            print("    [WARN] workflow 没有 concurrency：连续 push 会排队跑多次"
                  " 20~40 分钟的构建，多数在跑过时版本")
    ga = os.path.join(_HERE, ".gitattributes")
    if not os.path.exists(ga):
        print("    [WARN] 没有 .gitattributes：Windows 工作区的 CRLF 会让 shell 脚本"
              " 到 Linux 上报 bad interpreter: /usr/bin/env bash^M")
    else:
        body = open(ga, encoding="utf-8").read()
        if "eol=lf" not in body:
            print("    [FAIL] .gitattributes 里没有 eol=lf：等于没锁 LF")
            fails.append(".gitattributes 没锁 LF")
        else:
            gi = os.path.join(_HERE, ".gitignore")
            gtxt = (open(gi, encoding="utf-8").read()
                    if os.path.exists(gi) else "")
            # 光有文件不够：它自己也会被"默认忽略一切"的 .gitignore 吞掉
            if re.search(r"^\*", gtxt, re.M) and "!.gitattributes" not in gtxt:
                print("    [FAIL] .gitignore 是'默认忽略一切'的写法，却没有"
                      " !.gitattributes —— 该文件根本没进仓库，锁 LF 等于没做"
                      "（本项目真中过这一枪）")
                fails.append(".gitattributes 未放行")
            else:
                print("    OK  .gitattributes 锁了 LF，且未被 .gitignore 吞掉")

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
