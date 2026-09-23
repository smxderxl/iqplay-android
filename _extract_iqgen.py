# -*- coding: utf-8 -*-
"""从 IQ生成器.py 自动抽取「纯算法层」-> iqgen_dsp.py（安卓版用）。

复用 `_extract_dsp.py` 的做法：AST 解析 -> 以入口白名单为起点做可达性 BFS ->
按原顺序输出被用到的顶层语句。区别在于：
  · IQ生成器.py 是 8273 行 / 133 个顶层函数 / 147 种调制，入口更多；
  · 它没有 GUI 弹窗混在算法里（tkinter 只在 App/SettingsDialog 两个类里用），
    所以 PATCHES 为空；
  · 素材目录 ORIG_DIR 是**可变模块级变量**，安卓端导入后直接给
    `iqgen_dsp.ORIG_DIR = <手机上的目录>` 再调 `refresh_library()` 即可，
    不需要文本替换；
  · 明确排除：App / SettingsDialog（tkinter）、draw_preview_axes /
    save_preview_png（matplotlib）、verify_with_iqplay（调外部程序）、
    cli_main（命令行）、_enable_windows_dpi_awareness、以及全部 settings/
    字体/外观函数 —— 它们都是宿主界面的事。

Rerun: python _extract_iqgen.py
"""
import ast
import builtins
import os
import re
import sys

SRC = r"E:\b210chegnxu\IQ生成器.py"
DST = r"E:\b210chegnxu\iqgen_dsp.py"

# 安卓版要用的入口（函数 + 界面需要的模块级常量）
ENTRIES = [
    # ---- 信号生成（主链路）----
    "build_iq", "build_preview", "_build_core",
    # ---- 信号库：解析 / 扫描 / 刷新 ----
    "resolve_signal", "catalog_names", "_special", "_init_special",
    "refresh_library", "lib_signature", "_scan_orig_files", "_scan_orig_sr",
    "_fill_mod_defaults", "_min_sample_rate", "_min_sample_rate_base",
    "_sr_eff_for_params", "_preview_sample_cap",
    # ---- 预览分析（Kivy 自绘要用）----
    "compute_spectrum", "occupied_bandwidth", "bw_window_for",
    "peak_abs", "power_and_peak",
    # ---- 跳频（预览要用跳频网格）----
    "hop_sequence", "hop_grid_freqs", "_hop_parse_seq",
    # ---- 落盘 ----
    "write_cs16", "make_xml_content", "write_xml",
    # ---- 杂项工具 ----
    "nice_sr", "_choose_dur", "_rng", "_pnum", "_mk",
    # ---- 界面要用的常量（分类 / 默认值 / 字段表 / 预置）----
    "ALL_MODS", "DIGITAL_FAMILIES", "ANALOG_MODS", "EXTENDED_MODS",
    "SIMPLE_MODS", "PSK_MODS",
    "GROUP_DEFS", "GROUP_COLOR", "GROUP_SHORT", "SIG_GROUP",
    "DEFAULTS", "MOD_DEFAULTS", "EXTRA_KEYS", "BASE_KEYS", "_FAMILY_FIELDS",
    "SLOT_PRESETS", "HOP_PRESETS", "HOP_PATTERNS", "HOP_PRESET_NONE",
    "PANEL_TITLES", "PANEL_LABELS",
    "SR_CAND", "MAX_SAMPLES", "PREVIEW_MAX_SAMPLES", "PREVIEW_HOP_MAX_SAMPLES",
    "PREVIEW_DEFAULT_DUR", "MIX_LIB_NONE", "_MIX_INHERIT",
]

# 抽取时做的文本替换（本项目没有必须替换的 GUI 调用，留空备用占位）
PATCHES = []

HEADER = '''\
# -*- coding: utf-8 -*-
"""iqgen_dsp.py —— IQ 信号生成算法层（纯 numpy，无任何 GUI 依赖）。

本文件由 `_extract_iqgen.py` 从 `IQ生成器.py` 自动抽取生成，供安卓版
`iqgen_android.py` 使用；桌面 tkinter 版 `IQ生成器.py` 不受影响。
算法只有这一份来源：**改算法请改 IQ生成器.py，然后重跑
`python _extract_iqgen.py`**，不要手改本文件。

未抽取（属于宿主界面 / 桌面专属）：
  · App / SettingsDialog、字体与外观（apply_appearance / ui_font / …）、
    iqgen_settings.json 相关
  · draw_preview_axes / save_preview_png（matplotlib 绘图，安卓端用 Kivy 自绘）
  · verify_with_iqplay（调外部程序）、cli_main（命令行入口）

安卓端用法：
    import iqgen_dsp as G
    G.ORIG_DIR = "/storage/emulated/0/信号素材"   # 素材目录可配置
    G.refresh_library()                          # 重建素材库索引
    p = {"mod": "QPSK", "sample_rate": 96000, ...}
    iq = G.build_iq(p)                           # 生成复数 IQ
    G.write_cs16(iq, "/sdcard/out.cs16")
    G.write_xml("/sdcard/out.cs16", sr, p)
"""
import datetime
import gc
import io
import json
import math
import os
import re
import struct
import sys
import wave

import numpy as np

# scipy 用于音频重采样/零相位滤波；缺失时算法层会自动回退（线性插值 / 频域掩模），
# 所以这里是可选依赖。必须放在 HEADER 里：源文件这一段写在 try 块内，
# 不在 AST 的 tree.body 里，抽取脚本扫不到 —— 漏了它就会报 `_HAVE_SCIPY` 未定义。
try:
    from scipy.signal import resample_poly
    _HAVE_SCIPY = True
except Exception:
    _HAVE_SCIPY = False

__all__ = []


def notify(msg, level="info"):
    """提示出口：算法层不弹窗，宿主程序（Kivy/控制台）可覆盖本函数。"""
    print("[" + level + "] " + str(msg))


'''


def main():
    src = open(SRC, encoding="utf-8").read()
    tree = ast.parse(src)

    defs = {}
    stmts = []
    for i, node in enumerate(tree.body):
        names = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                names += [n.id for n in ast.walk(t) if isinstance(n, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        for n in names:
            defs[n] = node
        stmts.append((i, names, node))

    import symtable
    st = symtable.symtable(src, SRC, "exec")
    child_tables = {t.get_name(): t for t in st.get_children()}

    def comp_locals(node):
        """节点内**由推导式/lambda 自己绑定**的名字。

        ⚠️ 不做这一步会误报：`GROUP_COLOR = {nm: col for nm, col, _s, _m in GROUP_DEFS}`
        里 `nm`/`col`/`sh` 在 key/value 位置是 Load 上下文，`ast.walk` 会把它们当成
        "引用了外部名字"，于是报未解析（实际是推导式自己的循环变量）。
        """
        out = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.comprehension):
                for t in ast.walk(sub.target):
                    if isinstance(t, ast.Name):
                        out.add(t.id)
            elif isinstance(sub, ast.Lambda):
                a = sub.args
                for x in a.posonlyargs + a.args + a.kwonlyargs:
                    out.add(x.arg)
                if a.vararg:
                    out.add(a.vararg.arg)
                if a.kwarg:
                    out.add(a.kwarg.arg)
        return out

    def free_names(node):
        out = set()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            tbl = child_tables.get(node.name)
            if tbl is None:
                return out
            for sym in tbl.get_symbols():
                if sym.is_global() or sym.is_free():
                    out.add(sym.get_name())
            for sub in ast.walk(node.args):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                    out.add(sub.id)
            for dec in node.decorator_list:
                for sub in ast.walk(dec):
                    if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                        out.add(sub.id)
            return out
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                out.add(sub.id)
        return out - comp_locals(node)

    queue = list(ENTRIES)
    seen = set()
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        node = defs.get(name)
        if node is None:
            continue
        for nm in free_names(node):
            if nm in defs and nm not in seen:
                queue.append(nm)

    keep_idx = set()
    for i, names, node in stmts:
        if any(n in seen for n in names):
            keep_idx.add(i)

    # 语料允许的模块名（与 HEADER 的 import 对齐）；GUI 相关一律不许出现
    ALLOWED_MODULES = {"os", "sys", "re", "json", "math", "wave", "struct",
                       "io", "gc", "datetime", "np", "notify",
                       # HEADER 里 try 导入的可选依赖（缺失时算法自动回退）
                       "_HAVE_SCIPY", "resample_poly"}
    FORBIDDEN = ("tkinter", "matplotlib", "PyQt5", "PySide2")

    defined = set(defs)
    unresolved = set()
    for i in sorted(keep_idx):
        _, names, node = stmts[i]
        for nm in free_names(node):
            if nm in defined or nm in dir(builtins) or nm.startswith("__"):
                continue
            if nm in ALLOWED_MODULES or nm in FORBIDDEN:
                continue
            unresolved.add(nm)

    lines = src.splitlines(keepends=True)
    out = [HEADER]
    for i, names, node in stmts:
        if i not in keep_idx:
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        seg = "".join(lines[node.lineno - 1:node.end_lineno])
        for old, new in PATCHES:
            if old in seg:
                seg = seg.replace(old, new)
        if not seg.endswith("\n\n"):
            seg = seg.rstrip("\n") + "\n"
        out.append("\n" + seg)

    out.append("\n\n__all__ = [%s]\n" % ", ".join(
        '"%s"' % n for n in sorted(
            n for n in seen
            if isinstance(defs.get(n), (ast.FunctionDef, ast.AsyncFunctionDef)))))

    body = "".join(out)
    with open(DST, "w", encoding="utf-8") as f:
        f.write(body)

    n_func = sum(1 for n in seen if isinstance(defs.get(n), ast.FunctionDef))
    n_const = sum(1 for n in seen
                  if n in defs and not isinstance(defs.get(n), ast.FunctionDef))
    print("抽取完成: %s" % DST)
    print("  函数 %d 个，常量 %d 个，输出 %d 行 / %.0f KB"
          % (n_func, n_const, len(body.splitlines()), len(body) / 1024.0))

    # 断言：不许把 GUI 库拉进来。
    # ⚠️ 只查**真正的导入语句** —— 直接全文搜关键字会被 HEADER 的说明文字误判
    # （本脚本第一版就报了 tkinter/matplotlib，其实是文档字符串里在解释"未抽取
    #  draw_preview_axes 等 matplotlib 函数"）。
    code_lines = [l for l in body.splitlines() if not l.lstrip().startswith("#")]
    code_txt = "\n".join(code_lines)
    bad = [k for k in FORBIDDEN
           if re.search(r"^\s*(?:import|from)\s+%s\b" % re.escape(k), code_txt, re.M)]
    print("  含 GUI 导入: %s" % (bad if bad else "无 ✓"))
    print("  未被解析的自由名字: %s" % (sorted(unresolved) if unresolved else "无"))
    missing = [e for e in ENTRIES if e not in defined]
    print("  入口缺失: %s" % (missing if missing else "无"))
    return 0 if not bad and not unresolved and not missing else 1


if __name__ == "__main__":
    sys.exit(main())
