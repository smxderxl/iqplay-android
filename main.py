# -*- coding: utf-8 -*-
"""安卓打包入口（buildozer / p4a 固定以 main.py 作为启动文件）。

这里只是一层转发壳：界面与逻辑全在 iqplay_android.py（界面）+ iqdsp.py（算法）
里，桌面调试时直接 `python iqplay_android.py` 效果完全一样。

注意：不要在本文件里写业务代码——p4a 会把整个 source.dir 打进 APK 的私有目录，
main.py 只负责把工作目录加进 sys.path 再调 run_app()。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from iqplay_android import main as run_app                     # noqa: E402

if __name__ == "__main__":
    run_app()
