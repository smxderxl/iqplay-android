# -*- coding: utf-8 -*-
"""iqplay_android.py —— IQ 信号综合分析仪【安卓版】

与桌面版 iqplay0911.py 的关系：
  · 算法层完全相同（`iqdsp.py` 由 `_extract_dsp.py` 从 iqplay0911.py 自动抽取，
    `_smoke_dsp.py` 保证两边结果逐位一致）；
  · 界面层用 Kivy 重写（tkinter 在安卓上没有可用实现），针对触摸操作与手机
    屏幕重排：顶部工具条 + 可缩放绘图区 + 底部标签栏。

页面：频谱｜瀑布｜余晖｜时域｜调制
（按需求去掉了 B210 发射页、URH 协议分析页与信号源页——UHD 在安卓上没有
驱动，信号源设备依赖 UDP 单向下发，手机侧无此硬件。）

运行环境（三选一）：
  1) Pydroid 3：装 numpy + kivy 两个包，直接打开本文件运行；
  2) Termux：pkg install python numpy，pip install kivy，再用 x11/wayland 或
     `KIVY_WINDOW=sdl2` 跑；
  3) buildozer 打包 APK（见同目录 build_android.sh / buildozer.spec）。

依赖：numpy、kivy（>=2.0）。界面全部自绘，不依赖 matplotlib。
"""
import math
import os
import sys
import threading
import time

# ---------------- Kivy 引导（必须在其它 kivy 模块之前） ----------------
os.environ.setdefault("KIVY_NO_ARGS", "1")
from kivy.config import Config                                   # noqa: E402
Config.set("input", "mouse", "mouse,multitouch_on_demand")        # 桌面调试可模拟多点
Config.set("kivy", "exit_on_escape", "0")
Config.set("graphics", "resizable", "1")

from kivy.app import App                                          # noqa: E402
from kivy.clock import Clock                                      # noqa: E402
from kivy.core.text import Label as CoreLabel                     # noqa: E402
from kivy.core.text import LabelBase                              # noqa: E402
from kivy.core.window import Window                               # noqa: E402
from kivy.graphics import Color, Line, Point, Rectangle           # noqa: E402
from kivy.graphics.texture import Texture                         # noqa: E402
from kivy.metrics import dp, sp                                   # noqa: E402
from kivy.properties import (BooleanProperty, ListProperty, NumericProperty,  # noqa: E402
                             ObjectProperty, StringProperty)
from kivy.uix.boxlayout import BoxLayout                          # noqa: E402
from kivy.uix.button import Button                                # noqa: E402
from kivy.uix.checkbox import CheckBox                            # noqa: E402
from kivy.uix.filechooser import FileChooserListView              # noqa: E402
from kivy.uix.gridlayout import GridLayout                        # noqa: E402
from kivy.uix.label import Label                                  # noqa: E402
from kivy.uix.popup import Popup                                  # noqa: E402
from kivy.uix.screenmanager import NoTransition, Screen, ScreenManager  # noqa: E402
from kivy.uix.scrollview import ScrollView                        # noqa: E402
from kivy.uix.spinner import Spinner                              # noqa: E402
from kivy.uix.textinput import TextInput                          # noqa: E402
from kivy.uix.togglebutton import ToggleButton                    # noqa: E402
from kivy.uix.widget import Widget                                # noqa: E402
from kivy.utils import platform                                   # noqa: E402

import numpy as np                                                # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import iqdsp as D                                                 # noqa: E402

IS_ANDROID = platform == "android"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
IS_PHONE_UI = IS_ANDROID or os.environ.get("IQPLAY_PHONE", "") not in ("", "0")

# ---------------- 中文字体（Kivy 默认字体没有汉字） ----------------
FONT_CANDIDATES = [
    # Android
    "/system/fonts/NotoSansCJK-Regular.ttc",
    "/system/fonts/NotoSansCJKsc-Regular.otf",
    "/system/fonts/NotoSansSC-Regular.otf",
    "/system/fonts/DroidSansFallbackFull.ttf",
    "/system/fonts/DroidSansFallback.ttf",
    "/system/fonts/DroidSansChinese.ttf",
    # Windows / macOS / Linux（桌面调试）
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyh.ttf",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    os.path.join(APP_DIR, "font.ttf"),      # 兜底：把任意中文字体改名放这里
    os.path.join(APP_DIR, "字体.ttf"),
]
FONT_USED = None
for _p in FONT_CANDIDATES:
    if os.path.exists(_p):
        try:
            # 覆盖默认字体名 Roboto —— 所有控件就都能显示中文了
            LabelBase.register(name="Roboto", fn_regular=_p)
            FONT_USED = _p
            break
        except Exception:
            continue

# ---------------- 主题 ----------------
C_BG = (0.055, 0.063, 0.086, 1)          # 绘图区背景
C_PANEL = (0.10, 0.11, 0.14, 1)          # 面板背景
C_BAR = (0.14, 0.15, 0.19, 1)            # 工具条背景
C_GRID = (0.22, 0.25, 0.30, 1)
C_FG = (0.88, 0.90, 0.94, 1)
C_DIM = (0.55, 0.60, 0.68, 1)
C_RT = (0.16, 0.86, 1.00, 1)             # 实时
C_AVG = (0.30, 1.00, 0.35, 1)            # 平均
C_MAX = (1.00, 0.32, 0.30, 1)            # 最大保持
C_ACCENT = (0.30, 0.85, 0.45, 1)
C_WARN = (1.00, 0.72, 0.25, 1)


# ======================================================================
# 色图（纯 numpy 查表，给定 0~1 返回 256x3 uint8）
# ======================================================================
def _jet_lut():
    x = np.linspace(0.0, 1.0, 256, dtype=np.float64)
    r = np.clip(1.5 - np.abs(4.0 * x - 3.0), 0, 1)
    g = np.clip(1.5 - np.abs(4.0 * x - 2.0), 0, 1)
    b = np.clip(1.5 - np.abs(4.0 * x - 1.0), 0, 1)
    return np.stack([r, g, b], axis=1)


def _hot_lut():
    x = np.linspace(0.0, 1.0, 256, dtype=np.float64)
    r = np.clip(3.0 * x, 0, 1)
    g = np.clip(3.0 * x - 1.0, 0, 1)
    b = np.clip(3.0 * x - 2.0, 0, 1)
    return np.stack([r, g, b], axis=1)


def _gray_lut():
    x = np.linspace(0.0, 1.0, 256, dtype=np.float64)
    return np.stack([x, x, x], axis=1)


def _viridis_lut():
    """8 段线性插值的 viridis 近似（够用且不需要 matplotlib）。"""
    stops = np.array([
        [0.267, 0.005, 0.329], [0.283, 0.141, 0.458], [0.254, 0.265, 0.530],
        [0.207, 0.372, 0.553], [0.164, 0.471, 0.558], [0.128, 0.567, 0.551],
        [0.135, 0.659, 0.518], [0.267, 0.749, 0.441], [0.478, 0.821, 0.318],
        [0.741, 0.873, 0.150], [0.993, 0.906, 0.144]], dtype=np.float64)
    xi = np.linspace(0.0, 1.0, len(stops))
    xo = np.linspace(0.0, 1.0, 256)
    return np.stack([np.interp(xo, xi, stops[:, k]) for k in range(3)], axis=1)


CMAPS = {
    "jet": _jet_lut(), "hot": _hot_lut(), "gray": _gray_lut(),
    "viridis": _viridis_lut(),
}
CMAP_U8 = {k: np.clip(v * 255.0, 0, 255).astype(np.uint8) for k, v in CMAPS.items()}


def colormap_rgb(name, norm01):
    """norm01: 0~1 数组 -> (..., 3) uint8。"""
    lut = CMAP_U8.get(name, CMAP_U8["jet"])
    idx = np.clip(np.nan_to_num(norm01, nan=0.0) * 255.0, 0, 255).astype(np.uint8)
    return lut[idx]


# ======================================================================
# 小工具
# ======================================================================
def fmt_freq(hz, unit="MHz"):
    """频率按单位格式化。"""
    if hz is None:
        return "---"
    u = {"Hz": 1.0, "KHz": 1e3, "MHz": 1e6}[unit]
    v = hz / u
    if abs(v) >= 1000:
        return "%.0f" % v
    if abs(v) >= 10:
        return "%.1f" % v
    if abs(v) >= 1:
        return "%.3f" % v
    return "%.6g" % v


def fmt_bw(hz):
    if hz is None:
        return "---"
    a = abs(hz)
    if a >= 1e6:
        return "%.3f MHz" % (a / 1e6)
    if a >= 1e3:
        return "%.2f kHz" % (a / 1e3)
    return "%.0f Hz" % a


def fmt_size(n):
    for u, s in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= s:
            return "%.1f %s" % (n / s, u)
    return "%d B" % n


def run_bg(work, on_done=None, on_error=None):
    """后台线程执行 work()，结果回主线程。手机上重活绝不能卡 UI。"""
    def _worker():
        try:
            res = work()
        except Exception as exc:                       # noqa: BLE001
            if on_error:
                Clock.schedule_once(lambda dt: on_error(exc))
            else:
                print("后台任务异常:", exc)
            return
        if on_done:
            Clock.schedule_once(lambda dt: on_done(res))
    threading.Thread(target=_worker, daemon=True).start()


def storage_dirs():
    """手机上可用的存储目录候选（按可读性过滤）。"""
    cands = [
        "/storage/emulated/0/Download", "/storage/emulated/0/下载",
        "/sdcard/Download", "/sdcard",
        "/storage/emulated/0", "/storage/emulated/0/Documents",
        os.path.expanduser("~"), APP_DIR,
    ]
    out = []
    for d in cands:
        try:
            if os.path.isdir(d) and os.access(d, os.R_OK):
                out.append(d)
        except Exception:
            continue
    return out


def default_work_dir():
    for d in ("/storage/emulated/0/Download", "/sdcard/Download"):
        if os.path.isdir(d):
            return d
    ds = storage_dirs()
    return ds[0] if ds else APP_DIR


def ask_android_permissions():
    """APK 环境请求存储权限；Pydroid 3 / Termux 下由宿主自己管权限。"""
    if not IS_ANDROID:
        return
    try:
        from android.permissions import Permission, request_permissions
        request_permissions([Permission.READ_EXTERNAL_STORAGE,
                             Permission.WRITE_EXTERNAL_STORAGE])
    except Exception:
        pass


def toast(msg):
    """轻提示：安卓上用 Toast（有 android 模块时），否则打印。"""
    print("[提示] %s" % msg)
    if IS_ANDROID:
        try:
            from android.runnable import run_on_ui_thread
            from jnius import autoclass

            @run_on_ui_thread
            def _t():
                autoclass("android.widget.Toast").makeText(
                    autoclass("org.kivy.android.PythonActivity").mActivity,
                    str(msg), 0).show()
            _t()
            return
        except Exception:
            pass
    print(msg)


# ======================================================================
# 音频播放（安卓/SDL/Windows 三路兜底）
# ======================================================================
class AudioPlayer(object):
    def __init__(self):
        self._snd = None
        self.tmp_wav = os.path.join(App.get_running_app().user_data_dir
                                    if App.get_running_app() else APP_DIR, "demod.wav")
        try:
            os.makedirs(os.path.dirname(self.tmp_wav), exist_ok=True)
        except Exception:
            self.tmp_wav = os.path.join(APP_DIR, "demod.wav")

    def play(self, audio_f32, rate=D.AUDIO_OUT_RATE):
        """audio_f32：float32 单声道。返回 (是否成功, 说明)。"""
        try:
            D.save_iq_wav  # noqa: B018  只是确保算法层就绪
            data = D.float32_to_wav_bytes(audio_f32, rate)
            with open(self.tmp_wav, "wb") as f:
                f.write(data)
        except Exception as e:
            return False, "写临时音频失败: %s" % e
        # 1) Kivy 音频后端（桌面走 SDL2，安卓走 android provider）
        try:
            from kivy.core.audio import SoundLoader
            if self._snd is not None:
                try:
                    self._snd.stop()
                    self._snd.unload()
                except Exception:
                    pass
            self._snd = SoundLoader.load(self.tmp_wav)
            if self._snd:
                self._snd.play()
                return True, "正在播放（%.1fs）" % (len(audio_f32) / float(rate))
        except Exception as e:
            print("Kivy 音频播放失败:", e)
        # 2) Windows 兜底
        try:
            import winsound
            winsound.PlaySound(self.tmp_wav, winsound.SND_FILENAME | winsound.SND_ASYNC)
            return True, "正在播放（winsound）"
        except Exception as e:
            print("winsound 播放失败:", e)
        return False, "本机没有可用的音频后端，已存到 %s" % self.tmp_wav

    def stop(self):
        try:
            if self._snd is not None:
                self._snd.stop()
        except Exception:
            pass
        try:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass


# ======================================================================
# 文字精灵（坐标轴刻度，用 CoreLabel 纹理画在 canvas.after 上）
# ======================================================================
class LabelSprite(object):
    _tex_cache = {}

    def __init__(self, canvas, font_size=10, color=C_FG):
        self._cs = canvas
        with canvas:
            self._c = Color(*color)
            self._r = Rectangle(texture=None, size=(0, 0))

    @classmethod
    def _tex(cls, text, color):
        key = (text, tuple(color))
        hit = cls._tex_cache.get(key)
        if hit is not None:
            return hit
        cl = CoreLabel(text=text, font_size=10, color=color)
        cl.refresh()
        tex = cl.texture
        if len(cls._tex_cache) > 400:
            cls._tex_cache.clear()
        cls._tex_cache[key] = tex
        return tex

    def draw(self, text, x, y, color=C_FG, align="left", valign="bottom"):
        tex = self._tex(str(text), color)
        if tex is None:
            return
        self._r.texture = tex
        w, h = tex.size
        self._r.size = (w, h)
        px = x - w if align == "right" else (x - w / 2.0 if align == "center" else x)
        py = y if valign == "bottom" else (y - h if valign == "top" else y - h / 2.0)
        self._r.pos = (px, py)

    def hide(self):
        self._r.texture = None
        self._r.size = (0, 0)


# ======================================================================
# 绘图基类：背景/网格/刻度 + 触摸平移捏合缩放
# ======================================================================
class PlotBase(Widget):
    xlim = ListProperty([0.0, 1.0])
    ylim = ListProperty([0.0, 1.0])
    xlabel = StringProperty("")
    ylabel = StringProperty("")
    title = StringProperty("")
    _home = None

    def __init__(self, **kw):
        super().__init__(**kw)
        self._xfmt = lambda v: "%.4g" % v
        self._yfmt = lambda v: "%.4g" % v
        self._touches = {}
        self._pinch = None
        self._ticks_x = [LabelSprite(self.canvas.after) for _ in range(7)]
        self._ticks_y = [LabelSprite(self.canvas.after) for _ in range(7)]
        self._lab_x = LabelSprite(self.canvas.after)
        self._lab_y = LabelSprite(self.canvas.after)
        self._lab_t = LabelSprite(self.canvas.after)
        with self.canvas.before:
            self._bg_c = Color(*C_BG)
            self._bg_r = Rectangle(pos=self.pos, size=self.size)
        with self.canvas.after:
            self._grid_c = Color(*C_GRID)
            self._grid_lines = [Line(width=0.6) for _ in range(14)]
            self._frame_c = Color(*C_DIM)
            # 注意：不要用 Line(rectangle=...) —— Kivy 里 rectangle 只在创建时有效，
            # 之后每赋一次值都会往 points 里「追加」4 个点，画出来是一堆斜线。
            self._frame = Line(width=1.0)
        self.bind(pos=self._on_layout, size=self._on_layout,
                  xlim=self._on_view, ylim=self._on_view)

    # ---- 视图 ----
    def set_view(self, xlim, ylim, remember=False):
        self.xlim = [float(xlim[0]), float(xlim[1])]
        self.ylim = [float(ylim[0]), float(ylim[1])]
        if remember or self._home is None:
            self._home = (list(self.xlim), list(self.ylim))

    def reset_view(self):
        if self._home:
            self.xlim = list(self._home[0])
            self.ylim = list(self._home[1])
        else:
            self.refresh_view(force=True)

    def refresh_view(self, force=False):
        """子类按数据自适应视图（force=False 时用户缩放过就不动）。"""
        raise NotImplementedError

    # ---- 坐标映射 ----
    def px(self, x):
        x0, x1 = self.xlim
        w = max(1.0, self.width)
        if x1 == x0:
            return self.x
        return self.x + (x - x0) / (x1 - x0) * w

    def py(self, y):
        y0, y1 = self.ylim
        h = max(1.0, self.height)
        if y1 == y0:
            return self.y
        return self.y + (y - y0) / (y1 - y0) * h

    def inv_x(self, px_):
        x0, x1 = self.xlim
        return x0 + (px_ - self.x) / max(1.0, self.width) * (x1 - x0)

    def inv_y(self, py_):
        y0, y1 = self.ylim
        return y0 + (py_ - self.y) / max(1.0, self.height) * (y1 - y0)

    # ---- 触摸 ----
    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return False
        touch.grab(self)
        if touch.is_double_tap:
            self.reset_view()
            return True
        self._touches[touch.uid] = (touch.x, touch.y)
        self._pinch = None
        return True

    def on_touch_move(self, touch):
        if touch.grab_current is not self:
            return False
        if touch.uid not in self._touches:
            self._touches[touch.uid] = (touch.x, touch.y)
            return True
        prev = self._touches[touch.uid]
        self._touches[touch.uid] = (touch.x, touch.y)
        if len(self._touches) >= 2:
            pts = list(self._touches.values())[:2]
            dist = math.hypot(pts[0][0] - pts[1][0], pts[0][1] - pts[1][1])
            if self._pinch and dist > 1:
                r = dist / self._pinch
                if 0.3 < r < 3.5:
                    cx = (pts[0][0] + pts[1][0]) / 2.0
                    cy = (pts[0][1] + pts[1][1]) / 2.0
                    self._zoom(r, r, cx, cy)     # 横纵同倍缩放，手指撑开就是"放大"
            self._pinch = dist
            return True
        dx, dy = touch.x - prev[0], touch.y - prev[1]
        self._pan(dx, dy)
        return True

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
        self._touches.pop(touch.uid, None)
        if len(self._touches) < 2:
            self._pinch = None
        return True

    def _zoom(self, rx, ry, cx=None, cy=None):
        cx = self.center_x if cx is None else cx
        cy = self.center_y if cy is None else cy
        x0, x1 = self.xlim
        y0, y1 = self.ylim
        ax = self.inv_x(cx)
        ay = self.inv_y(cy)
        nx0 = ax + (x0 - ax) / rx
        nx1 = ax + (x1 - ax) / rx
        ny0 = ay + (y0 - ay) / ry
        ny1 = ay + (y1 - ay) / ry
        if abs(nx1 - nx0) < 1e-12 or abs(ny1 - ny0) < 1e-12:
            return
        self.xlim = [nx0, nx1]
        self.ylim = [ny0, ny1]

    def _pan(self, dx, dy):
        x0, x1 = self.xlim
        y0, y1 = self.ylim
        sx = (x1 - x0) / max(1.0, self.width)
        sy = (y1 - y0) / max(1.0, self.height)
        self.xlim = [x0 - dx * sx, x1 - dx * sx]
        self.ylim = [y0 - dy * sy, y1 - dy * sy]

    # ---- 布局与重绘 ----
    def _on_layout(self, *_):
        self._bg_r.pos = self.pos
        self._bg_r.size = self.size
        self._redraw()

    def _on_view(self, *_):
        self._redraw()

    def on_pos(self, *_):
        self._on_layout()

    def on_size(self, *_):
        self._on_layout()

    def _draw_axes(self):
        pad_l, pad_b = dp(46), dp(20)
        x, y, w, h = self.x, self.y, self.width, self.height
        x0p, x1p = x + pad_l, x + w - dp(6)
        y0p, y1p = y + pad_b, y + h - dp(6)
        nx, ny = 6, 5
        self._lab_x.draw(self.xlabel, (x0p + x1p) / 2.0, y + dp(2),
                         color=C_DIM, align="center")
        self._lab_y.draw(self.ylabel, x + dp(2), (y0p + y1p) / 2.0, color=C_DIM)
        self._lab_t.draw(self.title, x0p, y + h - dp(16), color=C_FG)
        gi = 0
        for i in range(nx + 1):
            fx = x0p + (x1p - x0p) * i / nx
            self._grid_lines[gi].points = [fx, y0p, fx, y1p]
            gi += 1
            sp = self._ticks_x[i]
            if i == 0:
                sp.draw(self._xfmt(self.xlim[0]), fx, y0p - dp(15), color=C_DIM)
            elif i == nx:
                sp.draw(self._xfmt(self.xlim[1]), fx, y0p - dp(15),
                        color=C_DIM, align="right")
            else:
                sp.draw(self._xfmt(self.inv_x(fx)), fx, y0p - dp(15),
                        color=C_DIM, align="center")
        for i in range(ny + 1):
            fy = y0p + (y1p - y0p) * i / ny
            self._grid_lines[gi].points = [x0p, fy, x1p, fy]
            gi += 1
            if i < len(self._ticks_y):
                self._ticks_y[i].draw(self._yfmt(self.inv_y(fy)), x0p - dp(4), fy,
                                      color=C_DIM, align="right")
        self._frame.points = [x0p, y0p, x1p, y0p, x1p, y1p, x0p, y1p, x0p, y0p]

    def draw_content(self, x0p, y0p, x1p, y1p):
        """子类重写：在像素矩形内画数据。"""

    def _redraw(self):
        if not self.get_root_window():
            return
        pad_l, pad_b = dp(46), dp(20)
        x0p, x1p = self.x + pad_l, self.right - dp(6)
        y0p, y1p = self.y + pad_b, self.top - dp(6)
        self._draw_axes()
        try:
            self.draw_content(x0p, y0p, x1p, y1p)
        except Exception as e:
            print("%s.draw_content 异常: %s" % (type(self).__name__, e))

    def redraw(self):
        Clock.unschedule(self._redraw)
        self._redraw()


# ======================================================================
# 频谱图
# ======================================================================
class SpectrumPlot(PlotBase):
    def __init__(self, **kw):
        self.freq_hz = None
        self.rt = None
        self.avg = None
        self.maxh = None
        self.show_rt = True
        self.show_avg = True
        self.show_max = True
        self.marks = []                      # [(freq_hz, 标签)]
        self.app_xunit = "MHz"
        super().__init__(**kw)
        self._xfmt = lambda v: fmt_freq(v, self.app_xunit)
        self._yfmt = lambda v: "%.0f" % v
        with self.canvas:
            self._c_rt = Color(*C_RT)
            self._ln_rt = Line(width=1.1)
            self._c_av = Color(*C_AVG)
            self._ln_av = Line(width=1.0)
            self._c_mx = Color(*C_MAX)
            self._ln_mx = Line(width=1.0)
            self._c_mk = Color(1, 1, 0, 1)
            self._ln_mk = Line(width=1.0, dash_offset=2)
        self._mark_sprites = [LabelSprite(self.canvas.after) for _ in range(4)]

    def set_xunit(self, unit):
        self.app_xunit = unit
        self._xfmt = lambda v: fmt_freq(v, unit)
        self._redraw()

    def set_data(self, freq_hz, rt=None, avg=None, maxh=None):
        self.freq_hz = freq_hz
        if rt is not None:
            self.rt = rt
        if avg is not None:
            self.avg = avg
        if maxh is not None:
            self.maxh = maxh

    def auto_ylim(self):
        """按当前三条曲线定 dB 量程。

        基类 ylim 默认是 [0, 1]，而频谱幅度是 dB（-110~+30）——不主动适配的话
        所有点都被 clip 到上下边界，画出来只剩两条竖线加一条顶边，完全看不出
        频谱形状（桌面版一直是自动 dB 量程，安卓版漏了这一步）。
        """
        arrs = [np.asarray(a, dtype=np.float64)
                for a in (self.rt, self.avg, self.maxh) if a is not None]
        if not arrs:
            return [self.ylim[0], self.ylim[1]]
        a = np.concatenate(arrs)
        a = a[np.isfinite(a)]
        if a.size == 0:
            return [self.ylim[0], self.ylim[1]]
        lo = float(np.percentile(a, 1.0)) - 5.0
        hi = float(np.max(a)) + 5.0
        if hi - lo < 10.0:                 # 平噪声时别把量程压成一条线
            lo = hi - 10.0
        return [lo, hi]

    def refresh_view(self, force=False):
        if self.freq_hz is None or not len(self.freq_hz):
            return
        if force or self._home is None:
            self.xlim = [float(self.freq_hz[0]), float(self.freq_hz[-1])]
            self.ylim = self.auto_ylim()
            self._home = (list(self.xlim), list(self.ylim))

    def _clip(self, arr, x0p, y0p, x1p, y1p):
        """把 (freq, amp) 裁剪到当前视图，并映射成像素点串。"""
        if arr is None or self.freq_hz is None:
            return []
        lo, hi = min(self.xlim), max(self.xlim)
        idx = np.searchsorted(self.freq_hz, [lo, hi])
        i0 = max(0, int(idx[0]) - 1)
        i1 = min(len(self.freq_hz), int(idx[1]) + 2)
        if i1 - i0 < 2:
            return []
        f = self.freq_hz[i0:i1]
        a = np.asarray(arr[i0:i1], dtype=np.float64)
        # 每像素列取最大值，保证窄峰不被抽掉
        npx = max(2, int(x1p - x0p))
        pts = []
        if (i1 - i0) > npx * 2:
            k = (i1 - i0) // npx
            m = k * npx
            a2 = a[:m].reshape(npx, k).max(axis=1)
            f2 = f[:m].reshape(npx, k).mean(axis=1)
        else:
            a2, f2 = a, f
        xs = self.x + (f2 - self.xlim[0]) / (self.xlim[1] - self.xlim[0] + 1e-30) * self.width
        ys = self.y + (a2 - self.ylim[0]) / (self.ylim[1] - self.ylim[0] + 1e-30) * self.height
        np.clip(xs, x0p - 2, x1p + 2, out=xs)
        np.clip(ys, y0p - 2, y1p + 2, out=ys)
        pts = np.empty(xs.size * 2, dtype=np.float32)
        pts[0::2] = xs
        pts[1::2] = ys
        return pts.tolist()

    def draw_content(self, x0p, y0p, x1p, y1p):
        self._ln_rt.points = (self._clip(self.rt, x0p, y0p, x1p, y1p)
                              if self.show_rt else [])
        self._ln_av.points = (self._clip(self.avg, x0p, y0p, x1p, y1p)
                              if self.show_avg else [])
        self._ln_mx.points = (self._clip(self.maxh, x0p, y0p, x1p, y1p)
                              if self.show_max else [])
        mk, i = [], 0
        for fhz, tag in self.marks[:4]:
            if self.xlim[0] <= fhz <= self.xlim[1]:
                x = self.px(fhz)
                mk += [x, y0p, x, y1p]
                self._mark_sprites[i].draw(tag, x, y1p - dp(14), color=(1, 1, 0, 1), align="center", valign="top")
            else:
                self._mark_sprites[i].hide()
            i += 1
        for j in range(len(self.marks), len(self._mark_sprites)):
            self._mark_sprites[j].hide()
        self._ln_mk.points = mk


# ======================================================================
# 瀑布图 / 余晖图（用 Texture 直接上传 numpy 数组）
# ======================================================================
class ImagePlot(PlotBase):
    """基类：把 (rows, cols) 的浮点矩阵按色标画成纹理。"""

    def __init__(self, **kw):
        self.buf = None
        self.cmap = "jet"
        self.vmin = 0.0
        self.vmax = 1.0
        self.freq_hz = None
        self._tex = None
        self.app_xunit = "MHz"
        super().__init__(**kw)
        self._xfmt = lambda v: fmt_freq(v, self.app_xunit)
        self._lab_x2 = LabelSprite(self.canvas.after)
        with self.canvas:
            self._img_c = Color(1, 1, 1, 1)
            self._img_r = Rectangle(texture=None, pos=self.pos, size=self.size)
        self.bind(pos=self._sync_img, size=self._sync_img)

    def set_xunit(self, unit):
        self.app_xunit = unit
        self._redraw()

    def _sync_img(self, *_):
        self._img_r.pos = (self.x, self.y)
        self._img_r.size = (self.width, self.height)

    def _upload(self):
        if self.buf is None or self.buf.size == 0:
            return
        rows, cols = self.buf.shape
        span = self.vmax - self.vmin
        norm = (self.buf - self.vmin) / (span if abs(span) > 1e-12 else 1.0)
        rgb = colormap_rgb(self.cmap, norm)
        if self._tex is None or self._tex.size[0] != cols or self._tex.size[1] != rows:
            self._tex = Texture.create(size=(cols, rows), colorfmt="rgb")
            self._tex.mag_filter = "nearest"
        # 每帧都重新绑定：draw_content 在无数据时会把 texture 摘掉，
        # 若只在新建纹理时绑定，清空后再来数据就永远看不见了。
        if self._img_r.texture is not self._tex:
            self._img_r.texture = self._tex
        # blit 的原点在左下角，行序要翻过来
        self._tex.blit_buffer(np.ascontiguousarray(rgb[::-1]).tobytes(),
                              colorfmt="rgb", bufferfmt="ubyte")

    def refresh_view(self, force=False):
        if self.freq_hz is None or not len(self.freq_hz):
            return
        if force or self._home is None:
            self._home = ([float(self.freq_hz[0]), float(self.freq_hz[-1])],
                          [self.ylim[0], self.ylim[1]])
            self.xlim = list(self._home[0])

    def draw_content(self, x0p, y0p, x1p, y1p):
        # 还没载入 / 刚被清空时 freq_hz 与 buf 都可能为空。以前这里直接下标，
        # 空数据切到瀑布页（或按了停止再切页）会抛
        # "'NoneType' object is not subscriptable" —— 界面照常显示，但日志被刷。
        if self.freq_hz is None or not len(self.freq_hz) or self.buf is None:
            self._img_r.size = (0, 0)      # 连矩形一起收掉
            return
        self._upload()
        # 频率范围外的东西用背景遮掉，保证横轴与频谱页一致
        f0, f1 = self.freq_hz[0], self.freq_hz[-1]
        total = f1 - f0 + 1e-30
        xa = x0p + (self.xlim[0] - f0) / total * (x1p - x0p)
        xb = x0p + (self.xlim[1] - f0) / total * (x1p - x0p)
        self._img_r.pos = (xa, y0p)
        self._img_r.size = (max(1.0, xb - xa), max(1.0, y1p - y0p))


class WaterfallPlot(ImagePlot):
    def __init__(self, rows=200, **kw):
        self.rows = rows
        self.filled = 0
        super().__init__(**kw)
        self._yfmt = lambda v: "%.0f" % v
        self._lab_y.draw("← 频率  时间 ↓", self.x + dp(4), self.y + dp(4), color=C_DIM)

    def push(self, amp_db):
        n = len(amp_db)
        if self.buf is None or self.buf.shape != (self.rows, n):
            self.buf = np.full((self.rows, n), float(np.min(amp_db)), dtype=np.float32)
            self.filled = 0
        self.buf[1:] = self.buf[:-1]
        self.buf[0] = amp_db
        self.filled = min(self.filled + 1, self.rows)

    def auto_range(self, auto=True, dmin=-110.0, dmax=10.0):
        if auto and self.buf is not None and self.filled > 0:
            data = self.buf[:self.filled]
            lo = float(np.percentile(data, 2.0))
            hi = float(np.percentile(data, 99.8))
            if hi > lo:
                self.vmin, self.vmax = lo, hi
                return
        self.vmin, self.vmax = dmin, dmax

    def clear(self):
        self.buf = None
        self.filled = 0
        self._tex = None
        # 注意：Kivy 里 Rectangle.texture 置 None 会被换成内置的白色默认纹理，
        # 所以不能靠"纹理为空"来表示没数据——必须把矩形尺寸收成 0，否则
        # 清空后瀑布区会糊上一整块白。
        self._img_r.texture = None
        self._img_r.size = (0, 0)


class PersistencePlot(ImagePlot):
    def __init__(self, bins=256, **kw):
        self.bins = bins
        self.vmin_db = None
        self.vmax_db = None
        self.decay = 0.90
        self.peak = 1.0
        super().__init__(**kw)
        self._yfmt = lambda v: "%.0f" % v
        self._lab_y.draw("← 频率  幅度 ↑", self.x + dp(4), self.y + dp(4), color=C_DIM)

    def push(self, amp_db):
        n = len(amp_db)
        amp = np.asarray(amp_db, dtype=np.float64)
        lo = float(np.percentile(amp, 2.0))
        hi = float(np.percentile(amp, 99.8))
        if hi > lo:
            pad = (hi - lo) * 0.15
            lo, hi = lo - pad, hi + pad
        else:
            lo, hi = -110.0, 10.0
        need = (self.buf is None or self.buf.shape != (self.bins, n)
                or self.vmin_db is None or lo < self.vmin_db or hi > self.vmax_db)
        if need:
            self.buf = np.zeros((self.bins, n), dtype=np.float32)
            self.vmin_db, self.vmax_db = lo, hi
            self._tex = None
        self.buf *= np.float32(self.decay)
        idx = (amp - self.vmin_db) / (self.vmax_db - self.vmin_db + 1e-30) * (self.bins - 1)
        idx = np.clip(np.nan_to_num(idx, nan=0.0, posinf=self.bins - 1, neginf=0.0),
                      0, self.bins - 1).astype(np.int64)
        self.buf[idx, np.arange(n)] += 1.0
        self.peak = max(1.0, float(self.buf.max()))

    def clear(self):
        self.buf = None
        self.vmin_db = self.vmax_db = None
        self._tex = None
        self._img_r.texture = None
        self._img_r.size = (0, 0)          # 理由同 WaterfallPlot.clear

    def _upload(self):
        self.vmin, self.vmax = 0.0, self.peak      # 命中计数 -> 色标强度
        super()._upload()


# ======================================================================
# 折线图（时域 / 调制解调曲线 / 星座）
# ======================================================================
class LinePlot(PlotBase):
    """多条曲线共享一套坐标的通用折线图。"""

    def __init__(self, colors=None, width=1.1, **kw):
        self.series = []            # [{'y':.., 'x':.., 'line':Line, 'color':Color}]
        self.xvals = None
        colors = colors or [C_RT, C_AVG, C_MAX, C_WARN]
        super().__init__(**kw)
        with self.canvas:
            for i in range(4):
                col = Color(*(colors[i % len(colors)]))
                ln = Line(width=width)
                self.series.append({"y": None, "x": None, "line": ln, "color": col,
                                    "base": colors[i % len(colors)]})

    def set_series(self, idx, y, x=None):
        if idx >= len(self.series):
            return
        self.series[idx]["y"] = y
        self.series[idx]["x"] = x
        if idx == 0:
            self.xvals = x

    def show_series(self, idx, on):
        s = self.series[idx]
        s["color"].rgba = s["base"] if on else (0, 0, 0, 0)
        if not on:
            s["line"].points = []

    def _pts(self, y, x, x0p, x1p, y0p, y1p):
        if y is None or len(y) == 0:
            return []
        y = np.asarray(y, dtype=np.float64)
        n = y.size
        if x is None:
            if self.xvals is not None and len(self.xvals) == n:
                xv = np.asarray(self.xvals, dtype=np.float64)
            else:
                xv = np.arange(n, dtype=np.float64)
                xv = self.xlim[0] + xv / max(1, n - 1) * (self.xlim[1] - self.xlim[0])
        else:
            xv = np.asarray(x, dtype=np.float64)
        lo, hi = min(self.xlim), max(self.xlim)
        if xv[0] <= xv[-1]:
            i0 = max(0, int(np.searchsorted(xv, lo)) - 1)
            i1 = min(n, int(np.searchsorted(xv, hi)) + 2)
        else:
            i0, i1 = 0, n
        if i1 - i0 < 2:
            return []
        xs_a, y_a = xv[i0:i1], y[i0:i1]
        npx = max(2, int(x1p - x0p))
        if (i1 - i0) > npx * 2:                     # 抽稀到约 1 点/像素
            k = (i1 - i0) // npx
            m = k * npx
            xs_a = xs_a[:m].reshape(npx, k).mean(axis=1)
            y_a = y_a[:m].reshape(npx, k)
            y_a = np.where(np.max(np.abs(y_a), axis=1) > 0,
                           y_a[np.arange(len(y_a)), np.argmax(np.abs(y_a), axis=1)],
                           y_a[:, 0])
        px = self.x + (xs_a - self.xlim[0]) / (self.xlim[1] - self.xlim[0] + 1e-30) * self.width
        py = self.y + (y_a - self.ylim[0]) / (self.ylim[1] - self.ylim[0] + 1e-30) * self.height
        np.clip(px, x0p - 2, x1p + 2, out=px)
        np.clip(py, y0p - 2, y1p + 2, out=py)
        out = np.empty(px.size * 2, dtype=np.float32)
        out[0::2] = px
        out[1::2] = py
        return out.tolist()

    def draw_content(self, x0p, y0p, x1p, y1p):
        for s in self.series:
            s["line"].points = self._pts(s["y"], s["x"], x0p, x1p, y0p, y1p)


class ConstPlot(PlotBase):
    """星座图散点。"""

    def __init__(self, **kw):
        self.pts_i = None
        self.pts_q = None
        super().__init__(**kw)
        self._xfmt = lambda v: "%.2f" % v
        self._yfmt = lambda v: "%.2f" % v
        with self.canvas:
            self._c = Color(*C_RT)
            self._pt = Point(points=[])
            self._ax_c = Color(*C_GRID)
            self._ax_h = Line(width=0.8)
            self._ax_v = Line(width=0.8)

    def set_points(self, i_vals, q_vals):
        self.pts_i = np.asarray(i_vals, dtype=np.float64)
        self.pts_q = np.asarray(q_vals, dtype=np.float64)

    def auto_view(self):
        if self.pts_i is None or self.pts_i.size == 0:
            return
        r = float(np.percentile(np.abs(self.pts_i) + np.abs(self.pts_q), 99.5)) * 1.15
        r = max(r, 1e-6)
        self._zoom(1.0, 1.0, None, None)
        self.xlim = [-r, r]
        self.ylim = [-r, r]
        self._home = ([self.xlim[0], self.xlim[1]], [self.ylim[0], self.ylim[1]])

    def draw_content(self, x0p, y0p, x1p, y1p):
        if self.pts_i is None or self.pts_i.size == 0:
            self._pt.points = []
            self._ax_h.points = []
            self._ax_v.points = []
            return
        npx = max(2, int(x1p - x0p))
        n = self.pts_i.size
        step = max(1, n // 3000)
        xs = self.px(self.pts_i[::step])
        ys = self.py(self.pts_q[::step])
        np.clip(xs, x0p, x1p, out=xs)
        np.clip(ys, y0p, y1p, out=ys)
        out = np.empty(xs.size * 2, dtype=np.float32)
        out[0::2] = xs
        out[1::2] = ys
        self._pt.points = out.tolist()
        self._ax_h.points = [x0p, self.py(0.0), x1p, self.py(0.0)]
        self._ax_v.points = [self.px(0.0), y0p, self.px(0.0), y1p]
        _ = npx


# ======================================================================
# 控件小工具
# ======================================================================
BTN_BG = (0.20, 0.22, 0.28, 1)


def tbtn(text, cb, width=None, bg=BTN_BG, color=(1, 1, 1, 1), fs=dp(13)):
    b = Button(text=text, size_hint=(None, 1) if width else (None, 1),
               width=width or dp(56), background_normal="", background_color=bg,
               color=color, font_size=fs)
    b.bind(on_release=lambda *_: cb())
    return b


def tlabel(text="", color=C_FG, fs=dp(12), h=None, w=None):
    """小标签。

    ⚠️ 给了 `w` / `h` 就必须**真的把 width / height 设上**：原来只设了
    size_hint 和 text_size，width 仍是默认的 100px —— 于是 `tlabel("深度", w=dp(34))`
    白占 100px，控制条被撑得异常宽（实测因此要多换 3~4 行）。
    """
    lb = Label(text=text, color=color, font_size=fs, halign="left", valign="middle",
               size_hint=(None if w else 1, None if h else 1))
    if w:
        lb.width = w
    if h:
        lb.height = h
    lb.text_size = (w or 100, None) if w else (lb.width, None)
    lb.bind(size=lambda *_: setattr(lb, "text_size", (lb.width, None)))
    return lb


def tinput(text="", w=dp(78), h=dp(30), hint=""):
    ti = TextInput(text=str(text), multiline=False, size_hint=(None, None),
                   size=(w, h), font_size=dp(13), hint_text=hint,
                   background_color=(0.16, 0.17, 0.21, 1), foreground_color=(1, 1, 1, 1))
    return ti


def tcheck(text, value=True, cb=None):
    box = BoxLayout(size_hint=(None, 1), width=dp(18 + 13 * len(text)), spacing=dp(2))
    cbx = CheckBox(size_hint=(None, 1), width=dp(26), active=value,
                   color=(1, 1, 1, 1))
    if cb:
        cbx.bind(active=lambda *_: cb(cbx.active))
    lb = Label(text=text, color=C_FG, font_size=dp(12))
    box.add_widget(cbx)
    box.add_widget(lb)
    box.cbx = cbx
    return box


def tspinner(values, text=None, w=dp(84), cb=None):
    sp = Spinner(text=text or values[0], values=values, size_hint=(None, 1),
                 width=w, font_size=dp(12), background_normal="",
                 background_color=BTN_BG)
    if cb:
        sp.bind(text=lambda *_: cb(sp.text))
    return sp


class CtrlBar(BoxLayout):
    """自动换行的控制条 —— 保证**所有控件在任何分辨率下都完整显示**。

    原来是横向 ScrollView：一行控件总宽超出屏幕时，右边的按钮会跑到屏幕外，
    用户既看不见也点不到（实测顶部三条都溢出：`手动XML` / `速度−` / `零频`
    后面全被裁掉）。现在改成"这一行放不下就换到下一行"，行数由内容决定，
    高度随之自适应。

    · `add(w)`：加一个控件。尺寸固定的用 `width`；想让它撑满本行剩余宽度的
      就设 `size_hint_x`（例如文件名标签）。
    · 高度 = 行数 × row_h（+行间距+上下留白），`size_hint_y` 恒为 None。
    · `add_gap()` 保留但**什么都不做**：换行布局里显式间隔没意义。
    """

    def __init__(self, h=dp(36), spacing=dp(5), gap_v=dp(3),
                 pad=(dp(5), dp(2)), flex_w=dp(70), **kw):
        super().__init__(orientation="vertical", size_hint_y=None,
                         spacing=gap_v, **kw)
        self.row_h = h
        self.hsp = spacing
        self.pad = pad
        self.flex_w = flex_w
        self._items = []
        self._reflowing = False
        self.height = h + 2 * pad[1]
        self.bind(width=self._reflow)
        self._reflow()

    # ---- 兼容旧 API ----
    def add(self, w):
        self._items.append(w)
        self._reflow()
        return w

    def add_gap(self, w=dp(10)):
        """换行布局自带换行，显式间隔没有意义：忽略（保留接口，免得旧调用报错）。"""
        return None

    # ---- 内部 ----
    def _intrinsic(self, w):
        """估算控件要占多宽。

        `size_hint_x is None` 的是定宽控件，用它自己的 width；
        其余视为"可伸缩"（如文件名标签），给它一个标称宽度用于换行计算，
        真实布局时它会撑满所在行的剩余空间。
        """
        if w.size_hint_x is None:
            try:
                return float(w.width) or self.flex_w
            except Exception:
                return self.flex_w
        return self.flex_w

    def _reflow(self, *a):
        if self._reflowing or not self._items:
            return
        avail = (self.width or Window.width) - 2 * self.pad[0]
        if avail <= 1:
            return
        self._reflowing = True
        try:
            # ⚠️ 必须先把控件从旧行里**摘下来**：clear_widgets() 只摘掉"行"
            # 这一层，行仍然持有它的子控件，直接再 add_widget 会报
            # "Cannot add ..., it already has a parent"。
            for w in self._items:
                if w.parent is not None:
                    w.parent.remove_widget(w)
            self.clear_widgets()
            rows, cur, cur_w = [], [], 0.0
            for w in self._items:
                cw = self._intrinsic(w)
                need = cw + (self.hsp if cur else 0.0)
                if cur and cur_w + need > avail:
                    rows.append(cur)
                    cur, cur_w, need = [], 0.0, cw
                cur.append(w)
                cur_w += need
            if cur:
                rows.append(cur)
            for r in rows:
                row = BoxLayout(size_hint_y=None, height=self.row_h,
                                spacing=self.hsp, padding=(self.pad[0], 0))
                for w in r:
                    row.add_widget(w)
                self.add_widget(row)
            n = max(1, len(rows))
            self.height = n * self.row_h + (n - 1) * self.spacing + 2 * self.pad[1]
        finally:
            self._reflowing = False

    # 便于测试/排查：当前排成了几行
    def row_count(self):
        return len(self.children)


# ======================================================================
# 各页面
# ======================================================================
class PageBase(BoxLayout):
    name = "page"
    title = "页面"

    def __init__(self, app, **kw):
        super().__init__(orientation="vertical", **kw)
        self.app = app

    def on_shown(self):
        """切到本页时调用。"""
        self.redraw()

    def redraw(self):
        pass


class SpectrumPage(PageBase):
    name = "spectrum"
    title = "频谱"

    def __init__(self, app, **kw):
        super().__init__(app, **kw)
        bar = CtrlBar()
        bar.add(tcheck("实时", True, lambda v: self._toggle("rt", v)))
        bar.add(tcheck("平均", True, lambda v: self._toggle("avg", v)))
        bar.add(tcheck("最大保持", True, lambda v: self._toggle("max", v)))
        bar.add(tspinner(["MHz", "KHz", "Hz"], app.xunit,
                         cb=lambda t: self._set_unit(t)))
        bar.add(tbtn("Y自适应", self._fit_y, dp(66)))
        bar.add(tbtn("加Mark", self._add_mark, dp(64)))
        bar.add(tbtn("清Mark", self._clear_mark, dp(64)))
        bar.add(tbtn("单次", self._single, dp(52)))
        self.add_widget(bar)
        self.plot = SpectrumPlot(size_hint=(1, 1))
        self.add_widget(self.plot)
        self.readout = Label(text="等待载入 IQ 文件…", color=C_DIM, font_size=dp(11),
                             size_hint_y=None, height=dp(40), halign="left",
                             valign="middle")
        self.readout.bind(size=lambda *_: setattr(self.readout, "text_size",
                                                  (self.readout.width, None)))
        self.add_widget(self.readout)

    def _toggle(self, which, val):
        p = self.plot
        if which == "rt":
            p.show_rt = val
        elif which == "avg":
            p.show_avg = val
        else:
            p.show_max = val
        p.redraw()

    def _set_unit(self, t):
        self.app.xunit = t
        self.plot.set_xunit(t)
        for k in ("waterfall", "persistence"):
            pg = self.app.pages.get(k)
            if pg:
                pg.plot.set_xunit(t)

    def _fit_y(self):
        p = self.plot
        if p.rt is None and p.avg is None and p.maxh is None:
            return
        p.ylim = p.auto_ylim()
        p._home = (list(p.xlim), list(p.ylim))
        p.redraw()

    def _add_mark(self):
        p = self.plot
        if p.rt is None or self.app.freq_hz is None:
            return
        fpk = float(self.app.freq_hz[int(np.argmax(p.rt))])
        p.marks.append((fpk, fmt_freq(fpk, self.app.xunit)))
        p.marks = p.marks[-4:]
        p.redraw()

    def _clear_mark(self):
        self.plot.marks = []
        self.plot.redraw()

    def _single(self):
        self.app.draw_static()

    def show_readout(self, amp):
        app = self.app
        if app.freq_hz is None or amp is None:
            return
        fpk = float(app.freq_hz[int(np.argmax(amp))])
        pk = float(np.max(amp))
        bw = D.calc_single_band(app.freq_hz, amp)
        self.readout.text = (
            "峰值 %.1f dB @ %s%s   |   3dB %s  |   6dB %s\n"
            "9dB %s  |   99%% %s  |   N=%d  %s   |   %s"
            % (pk, fmt_freq(fpk, app.xunit), app.xunit,
               fmt_bw(bw.get("3dB")), fmt_bw(bw.get("6dB")),
               fmt_bw(bw.get("9dB")), fmt_bw(bw.get("99%")),
               app.N_fft, "%.3f×" % app.rate,
               "播放中" if app.running else "已停止"))

    def redraw(self):
        self.plot.refresh_view()
        self.plot.redraw()


class WaterfallPage(PageBase):
    name = "waterfall"
    title = "瀑布"

    def __init__(self, app, **kw):
        super().__init__(app, **kw)
        bar = CtrlBar()
        bar.add(tlabel("深度", fs=dp(12), w=dp(34)))
        bar.add(tspinner(["100", "200", "400", "800"], "200", dp(70),
                         cb=lambda t: self._set_rows(int(t))))
        bar.add(tlabel("色标", fs=dp(12), w=dp(34)))
        bar.add(tspinner(list(CMAPS.keys()), "jet", dp(84),
                         cb=lambda t: self._set_cmap(t)))
        bar.add(tcheck("自动色标", True, None))
        bar.add(tbtn("清空", self._clear, dp(56)))
        self.add_widget(bar)
        self.plot = WaterfallPlot(rows=200, size_hint=(1, 1))
        self.plot.ylim = [0, 1]
        self.add_widget(self.plot)
        self.info = Label(text="", color=C_DIM, font_size=dp(11), size_hint_y=None,
                          height=dp(22))
        self.add_widget(self.info)

    def _set_rows(self, n):
        self.plot.rows = n
        self.plot.clear()
        self.plot.redraw()

    def _set_cmap(self, c):
        self.plot.cmap = c
        self.plot.redraw()

    def _clear(self):
        self.plot.clear()
        self.info.text = "已清空"

    def redraw(self):
        p = self.plot
        p.freq_hz = self.app.freq_hz
        p.auto_range(True)
        p.refresh_view()
        p.redraw()
        if p.buf is not None:
            self.info.text = ("深度 %d 行｜已填充 %d｜色标 %.1f~%.1f dB"
                              % (p.rows, p.filled, p.vmin, p.vmax))


class PersistencePage(PageBase):
    name = "persistence"
    title = "余晖"

    def __init__(self, app, **kw):
        super().__init__(app, **kw)
        bar = CtrlBar()
        bar.add(tlabel("衰减", fs=dp(12), w=dp(34)))
        self.sp_decay = tspinner(["0.70", "0.80", "0.90", "0.95", "0.98"], "0.90",
                                 dp(74), cb=lambda t: self._set_decay(float(t)))
        bar.add(self.sp_decay)
        bar.add(tlabel("层数", fs=dp(12), w=dp(34)))
        bar.add(tspinner(["128", "256", "512"], "256", dp(70),
                         cb=lambda t: self._set_bins(int(t))))
        bar.add(tlabel("色标", fs=dp(12), w=dp(34)))
        bar.add(tspinner(list(CMAPS.keys()), "jet", dp(84),
                         cb=lambda t: self._set_cmap(t)))
        bar.add(tbtn("清空", self._clear, dp(56)))
        self.add_widget(bar)
        self.plot = PersistencePlot(bins=256, size_hint=(1, 1))
        self.plot.ylim = [0, 1]
        self.add_widget(self.plot)
        self.info = Label(text="", color=C_DIM, font_size=dp(11), size_hint_y=None,
                          height=dp(22))
        self.add_widget(self.info)

    def _set_decay(self, d):
        self.plot.decay = d

    def _set_bins(self, n):
        self.plot.bins = n
        self.plot.clear()
        self.plot.redraw()

    def _set_cmap(self, c):
        self.plot.cmap = c
        self.plot.redraw()

    def _clear(self):
        self.plot.clear()
        self.info.text = "已清空"

    def redraw(self):
        p = self.plot
        p.freq_hz = self.app.freq_hz
        p.refresh_view()
        p.redraw()
        if p.buf is not None:
            self.info.text = ("衰减 %g｜%d 层幅度｜%.0f~%.0f dBFS"
                              % (p.decay, p.bins, p.vmin_db or 0, p.vmax_db or 0))


class TimePage(PageBase):
    name = "time"
    title = "时域"

    def __init__(self, app, **kw):
        super().__init__(app, **kw)
        bar = CtrlBar()
        bar.add(tlabel("时长(ms)", fs=dp(12), w=dp(56)))
        self.in_dur = tinput("0.5", w=dp(56))
        bar.add(self.in_dur)
        bar.add(tlabel("带宽(kHz)", fs=dp(12), w=dp(64)))
        self.in_bw = tinput("0", w=dp(52))
        bar.add(self.in_bw)
        bar.add(tbtn("峰值对齐", lambda: self.draw(center="peak"), dp(70)))
        bar.add(tbtn("零频", lambda: self.draw(center="zero"), dp(52)))
        self.add_widget(bar)
        self.plot = LinePlot(colors=[C_RT, C_MAX], width=1.0, size_hint=(1, 1))
        self.plot.title = "I / Q 波形"
        self.plot.xlabel = "时间(ms)"
        self.plot.ylabel = "幅度"
        self.add_widget(self.plot)
        self.env = LinePlot(colors=[C_AVG], width=1.0, size_hint=(1, 1))
        self.env.title = "包络"
        self.env.xlabel = "时间(ms)"
        self.env.ylabel = "包络"
        self.add_widget(self.env)
        self.info = Label(text="", color=C_DIM, font_size=dp(11), size_hint_y=None,
                          height=dp(22))
        self.add_widget(self.info)

    def draw(self, center="peak"):
        app = self.app
        if app.iq is None or app.fs <= 0:
            toast("先载入 IQ 文件并设置采样率")
            return
        try:
            dur = max(0.01, float(self.in_dur.text or "0.5"))
            bw = max(0.0, float(self.in_bw.text or "0") * 1000.0)
        except ValueError:
            toast("时长/带宽要填数字")
            return
        n = int(dur * app.fs / 1000.0)
        n = max(64, min(n, app.iq.size))
        start = min(app.offset, app.iq.size - n)
        start = max(0, start)
        blk = np.asarray(app.iq[start:start + n], dtype=np.complex128)
        if bw > 0:
            blk = D.bandpass_filter_complex(blk, app.fs, bw, 0.0)
        t_ms = np.arange(blk.size) / app.fs * 1e3
        self.plot.set_series(0, np.real(blk), t_ms)
        self.plot.set_series(1, np.imag(blk), t_ms)
        env = np.abs(blk)
        self.env.set_series(0, env, t_ms)
        ylim = [-1.05, 1.05]
        for pk in (float(np.max(np.abs(np.real(blk)))), float(np.max(np.abs(np.imag(blk))))):
            if pk > 0.95:
                ylim = [-pk * 1.1, pk * 1.1]
        self.plot.set_view([float(t_ms[0]), float(t_ms[-1])], ylim, remember=True)
        self.env.set_view([float(t_ms[0]), float(t_ms[-1])],
                          [0.0, max(1e-3, float(env.max()) * 1.15)], remember=True)
        self.plot.redraw()
        self.env.redraw()
        self.info.text = ("起点 %.3f ms｜%d 点｜%s｜带宽 %s"
                          % (start / app.fs * 1e3, blk.size,
                             "峰值对齐" if center == "peak" else "零频",
                             fmt_bw(bw) if bw > 0 else "不滤波"))

    def redraw(self):
        self.plot.redraw()
        self.env.redraw()


class ModPage(PageBase):
    name = "mod"
    title = "调制"

    def __init__(self, app, **kw):
        super().__init__(app, **kw)
        bar = CtrlBar()
        bar.add(tlabel("点数", fs=dp(12), w=dp(34)))
        self.sp_points = tspinner(["2048", "8192", "32768", "65536"], "8192", dp(78))
        bar.add(self.sp_points)
        bar.add(tbtn("绘制", self.draw, dp(52)))
        bar.add(tbtn("自动识别", self.identify, dp(74), bg=(0.20, 0.42, 0.30, 1)))
        self.sp_mode = tspinner(["AM", "FM"], "FM", dp(58), cb=lambda t: self.draw())
        bar.add(self.sp_mode)
        bar.add(tbtn("解调并播放", self.play_audio, dp(86), bg=(0.30, 0.28, 0.45, 1)))
        bar.add(tbtn("存WAV", self.save_wav, dp(64)))
        self.add_widget(bar)
        self.const = ConstPlot(size_hint=(1, 1))
        self.const.title = "星座图 I-Q（已校载频）"
        self.const.xlabel = "I"
        self.const.ylabel = "Q"
        self.add_widget(self.const)
        self.curve = LinePlot(colors=[C_WARN], width=1.0, size_hint=(1, 1))
        self.curve.title = "AM 解调(包络)"
        self.curve.xlabel = "时间(ms)"
        self.curve.ylabel = "幅度"
        self._mode = "FM"
        self.add_widget(self.curve)
        self.info = Label(text="", color=C_DIM, font_size=dp(11), size_hint_y=None,
                          height=dp(24), halign="left", valign="middle")
        self.info.bind(size=lambda *_: setattr(self.info, "text_size",
                                               (self.info.width, None)))
        self.add_widget(self.info)
        self._block = None
        self._audio = None

    def get_block(self):
        app = self.app
        if app.iq is None or app.fs <= 0:
            return None
        try:
            n_pts = int(self.sp_points.text)
        except Exception:
            n_pts = 8192
        n_pts = max(64, min(n_pts, app.iq.size))
        start = min(app.offset, app.iq.size - n_pts)
        start = max(0, start)
        return np.asarray(app.iq[start:start + n_pts], dtype=np.complex128), start

    def _curve_mode(self):
        t = getattr(self, "sp_mode", None)
        return t.text if t is not None else self._mode

    def draw(self):
        got = self.get_block()
        if not got:
            toast("先载入 IQ 文件并设置采样率")
            return
        block, start = got
        fs = self.app.fs
        fr, amp = D.calc_fft_block(block[:min(self.app.N_fft, block.size)], self.app.N_fft)
        info = D.estimate_modulation(block, fs, fr * fs, amp)
        fc = info.get("carrier_freq_hz", info.get("peak_freq_hz", 0.0)) or 0.0
        t = np.arange(block.size, dtype=np.float64) / fs
        corr = block * np.exp(-1j * 2.0 * np.pi * fc * t)
        step = max(1, block.size // 2000)
        self.const.set_points(np.real(corr[::step]), np.imag(corr[::step]))
        self.const.auto_view()
        mode = self._curve_mode()
        self._mode = mode
        if mode == "AM":
            y = D.am_demod(block)
            self.curve.title = "AM 解调(包络)"
        else:
            y = D.fm_demod(block)
            self.curve.title = "FM 解调(瞬时频率 rad/sample)"
        t_ms = np.arange(y.size) / fs * 1e3
        self.curve.set_series(0, y, t_ms)
        lim = float(np.percentile(np.abs(y), 99.5)) * 1.2 if y.size else 1.0
        lim = max(lim, 1e-6)
        self.curve.set_view([float(t_ms[0]), float(t_ms[-1])], [-lim, lim], remember=True)
        self.const.redraw()
        self.curve.redraw()
        self._block = block
        self.info.text = ("起点 %.3f ms｜%d 点｜载频 %s Hz｜识别: %s (%.0f%%)｜%s"
                          % (start / fs * 1e3, block.size, "%.0f" % fc,
                             info.get("modulation") or "—",
                             100.0 * float(info.get("confidence") or 0),
                             (info.get("reason") or "")[:40]))

    def identify(self):
        got = self.get_block()
        if not got:
            toast("先载入 IQ 文件并设置采样率")
            return
        block, _ = got
        fs = self.app.fs
        self.info.text = "正在自动识别调制方式…（手机 CPU 需要几秒）"

        def work():
            fr, amp = D.calc_fft_block(block[:min(1024, block.size)], 1024)
            return D.estimate_modulation(block, fs, fr * fs, amp)

        def done(info):
            lines = ["识别结果: %s   置信度 %.0f%%"
                     % (info.get("modulation") or "—",
                        100.0 * float(info.get("confidence") or 0))]
            for k in ("symbol_rate_hz", "peak_freq_hz", "bw_3db_hz", "bw_99_hz"):
                if info.get(k):
                    lines.append("%s=%s" % (k, fmt_bw(info[k])))
            if info.get("reason"):
                lines.append("依据: %s" % str(info["reason"])[:120])
            self.info.text = "  |  ".join(lines)
            toast("识别完成: %s" % (info.get("modulation") or "—"))

        run_bg(work, done, lambda e: setattr(self.info, "text", "识别失败: %s" % e))

    def play_audio(self):
        got = self.get_block()
        if not got:
            toast("先载入 IQ 文件并设置采样率")
            return
        block, _ = got
        fs = self.app.fs
        mode = self._curve_mode()
        self.info.text = "正在解调…"

        def work():
            return D.demod_to_audio(block, fs, mode, 0.0, fs * 0.4)

        def done(res):
            audio, info = res
            ok, msg = self.app.audio.play(audio)
            self._audio = audio
            self.info.text = ("%s 解调: %.2fs｜峰值 %.1f dB｜%s"
                              % (mode, info.get("dur_s", 0), info.get("peak_db", 0), msg))
            if not ok:
                toast(msg)

        run_bg(work, done, lambda e: setattr(self.info, "text", "解调失败: %s" % e))

    def save_wav(self):
        if self._audio is None:
            toast("先点【解调并播放】")
            return
        path = os.path.join(default_work_dir(), "demo_%s.wav"
                            % time.strftime("%H%M%S"))
        try:
            with open(path, "wb") as f:
                f.write(D.float32_to_wav_bytes(self._audio))
            toast("已保存 %s" % path)
            self.info.text = "已保存 %s" % path
        except Exception as e:
            toast("保存失败: %s" % e)

    def redraw(self):
        self.const.redraw()
        self.curve.redraw()


# ======================================================================
# 文件选择（安卓没有 tkinter 的 filedialog，用 Kivy 自带浏览器 + 手填路径）
# ======================================================================
IQ_EXT = (".cs16", ".c16", ".cf32", ".cfile", ".complex", ".cu8", ".cs8",
          ".wav", ".bin", ".iq", ".dat")

# 「从 xml 反推 IQ 文件名」的目录索引缓存：{绝对路径: (目录签名, 名字集合)}
_XML_INDEX_CACHE = {}


def xml_derived_names(folder):
    """从目录里的 `<名字>.xml` 反推出 IQ 文件名集合（带缓存）。

    这是**最权威的识别方式**：素材 / 录制目录里每个 IQ 数据文件都配一个
    `<文件名>.xml`（里面存 sample_rate，程序也靠它读采样率，见
    `xml_candidates()`），所以把 `.xml` 去掉剩下的就是 IQ 文件名。

    比"按扩展名猜"可靠得多 —— 那类名字里自带点的
    （`16psk_25k_24.3k_0.1`、`2fsk_12.7k_4k`）会把 `os.path.splitext` 骗过去，
    取到 `'.1'`、`'.7k_4k'` 这种"假扩展名"，于是真文件全被隐藏
    （实测某目录 547 个文件里 412 个看不见）。
    """
    try:
        key = os.path.abspath(folder)
        st = os.stat(key)
        sig = (st.st_mtime_ns, st.st_ino)
    except Exception:
        key, sig = str(folder), None
    hit = _XML_INDEX_CACHE.get(key)
    if hit is not None and hit[0] == sig:
        return hit[1]
    names = set()
    try:
        for n in os.listdir(folder):
            if len(n) > 4 and n.lower().endswith(".xml"):
                names.add(n[:-4])                 # 去掉 ".xml" 就是 IQ 文件名
    except Exception:
        pass
    _XML_INDEX_CACHE[key] = (sig, names)
    return names


def looks_like_iq(name, folder=None):
    """这个文件是不是 IQ 数据文件？（文件浏览器的过滤器用）

    判据按可靠性排序：
      1) 同目录下存在 `<文件名>.xml` —— **确凿**：文件名就是从 xml 反推出来的；
      2) 扩展名是明确的 IQ 格式（.cs16 / .cf32 / .wav …）；
      3) 完全没有扩展名 —— 原始 IQ 转储的常见形态；
      4) 其余不算（想看全部就按界面上的「所有文件」）。

    `folder` 传进来的就是 Kivy 过滤器给的当前目录；不传则只能靠 2)/3) 判断。
    """
    name = str(name)
    if folder and name in xml_derived_names(folder):
        return True
    ext = os.path.splitext(name)[1].lower()
    if ext in IQ_EXT:
        return True
    return ext == ""



class FileBrowser(Popup):
    def __init__(self, app, on_pick, **kw):
        self.app = app
        self.on_pick = on_pick
        self.show_all = False
        super().__init__(title="选择 IQ 文件", size_hint=(0.96, 0.94),
                         title_color=C_FG, separator_color=C_ACCENT, **kw)
        root = BoxLayout(orientation="vertical", spacing=dp(4), padding=dp(4))
        top = BoxLayout(size_hint_y=None, height=dp(34), spacing=dp(4))
        self.bt_all = tbtn("所有文件", self._toggle_all, dp(76))
        top.add_widget(self.bt_all)
        self.in_path = tinput(default_work_dir(), w=Window.width * 0.55, h=dp(30))
        top.add_widget(self.in_path)
        top.add_widget(tbtn("跳转", self._goto, dp(56)))
        root.add_widget(top)
        dirs = storage_dirs()
        self.fc = FileChooserListView(path=dirs[0] if dirs else APP_DIR,
                                      filters=self._filters(),
                                      size_hint=(1, 1))
        self.fc.bind(on_submit=self._submit)
        self._tune_scrollbars(self.fc)
        # 再绑一次 on_open：KV 里的子控件在个别 Kivy 版本上要等布局时才建齐，
        # 那时再调一遍是幂等的，不会有害。
        self.bind(on_open=lambda *_: self._tune_scrollbars(self.fc))
        root.add_widget(self.fc)
        self.lb_path = Label(text=self.fc.path, color=C_DIM, font_size=dp(10),
                             size_hint_y=None, height=dp(20), halign="left")
        root.add_widget(self.lb_path)
        self.fc.bind(path=lambda *_: setattr(self.lb_path, "text", self.fc.path))
        bot = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6))
        for d in dirs[:4]:
            bot.add_widget(tbtn(os.path.basename(d.rstrip("/")) or d,
                                lambda p=d: self._set_path(p), dp(84)))
        bot.add_widget(tbtn("取消", lambda: self.dismiss(), dp(64)))
        bot.add_widget(tbtn("载入", self._ok, dp(64), bg=(0.20, 0.45, 0.25, 1)))
        root.add_widget(bot)
        self.content = root

    def _filters(self):
        if self.show_all:
            return ["*"]
        # ⚠️ 必须把 Kivy 传进来的 folder 一起交给 looks_like_iq：识别 IQ 文件靠的
        # 是"同目录下有没有同名 .xml"（从 xml 反推文件名），不看目录就判不出来。
        return [lambda folder, name: looks_like_iq(name, folder)]

    @staticmethod
    def _tune_scrollbars(root):
        """把 FileChooser 内部那个 ScrollView 的滚动条改成"手指拖得动"的。

        实测（Kivy 2.3 的 kivy/data/style.kv 里 <FileChooserListLayout>）：
        内部 ScrollView 用的是**默认值**——`scroll_type=['content']`、
        `bar_width='2dp'`。于是：
          · Kivy 文档写明：scroll_type 为 ['content'] 时"只能拖动内容"，
            要含 'bars'（即 ['bars','content']）才能靠**拖滚动条本身**来滚。
            默认不含 'bars' —— 所以手指按在右侧滑块上完全没反应。
          · bar_width 默认 2dp，在手机上就是一根头发丝，根本按不中。
        这里把最外层那个 ScrollView 改成 22dp 宽、常显（半透明）、可拖。
        BFS 保证先遇到最外层（真正的滚动容器），只改那一个。
        返回被改的 ScrollView（找不到返回 None，便于测试断言）。
        """
        from collections import deque
        from kivy.uix.scrollview import ScrollView
        q = deque([root])
        seen = 0
        while q and seen < 800:                 # 防御：别在异常树上死循环
            w = q.popleft()
            seen += 1
            if isinstance(w, ScrollView):
                try:
                    w.scroll_type = ["bars", "content"]
                    w.bar_width = dp(22)
                    w.bar_margin = dp(2)
                    w.bar_color = (1.0, 1.0, 1.0, 0.80)
                    w.bar_inactive_color = (1.0, 1.0, 1.0, 0.40)
                except Exception:
                    return None
                return w
            try:
                q.extend(w.children)
            except Exception:
                pass
        return None

    def _toggle_all(self):
        self.show_all = not self.show_all
        self.fc.filters = self._filters()
        # 按钮文字跟着状态走，避免"点了到底显示的是哪种"看不出来
        self.bt_all.text = "仅 IQ 文件" if self.show_all else "所有文件"

    def _set_path(self, p):
        try:
            self.fc.path = p
        except Exception as e:
            toast("目录打不开: %s" % e)

    def _goto(self):
        p = self.in_path.text.strip()
        if os.path.isfile(p):
            self._pick(p)
        else:
            self._set_path(p)

    def _submit(self, _fc, selection, *_):
        if selection:
            self._pick(selection[0])

    def _ok(self):
        sel = self.fc.selection
        if sel:
            self._pick(sel[0])
        else:
            toast("先点一个文件")

    def _pick(self, path):
        if not os.path.isfile(path):
            toast("这不是文件: %s" % path)
            return
        self.dismiss()
        self.on_pick(path)


# ======================================================================
# 主界面
# ======================================================================
NAV = [("spectrum", "频谱"), ("waterfall", "瀑布"), ("persistence", "余晖"),
       ("time", "时域"), ("mod", "调制")]


class RootWidget(BoxLayout):
    def __init__(self, app, **kw):
        super().__init__(orientation="vertical", **kw)
        self.app = app
        self.add_widget(self._build_top())
        self.add_widget(self._build_ctrl())
        self.sm = ScreenManager(transition=NoTransition())
        for key, title in NAV:
            page = app.pages[key]
            scr = Screen(name=key)
            scr.add_widget(page)
            self.sm.add_widget(scr)
        self.add_widget(self.sm)
        self.add_widget(self._build_nav())

    # ---- 顶部：文件 / 采样率 / XML 自动·手动 ----
    def _build_top(self):
        # 也用 CtrlBar：屏幕窄时"自动XML/手动XML"会换到下一行，不会被裁掉
        # （普通 BoxLayout 里，定宽按钮会把弹性标签挤爆、自己溢出屏幕）
        bar = CtrlBar(h=dp(38), pad=(dp(6), dp(2)))
        with bar.canvas.before:
            self._top_c = Color(*C_BAR)
            self._top_r = Rectangle(pos=bar.pos, size=bar.size)
        bar.bind(pos=lambda *_: setattr(self._top_r, "pos", bar.pos),
                 size=lambda *_: setattr(self._top_r, "size", bar.size))
        bar.add(tbtn("文件", self.app.choose_file, dp(52),
                     bg=(0.20, 0.35, 0.50, 1)))
        self.lb_file = Label(text="（未选择）", color=C_FG, font_size=dp(11),
                             shorten=True, shorten_from="center")
        bar.add(self.lb_file)                       # size_hint_x=1 -> 撑满本行剩余
        self.bt_xml_auto = ToggleButton(text="自动XML", group="xml", state="down",
                                        size_hint=(None, 1), width=dp(68),
                                        font_size=dp(12), background_normal="",
                                        background_color=(0.25, 0.55, 0.30, 1))
        self.bt_xml_man = ToggleButton(text="手动XML", group="xml", state="normal",
                                       size_hint=(None, 1), width=dp(68),
                                       font_size=dp(12), background_normal="",
                                       background_color=BTN_BG)
        self.bt_xml_auto.bind(on_release=lambda *_: self.app.set_xml_mode("auto"))
        self.bt_xml_man.bind(on_release=lambda *_: self.app.set_xml_mode("manual"))
        bar.add(self.bt_xml_auto)
        bar.add(self.bt_xml_man)
        return bar

    # ---- 第二行：播放控制 ----
    def _build_ctrl(self):
        bar = CtrlBar(h=dp(38))
        with bar.canvas.before:
            self._ctl_c = Color(*C_PANEL)
            self._ctl_r = Rectangle(pos=bar.pos, size=bar.size)
        bar.bind(pos=lambda *_: setattr(self._ctl_r, "pos", bar.pos),
                 size=lambda *_: setattr(self._ctl_r, "size", bar.size))
        bar.add(tlabel("fs kHz", fs=dp(11), w=dp(44)))
        bar.add(self.app.in_fs)
        bar.add(tbtn("▶ 播放", self.app.play, dp(70), bg=(0.20, 0.45, 0.25, 1)))
        bar.add(tbtn("⏸ 暂停", self.app.pause, dp(70), bg=(0.35, 0.32, 0.18, 1)))
        bar.add(tbtn("■ 停止", self.app.stop, dp(70), bg=(0.45, 0.20, 0.20, 1)))
        bar.add(tbtn("速度−", lambda: self.app.bump_rate(0.5), dp(64)))
        self.lb_rate = tlabel("1.00×", fs=dp(12), w=dp(52))
        bar.add(self.lb_rate)
        bar.add(tbtn("速度＋", lambda: self.app.bump_rate(2.0), dp(64)))
        bar.add(tlabel("N_fft", fs=dp(11), w=dp(40)))
        self.sp_nfft = tspinner(["512", "1024", "2048", "4096"], "1024", dp(70),
                                cb=lambda t: self.app.set_nfft(int(t)))
        bar.add(self.sp_nfft)
        bar.add(tlabel("载入点数", fs=dp(11), w=dp(60)))
        self.sp_load = tspinner(["0.5M", "1M", "2M", "4M", "8M"], "2M", dp(64),
                                cb=lambda t: self.app.set_max_load(t))
        bar.add(self.sp_load)
        bar.add(tbtn("导出PNG", self.app.export_png, dp(76)))
        bar.add(tbtn("关于", self.app.show_about, dp(56)))
        return bar

    # ---- 底部标签栏 ----
    def _build_nav(self):
        sv = ScrollView(size_hint_y=None, height=dp(50), do_scroll_y=False,
                        bar_width=dp(2))
        row = BoxLayout(size_hint=(None, 1), spacing=dp(3), padding=(dp(4), dp(3)))
        row.bind(minimum_width=row.setter("width"))
        self.nav_btns = {}
        for key, title in NAV:
            b = ToggleButton(text=title, group="nav", size_hint=(None, 1),
                             width=dp(58), font_size=dp(12), background_normal="",
                             background_color=BTN_BG if key != "spectrum"
                             else (0.25, 0.45, 0.65, 1))
            b.bind(on_release=lambda _b, k=key: self.goto(k))
            self.nav_btns[key] = b
            row.add_widget(b)
        sv.add_widget(row)
        self.nav_btns["spectrum"].state = "down"
        return sv

    def goto(self, key):
        self.sm.current = key
        for k, b in self.nav_btns.items():
            b.background_color = (0.25, 0.45, 0.65, 1) if k == key else BTN_BG
        self.app.pages[key].on_shown()

    def set_rate_label(self, rate):
        self.lb_rate.text = "%.2f×" % rate


# ======================================================================
# 应用
# ======================================================================
class IQApp(App):
    title = "IQ 信号综合分析仪（安卓版）"

    def build(self):
        self.iq = None
        self.fs = 0.0
        self.path = ""
        self.offset = 0
        self.N_fft = 1024
        self.rate = 1.0
        self.running = False
        self.avg = None
        self.maxh = None
        self.freq_hz = None
        self._fs_cache = 0.0
        self._frame_i = 0
        self.xunit = "MHz"
        self.xml_mode = "auto"
        self.max_load = 2_000_000
        self.audio = AudioPlayer()
        self.in_fs = tinput("1000.0", w=dp(92))
        self.pages = {
            "spectrum": SpectrumPage(self),
            "waterfall": WaterfallPage(self),
            "persistence": PersistencePage(self),
            "time": TimePage(self),
            "mod": ModPage(self),
        }
        self.root_w = RootWidget(self)
        Clock.schedule_once(self._after_build, 0.3)
        return self.root_w

    # ---- 启动收尾 ----
    def _after_build(self, _dt):
        ask_android_permissions()
        if IS_ANDROID:
            try:
                Window.softinput_mode = "below_target"
            except Exception:
                pass
        else:
            try:
                # 桌面预览按最窄的常见手机宽度（360dp）摆，能塞下就一定能上真机。
                # 注意别超过屏幕物理高度：窗口比屏幕高时 Kivy 截帧缓冲的行距会对不上，
                # Window.screenshot() 出来的图会被整体剪切（看着像"斜线"，其实渲染是对的）。
                if not IS_PHONE_UI:
                    Window.size = (360, 620)
            except Exception:
                pass
        print("中文字体: %s" % (FONT_USED or "未找到中文字体（界面汉字会变方框）"))
        print("工作目录: %s" % default_work_dir())
        toast("中文字体: %s" % (os.path.basename(FONT_USED) if FONT_USED else "缺失"))

    # ---- 文件 ----
    def choose_file(self):
        FileBrowser(self, self.load_file).open()

    def load_file(self, path):
        self.path = path
        n = self.max_load
        self.root_w.lb_file.text = os.path.basename(path)
        toast("正在载入 %s …" % os.path.basename(path))

        def work():
            iq = D.load_iq_any(path, "auto", n)
            if iq.size < 64:
                raise ValueError("有效样本不足 64 点")
            return iq.astype(np.complex64) if iq.size > 262144 else iq

        def done(iq):
            self.iq = iq
            self.offset = 0
            self.avg = None
            self.maxh = None
            self.freq_hz = None
            self._fs_cache = 0.0
            for k in ("waterfall", "persistence"):
                self.pages[k].plot.clear()
            # 新数据的幅度范围不一样，让频谱页重新自适应一次 Y 量程
            self.pages["spectrum"].plot._home = None
            total = os.path.getsize(path)
            self.root_w.lb_file.text = "%s（%s, %d 点）" % (
                os.path.basename(path), fmt_size(total), self.iq.size)
            if self.xml_mode == "auto":
                self._apply_xml_sr(quiet=True)
            self.draw_static()
            toast("已载入 %d 点" % self.iq.size)

        run_bg(work, done, lambda e: toast("载入失败: %s" % e))

    # ---- XML 采样率（自动 / 手动，与桌面版同逻辑）----
    def xml_candidates(self, path):
        d = os.path.dirname(os.path.abspath(path))
        base = os.path.basename(path)
        out = [os.path.join(d, base + ".xml")]
        stem = os.path.splitext(base)[0]
        if stem and stem != base:
            out.append(os.path.join(d, stem + ".xml"))
        return out

    def _apply_xml_sr(self, quiet=False):
        if not self.path or not os.path.exists(self.path):
            if not quiet:
                toast("先选一个 IQ 文件")
            return False
        hit = None
        for c in self.xml_candidates(self.path):
            if os.path.exists(c):
                hit = c
                break
        if hit is None:
            if not quiet:
                toast("没有同名 XML（%s.xml / %s.xml）"
                      % (os.path.basename(self.path),
                         os.path.splitext(os.path.basename(self.path))[0]))
            return False
        sr = D.parse_xml_sample_rate(hit)
        if not sr or sr <= 0:
            if not quiet:
                toast("XML 里没有合法采样率")
            return False
        self.fs = float(sr)
        self.in_fs.text = "%.6f" % (self.fs / 1000.0)
        # 采样率变了，频率轴必须按新刻度重建：只清 freq_hz 不重画的话，
        # 频谱横轴与曲线会一直空着，直到用户再按一次播放。
        self.freq_hz = None
        self._fs_cache = 0.0
        self.pages["spectrum"].plot._home = None      # 频率轴量程要跟着重设
        if self.iq is not None:
            self.draw_static()
        if not quiet:
            toast("读到采样率 %.6f kHz" % (self.fs / 1000.0))
        return True

    def set_xml_mode(self, mode):
        self.xml_mode = "auto" if mode == "auto" else "manual"
        w = self.root_w
        w.bt_xml_auto.background_color = ((0.25, 0.55, 0.30, 1) if self.xml_mode == "auto"
                                          else BTN_BG)
        w.bt_xml_man.background_color = ((0.25, 0.55, 0.30, 1) if self.xml_mode == "manual"
                                         else BTN_BG)
        if self.xml_mode == "auto":
            self._apply_xml_sr(quiet=True)
        else:
            toast("手动模式：点【手动XML】读一次采样率")
            self._apply_xml_sr(quiet=False)

    # ---- 采样率 / 参数 ----
    def fs_now(self):
        try:
            v = float(self.in_fs.text) * 1000.0
            return v if v > 0 else 0.0
        except Exception:
            return 0.0

    def set_nfft(self, n):
        self.N_fft = int(n)
        self.freq_hz = None
        self.avg = None
        self.maxh = None
        self._fs_cache = 0.0
        for k in ("waterfall", "persistence"):
            self.pages[k].plot.clear()
        self.pages["spectrum"].plot._home = None      # 频率轴量程要跟着重设
        # 与读 XML 同理：清掉频率轴后必须立刻按新点数重画，否则频谱页会空着
        if self.iq is not None:
            self.draw_static()

    def set_max_load(self, txt):
        try:
            self.max_load = int(float(txt.replace("M", "")) * 1e6)
        except Exception:
            self.max_load = 2_000_000

    def bump_rate(self, factor):
        self.rate = min(64.0, max(0.05, self.rate * factor))
        self.root_w.set_rate_label(self.rate)

    # ---- 播放 ----
    def play(self):
        self.fs = self.fs_now()
        if self.iq is None:
            toast("先选一个 IQ 文件")
            return
        if self.fs <= 0:
            toast("采样率不合法（kHz）")
            return
        self.running = True
        Clock.unschedule(self._tick)
        Clock.schedule_interval(self._tick, 0.12)

    def pause(self):
        self.running = False
        Clock.unschedule(self._tick)

    def stop(self):
        self.running = False
        Clock.unschedule(self._tick)
        self.offset = 0
        self.avg = None
        self.maxh = None
        for k in ("waterfall", "persistence"):
            self.pages[k].plot.clear()
            self.pages[k].redraw()

    def draw_static(self):
        if self.iq is None:
            return
        self.fs = self.fs_now() or self.fs
        n = self.N_fft
        blk = np.asarray(self.iq[:min(n, self.iq.size)], dtype=np.complex128)
        self.avg = None
        self.maxh = None
        self._process_block(blk, readout=True, force=True)

    def _tick(self, _dt):
        if not self.running or self.iq is None:
            return
        n = self.N_fft
        if self.offset + n > self.iq.size:
            self.offset = 0
        blk = np.asarray(self.iq[self.offset:self.offset + n], dtype=np.complex128)
        self.offset += max(1, int(n * 0.5 * self.rate))
        self._process_block(blk, readout=True)

    def _process_block(self, blk, readout=True, force=False):
        if blk.size < 8:
            return
        fs = self.fs or self.fs_now() or 1.0
        fr, amp = D.calc_fft_block(blk, self.N_fft)
        if (force or self.freq_hz is None or self.freq_hz.size != amp.size
                or self._fs_cache != fs):
            self.freq_hz = fr * fs
            self._fs_cache = fs
            self.avg = None
            self.maxh = None
        if self.avg is None or self.avg.size != amp.size:
            self.avg = amp.copy()
            self.maxh = amp.copy()
        else:
            self.avg = 0.7 * self.avg + 0.3 * amp
            self.maxh = np.maximum(self.maxh, amp)

        sp = self.pages["spectrum"]
        sp.plot.set_data(self.freq_hz, amp, self.avg, self.maxh)
        sp.show_readout(amp)
        # 瀑布/余晖始终入队（切页时才有历史），但只重绘当前可见页
        self.pages["waterfall"].plot.push(amp)
        self.pages["persistence"].plot.push(amp)
        self._frame_i += 1
        self._redraw_visible(blk)

    def _redraw_visible(self, blk=None):
        cur = self.root_w.sm.current
        if cur == "spectrum":
            p = self.pages["spectrum"].plot
            p.freq_hz = self.freq_hz
            p.refresh_view()
            p.redraw()
        elif cur in ("waterfall", "persistence"):
            self.pages[cur].redraw()
        elif cur == "time" and blk is not None and self._frame_i % 4 == 0:
            self.pages["time"].draw()
        elif cur == "mod" and blk is not None and self._frame_i % 8 == 0:
            self.pages["mod"].draw()

    # ---- 导出 / 关于 ----
    def export_png(self):
        name = "iqplay_%s.png" % time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(default_work_dir(), name)
        try:
            Window.screenshot(name=path)
            toast("已保存 %s" % path)
        except Exception as e:
            toast("截图失败: %s" % e)

    def show_about(self):
        txt = (
            "IQ 信号综合分析仪 · 安卓版\n\n"
            "算法层: iqdsp.py（与桌面版 iqplay0911.py 逐位一致）\n"
            "界面: Kivy 触摸界面（频谱/瀑布/余晖/时域/调制）\n\n"
            "操作提示:\n"
            "  · 单指拖动 = 平移，双指捏合 = 缩放，双击 = 复位\n"
            "  · 顶部【文件】选 IQ；【自动XML】选中时选完文件自动读同名 XML 采样率\n"
            "  · 播放控制条里的『载入点数』决定大文件只读前多少点（省内存）\n\n"
            "本版按需求去掉了 B210 发射、URH 协议分析与信号源页。\n\n"
            "字体: %s\n工作目录: %s" % (FONT_USED or "未找到", default_work_dir()))
        Popup(title="关于", content=Label(text=txt, font_size=dp(11), halign="left",
                                          valign="top", color=C_FG),
              size_hint=(0.92, 0.8), title_color=C_FG,
              separator_color=C_ACCENT).open()

    def on_stop(self):
        Clock.unschedule(self._tick)
        try:
            self.audio.stop()
        except Exception:
            pass


def main():
    """统一入口：桌面直接跑本文件、或经 main.py（buildozer 约定的启动名）都走这里。"""
    IQApp().run()


if __name__ == "__main__":
    main()
