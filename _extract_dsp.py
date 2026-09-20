# -*- coding: utf-8 -*-
"""从 iqplay0911.py 自动抽取「纯算法层」-> iqdsp.py（安卓版用）。

做法：AST 解析 -> 以一批入口函数为起点做可达性分析 -> 按原顺序输出被用到的
顶层语句 -> 替换掉 tkinter/matplotlib 相关调用（只有 1 处 messagebox）。

Rerun: python _extract_dsp.py
"""
import ast
import builtins
import os
import sys

SRC = r"E:\b210chegnxu\iqplay0911.py"
DST = r"E:\b210chegnxu\iqdsp.py"

# 安卓版要用到的入口
ENTRIES = [
    # 文件/采样率
    "parse_xml_sample_rate", "load_iq_file", "load_iq_any", "urh_detect_fmt",
    # 频谱
    "calc_fft_block", "calc_single_band",
    # 时域/调制域
    "bandpass_filter_complex", "am_demod", "fm_demod", "estimate_modulation",
    # 解调监听
    "demod_to_audio", "float32_to_wav_bytes",
    # 落盘 / 生成
    "save_iq_cs16", "save_iq_wav", "make_sine_wave", "parse_hex_str",
    # 解调监听的音频采样率
    "AUDIO_OUT_RATE",
    # 注：信号源设备（UDP 打包，siggen_* / SIGGEN_* / DT_* / MODE_* / SYS_* 等）
    #     已在安卓版中整体移除——手机侧没有该信号源硬件，且依赖 UDP 单向下发。
    #     桌面版 iqplay0911.py 仍保留完整实现，只是不再抽进 iqdsp.py。
]

# 抽取时做的替换：把 GUI 弹窗换成算法层自己的 notify()
PATCHES = [
    ('        messagebox.showwarning("IQ文件截断警告", warn_msg)',
     '        notify(warn_msg, "warn")'),
]

HEADER = '''\
# -*- coding: utf-8 -*-
"""iqdsp.py —— IQ 信号处理算法层（纯 numpy，无任何 GUI 依赖）。

本文件由 `_extract_dsp.py` 从 `iqplay0911.py` 自动抽取生成，供安卓版
`iqplay_android.py` 使用；桌面 tkinter 版 `iqplay0911.py` 不受影响。
抽取时把唯一的 GUI 依赖（IQ 截断时的 messagebox）换成了本文件的 notify()。
按安卓版需求，B210 发射页 / URH 协议分析页 / 信号源设备（UDP 打包）相关的
函数与常量均未抽取——桌面版 iqplay0911.py 里仍然完整保留。

抽取来源: iqplay0911.py
不要手改本文件——改算法请改 iqplay0911.py 后重新跑 `python _extract_dsp.py`。
"""
import gc
import io
import math
import os
import re
import wave

import numpy as np

__all__ = []


def notify(msg, level="info"):
    """提示出口：算法层不弹窗，宿主程序（Kivy/控制台）可覆盖本函数。"""
    print("[" + level + "] " + str(msg))


'''


def main():
    src = open(SRC, encoding="utf-8").read()
    tree = ast.parse(src)

    # 顶层定义表：名字 -> 语句节点
    defs = {}
    stmts = []          # 保留顺序的 (index, 名字列表, 节点)
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

    # 每个顶层作用域引用到「外部名字」——用 symtable 精确取自由名字（不含局部变量）
    import symtable
    st = symtable.symtable(src, SRC, "exec")
    child_tables = {t.get_name(): t for t in st.get_children()}
    global_names = set()
    for sym in st.get_symbols():
        if sym.is_global() or (sym.is_referenced() and not sym.is_assigned()):
            global_names.add(sym.get_name())

    def free_names(node):
        """顶层语句里对「模块级名字」的引用（函数体内部用 symtable 精确取）。"""
        out = set()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            tbl = child_tables.get(node.name)
            if tbl is None:
                return out
            for sym in tbl.get_symbols():
                if sym.is_global() or sym.is_free():
                    out.add(sym.get_name())
            # 默认参数值 / 装饰器是在外层作用域求值的，symtable 不计入函数表
            for sub in ast.walk(node.args):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                    out.add(sub.id)
            for dec in node.decorator_list:
                for sub in ast.walk(dec):
                    if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                        out.add(sub.id)
            return out
        # 常量赋值语句：直接收集右边引用
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                out.add(sub.id)
        return out

    # 可达性 BFS
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
    global_names |= {n for n in seen if n in defs}

    # 收集要输出的语句
    keep_idx = set()
    for i, names, node in stmts:
        if any(n in seen for n in names):
            keep_idx.add(i)

    # 未解析的自由名字（应为 0，否则说明漏抽了东西）
    defined = set(defs)
    unresolved = set()
    for i in sorted(keep_idx):
        _, names, node = stmts[i]
        for nm in free_names(node):
            if nm in defined or nm in dir(builtins) or nm.startswith("__"):
                continue
            if nm in ("os", "re", "io", "gc", "math", "struct", "wave", "np"):
                continue
            if nm in ("messagebox",):    # 已在 PATCHES 里换成 notify()，源文件里还有引用
                continue
            unresolved.add(nm)

    lines = src.splitlines(keepends=True)
    out = [HEADER]
    for i, names, node in stmts:
        if i not in keep_idx:
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue                      # 统一用文件头的 import
        seg = "".join(lines[node.lineno - 1:node.end_lineno])
        for old, new in PATCHES:
            if old in seg:
                seg = seg.replace(old, new)
        # 顶层语句之间空一行
        if not seg.endswith("\n\n"):
            seg = seg.rstrip("\n") + "\n"
        out.append("\n" + seg)

    out.append("\n\n__all__ = [%s]\n" % ", ".join('"%s"' % n for n in sorted(
        n for n in seen if isinstance(defs.get(n), (ast.FunctionDef, ast.AsyncFunctionDef)))))

    with open(DST, "w", encoding="utf-8") as f:
        f.write("".join(out))

    n_func = sum(1 for n in seen if isinstance(defs.get(n), (ast.FunctionDef,)))
    n_const = sum(1 for n in seen if not isinstance(defs.get(n), (ast.FunctionDef,))
                  and n in defs)
    print("抽取完成: %s" % DST)
    print("  函数 %d 个，常量 %d 个，输出 %d 行"
          % (n_func, n_const, len("".join(out).splitlines())))
    print("  未被解析的自由名字: %s" % (sorted(unresolved) if unresolved else "无"))
    missing = [e for e in ENTRIES if e not in defined]
    print("  入口缺失: %s" % (missing if missing else "无"))
    return 0 if not unresolved and not missing else 1


if __name__ == "__main__":
    sys.exit(main())
