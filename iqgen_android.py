# -*- coding: utf-8 -*-
"""IQ 生成页（安卓版）—— 桌面 IQ生成器.py 的手机界面。

算法一份来源：`iqgen_dsp.py`（由 `_extract_iqgen.py` 从 IQ生成器.py 自动抽取，
改算法请改 IQ生成器.py 后重跑抽取脚本）。本文件只负责界面。

设计（手机屏幕窄，所以分层收纳）：
  · 顶部一条：选信号 / 当前信号名 / 参数 / 预览 / 导出 / 素材目录
  · 预览区：上半频谱、下半时域（复用 iqplay 的 SpectrumPlot / LinePlot）
  · 参数弹窗：常用参数（固定几个）+ **该制式专属参数（按 _FAMILY_FIELDS 只显示
    它真正需要的 1~5 个）** + 高级（TDMA / 带通 / 混合 / 跳频）+ 其它参数(k=v)
  · 生成/导出一律丢后台线程（大信号会跑几秒，绝不能卡 UI）

⚠️ 本文件**不能**在顶层 `from iqplay_android import ...` 具体名字之外的循环引用：
它在 iqplay_android 内部被**延迟导入**（见 IQApp.build 里的 import），此时
iqplay_android 已完全加载，所以顶层 `import iqplay_android as P` 是安全的。
"""
import os
import time

import numpy as np
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.togglebutton import ToggleButton
from kivy.uix.widget import Widget

import iqgen_dsp as G
import iqplay_android as P


# ---------------------------------------------------------------------------
# 参数字段的界面元数据
# ---------------------------------------------------------------------------
# 常用参数（固定显示，手机上一眼能改的那几个）
COMMON_FIELDS = [
    ("sample_rate", "采样率Hz"), ("duration", "时长s"),
    ("amplitude", "幅度"), ("symbol_rate", "符号率"),
    ("rolloff", "滚降"), ("M", "阶数M"),
    ("mu", "调制度mu"), ("dev", "频偏Hz"),
    ("carrier", "载波Hz"), ("tone", "音调Hz"),
    ("seed", "随机种子"), ("snr_db", "信噪比dB"),
]

# 中文标签表：没列到的字段直接显示键名（界面仍可用，只是不够好看）
FIELD_LABEL = {
    "mod_index": "调制指数", "bridge_bw": "网桥带宽", "bridge_mod": "网桥调制",
    "bridge_duty": "网桥占空", "bridge_frame": "网桥帧", "chip_rate": "码片速率",
    "ofdm_bw": "OFDM带宽", "ofdm_nused": "OFDM子载波", "ofdm_nfft": "OFDM点数",
    "ofdm_cp": "OFDM循环前缀", "ofdm_const": "OFDM星座",
    "lora_sf": "LoRa扩频因子", "lora_bw": "LoRa带宽",
    "mfsk_tones": "音数", "tone_spacing": "音间隔Hz",
    "sub_type": "副载波类型", "sub_fc": "副载波Hz", "sub_dev": "副载波频偏",
    "sub_depth": "副载波深度",
    "pulse_pri": "脉冲周期us", "pulse_pw": "脉冲宽度us", "pulse_fc": "脉冲载频Hz",
    "pp_spacing": "脉冲对间隔us", "pp_pw": "脉冲对宽度us",
    "pd_pri": "脉冲多普勒周期us", "pd_pw": "脉冲多普勒宽度us",
    "fh_band": "跳频带宽Hz", "fh_rate": "跳速跳/s", "fh_nch": "跳频频点数",
    "uwb_bw": "UWB带宽Hz", "uwb_prf": "UWB脉冲重复率",
    "rfid_tari": "RFID Tari", "rfid_blf": "RFID BLF",
    "nfc_rate": "NFC速率", "nfc_blf": "NFC BLF",
    "gmsk_sym": "GMSK符号率", "gmsk_bt": "GMSK BT", "gmsk_h": "GMSK 调制指数",
    "ais_bw": "AIS带宽", "atv_bw": "ATV带宽", "atv_fc": "ATV载频",
    "adsb_rate": "ADS-B速率", "radar": "雷达图案", "radar_spec": "雷达规格",
    "radar_taper": "雷达加权", "real_out": "输出实信号",
    "snr_mode": "SNR基准", "occupied_bw": "占用带宽Hz",
    "idle_head": "帧头静默s", "idle_tail": "帧尾静默s",
    "idle_head_f": "帧头静默段", "idle_tail_f": "帧尾静默段",
    "sym_period": "码元周期", "sym_pat": "码元图案",
    "chip_roll": "码片滚降", "msg_bw": "报文带宽", "msg_order": "报文阶数",
    "msg_pk": "报文峰均比", "msg_hp": "报文高通", "dev_shape": "频偏波形",
    "msg_mode": "报文模式", "msg_norm": "报文归一化", "auto_noise": "自动噪声",
    "morse_wpm": "摩斯WPM", "morse_fc": "摩斯载频Hz", "morse_text": "摩斯文本",
    "fmcw_bw": "FMCW带宽Hz", "fmcw_pri": "FMCW周期s", "fmcw_fc": "FMCW载频Hz",
    "fmcw_shape": "FMCW波形",
    "cdma_qpsk": "CDMA QPSK", "ssb_side": "SSB边带",
    "ssb_flo": "SSB下限Hz", "ssb_fhi": "SSB上限Hz",
    "th_frame": "跳时帧长s", "th_slot": "跳时时隙s", "th_duty": "跳时占空",
    "th_rate": "跳时速率", "burst_mod": "突发调制", "burst_frame": "突发帧长",
    "burst_nslot": "突发时隙数", "burst_duty": "突发占空", "burst_slot": "突发时隙",
    "fh_inner": "跳频内层调制", "hop_voice_type": "跳频话音类型",
    "vor_azimuth": "VOR方位", "ils_f90": "ILS 90Hz", "ils_f150": "ILS 150Hz",
    "ils_d90": "ILS DDM90", "ils_d150": "ILS DDM150",
    "vsb_vest": "VSB残留边带", "pm_index": "PM调制指数",
    "atsc_pilot": "ATSC导频", "time_fc": "授时载频Hz",
    "rds_fc": "RDS载频Hz", "rds_rate": "RDS速率",
    "selcal_f1": "SELCAL音1Hz", "selcal_f2": "SELCAL音2Hz",
    "selcal_seg": "SELCAL段长", "dop_fc": "多普勒载频Hz",
    "dop_freq": "多普勒频移Hz", "dop_rate": "多普勒速率", "dop_scatter": "多普勒散射",
    "replay_src": "素材来源", "replay_block": "素材块大小", "sps": "每符号样点",
}

# 高级区：固定显示这些分组（都是 EXTRA_KEYS 里的）
ADV_FIELDS = [
    ("slot_count", "TDMA时隙数"), ("slot_dur", "时隙ms"), ("slot_tx", "发射时隙"),
    ("bpf_fc", "带通中心Hz"), ("bpf_bw", "带通带宽Hz"), ("bpf_order", "带通阶数"),
    ("mix", "同频混合"),
    ("hop_on", "跳频开关"), ("hop_points", "跳频点数"), ("hop_band", "跳频带宽Hz"),
    ("hop_rate", "跳速跳/s"), ("hop_pattern", "跳频图案"),
]


# 「字段前缀 → 哪些制式用它」。
# 光靠 `_FAMILY_FIELDS` 不够：它只覆盖 26 个字段，而 EXTRA_KEYS 有 120 多个 ——
# 像 LoRa 的 lora_sf/lora_bw、GSM 的 gmsk_sym、MORSE 的 morse_wpm 都不在里面
# （桌面版是"所有字段都建控件、按 _FAMILY_FIELDS 决定启用/禁用"，手机放不下
#   120 个控件，所以这里用前缀把字段归属到制式，只显示该制式真正用得到的）。
PREFIX_MOD = {
    "lora": ("LORA",), "ofdm": ("NR5G", "DVBT", "ISDBT", "DRM", "NBIOT",
                                "LTEM", "FREEDV", "HDRADIO", "LTE", "DTMB",
                                "CDR", "DAB"),
    "morse": ("MORSE",), "fmcw": ("FMCW",),
    "mfsk": ("WSPR", "JT65", "OLIVIA", "16FSK"), "tone": ("WSPR", "JT65", "OLIVIA"),
    "sub": ("NOAAAPT", "SSTV", "FAX"),
    "pulse": ("PAM", "PWM", "PPM", "PULSEDOPPLER", "DME", "SSR"),
    "pp": ("PULSEPAIR",), "pd": ("PULSEDOPPLER",),
    "uwb": ("UWB",), "rfid": ("RFID",), "nfc": ("NFC",), "burst": ("BURST",),
    "fh": ("FREQHOP", "LINK16", "HAVEQUICK", "SINCGARS"),
    "hop": ("FREQHOP", "FASTHOP", "LINK16", "HAVEQUICK", "SINCGARS", "HOPVOICE"),
    "gmsk": ("GSM", "GMSK"), "ssb": ("SSB",), "atv": ("ATV",), "adsb": ("ADSB",),
    "radar": ("RADAR", "SAR"), "th": ("THSS",), "rds": ("RDS",),
    "dop": ("CWDOPPLER",), "selcal": ("SELCAL",), "time": ("TIME",),
    "ils": ("ILS",), "vor": ("VOR",), "ais": ("AIS",),
    "atsc": ("ATSC",), "vsb": ("VSB",), "cdma": ("DSCDMA",),
    "bridge": ("BRIDGE", "WIFI6", "WIFI7"),
    "chip": ("GPS", "GLONASS", "BEIDOU", "GALILEO", "ZIGBEE", "DSSS"),
    "replay": ("REPLAY",),
}


def field_label(key):
    return FIELD_LABEL.get(key, str(key))


def own_fields(mod):
    """该制式真正用得到的「专属字段」（手机界面只显示这些）。

    这是手机界面能做得下的关键：147 种调制一共 120 多个扩展键，但每种制式通常
    只用到 1~5 个。来源三处并集：
      1) `_FAMILY_FIELDS` 反查 —— 制式明确列出的字段；
      2) `MOD_DEFAULTS[mod]` 的键 —— 该制式的预置参数（如 LTE 的 ofdm_bw）；
      3) `PREFIX_MOD` 前缀归属 —— 覆盖 `_FAMILY_FIELDS` 漏掉的（lora_* / gmsk_* …）。
    已经在"常用参数"里出现的不重复显示。
    """
    modu = str(mod or "").upper()
    out, seen = [], set()

    def add(k):
        if k not in seen:
            seen.add(k)
            out.append(k)

    for key, mods in G._FAMILY_FIELDS.items():
        if mod in mods:
            add(key)
    for key in (G.MOD_DEFAULTS.get(modu) or {}):
        add(key)
    for key in sorted(G.EXTRA_KEYS):
        pref = str(key).split("_")[0].lower()
        mods = PREFIX_MOD.get(pref) or PREFIX_MOD.get(str(key).lower())
        if not mods:
            continue
        if mod in mods or modu in [str(m).upper() for m in mods]:
            add(key)

    common = set(k for k, _ in COMMON_FIELDS)
    return [k for k in out if k not in common]


def default_params(mod):
    """新选一个制式时该给的整套参数（DEFAULTS + 制式预置）。"""
    p = dict(G.DEFAULTS)
    p["mod"] = mod
    p["modulation"] = mod
    p["seed"] = ""
    try:
        G._fill_mod_defaults(mod, p)      # 制式预置（LTE 的 ofdm_bw 之类）
    except Exception as e:
        print("_fill_mod_defaults(%s) 失败: %s" % (mod, e))
    return p


def parse_extra(text):
    """把 "lora_sf=10,lora_bw=125000" 解析成字典（对应桌面版 --extra）。"""
    out = {}
    for item in str(text or "").replace("，", ",").split(","):
        item = item.strip()
        if not item or "=" not in item:
            continue
        k, v = item.split("=", 1)
        k, v = k.strip(), v.strip()
        if not k:
            continue
        try:
            out[k] = float(v)
        except ValueError:
            out[k] = v
    return out


def extra_to_text(p, skip):
    """把参数里"没有界面控件"的部分转成 k=v 文本（回填给"其它参数"输入框）。"""
    parts = []
    for k, v in sorted(p.items()):
        if k in skip or k in ("mod", "modulation"):
            continue
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            parts.append("%s=%g" % (k, v))
        elif isinstance(v, str) and v:
            parts.append("%s=%s" % (k, v))
    return ",".join(parts)


def fmt_hz(v):
    v = float(v or 0)
    for unit, div in (("G", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(v) >= div:
            return "%.4g%s" % (v / div, unit)
    return "%.6g" % v


def auto_filename(p):
    """导出文件名：制式_采样率_时长，如 QPSK_96k_2s。"""
    mod = str(p.get("mod") or "IQ")
    sr = fmt_hz(p.get("sample_rate") or 0)
    dur = "%.6g" % float(p.get("duration") or 0)
    bad = '\\/:*?"<>| ,'
    mod = "".join(c for c in mod if c not in bad)
    return "%s_%s_%ss" % (mod, sr, dur)


# ---------------------------------------------------------------------------
# 信号选择弹窗：搜索 + 按应用领域分组
# ---------------------------------------------------------------------------
class SignalPicker(Popup):
    def __init__(self, page, on_pick, **kw):
        self.page = page
        self.on_pick = on_pick
        super().__init__(title="选择信号（%d 种 + 素材库）"
                               % len(G.ALL_MODS), size_hint=(0.96, 0.94),
                         title_color=P.C_FG, separator_color=P.C_ACCENT, **kw)
        root = BoxLayout(orientation="vertical", spacing=dp(4), padding=dp(4))

        top = BoxLayout(size_hint_y=None, height=dp(34), spacing=dp(4))
        self.in_q = P.tinput("", w=dp(120), h=dp(30), hint="搜索信号名/关键词")
        self.in_q.size_hint_x = 1
        self.in_q.bind(text=lambda *_: self._fill())
        top.add_widget(self.in_q)
        top.add_widget(P.tbtn("搜索", lambda: self._fill(), dp(56)))
        root.add_widget(top)

        self.sv = ScrollView(bar_width=dp(22), bar_margin=dp(2),
                             bar_color=(1, 1, 1, .8), bar_inactive_color=(1, 1, 1, .4))
        # 手机上滑不动的三条必须都改（见 kivy-android-port 技能第 15 条）
        self.sv.scroll_type = ["bars", "content"]
        self.sv.scroll_timeout = 1200
        self.sv.scroll_distance = dp(8)
        self.box = BoxLayout(orientation="vertical", size_hint_y=None,
                             spacing=dp(6), padding=(dp(2), dp(2)))
        self.box.bind(minimum_height=self.box.setter("height"))
        self.sv.add_widget(self.box)
        root.add_widget(self.sv)

        bot = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6))
        bot.add_widget(Widget())
        bot.add_widget(P.tbtn("取消", lambda: self.dismiss(), dp(64)))
        root.add_widget(bot)

        self.content = root
        self._fill()

    def _fill(self):
        q = (self.in_q.text or "").strip().lower()
        self.box.clear_widgets()
        n = 0

        # ---- 素材库（扫描到的 signalwave 素材，可回放/参照）----
        lib = [nm for nm in G.catalog_names() if nm and not nm.endswith(".xml")]
        if lib:
            lib = [nm for nm in lib if not q or q in nm.lower()]
            if lib:
                n += len(lib)
                self.box.add_widget(self._head("素材库（%d）" % len(lib)))
                bar = P.CtrlBar(h=dp(32))
                for nm in lib[:120]:
                    bar.add(P.tbtn(nm[:16], lambda _b=None, x=nm: self._choose(x),
                                   dp(112), bg=(0.22, 0.30, 0.22, 1), fs=dp(11)))
                self.box.add_widget(bar)

        # ---- 调制方式：按应用领域(GROUP_DEFS)分组 ----
        grouped = {}
        for nm, _col, _short, mods in G.GROUP_DEFS:
            for m in mods:
                if not q or q in str(m).lower():
                    grouped.setdefault(nm, []).append(m)
        rest = [m for m in G.ALL_MODS
                if (not q or q in str(m).lower())
                and not any(m in mods for _n, _c, _s, mods in G.GROUP_DEFS)]
        if rest:
            grouped["其它"] = rest

        for gname, mods in grouped.items():
            if not mods:
                continue
            n += len(mods)
            self.box.add_widget(self._head("%s（%d）" % (gname, len(mods))))
            bar = P.CtrlBar(h=dp(32))
            for m in mods:
                bar.add(P.tbtn(str(m), lambda _b=None, x=m: self._choose(x),
                               dp(84), fs=dp(11)))
            self.box.add_widget(bar)

        if n == 0:
            self.box.add_widget(Label(text="没有匹配的信号", color=P.C_DIM,
                                      font_size=dp(12), size_hint_y=None,
                                      height=dp(30)))

    def _head(self, text):
        return Label(text=text, color=P.C_WARN, font_size=dp(12),
                     bold=True, size_hint_y=None, height=dp(22), halign="left",
                     valign="middle")

    def _choose(self, name):
        self.dismiss()
        self.on_pick(name)


# ---------------------------------------------------------------------------
# 参数弹窗：常用 + 制式专属 + 高级
# ---------------------------------------------------------------------------
class ParamDialog(Popup):
    def __init__(self, page, p, on_apply, **kw):
        self.page = page
        self.p = dict(p)
        self.on_apply = on_apply
        self.inputs = {}
        mod = str(p.get("mod") or "")
        super().__init__(title="参数 · %s" % mod, size_hint=(0.96, 0.94),
                         title_color=P.C_FG, separator_color=P.C_ACCENT, **kw)

        root = BoxLayout(orientation="vertical", spacing=dp(4), padding=dp(4))
        sv = ScrollView(bar_width=dp(22), bar_margin=dp(2),
                        bar_color=(1, 1, 1, .8), bar_inactive_color=(1, 1, 1, .4))
        sv.scroll_type = ["bars", "content"]
        sv.scroll_timeout = 1200
        sv.scroll_distance = dp(8)
        inner = BoxLayout(orientation="vertical", size_hint_y=None,
                          spacing=dp(8), padding=(dp(2), dp(4)))
        inner.bind(minimum_height=inner.setter("height"))

        # ---- 常用参数 ----
        inner.add_widget(self._head("常用参数"))
        bar = P.CtrlBar(h=dp(34))
        for key, lab in COMMON_FIELDS:
            if key in ("seed", "snr_db"):
                continue                     # 这两个放在"输出"组里更合适
            self._field(bar, key, lab)
        inner.add_widget(bar)

        # ---- 该制式的专属参数（按 _FAMILY_FIELDS 只显示需要的）----
        own = own_fields(mod)
        if own:
            inner.add_widget(self._head("「%s」专属参数（%d 个）" % (mod, len(own))))
            bar = P.CtrlBar(h=dp(34))
            for key in own:
                self._field(bar, key, field_label(key))
            inner.add_widget(bar)

        # ---- 高级（TDMA / 带通 / 混合 / 跳频）----
        inner.add_widget(self._head("高级"))
        bar = P.CtrlBar(h=dp(34))
        for key, lab in ADV_FIELDS:
            self._field(bar, key, lab, required=False)
        inner.add_widget(bar)

        # ---- 噪声与种子 ----
        inner.add_widget(self._head("噪声 / 随机"))
        bar = P.CtrlBar(h=dp(34))
        self._field(bar, "seed", "随机种子")
        self._field(bar, "snr_db", "信噪比dB")
        self._field(bar, "real_out", "输出实信号")
        inner.add_widget(bar)

        # ---- 其它参数（任何没控件的键都能在这里手填，对应 --extra）----
        inner.add_widget(self._head("其它参数（k=v，逗号分隔）"))
        skip = set(self.inputs) | {"mod", "modulation"}
        self.in_extra = P.tinput(extra_to_text(self.p, skip), w=dp(120), h=dp(30),
                                 hint="如 lora_sf=10,lora_bw=125000")
        self.in_extra.size_hint_x = 1
        w = BoxLayout(size_hint_y=None, height=dp(32), spacing=dp(4))
        w.add_widget(self.in_extra)
        inner.add_widget(w)
        hint = Label(text="上面没出现的参数都能写在这里；留空则按制式预置。",
                     color=P.C_DIM, font_size=dp(10), size_hint_y=None,
                     height=dp(18), halign="left")
        inner.add_widget(hint)

        sv.add_widget(inner)
        root.add_widget(sv)

        bot = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6))
        bot.add_widget(P.tbtn("按预置重置", self._reset, dp(90),
                              bg=(0.40, 0.30, 0.18, 1)))
        bot.add_widget(Widget())
        bot.add_widget(P.tbtn("取消", lambda: self.dismiss(), dp(64)))
        bot.add_widget(P.tbtn("应用", self._apply, dp(72),
                              bg=(0.20, 0.45, 0.25, 1)))
        root.add_widget(bot)
        self.content = root

    def _head(self, text):
        return Label(text=text, color=P.C_WARN, font_size=dp(12), bold=True,
                     size_hint_y=None, height=dp(22), halign="left",
                     valign="middle")

    def _field(self, bar, key, lab, required=True):
        if key in self.inputs:
            return
        v = self.p.get(key, "")
        if isinstance(v, float):
            txt = "%g" % v
        elif v is None:
            txt = ""
        else:
            txt = str(v)
        bar.add(P.tlabel(lab, fs=dp(11), w=dp(84)))
        ti = P.tinput(txt, w=dp(84), h=dp(28))
        self.inputs[key] = ti
        bar.add(ti)

    def _collect(self):
        """把界面上的输入收回参数字典（数字能转就转，空字符串表示不设）。"""
        self.p.pop("name", None)
        for key, ti in self.inputs.items():
            txt = (ti.text or "").strip()
            if txt == "":
                self.p.pop(key, None)
                continue
            try:
                val = float(txt)
                self.p[key] = int(val) if val.is_integer() and key in (
                    "M", "ofdm_nfft", "ofdm_nused", "mfsk_tones", "slot_count",
                    "hop_points", "bpf_order", "spf") else val
            except ValueError:
                self.p[key] = txt
        self.p.update(parse_extra(self.in_extra.text))

    def _reset(self):
        mod = str(self.p.get("mod") or "")
        fresh = default_params(mod)
        for key, ti in self.inputs.items():
            v = fresh.get(key, "")
            ti.text = "" if v is None else ("%g" % v if isinstance(v, float)
                                            else str(v))
        skip = set(self.inputs) | {"mod", "modulation"}
        self.in_extra.text = extra_to_text(fresh, skip)

    def _apply(self):
        self._collect()
        self.dismiss()
        self.on_apply(self.p)


# ---------------------------------------------------------------------------
# 主页面
# ---------------------------------------------------------------------------
class IQGenPage(P.PageBase):
    name = "iqgen"
    title = "IQ生成"

    def __init__(self, app, **kw):
        super().__init__(app, **kw)
        self.mod = "QPSK"
        self.params = default_params(self.mod)
        self.iq = None
        self.sr = 0.0
        self.busy = False

        bar = P.CtrlBar(h=dp(36))
        bar.add(P.tbtn("选信号", self.pick_signal, dp(64),
                       bg=(0.20, 0.35, 0.50, 1)))
        self.lb_mod = Label(text="QPSK", color=P.C_FG, font_size=dp(12),
                            shorten=True, shorten_from="right")
        bar.add(self.lb_mod)
        bar.add(P.tbtn("参数", self.edit_params, dp(52)))
        bar.add(P.tbtn("预览", self.do_preview, dp(52), bg=(0.22, 0.40, 0.55, 1)))
        bar.add(P.tbtn("导出", self.do_export, dp(52),
                       bg=(0.18, 0.45, 0.28, 1)))
        bar.add(P.tbtn("素材目录", self.choose_lib_dir, dp(76),
                       bg=(0.35, 0.30, 0.18, 1)))
        self.add_widget(bar)

        self.plot_spec = P.SpectrumPlot(size_hint=(1, 0.55))
        self.plot_spec.title = "预览频谱"
        self.plot_spec.xlabel = "频率"
        self.plot_spec.ylabel = "dB"
        self.add_widget(self.plot_spec)

        self.plot_time = P.LinePlot(colors=[P.C_RT, P.C_MAX, P.C_AVG], width=1.0,
                                    size_hint=(1, 0.45))
        self.plot_time.title = "预览时域 I / Q"
        self.plot_time.xlabel = "时间(ms)"
        self.plot_time.ylabel = "幅度"
        self.add_widget(self.plot_time)

        self.lb_info = Label(text="选一个信号 → 预览 / 导出",
                             color=P.C_DIM, font_size=dp(10),
                             size_hint_y=None, height=dp(22), halign="left",
                             valign="middle")
        self.lb_info.bind(width=lambda *_: setattr(
            self.lb_info, "text_size", (self.lb_info.width, None)))
        self.add_widget(self.lb_info)

        if G.ORIG_DIR and os.path.isdir(G.ORIG_DIR):
            G.refresh_library()
        self._update_lib_hint()

    # ---- 素材库 ----
    def choose_lib_dir(self):
        P.FileBrowser(self.app, self._set_lib_dir, dir_mode=True).open()

    def _set_lib_dir(self, path):
        G.ORIG_DIR = path
        G.refresh_library()
        self._update_lib_hint()
        P.toast("素材目录已设为 %s（%d 个条目）" % (path, len(G._SPECIAL)))

    def _update_lib_hint(self):
        n = len([x for x in G.catalog_names() if not x.endswith(".xml")])
        self.app._iqgen_lib_n = n

    # ---- 选信号 ----
    def pick_signal(self, *_):
        SignalPicker(self, self.set_mod).open()

    def set_mod(self, name):
        if not name:
            return
        self.mod = name
        self.params = default_params(name)
        self.lb_mod.text = name
        self.iq = None
        self.plot_spec.set_data(np.zeros(0), rt=np.zeros(0))
        self.plot_spec.redraw()
        self.lb_info.text = "已选 %s（%d 个专属参数）→ 预览 / 导出" % (
            name, len(own_fields(name)))
        self.do_preview()

    # ---- 参数 ----
    def edit_params(self, *_):
        ParamDialog(self, self.params, self._apply_params).open()

    def _apply_params(self, p):
        self.params = p
        self.mod = str(p.get("mod") or self.mod)
        self.lb_mod.text = self.mod
        self.do_preview()

    # ---- 预览 ----
    def do_preview(self, *_):
        if self.busy:
            P.toast("正在处理，请稍候")
            return
        self.busy = True
        self.lb_info.text = "生成预览中…"
        p = dict(self.params)

        def work():
            t0 = time.time()
            iq, sr, note = G.build_preview(p)
            nfft = 2048
            fr, psd = G.compute_spectrum(iq, sr, nfft)
            bw = G.occupied_bandwidth(iq, sr, 0.99, max_len=8192)
            n = min(int(iq.size), 4000)
            seg = iq[:n]
            t_ms = np.arange(n, dtype=np.float64) * 1000.0 / max(sr, 1.0)
            return (iq, sr, note, fr, psd, bw, t_ms, seg,
                    np.abs(seg), time.time() - t0)

        P.run_bg(work, self._preview_done, self._fail)

    def _preview_done(self, res):
        (iq, sr, note, fr, psd, bw, t_ms, seg, env, dt) = res
        self.busy = False
        self.sr = sr
        self.iq = iq

        sp = self.plot_spec
        sp.set_data(fr, rt=psd, avg=None, maxh=None)
        sp.refresh_view(force=True)
        sp.redraw()

        lp = self.plot_time
        lp.set_series(0, np.real(seg), x=t_ms)
        lp.set_series(1, np.imag(seg), x=t_ms)
        lp.show_series(2, False)
        lo = min(float(np.min(np.real(seg))), float(np.min(np.imag(seg))))
        hi = max(float(np.max(np.real(seg))), float(np.max(np.imag(seg))))
        if hi - lo < 1e-9:
            lo, hi = lo - 0.1, hi + 0.1
        lp.set_view([float(t_ms[0]), float(t_ms[-1])], [lo * 1.05, hi * 1.05],
                    remember=True)
        lp.redraw()

        self.lb_info.text = ("预览 %d 点 @ %s ｜ 占用带宽 %s ｜ %.2fs%s"
                             % (iq.size, fmt_hz(sr), fmt_hz(bw), dt,
                                ("　" + note) if note else ""))

    def _fail(self, exc):
        self.busy = False
        self.lb_info.text = "失败：%s" % exc
        P.toast("生成失败：%s" % exc)

    # ---- 导出 ----
    def out_dir(self):
        for d in ("/storage/emulated/0/Download", "/sdcard/Download",
                  "/storage/emulated/0", os.path.expanduser("~"), P.APP_DIR):
            try:
                if os.path.isdir(d) and os.access(d, os.W_OK):
                    sub = os.path.join(d, "IQ生成")
                    os.makedirs(sub, exist_ok=True)
                    return sub
            except Exception:
                continue
        return P.APP_DIR

    def do_export(self, *_):
        if self.busy:
            P.toast("正在处理，请稍候")
            return
        self.busy = True
        self.lb_info.text = "生成并写盘中…"
        p = dict(self.params)
        out = self.out_dir()
        name = auto_filename(p)
        path = os.path.join(out, name + ".cs16")

        def work():
            t0 = time.time()
            iq = G.build_iq(dict(p))
            G.write_cs16(iq, path)
            G.write_xml(path, float(p.get("sample_rate") or 0.0), dict(p))
            return (path, int(iq.size), time.time() - t0)

        P.run_bg(work, self._export_done, self._fail)

    def _export_done(self, res):
        path, n, dt = res
        self.busy = False
        size = os.path.getsize(path) if os.path.exists(path) else 0
        self.lb_info.text = ("已导出 %s（%d 点，%s，%.1fs）"
                             % (os.path.basename(path), n,
                                P.fmt_size(size), dt))
        P.toast("已保存到 %s" % path)

    def redraw(self):
        # ⚠️ 只有 SpectrumPlot / ImagePlot 实现了 refresh_view()，基类 `PlotBase`
        # 里它是个**抽象方法**（直接 `raise NotImplementedError`）——
        # LinePlot 没有实现，切页时调它会让整个界面崩在 on_shown 里。
        # LinePlot 的视图范围在 _preview_done 里已经用 set_view(remember=True) 定好。
        self.plot_spec.refresh_view()
        self.plot_spec.redraw()
        self.plot_time.redraw()
