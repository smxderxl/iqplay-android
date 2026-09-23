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



DIGITAL_FAMILIES = ["ASK", "2FSK", "4FSK", "8FSK", "16FSK", "CP4FSK", "BPSK", "QPSK",
                     "8PSK", "16PSK", "OQPSK",
                     "16QAM", "32QAM", "64QAM", "256QAM", "1024QAM", "4096QAM",
                     "16APSK", "32APSK", "64APSK",
                     "MSK", "DPSK", "Pi4DQPSK"]

ANALOG_MODS = ["AM", "FM"]

EXTENDED_MODS = [
    # ---------- 广播 / 电视 ----------
    "LTE", "DTMB", "CDR", "DAB", "DVBT", "DVBS", "DVBS2", "DVBC", "ATSC",
    "ATSC3", "ISDBT", "DRM", "HDRADIO", "ATV", "RDS",
    # ---------- 蜂窝移动通信 / 车联网 (2G/3G/4G/5G/物联网) ----------
    "GSM", "CDMA", "WCDMA", "NR5G", "NBIOT", "LTEM", "GSMR", "DSRC",
    # ---------- 无线局域网 / 个域网 / 短距 / 抄表 ----------
    "BRIDGE", "WIFI6", "WIFI7", "BT", "ZIGBEE", "ZWAVE", "UWB", "DECT",
    "LORA", "SIGFOX", "RFID", "NFC", "RKE", "TPMS",
    "THREAD", "WISUN", "WMBUS",
    # ---------- 专业集群 / 数字对讲 ----------
    "TETRA", "DMR", "PDT", "P25", "NXDN", "DPMR", "DSTAR", "C4FM", "M17", "FREEDV",
    # ---------- 航空 / 航海 ----------
    "ADSB", "ACARS", "VDL2", "VDL4", "VOR", "ILS", "DME", "SSR", "NDB",
    "SELCAL", "AIS", "NAVTEX", "DSC",
    # ---------- 卫星导航 / 卫星通信 / 气象 ----------
    "GPS", "GLONASS", "BEIDOU", "GALILEO", "NAVIC", "QZSS", "SBAS",
    "IRIDIUM", "INMARSAT", "NOAAAPT", "METEOR", "SAT", "CCSDS", "VSAT",
    "RADIOSONDE",
    # ---------- 短波 / 业余无线电 ----------
    "SSB", "MORSE", "RTTY", "PSK31", "FT8", "FT4", "FSK441", "ALE", "MT63",
    "AMTOR", "SSTV", "FAX", "WSPR", "JT65", "OLIVIA", "HELL",
    # ---------- 军用电台 / 数据链 ----------
    "LINK16", "HAVEQUICK", "SINCGARS",
    # ---------- 寻呼 / 雷达 ----------
    "POCSAG", "FLEX", "FREQHOP", "RADAR", "FMCW", "PULSEDOPPLER", "SAR",
    "CWDOPPLER", "PI4PSK",
    # ---------- 授时 / 时间信号 ----------
    "TIME",
    # ---------- 调制基元: 模拟补充 / 扩频 / 脉冲 ----------
    "DSBSC", "VSB", "PM", "NBFM", "WBFM",
    "SOQPSK", "GFSK", "GMSK", "DSSS", "THSS",
    "PAM", "PWM", "PPM",
]

SIMPLE_MODS = ["CW", "AWGN"]

ALL_MODS = DIGITAL_FAMILIES + ANALOG_MODS + EXTENDED_MODS + SIMPLE_MODS

GROUP_DEFS = [
    ("模拟调制", "#B45309", "模拟",
     ["AM", "FM", "PM", "NBFM", "WBFM", "SSB", "DSBSC", "VSB", "ATV"]),
    ("数字基带", "#6D28D9", "数字",
     ["ASK", "2FSK", "4FSK", "8FSK", "16FSK", "CP4FSK", "MSK", "GMSK", "GFSK",
      "BPSK", "QPSK", "8PSK", "16PSK", "OQPSK", "SOQPSK", "Pi4DQPSK",
      "Pi4PSK", "DPSK",
      "16QAM", "32QAM", "64QAM", "256QAM", "1024QAM", "4096QAM",
      "16APSK", "32APSK", "64APSK",
      "PAM", "PWM", "PPM"]),
    ("扩频", "#0891B2", "扩频",
     ["DSSS", "THSS", "FREQHOP", "LORA", "SIGFOX", "UWB", "CDMA"]),
    ("蜂窝物联", "#DC2626", "蜂窝",
     ["GSM", "WCDMA", "NR5G", "LTE", "NBIOT", "LTEM", "GSMR", "DSRC"]),
    ("广播电视", "#DB2777", "广视",
     ["DAB", "DVBT", "DVBS", "DVBS2", "DVBC", "ATSC", "ATSC3", "ISDBT",
      "DRM", "HDRADIO", "CDR", "DTMB", "RDS"]),
    ("短距无线", "#059669", "短距",
     ["BRIDGE", "WIFI6", "WIFI7", "BT", "ZIGBEE", "ZWAVE", "DECT",
      "RFID", "NFC", "RKE", "TPMS", "THREAD", "WISUN", "WMBUS"]),
    ("集群对讲", "#2563EB", "集群",
     ["TETRA", "DMR", "PDT", "P25", "NXDN", "DPMR", "DSTAR", "C4FM", "M17",
      "FREEDV"]),
    ("航空航海", "#0369A1", "航空",
     ["ADSB", "ACARS", "VDL2", "VDL4", "VOR", "ILS", "DME", "SSR", "NDB",
      "SELCAL", "AIS", "NAVTEX", "DSC"]),
    ("卫星导航", "#7C3AED", "卫星",
     ["GPS", "GLONASS", "BEIDOU", "GALILEO", "NAVIC", "QZSS", "SBAS",
      "IRIDIUM", "INMARSAT", "NOAAAPT", "METEOR", "SAT", "CCSDS", "VSAT",
      "RADIOSONDE"]),
    ("业余短波", "#65A30D", "业余",
     ["MORSE", "RTTY", "PSK31", "FT8", "FT4", "FSK441", "ALE", "MT63",
      "AMTOR", "SSTV", "FAX", "WSPR", "JT65", "OLIVIA", "HELL"]),
    ("军用电台", "#B91C1C", "军用",
     ["LINK16", "HAVEQUICK", "SINCGARS"]),
    ("雷达寻呼", "#EA580C", "雷达",
     ["RADAR", "FMCW", "PULSEDOPPLER", "SAR", "CWDOPPLER",
      "POCSAG", "FLEX"]),
    ("授时其它", "#475569", "其它",
     ["TIME", "CW", "AWGN", "REPLAY"]),
]

GROUP_COLOR = {nm: col for nm, col, _s, _m in GROUP_DEFS}

GROUP_SHORT = {nm: sh for nm, _c, sh, _m in GROUP_DEFS}

SIG_GROUP = {}

PANEL_TITLES = {
    "top":  "信号选择",
    "box":  "基本参数",
    "br":   "网桥(OFDM)参数",
    "sp":   "专用参数 (SSB·莫尔斯·FMCW·OFDM)",
    "src":  "信源",
    "outf": "输出设置",
    "slf":  "时隙 (TDMA)",
    "bpf":  "输出滤波 (带通)",
    "mix":  "同频混合 (多路信号同频叠加)",
    "fh":   "跳频发射 (对任意信号跳频)",
    "pv":   "预览  功率谱 / 时域 I-Q / 星座图  (整幅大图, 尺寸可调)",
    "log":  "日志",
}

PANEL_LABELS = {
    "top": "信号选择", "box": "基本参数", "br": "网桥参数", "sp": "专用参数",
    "src": "信源", "outf": "输出设置", "slf": "时隙TDMA", "bpf": "输出滤波",
    "mix": "同频混合", "fh": "跳频发射", "pv": "预览", "log": "日志",
}

PSK_MODS = ["BPSK", "QPSK", "8PSK", "16PSK", "DPSK", "Pi4DQPSK", "Pi4PSK"]

BASE_KEYS = {"mod", "name", "modulation", "sample_rate", "duration", "amplitude",
             "symbol_rate", "rolloff", "M", "mu", "dev", "carrier", "tone",
             "seed", "source_mode", "bits", "audio_path", "occupied_bw",
             "snr_db", "snr_mode"}

EXTRA_KEYS = {"lora_sf", "lora_bw", "chip_rate", "ofdm_bw", "ofdm_nused", "duplex",
              "gmsk_sym", "gmsk_bt", "ais_bw", "atv_bw", "atv_fc", "adsb_rate",
              "fh_band", "fh_rate", "radar", "snr_db", "snr_mode",
              "idle_head", "idle_tail", "idle_head_f", "idle_tail_f", "sym_period",
              "sym_pat", "chip_roll",               "radar_spec", "radar_taper", "real_out",
              "msg_bw", "msg_order", "msg_pk", "msg_hp", "dev_shape",
              "msg_mode", "msg_norm", "auto_noise",
              "slot_count", "slot_dur", "slot_tx", "slot_ramp",
              "bpf_fc", "bpf_bw", "bpf_order", "bpf_flo", "bpf_fhi",
              "mix", "foff", "amp",
              "replay_src", "replay_block", "sps",
              "bridge_bw", "bridge_mod", "bridge_duty", "bridge_frame",
              # --- 新增真实世界信号的参数 ---
              "ofdm_nfft", "ofdm_cp", "ofdm_const",
              "cdma_qpsk", "ssb_side", "ssb_flo", "ssb_fhi",
              "morse_wpm", "morse_fc", "morse_text",
              "fmcw_bw", "fmcw_pri", "fmcw_fc", "fmcw_shape",
              # --- 第二批: 多音MFSK / 副载波 / 脉冲 / 跳时 / UWB / 突发 ---
              "mfsk_tones", "tone_spacing",
              "sub_type", "sub_fc", "sub_dev", "sub_depth",
              "pulse_pri", "pulse_pw", "pulse_fc",
              "th_frame", "th_slot", "th_duty", "th_rate",
              "pp_spacing", "pp_pw", "pp_rate", "pp_fc",
              "pd_pri", "pd_pw", "pd_fc", "pd_chirp",
              "uwb_bw", "uwb_prf",
              "rfid_tari", "rfid_blf", "nfc_rate", "nfc_blf",
              "burst_mod", "burst_frame", "burst_nslot", "burst_duty", "burst_slot",
              "fh_nch", "fh_inner", "hop_voice_type",
              "vor_azimuth", "ils_f90", "ils_f150", "ils_d90", "ils_d150",
              "gmsk_h", "vsb_vest", "pm_index", "atsc_pilot", "M",
              # --- 第三批: 授时 / RDS / SELCAL / CW 多普勒 ---
              "time_fc", "rds_fc", "rds_rate",
              "selcal_f1", "selcal_f2", "selcal_seg",
              "dop_fc", "dop_freq", "dop_rate", "dop_scatter",
              # --- 通用跳频发射模块 ---
              "hop_on", "hop_pattern", "hop_points", "hop_band", "hop_rate",
              "hop_guard", "hop_foff", "hop_cphase", "hop_seq", "hop_preset"}

DEFAULTS = {
    "sample_rate": 96000, "duration": 2.0, "amplitude": 0.8,
    "symbol_rate": 6000, "rolloff": 0.35, "M": 2,
    "mu": 0.5, "dev": 5000, "carrier": 10000, "tone": 1000
}

MOD_DEFAULTS = {
    "LTE":  {"ofdm_bw": 8.9e6, "sample_rate": 15360000.0},
    "DTMB": {"ofdm_bw": 7.5e6, "sample_rate": 30240000.0},
    "CDR":  {"ofdm_bw": 400e3, "sample_rate": 816000.0},
    "GSM":  {"gmsk_sym": 270833.0, "gmsk_bt": 0.5, "sample_rate": 1280000.0},
    "AIS":  {"gmsk_sym": 9600.0, "gmsk_bt": 0.5, "ais_bw": 108e3,
             "sample_rate": 230400.0},
    "ATV":  {"atv_bw": 8e6, "sample_rate": 19200000.0},
    "ADSB": {"adsb_rate": 1e6, "sample_rate": 8000000.0},
    "BRIDGE": {"bridge_bw": 20e6, "bridge_mod": "QPSK", "sample_rate": 20000000.0},

    # ---- 修正: 以下几种原本没有预置采样率, 默认 96 kHz 远低于其真实带宽,
    #      生成的频谱被折叠/占满全频带 (GPS 1.023 Mcps、LoRa 125 kHz 都是这样) ----
    # GPS L1 C/A: 码片 1.023 Mcps, 主瓣 2.046 MHz
    "GPS":    {"chip_rate": 1.023e6, "sample_rate": 5000000.0},
    # GLONASS: FDMA, 码片 511 kcps
    "GLONASS": {"chip_rate": 511e3, "sample_rate": 2500000.0},
    # 北斗 B1I: 码片 2.046 Mcps
    "BEIDOU": {"chip_rate": 2.046e6, "sample_rate": 8000000.0},
    # LoRa: 带宽 125 kHz (另有 250/500 kHz), 扩频因子 SF7~SF12
    "LORA":   {"lora_bw": 125e3, "lora_sf": 10, "sample_rate": 256000.0},
    # 跳频: 跳频带宽必须落在采样率内, 原来默认 5 MHz 带 / 96 kHz 采样必然混叠
    "FREQHOP": {"fh_band": 1.0e6, "fh_rate": 1000.0, "sample_rate": 4000000.0},
    # DAB / DAB+: 1.536 MHz 信道, 1536 个子载波, 采样率 2.048 MHz, GI=504/2048
    "DAB":    {"ofdm_bw": 1.536e6, "ofdm_nfft": 2048, "ofdm_nused": 1536,
               "ofdm_cp": 504.0 / 2048.0, "sample_rate": 2048000.0},

    # ================= 新增: 真实世界常见信号 (标准参数预置) =================
    # 5G NR: CP-OFDM, 20 MHz 载波 / mu=0 (子载波间隔 15 kHz, Nfft=2048,
    #        1200 个占用子载波 -> 实际占用 18 MHz), 采样率 30.72 MHz
    "NR5G":   {"ofdm_bw": 20e6, "ofdm_nfft": 2048, "ofdm_cp": 0.07,
               "ofdm_nused": 1200, "ofdm_const": "64QAM",
               "sample_rate": 30720000.0},
    # DVB-T/T2: COFDM 8 MHz 信道, 2k 模式 (1705 个子载波), GI=1/8,
    #          标准采样率 64/7 MHz = 9.142857 MHz
    "DVBT":   {"ofdm_bw": 8e6, "ofdm_nfft": 2048, "ofdm_cp": 0.125,
               "ofdm_nused": 1705, "ofdm_const": "64QAM",
               "sample_rate": 9142857.0},
    # WCDMA (3G UMTS): 码片 3.84 Mcps, 5 MHz 信道, RRC 滚降 0.22
    "WCDMA":  {"chip_rate": 3.84e6, "rolloff": 0.22, "sample_rate": 15360000.0},
    # CDMA (IS-95 / cdma2000 1x): 码片 1.2288 Mcps, 1.25 MHz 信道
    "CDMA":   {"chip_rate": 1.2288e6, "rolloff": 0.22, "sample_rate": 5000000.0},
    # TETRA: pi/4-DQPSK, 18 ksym/s (36 kbit/s), 25 kHz 信道, 滚降 0.35
    "TETRA":  {"symbol_rate": 18000.0, "rolloff": 0.35, "sample_rate": 96000.0},
    # DMR / PDT: 4FSK 连续相位, 4.8 ksym/s (9.6 kbit/s), 12.5 kHz 信道,
    #            调制指数 h=0.27 -> 频偏 +-648 / +-1944 Hz (PDT 为国产警用标准)
    "DMR":    {"symbol_rate": 4800.0, "mod_index": 0.27, "rolloff": 0.2,
               "sample_rate": 48000.0},
    "PDT":    {"symbol_rate": 4800.0, "mod_index": 0.27, "rolloff": 0.2,
               "sample_rate": 48000.0},
    # P25 Phase1 C4FM: 4FSK, 4.8 ksym/s, 频偏 +-600 / +-1800 Hz
    "P25":    {"symbol_rate": 4800.0, "dev": 600.0, "rolloff": 0.2,
               "sample_rate": 48000.0},
    # POCSAG 寻呼: 2FSK, 512/1200/2400 baud, 频移 +-4.5 kHz
    "POCSAG": {"symbol_rate": 1200.0, "dev": 2250.0, "rolloff": 0.35,
               "sample_rate": 48000.0},
    # ZigBee 802.15.4: O-QPSK + 半正弦成形, 2 Mchip/s (250 kbit/s)
    "ZIGBEE": {"chip_rate": 2.0e6, "sample_rate": 8000000.0},
    # 蓝牙 (BR/EDR + BLE): GFSK, 1 Msym/s, BT=0.5, 频偏约 +-185 kHz
    "BT":     {"gmsk_sym": 1.0e6, "gmsk_bt": 0.5, "sample_rate": 8000000.0},
    # SSB 短波单边带话音: 300~3000 Hz, 抑制载波
    "SSB":    {"ssb_side": "USB", "msg_bw": 3000.0, "ssb_flo": 300.0,
               "sample_rate": 48000.0},
    # ACARS 飞机数据链: MSK 2400 baud (VHF 131.55 MHz 等)
    "ACARS":  {"symbol_rate": 2400.0, "rolloff": 0.35, "sample_rate": 48000.0},
    # NAVTEX 航海电传: FSK 100 baud, 频移 170 Hz (518 kHz)
    "NAVTEX": {"symbol_rate": 100.0, "dev": 85.0, "rolloff": 0.35,
               "sample_rate": 48000.0},
    # 莫尔斯等幅报 (CW/A1A): 20 WPM, 800 Hz 差拍音调
    "MORSE":  {"morse_wpm": 20.0, "morse_fc": 800.0, "sample_rate": 48000.0},
    # FMCW 调频连续波雷达 (车载 77 GHz 典型): 扫频带宽 150 MHz, 周期 1 ms
    "FMCW":   {"fmcw_bw": 150e6, "fmcw_pri": 1e-3, "fmcw_shape": "saw",
               "sample_rate": 200000000.0},
    # 伽利略导航: E1 码片 1.023 Mcps (与 GPS L1 同码率)
    "GALILEO": {"chip_rate": 1.023e6, "sample_rate": 5000000.0},
    # 业余无线电数字模式
    "RTTY":   {"symbol_rate": 45.45, "dev": 85.0, "rolloff": 0.35,
               "sample_rate": 48000.0},     # 45.45 baud, 频移 170 Hz
    "PSK31":  {"symbol_rate": 31.25, "rolloff": 0.35,
               "sample_rate": 48000.0},     # BPSK 31.25 baud
    "FT8":    {"symbol_rate": 6.25, "dev": 3.125, "rolloff": 0.35,
               "sample_rate": 8000.0},      # 8-FSK, 音隔 6.25 Hz, 6.25 baud

    # ================= 第二批: 调制基元补全 =================
    # 高阶 QAM (Wi-Fi 5/6/7, DVB-C, 有线电视)
    "256QAM":  {"symbol_rate": 1.0e6, "rolloff": 0.35, "sample_rate": 8000000.0},
    "1024QAM": {"symbol_rate": 1.0e6, "rolloff": 0.35, "sample_rate": 8000000.0},
    "4096QAM": {"symbol_rate": 1.0e6, "rolloff": 0.35, "sample_rate": 8000000.0},
    # APSK (卫星通信 DVB-S2/S2X, 峰均比低于同阶 QAM)
    "16APSK":  {"symbol_rate": 1.0e6, "rolloff": 0.35, "sample_rate": 8000000.0},
    "32APSK":  {"symbol_rate": 1.0e6, "rolloff": 0.35, "sample_rate": 8000000.0},
    "64APSK":  {"symbol_rate": 1.0e6, "rolloff": 0.35, "sample_rate": 8000000.0},
    # O-QPSK: Q 路错开半个符号, 包络起伏小于 QPSK
    "OQPSK":   {"symbol_rate": 100e3, "rolloff": 0.35, "sample_rate": 800000.0},
    # SOQPSK: IRIG-106 靶场遥测标准, 恒包络, 占用 ≈1.2·Rs
    "SOQPSK":  {"symbol_rate": 5.0e6, "sample_rate": 40000000.0},
    # GFSK: 通用高斯频移键控 (蓝牙 BR/EDR h=0.32, DECT h=0.5)
    "GFSK":    {"gmsk_sym": 1.0e6, "gmsk_bt": 0.5, "gmsk_h": 0.32,
                "sample_rate": 8000000.0},
    # 16 音 MFSK
    "16FSK":   {"symbol_rate": 4800.0, "dev": 1200.0, "rolloff": 0.35,
                "sample_rate": 48000.0},
    # DSSS 直接序列扩频 (802.11b 1/2 Mbps 就是 11 Mchip/s 的 DSSS)
    "DSSS":    {"chip_rate": 11.0e6, "rolloff": 0.35, "sample_rate": 44000000.0},
    # THSS 跳时扩频
    "THSS":    {"th_frame": 2e-3, "th_slot": 8, "th_duty": 0.25,
                "th_rate": 200e3, "sample_rate": 2000000.0},
    # 脉冲调制三兄弟
    "PAM":     {"pulse_pri": 100e-6, "pulse_pw": 10e-6, "msg_bw": 5000.0,
                "sample_rate": 2000000.0},
    "PWM":     {"pulse_pri": 50e-6, "pulse_pw": 25e-6, "msg_bw": 2000.0,
                "sample_rate": 2000000.0},
    "PPM":     {"pulse_pri": 2e-3, "pulse_pw": 300e-6, "msg_bw": 200.0,
                "sample_rate": 2000000.0},
    # 模拟调制补充
    "DSBSC":   {"msg_bw": 4000.0, "sample_rate": 48000.0},
    "VSB":     {"msg_bw": 4.2e6, "vsb_vest": 1.25e6, "sample_rate": 12000000.0},
    "PM":      {"pm_index": 1.5, "msg_bw": 3000.0, "sample_rate": 48000.0},
    # msg_norm="rms": 素材实测 FM 的瞬时频率 std ≈ dev, 对应的就是 rms 归一化
    "NBFM":    {"dev": 2500.0, "msg_bw": 3000.0, "msg_norm": "rms",
                "sample_rate": 48000.0},
    "WBFM":    {"dev": 75000.0, "msg_bw": 53000.0, "msg_norm": "rms",
                "sample_rate": 384000.0},

    # ================= 第二批: 真实制式 =================
    # --- 广播 / 电视 ---
    # DVB-S2 卫星电视: 16APSK / 32APSK, 典型符号率 27.5 MSym/s (33 MHz 转发器)
    "DVBS2":   {"symbol_rate": 27.5e6, "rolloff": 0.20, "sample_rate": 40000000.0},
    # DVB-C 有线电视: 64/256QAM, 6.9 MSym/s -> 占用 7.94 MHz (8 MHz 频道)
    "DVBC":    {"symbol_rate": 6.9e6, "rolloff": 0.15, "sample_rate": 16000000.0},
    # ATSC 8-VSB: 8 电平 PAM 10.762 MSym/s, 残留 0.31 MHz -> 占用 5.69 MHz
    "ATSC":    {"symbol_rate": 10.762e6, "rolloff": 0.115, "vsb_vest": 0.31e6,
                "atsc_pilot": -2.69e6, "sample_rate": 24000000.0},
    # ISDB-T (日/巴): 6 MHz 频道, 2k 模式 1405 个子载波
    "ISDBT":   {"ofdm_bw": 5.57e6, "ofdm_nfft": 2048, "ofdm_cp": 0.125,
                "ofdm_nused": 1405, "ofdm_const": "64QAM",
                "sample_rate": 8127000.0},
    # DRM 短波数字广播: 9 kHz 频道, COFDM
    "DRM":     {"ofdm_bw": 8.6e3, "ofdm_nfft": 256, "ofdm_cp": 0.2,
                "ofdm_const": "16QAM", "sample_rate": 12000.0},
    # HD Radio (IBOC): 中间模拟 FM + 两侧数字 OFDM 边带, 结构与 CDR 同构
    "HDRADIO": {"ofdm_bw": 400e3, "cdr_fm_dev": 75e3, "cdr_side_bw": 69e3,
                "cdr_fm_bw": 15e3, "sample_rate": 1024000.0},
    # --- 蜂窝 / 物联网 ---
    # NB-IoT: 12 个子载波 × 15 kHz = 180 kHz
    "NBIOT":   {"ofdm_bw": 180e3, "ofdm_nfft": 128, "ofdm_cp": 0.0833,
                "ofdm_nused": 12, "ofdm_const": "QPSK", "sample_rate": 1920000.0},
    # LTE-M (eMTC) 1.4 MHz: 72 个子载波
    "LTEM":    {"ofdm_bw": 1.08e6, "ofdm_nfft": 128, "ofdm_cp": 0.0833,
                "ofdm_nused": 72, "ofdm_const": "QPSK", "sample_rate": 1920000.0},
    # --- 无线局域网 / 个域网 ---
    "WIFI6":   {"bridge_bw": 80e6, "bridge_mod": "1024QAM", "bridge_duty": 0.6,
                "sample_rate": 100000000.0},     # 802.11ax
    "WIFI7":   {"bridge_bw": 160e6, "bridge_mod": "4096QAM", "bridge_duty": 0.6,
                "sample_rate": 200000000.0},     # 802.11be
    # Z-Wave (868/908 MHz 智能家居): FSK ±20 kHz
    "ZWAVE":   {"symbol_rate": 9600.0, "dev": 20000.0, "rolloff": 0.35,
                "sample_rate": 200000.0},
    # HRP-UWB 802.15.4z (带宽按 80 MHz 缩比建模, 真实 499.2 MHz 需 ≥1 GS/s)
    "UWB":     {"uwb_bw": 80e6, "uwb_prf": 64e6, "sample_rate": 200000000.0},
    # DECT 数字无绳电话: GFSK 1.152 Msym/s, 帧 10 ms / 24 时隙
    "DECT":    {"burst_mod": "GFSK", "gmsk_sym": 1.152e6, "gmsk_bt": 0.5,
                "gmsk_h": 0.5, "burst_frame": 10e-3, "burst_nslot": 24,
                "burst_duty": 0.85, "sample_rate": 4000000.0},
    # Sigfox 超窄带: 100 bit/s DBPSK
    "SIGFOX":  {"symbol_rate": 100.0, "rolloff": 0.35, "sample_rate": 8000.0},
    # --- RFID / NFC / 车钥匙 / 胎压 ---
    "RFID":    {"rfid_tari": 12.5e-6, "rfid_blf": 160e3, "sample_rate": 2000000.0},
    "NFC":     {"nfc_rate": 106e3, "nfc_blf": 848e3, "sample_rate": 8000000.0},
    "RKE":     {"symbol_rate": 2000.0, "rolloff": 0.35, "M": 2, "mu": 1.0,
                "sample_rate": 48000.0},        # 433 MHz 车钥匙 OOK
    "TPMS":    {"symbol_rate": 19200.0, "rolloff": 0.35, "M": 2, "mu": 1.0,
                "sample_rate": 200000.0},       # 胎压监测 ASK/OOK
    # --- 专业集群 / 数字对讲 ---
    "NXDN":    {"symbol_rate": 4800.0, "dev": 350.0, "rolloff": 0.2,
                "sample_rate": 48000.0},        # 6.25 kHz 4FSK
    "DPMR":    {"symbol_rate": 2400.0, "dev": 438.0, "rolloff": 0.2,
                "sample_rate": 48000.0},        # dPMR446 6.25 kHz 4FSK
    "DSTAR":   {"gmsk_sym": 4800.0, "gmsk_bt": 0.5, "sample_rate": 48000.0},
    "C4FM":    {"symbol_rate": 4800.0, "dev": 583.0, "rolloff": 0.2,
                "sample_rate": 48000.0},        # Yaesu System Fusion
    "M17":     {"symbol_rate": 4800.0, "dev": 800.0, "rolloff": 0.2,
                "sample_rate": 48000.0},        # 开源数字语音
    "FREEDV":  {"ofdm_bw": 1125.0, "ofdm_nfft": 64, "ofdm_cp": 0.1,
                "ofdm_nused": 9, "ofdm_const": "QPSK", "sample_rate": 8000.0},
    # --- 航空 / 航海 ---
    # VDL Mode 2: D8PSK 31.5 kbit/s -> 10.5 kbaud (25 kHz 信道)
    "VDL2":    {"symbol_rate": 10500.0, "rolloff": 0.35, "sample_rate": 48000.0},
    "VOR":     {"vor_azimuth": 45.0, "sample_rate": 48000.0},
    "ILS":     {"ils_d90": 0.20, "ils_d150": 0.20, "ils_ident": 0.15,
                "sample_rate": 48000.0},
    "DME":     {"pp_spacing": 12e-6, "pp_pw": 3.5e-6, "pp_rate": 1200.0,
                "sample_rate": 2000000.0},
    "SSR":     {"pp_spacing": 8e-6, "pp_pw": 0.45e-6, "pp_rate": 300.0,
                "sample_rate": 10000000.0},
    "DSC":     {"symbol_rate": 100.0, "dev": 85.0, "rolloff": 0.35,
                "sample_rate": 48000.0},        # 海上 DSC 遇险呼叫
    # --- 卫星 ---
    "IRIDIUM": {"burst_mod": "QPSK", "symbol_rate": 25000.0, "rolloff": 0.35,
                "burst_frame": 90e-3, "burst_nslot": 4, "burst_duty": 0.9,
                "sample_rate": 200000.0},
    "INMARSAT": {"symbol_rate": 168e3, "rolloff": 0.25, "sample_rate": 1000000.0},
    "NOAAAPT": {"sub_type": "am", "sub_fc": 2400.0, "sub_depth": 0.8,
                "msg_bw": 2000.0, "sample_rate": 48000.0},
    "METEOR":  {"symbol_rate": 72000.0, "rolloff": 0.35, "sample_rate": 400000.0},
    # --- 业余无线电 ---
    "SSTV":    {"sub_type": "fm", "sub_fc": 1500.0, "sub_dev": 800.0,
                "msg_bw": 1000.0, "sample_rate": 48000.0},
    "FAX":     {"sub_type": "fm", "sub_fc": 1500.0, "sub_dev": 400.0,
                "msg_bw": 1000.0, "sample_rate": 48000.0},
    "WSPR":    {"mfsk_tones": 4, "tone_spacing": 1.4648, "symbol_rate": 1.4648,
                "sample_rate": 8000.0},
    "JT65":    {"mfsk_tones": 65, "tone_spacing": 2.6917, "symbol_rate": 2.6917,
                "sample_rate": 8000.0},
    "OLIVIA":  {"mfsk_tones": 32, "tone_spacing": 31.25, "symbol_rate": 31.25,
                "sample_rate": 8000.0},
    "HELL":    {"symbol_rate": 122.5, "rolloff": 0.35, "M": 2, "mu": 1.0,
                "sample_rate": 48000.0},        # Feld-Hell 传真电报 OOK
    # --- 军用 ---
    "LINK16":  {"fh_band": 153e6, "fh_nch": 51, "fh_rate": 76923.0,
                "fh_inner": "MSK", "symbol_rate": 5e6,
                "sample_rate": 200000000.0},
    "HAVEQUICK": {"hop_voice_type": "am", "mu": 0.8, "msg_bw": 3000.0,
                  "fh_band": 4e6, "fh_rate": 50.0, "sample_rate": 10000000.0},
    "SINCGARS":  {"hop_voice_type": "fm", "dev": 5000.0, "msg_bw": 3000.0,
                  "fh_band": 4e6, "fh_rate": 100.0, "sample_rate": 10000000.0},
    # --- 其它 ---
    "FLEX":    {"symbol_rate": 3200.0, "dev": 2400.0, "rolloff": 0.35,
                "sample_rate": 48000.0},        # 高速寻呼 3200 baud 4-FSK
    "PULSEDOPPLER": {"pd_pri": 200e-6, "pd_pw": 10e-6, "pd_fc": 0.0,
                     "pd_chirp": 5e6, "sample_rate": 20000000.0},

    # ================= 第三批: 全网查漏补充 =================
    # --- 授时 / 时间信号 (长波) ---
    # BPC(中国 68.5k) / DCF77(德 77.5k) / WWVB(美 60k) / MSF(英 60k):
    # 每秒 1 bit, 秒首把载波降到 25% 持续 100 ms(0) 或 200 ms(1),
    # 第 59 秒不降幅作分钟标志 -> 极低速 ASK, 占用带宽仅几十 Hz。
    "TIME":    {"time_fc": 68.5e3, "sample_rate": 250000.0},
    # --- FM 广播副载波 ---
    # RDS/RBDS: 57 kHz 副载波, 1187.5 bps 差分 BPSK(双相码), 占用约 ±2.4 kHz
    "RDS":     {"rds_fc": 57000.0, "rds_rate": 1187.5, "sample_rate": 250000.0},
    # --- 航空补充 ---
    # NDB 无方向信标: 调幅报, 识别音 400/1020 Hz 键控
    "NDB":     {"tone": 1020.0, "mu": 0.7, "msg_bw": 0.0, "msg_mode": "tone",
                "sample_rate": 48000.0},
    # SELCAL 选择呼叫: 12 个标准音(312.6~1479.2 Hz), 两对音各发 1 s 的调幅音
    "SELCAL":  {"selcal_f1": 426.6, "selcal_f2": 524.8, "selcal_seg": 1.0,
                "mu": 0.7, "sample_rate": 48000.0},
    # VDL Mode 4: GFSK 19.2 kbit/s, 25 kHz 信道 (自组织时分多址)
    "VDL4":    {"gmsk_sym": 19200.0, "gmsk_bt": 0.5, "sample_rate": 192000.0},
    # --- 交通 / 铁路 ---
    # GSM-R 铁路移动通信: GMSK 270.833 ksym/s, BT=0.3 (与 GSM 同, 频段不同)
    "GSMR":    {"gmsk_sym": 270833.0, "gmsk_bt": 0.3, "sample_rate": 1280000.0},
    # DSRC / ITS-G5 (802.11p): 10 MHz 信道, 时钟 10 MHz, 64 点 FFT,
    # 52 个占用子载波 -> 占用约 8.3 MHz
    "DSRC":    {"ofdm_bw": 8.3e6, "ofdm_nfft": 64, "ofdm_nused": 52,
                "ofdm_cp": 0.125, "sample_rate": 10000000.0},
    # --- 卫星 / 气象补充 ---
    # NavIC (印度 IRNSS): L5/S 波段 BPSK, 码片 1.023 Mcps
    "NAVIC":   {"chip_rate": 1.023e6, "sample_rate": 5000000.0},
    # QZSS (日本准天顶): L1C/L5, 与 GPS 同码率 1.023 Mcps
    "QZSS":    {"chip_rate": 1.023e6, "sample_rate": 5000000.0},
    # SBAS 星基增强 (WAAS/EGNOS/MSAS/SDCM): L1 BPSK 1.023 Mcps
    "SBAS":    {"chip_rate": 1.023e6, "sample_rate": 5000000.0},
    # CCSDS 卫星遥测: BPSK 2.048 Msps (常用档之一)
    "CCSDS":   {"symbol_rate": 2.048e6, "rolloff": 0.35, "sample_rate": 8192000.0},
    # DVB-S (第一代卫星电视): QPSK, 典型符号率 27.5 Msps, 滚降 0.35
    "DVBS":    {"symbol_rate": 27.5e6, "rolloff": 0.35, "sample_rate": 80000000.0},
    # VSAT 卫星小站回传: QPSK 2.048 Msps
    "VSAT":    {"symbol_rate": 2.048e6, "rolloff": 0.25, "sample_rate": 8192000.0},
    # 探空气球 (Vaisala RS41/RS92 类): GFSK 4.8 ksym/s, ±2.4 kHz
    "RADIOSONDE": {"gmsk_sym": 4800.0, "gmsk_bt": 0.5, "sample_rate": 48000.0},
    # --- 广播补充 ---
    # ATSC 3.0: 6 MHz 信道 COFDM (8K/16K/32K FFT), 占用约 5.5 MHz
    "ATSC3":   {"ofdm_bw": 5.5e6, "ofdm_nfft": 2048, "ofdm_nused": 1387,
                "ofdm_cp": 0.06, "sample_rate": 8000000.0},
    # --- 短距 / 物联网补充 ---
    # Thread / Matter: 802.15.4 O-QPSK 半正弦, 2 Mchip/s (250 kbit/s)
    "THREAD":  {"chip_rate": 2.0e6, "sample_rate": 8000000.0},
    # Wi-SUN (802.15.4g): 2-FSK 50 ksym/s, 频移 ±25 kHz, 占用约 150 kHz
    "WISUN":   {"symbol_rate": 50e3, "dev": 25e3, "rolloff": 0.35,
                "sample_rate": 400000.0},
    # Wireless M-Bus (EN 13757-4 无线抄表): 868 MHz, 2-FSK 100 ksym/s,
    # 频移 ±25 kHz, 占用约 150 kHz
    "WMBUS":   {"symbol_rate": 100e3, "dev": 25e3, "rolloff": 0.35,
                "sample_rate": 400000.0},
    # --- 雷达补充 ---
    # SAR 合成孔径雷达: 大带宽线性调频脉冲串 (这里取 100 MHz 调频带宽)
    "SAR":     {"pd_pri": 500e-6, "pd_pw": 40e-6, "pd_fc": 0.0,
                "pd_chirp": 100e6, "sample_rate": 200000000.0},
    # CW 多普勒雷达: 单频连续波 + 目标多普勒调制 (多普勒几十 Hz ~ 几 kHz)
    "CWDOPPLER": {"dop_fc": 0.0, "dop_freq": 300.0, "dop_rate": 2.0,
                  "dop_scatter": 3, "sample_rate": 48000.0},
    # --- 业余数字模式补充 ---
    # FT4: 4-FSK, 音隔 20.833 Hz, 符号率 20.83 baud (48 ms/符号), 占用 90 Hz
    "FT4":     {"symbol_rate": 20.833, "dev": 10.4165, "rolloff": 0.35,
                "sample_rate": 8000.0},
    # FSK441 流星散射: 4-FSK, 441 baud, 音隔 441 Hz, 占用约 1.75 kHz
    "FSK441":  {"symbol_rate": 441.0, "dev": 220.5, "rolloff": 0.35,
                "sample_rate": 8000.0},
    # ALE 自动链路建立 (MIL-STD-188-141): 8-FSK, 125 baud, 音隔 250 Hz
    "ALE":     {"symbol_rate": 125.0, "dev": 125.0, "rolloff": 0.35,
                "sample_rate": 8000.0},
    # MT63: 64 个并行音, 音隔 15.625 Hz, 总占用约 1 kHz
    "MT63":    {"mfsk_tones": 64, "tone_spacing": 15.625,
                "symbol_rate": 15.625, "sample_rate": 8000.0},
    # AMTOR (业余电传): 2-FSK 100 baud, 频移 170 Hz, 带 ARQ/FEC
    "AMTOR":   {"symbol_rate": 100.0, "dev": 85.0, "rolloff": 0.35,
                "sample_rate": 48000.0},
    # --- 调制基元补充 ---
    # GMSK 通用: h=0.5 (与 GSM 一致), BT=0.3
    "GMSK":    {"gmsk_sym": 100e3, "gmsk_bt": 0.3, "gmsk_h": 0.5,
                "sample_rate": 800000.0},
}

SLOT_PRESETS = {
    "dmr":   (2, 0.030),
    "pdt":   (2, 0.030),
    "gsm":   (8, 577e-6),
    "gsmr":  (8, 577e-6),
    "tetra": (4, 0.0141667),
}

ORIG_DIR = r"E:\NEW\signalwave"

SR_CAND = [32000, 48000, 64000, 96000, 128000, 192000, 256000, 384000,
            512000, 768000, 1.0e6, 1.28e6, 1.536e6, 2.0e6, 2.5e6, 3.0e6,
            4.0e6, 5.0e6, 6.0e6, 8.0e6, 10.0e6, 12.0e6, 15.36e6, 16.0e6,
            20.0e6, 24.0e6, 25.0e6, 30.72e6, 40.0e6, 50.0e6, 61.44e6,
            80.0e6, 100.0e6, 122.88e6, 160.0e6, 200.0e6]

MAX_SAMPLES = 40_000_000

PREVIEW_MAX_SAMPLES = 1 << 18      # 预览最多 262144 个 I/Q 样本

PREVIEW_HOP_MAX_SAMPLES = 2_000_000  # 跳频预览的上限(要装下若干个跳周期)

PREVIEW_DEFAULT_DUR = 0.05         # 默认预览时长上限 (s)

def nice_sr(bw):
    for c in SR_CAND:
        if c >= bw: return c
    return SR_CAND[-1]

def _choose_dur(sr):
    if sr >= 8e6: return 0.15
    if sr >= 2e6: return 0.25
    if sr >= 5e5: return 0.4
    if sr >= 1.2e5: return 0.7
    return 1.2

def _fill_mod_defaults(mod, d):
    """补上该制式在 MOD_DEFAULTS 里的默认参数 (与 _build_core 完全同一套判据)。

    调 _min_sample_rate 之前必须先补: 有些制式的带宽**只写在 MOD_DEFAULTS 里**
    (如 WIFI6 的 bridge_bw=80 MHz), 不补的话它的带宽在预测采样率时就是 0,
    预测值会偏小一个数量级 —— 后果是预览先按小采样率算时长、合成出一个几十倍
    大的数组再截掉(WIFI6 预览实测 1.24 s, 而它本该 0.05 s)。
    """
    for k, v in (MOD_DEFAULTS.get(str(mod or "").upper()) or {}).items():
        if not d.get(k):
            d[k] = v
    return d

def _min_sample_rate(mod, p):
    """在"该制式本身需要的最小采样率"之上, 再叠加跳频带宽需求。"""
    need = _min_sample_rate_base(mod, p)
    if p.get("hop_on"):
        band = max(0.0, float(p.get("hop_band") or 0.0))
        foff = abs(float(p.get("hop_foff") or 0.0))
        if band > 0 or foff > 0:
            # 跳频后总占用 = 跳频总带宽 + 信号本体带宽 (+ 起始偏移的两倍)
            # 注意: 这里必须先把它声明的采样率归零再问一次 ——
            # _min_sample_rate_base 会以 max(sr_decl, ...) 兜底, 直接拿它的
            # 返回值当"信号带宽"会把声明采样率重复算进去, 采样率白白翻倍。
            p0 = dict(p)
            p0["sample_rate"] = 0.0
            try:
                sig = float(_min_sample_rate_base(mod, p0))
            except Exception:
                sig = 0.0
            need = max(need, (band + 2.0 * foff + sig) * 1.05)
    return need

def _min_sample_rate_base(mod, p):
    sr_decl = float(p.get("sample_rate") or 0.0)
    sym = float(p.get("symbol_rate") or 0.0)
    ro = float(p.get("rolloff") or 0.35)

    if mod == "LORA":
        bw = float(p.get("lora_bw") or 0.0)
        return max(sr_decl, bw * 1.25)
    if mod in ("GPS", "GLONASS", "BEIDOU", "GALILEO",
               "NAVIC", "QZSS", "SBAS"):
        cr = float(p.get("chip_rate") or 0.0)
        return max(sr_decl, cr * 2.0 * 1.25)
    if mod == "CDR":
        bw = float(p.get("ofdm_bw") or 0.0)
        return max(sr_decl, bw * 1.10) if bw > 0 else sr_decl
    if mod in ("LTE", "DTMB"):
        bw = float(p.get("ofdm_bw") or 0.0)
        return max(sr_decl, bw / 0.9 * 1.05) if bw > 0 else sr_decl
    if mod in ("GSM", "AIS", "BT"):
        gs = float(p.get("gmsk_sym") or 0.0)
        if gs <= 0:
            return sr_decl
        return max(sr_decl, gs * 4)
    if mod == "ADSB":
        r = float(p.get("adsb_rate") or 0.0)
        return max(sr_decl, r * 2) if r > 0 else sr_decl
    if mod == "ATV":
        bw = float(p.get("atv_bw") or 0.0)
        return max(sr_decl, bw * 1.25)
    if mod == "BRIDGE":
        bw = float(p.get("bridge_bw") or 0.0)
        if bw <= 0:
            bw = sr_decl if sr_decl > 0 else 20e6
        return max(sr_decl, bw * 1.001)
    if mod in ("MORSE", "SSB"):
        return sr_decl

    if mod == "RADAR":
        # 雷达样式的 f_lo/f_hi 是 MHz 量级 (见 _radar_spec: *1e6), 而默认
        # 采样率只有 96 kHz —— 线性调频会被整个折叠掉, 出来的根本不是 chirp。
        # 按样式里最大的 |f| 抬升采样率 (样式的 pw/pri 是 us, 不受影响)。
        try:
            spec = p.get("radar_spec")
            if not spec:
                pat = RADAR_PATTERNS.get(p.get("radar", "agile1"),
                                         RADAR_PATTERNS["agile1"])
                spec = _radar_spec(pat)
            fmax = 0.0
            for g in spec:
                fmax = max(fmax, abs(float(g["f0"])), abs(float(g["f1"])))
            if fmax > 0:
                return max(sr_decl, fmax * 2.0 * 1.05)
        except Exception:
            pass
        return sr_decl

    # --- 新增真实世界信号 ---
    if mod in ("NR5G", "DVBT"):
        bw = float(p.get("ofdm_bw") or 0.0)
        return max(sr_decl, bw * 1.05) if bw > 0 else sr_decl
    if mod in ("WCDMA", "CDMA"):
        cr = float(p.get("chip_rate") or 0.0)
        return max(sr_decl, cr * 2.0 * 1.25) if cr > 0 else sr_decl
    if mod == "ZIGBEE":
        cr = float(p.get("chip_rate") or 0.0)
        return max(sr_decl, cr * 4) if cr > 0 else sr_decl
    if mod == "FMCW":
        bw = float(p.get("fmcw_bw") or 0.0)
        return max(sr_decl, bw * 1.05) if bw > 0 else sr_decl

    # --- 第二批: 新增信号的自适应采样率 ---
    if mod in ("NR5G", "DVBT", "ISDBT", "DRM", "NBIOT", "LTEM", "FREEDV"):
        bw = float(p.get("ofdm_bw") or 0.0)
        return max(sr_decl, bw * 1.10) if bw > 0 else sr_decl
    if mod in ("WIFI6", "WIFI7"):
        bw = float(p.get("bridge_bw") or 0.0)
        return max(sr_decl, bw * 1.001) if bw > 0 else sr_decl
    if mod == "HDRADIO":
        bw = float(p.get("ofdm_bw") or 0.0)
        return max(sr_decl, bw * 1.10) if bw > 0 else sr_decl
    if mod == "DSSS":
        cr = float(p.get("chip_rate") or 0.0)
        ro_d = float(p.get("rolloff") or 0.35)
        return max(sr_decl, cr * (1.0 + ro_d) * 1.05) if cr > 0 else sr_decl
    if mod == "SOQPSK":
        return max(sr_decl, sym * 1.5) if sym > 0 else sr_decl
    if mod in ("GFSK", "DECT", "IRIDIUM"):
        gs = float(p.get("gmsk_sym") or 0.0)
        if gs > 0:
            return max(sr_decl, gs * 4)
        return max(sr_decl, sym * (1.0 + ro) * 1.2) if sym > 0 else sr_decl
    if mod == "THSS":
        tr = float(p.get("th_rate") or 0.0)
        return max(sr_decl, tr * 2.5) if tr > 0 else sr_decl
    if mod in ("PAM", "PWM", "PPM"):
        pw = float(p.get("pulse_pw") or 0.0)
        fc = abs(float(p.get("pulse_fc") or 0.0))
        return max(sr_decl, (1.0 / pw + 2.0 * fc) * 1.4) if pw > 0 else sr_decl
    if mod in ("DME", "SSR"):
        pw = float(p.get("pp_pw") or 0.0)
        sp = abs(float(p.get("pp_spacing") or 0.0))
        return max(sr_decl, (2.0 / max(pw, 1e-12) + 1.0 / max(sp, 1e-12)) * 1.2)
    if mod == "PULSEDOPPLER":
        pw = float(p.get("pd_pw") or 0.0)
        ch = float(p.get("pd_chirp") or 0.0)
        fc = abs(float(p.get("pd_fc") or 0.0))
        return max(sr_decl, max(2.0 / max(pw, 1e-12), ch + 2.0 * fc) * 1.2)
    if mod == "UWB":
        bw = float(p.get("uwb_bw") or 0.0)
        return max(sr_decl, bw * 1.1) if bw > 0 else sr_decl
    if mod == "RFID":
        blf = float(p.get("rfid_blf") or 0.0)
        tari = float(p.get("rfid_tari") or 0.0)
        need = max(2.0 * blf * 1.3, 4.0 / max(tari, 1e-12))
        return max(sr_decl, need)
    if mod == "NFC":
        blf = float(p.get("nfc_blf") or 0.0)
        return max(sr_decl, 2.0 * blf * 1.5) if blf > 0 else sr_decl
    if mod == "LINK16":
        band = float(p.get("fh_band") or 0.0)
        return max(sr_decl, band * 1.1) if band > 0 else sr_decl
    if mod in ("HAVEQUICK", "SINCGARS"):
        band = float(p.get("fh_band") or 0.0)
        mbw = float(p.get("msg_bw") or 3000.0)
        d = float(p.get("dev") or 0.0)
        return max(sr_decl, band + 2.0 * (d + mbw) * 1.1)
    if mod in ("WSPR", "JT65", "OLIVIA"):
        n = int(p.get("mfsk_tones") or 4)
        sp = float(p.get("tone_spacing") or 0.0)
        return max(sr_decl, n * sp * 1.3) if sp > 0 else sr_decl
    if mod in ("NOAAAPT", "SSTV", "FAX"):
        fc = float(p.get("sub_fc") or 0.0)
        dev = float(p.get("sub_dev") or 0.0)
        dep = float(p.get("sub_depth") or 0.0)
        mbw = float(p.get("msg_bw") or 0.0)
        span = 2.0 * (fc + dev + mbw) if str(p.get("sub_type") or "fm").lower() == "fm" \
            else 2.0 * (fc + (mbw if dep > 0 else 0.0))
        return max(sr_decl, span * 1.1)
    if mod in ("NBFM", "WBFM"):
        d = float(p.get("dev") or 0.0)
        mbw = float(p.get("msg_bw") or 0.0)
        return max(sr_decl, 2.0 * (d + mbw) * 1.05) if d > 0 else sr_decl
    if mod == "ATSC":
        rs = float(p.get("symbol_rate") or 0.0)
        ro_a = float(p.get("rolloff") or 0.115)
        return max(sr_decl, rs * (1.0 + ro_a) * 1.15) if rs > 0 else sr_decl
    if mod in ("DVBS2", "DVBC", "VDL2", "METEOR", "INMARSAT"):
        return max(sr_decl, sym * (1.0 + ro) * 1.1) if sym > 0 else sr_decl
    if mod in ("VOR", "ILS"):
        return max(sr_decl, 24000.0)

    # --- 第三批 ---
    if mod == "TIME":       # 长波授时: 载波 40~80 kHz, 必须落在采样率内
        fc = abs(float(p.get("time_fc") or 0.0))
        return max(sr_decl, fc * 2.0 * 1.2) if fc > 0 else sr_decl
    if mod == "RDS":        # 57 kHz 副载波 + ±2.4 kHz 边带
        fc = abs(float(p.get("rds_fc") or 57000.0))
        rb = float(p.get("rds_rate") or 1187.5)
        return max(sr_decl, (fc + 2.0 * rb) * 2.0 * 1.05)
    if mod == "SELCAL":     # 最高音 1479 Hz, 调幅占用约 ±1.5 kHz
        return max(sr_decl, 12000.0)
    if mod == "CWDOPPLER":
        fc = abs(float(p.get("dop_fc") or 0.0))
        fd = float(p.get("dop_freq") or 0.0)
        fm = float(p.get("dop_rate") or 0.0)
        return max(sr_decl, 2.0 * (fc + fd + fm) * 1.3)
    if mod in ("GSMR", "VDL4", "RADIOSONDE", "GMSK"):
        gs = float(p.get("gmsk_sym") or 0.0)
        return max(sr_decl, gs * 4) if gs > 0 else sr_decl
    if mod == "THREAD":
        cr = float(p.get("chip_rate") or 0.0)
        return max(sr_decl, cr * 4) if cr > 0 else sr_decl
    if mod in ("ATSC3", "DSRC"):
        bw = float(p.get("ofdm_bw") or 0.0)
        return max(sr_decl, bw * 1.10) if bw > 0 else sr_decl
    if mod == "MT63":
        n = int(p.get("mfsk_tones") or 64)
        sp = float(p.get("tone_spacing") or 0.0)
        return max(sr_decl, n * sp * 1.3) if sp > 0 else sr_decl
    if mod == "CCSDS":
        return max(sr_decl, sym * (1.0 + ro) * 1.1) if sym > 0 else sr_decl

    M = int(p.get("M") or 2)
    dev = float(p.get("dev") or 0.0)

    # 只按"奈奎斯特"约束抬升采样率。原先额外要求 sym*4 (每符号至少 4 采样),
    # 会把 16qam_1.9m_1.6m_0.3 这类信号的采样率从素材的 3.2 MHz 抬到 8 MHz,
    # 频率轴整体错位, 频谱无法与素材对齐。素材本身就常用 sps=2。
    if dev > 0:
        outermost = (M - 1) * dev                 # 最外侧频偏
        need_bw = 2.0 * outermost + sym * (1.0 + ro)
    elif sym > 0:
        need_bw = sym * (1.0 + ro)
    else:
        return sr_decl

    return max(sr_decl, need_bw * 1.05)

def _rng(seed):
    if seed in (None, ""): return np.random.default_rng()
    try: return np.random.default_rng(int(seed))
    except Exception: return np.random.default_rng()

RADAR_PATTERNS = {
    "agile1": {"type": "agile", "segments": [
        {"f_lo": -2.5, "f_hi": -0.5, "pw": 140, "gap": 0, "count": 1},
        {"f_lo": 0.5, "f_hi": 2.5, "pw": 140, "gap": 0, "count": 1}]},
    "agile2": {"type": "agile", "segments": [
        {"f_lo": -3.5, "f_hi": -2.5, "pw": 160, "gap": 0, "count": 1},
        {"f_lo": 2.5, "f_hi": 3.5, "pw": 180, "gap": 0, "count": 1}]},
    "agile3": {"type": "agile", "segments": [
        {"f_lo": -4.0, "f_hi": -3.0, "pw": 200, "gap": 0, "count": 1},
        {"f_lo": 0.5, "f_hi": 1.5, "pw": 150, "gap": 0, "count": 1}]},
    "agile4": {"type": "agile", "segments": [
        {"f_lo": -2.5, "f_hi": -2.0, "pw": 120, "gap": 0, "count": 1},
        {"f_lo": 0.5, "f_hi": 1.5, "pw": 150, "gap": 0, "count": 1}]},
    "fagile1": {"type": "agile", "frame": 0.4, "segments": [
        {"f_lo": -6, "f_hi": -4, "pw": 150, "gap": 20, "count": 3},
        {"f_lo": -1, "f_hi": 1, "pw": 100, "gap": 20, "count": 3},
        {"f_lo": 4, "f_hi": 6, "pw": 130, "gap": 20, "count": 3}]},
    "fagile2": {"type": "agile", "frame": 0.4, "segments": [
        {"f_lo": -6, "f_hi": -4, "pw": 130, "gap": 20, "count": 3},
        {"f_lo": -1, "f_hi": 1, "pw": 200, "gap": 20, "count": 3},
        {"f_lo": 3, "f_hi": 5, "pw": 100, "gap": 20, "count": 3}]},
    "fagile3": {"type": "agile", "frame": 0.4, "segments": [
        {"f_lo": -7, "f_hi": -5, "pw": 200, "gap": 20, "count": 3},
        {"f_lo": -1, "f_hi": 1, "pw": 150, "gap": 20, "count": 3},
        {"f_lo": 3, "f_hi": 5, "pw": 100, "gap": 20, "count": 3}]},
    "fagile4": {"type": "agile", "frame": 0.4, "segments": [
        {"f_lo": -7, "f_hi": -5, "pw": 200, "gap": 20, "count": 2},
        {"f_lo": -1, "f_hi": 1, "pw": 150, "gap": 20, "count": 2},
        {"f_lo": 3, "f_hi": 5, "pw": 100, "gap": 20, "count": 3}]},
    "fagile5": {"type": "agile", "frame": 0.4, "segments": [
        {"f_lo": -6, "f_hi": -4, "pw": 200, "gap": 20, "count": 3},
        {"f_lo": -1, "f_hi": 1, "pw": 150, "gap": 20, "count": 3},
        {"f_lo": 4, "f_hi": 6, "pw": 100, "gap": 20, "count": 2}]},
    "group1": {"type": "group", "segments": [
        {"pw": 200, "pri": 400, "count": 2},
        {"pw": 400, "pri": 1200, "count": 3},
        {"pw": 100, "pri": 400, "count": 5}]},
    "group2": {"type": "group", "segments": [
        {"pw": 150, "pri": 300, "count": 2},
        {"pw": 250, "pri": 600, "count": 1},
        {"pw": 120, "pri": 600, "count": 2}]},
    "group3": {"type": "group", "segments": [
        {"pw": 200, "pri": 600, "count": 3},
        {"pw": 150, "pri": 300, "count": 2},
        {"pw": 500, "pri": 1500, "count": 3}]},
    "group4": {"type": "group", "segments": [
        {"pw": 50, "pri": 150, "count": 5},
        {"pw": 20, "pri": 60, "count": 2},
        {"pw": 100, "pri": 400, "count": 2}]},
    "group5": {"type": "group", "segments": [
        {"pw": 80, "pri": 100, "count": 2},
        {"pw": 120, "pri": 360, "count": 1},
        {"pw": 150, "pri": 450, "count": 3}]},
}

def read_wav_mono(path):
    """读取 wav -> (采样率, [-1,1] 浮点单声道数组)。"""
    w = wave.open(path, "rb")
    try:
        nch = w.getnchannels(); sw = w.getsampwidth(); fr = w.getframerate(); nf = w.getnframes()
        raw = w.readframes(nf)
    finally:
        w.close()

    if sw == 2:
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sw == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sw == 4:
        data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError("不支持的 wav 位宽: %d" % sw)

    if nch > 1:
        data = data.reshape(-1, nch)[:, 0]
    else:
        data = data.reshape(-1)
    return fr, np.asarray(data, dtype=np.float32)

def prepare_audio_source(audio_path, sr, n_samples):
    """把外部 wav 信源整理成长度 == n_samples 的 [-1,1] 消息信号。

    修复记录:
      * 原函数里使用了未定义的 `path` / `audio`, 且先调用未归一化的局部变量;
      * resample_poly 的 up/down 传反且没有约分, 长音频会瞬间撑爆内存;
      * 内嵌的 _interp 把入参覆盖后再也没排上用场。
    """
    if not audio_path:
        raise ValueError("未指定音频文件")

    n_samples = int(max(1, n_samples))
    fr, audio = read_wav_mono(audio_path)

    if audio.size == 0:
        raise ValueError("音频文件为空")

    audio = audio - float(np.mean(audio))
    peak = float(np.max(np.abs(audio)))
    if peak > 1e-9:
        audio = audio / peak

    # ---- 重采样到目标采样率 ----
    if fr > 0 and abs(float(fr) - float(sr)) > 1.0 and audio.size > 1:
        if _HAVE_SCIPY:
            try:
                g = math.gcd(int(round(sr)), int(round(fr))) or 1
                up, down = int(round(sr)) // g, int(round(fr)) // g
                rs = resample_poly(audio, up, down)
            except Exception:
                rs = None
        else:
            rs = None

        if rs is None:  # 退化: 线性插值重采样
            x_old = np.linspace(0.0, 1.0, audio.size, endpoint=False)
            x_new = np.linspace(0.0, 1.0, n_samples, endpoint=False)
            rs = np.interp(x_new, x_old, audio)
    else:
        rs = audio

    rs = np.asarray(rs, dtype=np.float32).reshape(-1)

    # ---- 对齐长度: 短则循环拼接, 长则截断 ----
    if rs.size < n_samples:
        reps = int(math.ceil(n_samples / float(rs.size)))
        rs = np.tile(rs, reps)
    rs = rs[:n_samples]

    rs = rs - float(np.mean(rs))
    peak2 = float(np.max(np.abs(rs)))
    if peak2 > 1e-9:
        rs = rs / peak2

    return rs

_conv_direct = np.convolve

def _next_fast_len(n):
    """返回 >= n 的最小 2/3/5 光滑数(FFT 友好长度, 比素数长度快得多)。"""
    n = int(max(1, n))
    limit = 1 << max(1, int(math.ceil(math.log2(n))) + 1)
    best = limit
    p2 = 1
    while p2 < limit:
        p3 = p2
        while p3 < limit:
            p5 = p3
            while p5 < limit:
                if n <= p5 < best:
                    best = p5
                p5 *= 5
            p3 *= 3
        p2 *= 2
    return best

def _fft_convolve_full(a, b, complex_in=False):
    """FFT 卷积(等价 full 模式的 np.convolve)。

    长数据走 overlap-add 分块, 内存占用与数据总长无关 —— 跳频场景下
    数据可以到 4000 万点, 一次性 FFT 会要几百 MB 且容易崩。
    """
    na, nb = int(a.size), int(b.size)
    n_out = na + nb - 1
    fwd = np.fft.fft if complex_in else np.fft.rfft
    inv = np.fft.ifft if complex_in else np.fft.irfft
    if n_out <= (1 << 24):                      # 16M 点以内: 一次 FFT 搞定
        nfft = _next_fast_len(n_out)
        return inv(fwd(a, nfft) * fwd(b, nfft), nfft)[:n_out]
    # 超长数据: 固定 FFT 长度 overlap-add
    nfft = 1 << 22
    blk = max(1 << 16, nfft - nb + 1)
    fb = fwd(b, nfft)
    out = np.zeros(n_out, dtype=a.dtype)
    for s in range(0, na, blk):
        chunk = a[s:min(na, s + blk)]
        seg = inv(fwd(chunk, nfft) * fb, nfft)
        seg = seg[:chunk.size + nb - 1]
        out[s:s + seg.size] += seg
    return out

def fast_convolve(a, b, mode="full"):
    """与 np.convolve 同语义, 但长核时自动改用 FFT。

    为什么需要: 跳频发射会把采样率抬到几十 MHz(如"蓝牙 FHSS" 79 MHz 总带宽
    -> 约 100 MHz 采样率), 若基带符号率只有几 k, 每符号样本数 sps 会到上万,
    脉冲成型核随之变成十几万点。此时 np.convolve 是 O(n·m) 暴力卷积:
    26 万样本要点 12 秒(预览卡死), 4000 万样本根本跑不完。
    分块 FFT 卷积数值等价, 但把同样运算压到 0.1 秒级。

    支持复数输入(I/Q 合成流), 复数走 fft/ifft, 实数走 rfft/irfft。
    """
    a = np.asarray(a)
    b = np.asarray(b)
    cplx = np.iscomplexobj(a) or np.iscomplexobj(b)
    dt = np.complex128 if cplx else np.float64
    a = a.astype(dt, copy=False).ravel()
    b = b.astype(dt, copy=False).ravel()
    na, nb = a.size, b.size
    if na == 0 or nb == 0:
        if mode == "same":
            return np.zeros(max(na, nb), dtype=dt)
        return np.zeros(0, dtype=dt)
    if na * nb <= (1 << 21):                    # 小规模直接用暴力法(更快)
        return _conv_direct(a, b, mode)

    full = _fft_convolve_full(a, b, cplx)
    if mode == "full":
        return full
    if mode == "valid":
        if na >= nb:
            return full[nb - 1:na]
        return full[na - 1:nb]
    # same: 与 np.convolve 一致 —— 居中取 max(na, nb) 个点
    n_out = max(na, nb)
    s = (nb - 1) // 2 if na >= nb else (na - 1) // 2
    return full[s:s + n_out]

def rrc_filter(sps, rolloff, span=8):
    sps = max(2, int(sps)); N = span * sps
    if N % 2 == 0: N += 1
    t = (np.arange(N) - (N // 2)) / float(sps)

    ro = float(rolloff or 0.0)
    if ro <= 0.0:      # 0 滚降即 sinc, 避免下面 1/(4*rolloff) 除零
        h = np.sinc(t)
    else:
        # 向量化实现(原来逐点 math.sin/cos 循环): 跳频场景下 sps 会到 1.6 万,
        # 抽头数 = span*sps ≈ 13 万, 纯 Python 循环要 0.16 s, 每次生成都白等。
        h = np.empty(N, dtype=np.float64)
        crit = np.abs(np.abs(t) - (1.0 / (4.0 * ro)))
        at = np.abs(t)
        center = at < 1e-12
        sing = (~center) & (crit < 1e-9)
        norm = ~(center | sing)

        h[center] = 1.0 - ro + 4.0 * ro / math.pi
        h[sing] = (ro / math.sqrt(2.0)) * (
            (1.0 + 2.0 / np.pi) * math.sin(np.pi / (4.0 * ro))
            + (1.0 - 2.0 / np.pi) * math.cos(np.pi / (4.0 * ro)))
        if norm.any():
            tm = t[norm]
            num = (np.sin(np.pi * tm * (1.0 - ro)) +
                   4.0 * ro * tm * np.cos(np.pi * tm * (1.0 + ro)))
            den = np.pi * tm * (1.0 - (4.0 * ro * tm) ** 2)
            h[norm] = num / den

    s = float(np.sum(h))
    if abs(s) > 1e-12:
        h = h / s
    return h

def constellation_points(mod, M):
    if mod in ("BPSK", "DPSK"):
        return np.array([1.0 + 0j, -1.0 + 0j], dtype=complex), 1

    if mod == "QPSK":
        pts = np.array([1 + 1j, -1 + 1j, -1 - 1j, 1 - 1j]) / math.sqrt(2)
        return pts, 2
    if mod == "8PSK":
        pts = np.exp(1j * (np.pi / 8 + np.arange(8) * (np.pi / 4)))
        return pts, 3
    if mod == "16PSK":
        pts = np.exp(1j * (np.arange(16) * (np.pi / 8)))
        return pts, 4

    if mod in ("16QAM", "32QAM", "64QAM", "256QAM", "1024QAM", "4096QAM"):
        return qam_constellation(mod), int(round(math.log2({"16QAM": 16,
                                                            "32QAM": 32,
                                                            "64QAM": 64,
                                                            "256QAM": 256,
                                                            "1024QAM": 1024,
                                                            "4096QAM": 4096}[mod])))

    # APSK (幅相键控): 同心圆环星座, 归一化后环半径比决定功放效率
    if mod in ("16APSK", "32APSK", "64APSK"):
        return apsk_constellation(mod), int(round(math.log2({"16APSK": 16,
                                                             "32APSK": 32,
                                                             "64APSK": 64}[mod])))

    if mod in ("2FSK", "4FSK", "8FSK", "MSK", "CP4FSK"):
        return None, int(round(math.log2(M)))

    if mod == "ASK":
        return None, int(round(math.log2(M)))

    raise ValueError("未知调制: %s" % mod)

def _qam_square(lvl):
    """方型 lvl×lvl QAM 星座 (lvl 为每维电平数, 例如 16 -> 256QAM)。

    电平取 -(lvl-1), -(lvl-3), ..., lvl-1, 按平均功率归一化。
    """
    lv = np.arange(-(lvl - 1), lvl, 2, dtype=np.float64)
    X, Y = np.meshgrid(lv, lv[::-1])
    pts = (X + 1j * Y).reshape(-1)
    pts = pts / math.sqrt(float(np.mean(np.abs(pts) ** 2)))
    return pts

def qam_constellation(mod):
    """方型 / 十字型 QAM 星座图 (已按平均功率归一化)。

    修复记录: 原实现里 `np.arange(lvl * 4)` 把电平表当成了个数,
    且 16QAM 分支写完后没有 return、其余分支的 allpts 未定义。
    """
    def _grid(lv):
        X, Y = np.meshgrid(lv, lv[::-1])
        return (X + 1j * Y).reshape(-1)

    if mod == "16QAM":
        pts = _grid(np.array([-3.0, -1.0, 1.0, 3.0]))
    elif mod == "64QAM":
        pts = _grid(np.array([-7.0, -5.0, -3.0, -1.0, 1.0, 3.0, 5.0, 7.0]))
    elif mod == "256QAM":
        pts = _qam_square(16)
    elif mod == "1024QAM":
        pts = _qam_square(32)          # Wi-Fi 6 (802.11ax)
    elif mod == "4096QAM":
        pts = _qam_square(64)          # Wi-Fi 7 (802.11be)
    elif mod == "32QAM":
        # 6x6 网格去掉四个角点 -> 32 点 (十字型 QAM)
        allp = _grid(np.array([-5.0, -3.0, -1.0, 1.0, 3.0, 5.0]))
        corners = {complex(-5, -5), complex(-5, 5), complex(5, -5), complex(5, 5)}
        pts = np.array([c for c in allp if complex(round(c.real), round(c.imag)) not in corners],
                       dtype=complex)
    else:
        raise ValueError("未知 QAM 阶数: %s" % mod)

    pts = np.asarray(pts, dtype=complex)
    pts = pts / math.sqrt(float(np.mean(np.abs(pts) ** 2)))
    return pts

def apsk_constellation(mod):
    """APSK (幅相键控) 星座: 同心圆环, 环上等间隔相位。

    DVB-S2 标准规定的环半径比 (gamma = 外环半径 / 内环半径):
      16APSK : 4 + 12 点,  gamma = 3.15
      32APSK : 4 + 12 + 16 点, gamma1 = 2.54, gamma2 = 4.33
      64APSK : 4 + 12 + 20 + 28 点, gamma1 = 2.4, gamma2 = 4.3, gamma3 = 7.0
      内环相位相对外环偏移 pi/4 (16APSK) / pi/8 (32APSK), 便于接收机判决。
    相比同阶 QAM, APSK 峰均比更低, 适合卫星行波管功放 (TWTA)。
    """
    def _ring(n, r, off):
        return r * np.exp(1j * (off + np.arange(n) * (2 * np.pi / n)))

    if mod == "16APSK":
        g = 3.15
        pts = np.concatenate([_ring(4, 1.0, np.pi / 4), _ring(12, g, np.pi / 12)])
    elif mod == "32APSK":
        g1, g2 = 2.54, 4.33
        pts = np.concatenate([_ring(4, 1.0, np.pi / 4),
                              _ring(12, g1, np.pi / 12),
                              _ring(16, g2, 0.0)])
    elif mod == "64APSK":
        g1, g2, g3 = 2.4, 4.3, 7.0
        pts = np.concatenate([_ring(4, 1.0, np.pi / 4),
                              _ring(12, g1, np.pi / 12),
                              _ring(20, g2, 0.0),
                              _ring(28, g3, np.pi / 14)])
    else:
        raise ValueError("未知 APSK 阶数: %s" % mod)

    pts = np.asarray(pts, dtype=complex)
    pts = pts / math.sqrt(float(np.mean(np.abs(pts) ** 2)))
    return pts

def build_bits(source_mode, manual_text, n_symbols, bits_per_symbol, seed=None):
    """生成符号索引序列。

    返回长度 == n_symbols 的 int64 数组, 取值 [0, 2**bits_per_symbol)。
    修复记录: 原实现引用了未定义的 `total_bits`, 且把 list 当成 str 去 encode。
    """
    n_symbols = int(max(0, n_symbols))
    bits_per_symbol = int(max(1, bits_per_symbol))

    if n_symbols <= 0:
        return np.zeros(0, dtype=np.int64)

    total_bits = n_symbols * bits_per_symbol

    if source_mode == "manual":
        raw = [ch for ch in str(manual_text or "") if ch in "01"]
        if not raw:
            raise ValueError("手动比特序列为空 (需要至少 1 个 0/1)")
        reps = int(math.ceil(total_bits / float(len(raw))))
        flat = np.array((raw * reps)[:total_bits], dtype=np.uint8)
    else:
        rng = _rng(seed)
        flat = rng.integers(0, 2, total_bits, dtype=np.uint8)

    if bits_per_symbol == 1:
        return flat[:n_symbols].astype(np.int64)

    mat = flat[:total_bits].reshape(n_symbols, bits_per_symbol)
    weights = (1 << np.arange(bits_per_symbol - 1, -1, -1)).astype(np.int64)
    return mat.dot(weights).astype(np.int64)

def _build_lora(p, sr, N, amp):
    bw = float(p.get("lora_bw", 125000.0))
    sf = int(p.get("lora_sf", 10))
    Tsym = (2 ** sf) / bw
    sps = max(4, int(round(Tsym * sr)))
    n_sym = max(1, N // sps)
    rng = _rng(p.get("seed"))
    syms = rng.integers(0, 2 ** sf, n_sym)          # LoRa 一个符号承载 sf 个比特
    t = np.arange(sps) / float(sr)
    out = np.zeros(N, dtype=np.complex64)
    idx = 0
    for s in syms:
        if idx + sps > N: break
        f0 = (s / (2 ** sf)) * bw - bw / 2.0
        f_inst = f0 + (bw / Tsym) * t
        f_inst = np.mod(f_inst + bw / 2.0, bw) - bw / 2.0
        phase = 2 * np.pi * np.cumsum(f_inst) / sr
        out[idx:idx + sps] = amp * np.exp(1j * phase)
        idx += sps

    peak = float(np.max(np.abs(out))) if len(out) else 1.0
    if peak > 1e-9:
        out = out * (amp / peak)
    return out.astype(np.complex64)

def _build_gnss(p, sr, N, amp):
    chip_rate = float(p.get("chip_rate", 1.023e6))
    rng = _rng(p.get("seed"))
    n_chips = int(N * chip_rate / sr) + 2
    chips = (rng.integers(0, 2, n_chips) * 2 - 1).astype(np.float64)

    if chip_rate > 0:
        chip_idx = np.floor(np.arange(N) * (chip_rate / sr)).astype(np.int64)
        chip_idx = np.minimum(chip_idx, n_chips - 1)
        sig = chips[chip_idx]
    else:
        sig = np.ones(N)

    fc = float(p.get("carrier", 0.0))
    t = np.arange(N) / sr
    iq = sig * np.exp(1j * 2 * np.pi * fc * t)

    beta = float(p.get("chip_roll") or 0.0)
    if beta > 0 and chip_rate > 0:
        f = np.fft.fftfreq(N, 1.0 / sr); r = np.abs(f - fc)
        f1, f2 = chip_rate * (1.0 - beta), chip_rate * (1.0 + beta)
        m = np.zeros_like(r)
        m[r <= f1] = 1.0
        mid = (r > f1) & (r < f2)
        m[mid] = 0.5 * (1 + np.cos(np.pi * (r[mid] - f1) / max(f2 - f1, 1e-9)))
        iq = np.fft.ifft(np.fft.fft(iq) * m)

    peak = float(np.max(np.abs(iq))) if len(iq) else 1.0
    if peak > 1e-9:
        iq = iq * (amp / peak)
    return iq.astype(np.complex64)

def _build_ofdm(p, sr, N, amp):
    """LTE / DTMB / 5G NR / DVB-T 类 CP-OFDM。

    可选参数 (p 内, 不填则保持旧行为):
      ofdm_nfft  : IFFT 点数 (默认 512)
      ofdm_cp    : 循环前缀长度比例 (默认 0.07)
      ofdm_nused : 占用子载波数 (给了就直接生效, 优先级高于 ofdm_bw 估算)
      ofdm_const : 子载波调制 BPSK/QPSK/16QAM/64QAM/256QAM (不填用复高斯)
    修复记录: 原实现里 n_used / time / freq 全是未定义变量,
    并且 while 循环内 idx 从不自增 (死循环), 结果从未写回 out。
    """
    Nfft = int(p.get("ofdm_nfft") or 512)
    Nfft = max(8, Nfft)
    cp = max(4, int(round(Nfft * float(p.get("ofdm_cp") or 0.07))))
    sym_len = Nfft + cp
    rng = _rng(p.get("seed"))

    bw = float(p.get("ofdm_bw") or 0.0)
    if p.get("ofdm_nused"):
        n_used = int(p.get("ofdm_nused"))
    elif bw > 0 and bw < sr:
        n_used = max(16, int(round(Nfft * bw / sr)))
    else:
        n_used = max(16, int(0.9 * Nfft))
    n_used = max(8, min(int(n_used), Nfft - 2))

    # 子载波调制星座 (不指定则用复高斯, 谱形几乎一样但更平滑)
    pts = None
    c = str(p.get("ofdm_const") or "").upper()
    if c == "BPSK":
        pts = np.array([1.0 + 0j, -1.0 + 0j], dtype=complex)
    elif c == "QPSK":
        pts = np.array([1 + 1j, -1 + 1j, -1 - 1j, 1 - 1j],
                       dtype=complex) / math.sqrt(2)
    elif c in ("16QAM", "64QAM"):
        pts = qam_constellation(c)
    elif c == "256QAM":
        pts = _qam_square(16)
    elif c == "1024QAM":
        pts = _qam_square(32)
    elif c == "4096QAM":
        pts = _qam_square(64)
    if pts is not None:
        pts = np.asarray(pts, dtype=complex)

    half = n_used // 2
    out = np.zeros(N, dtype=np.complex64)
    idx = 0

    while idx + sym_len <= N:
        if pts is not None:
            data = pts[rng.integers(0, len(pts), n_used).astype(int)]
        else:
            data = (rng.standard_normal(n_used) + 1j * rng.standard_normal(n_used)) / math.sqrt(2.0)

        freq = np.zeros(Nfft, dtype=complex)
        # 两端填充 -> 中间为保护带; 跳过 DC 子载波 (index 0)
        freq[1:1 + half] = data[:half]
        freq[Nfft - (n_used - half):Nfft] = data[half:n_used]

        time = np.fft.ifft(freq) * math.sqrt(Nfft)
        sym = np.concatenate([time[Nfft - cp:], time])
        out[idx:idx + sym_len] = sym[:sym_len]
        idx += sym_len

    if idx == 0:                                    # 连一个符号都放不下
        return _build_cw_like(p, sr, N, amp)

    rem = N - idx
    if rem > 0:
        out[idx:] = out[:rem]                       # 尾部不足一个符号 -> 回卷补齐

    peak = float(np.max(np.abs(out))) if len(out) else 1.0
    if peak > 1e-9:
        out = out * (amp / peak)
    return out.astype(np.complex64)

def _build_cw_like(p, sr, N, amp):
    """兜底: 数据不足时输出一段常数包络信号, 保证长度正确。"""
    t = np.arange(int(max(N, 0))) / float(sr)
    return (amp * np.exp(1j * np.zeros_like(t))).astype(np.complex64)

def _build_dab(p, sr, N, amp):
    """数字音频广播 (DAB): 按实测谱形合成。

    频谱塑形而非"平板 OFDM": 中间窄的音频核心 + 两侧弱边带。
    横轴以占用带宽为基准归一化, 填 420 kHz 得原机谱形,
    填其它带宽则按同一几何比例缩放。
    """
    bw = float(p.get("ofdm_bw") or p.get("occupied_bw") or 1.536e6)
    if not (bw > 0):
        bw = 1.536e6
    # 说明: 早期版本用 DAB_MASK 频谱塑形来拟合某一路 420 kHz 素材,
    # 但真实 DAB/DAB+ 是 1.536 MHz 宽的 COFDM (1536 个子载波, 子载波间隔
    # 1 kHz, 采样率 2.048 MHz, 循环前缀 504/2048), 谱形是平顶的 "砖块",
    # 不是"窄核+弱边带"。这里改按标准 COFDM 生成 (参数见 MOD_DEFAULTS["DAB"])。
    q = dict(p)
    q["ofdm_bw"] = bw
    if not q.get("ofdm_nfft"):
        q["ofdm_nfft"] = 2048
    if not q.get("ofdm_nused"):
        q["ofdm_nused"] = max(16, int(round(2048 * bw / max(sr, 1.0))))
    if not q.get("ofdm_cp"):
        q["ofdm_cp"] = 504.0 / 2048.0        # DAB 模式 I 的保护间隔
    return _build_ofdm(q, sr, N, amp)

def _cdr_ofdm_side(p, sr, N, amp, f_off, bw):
    """CDR 单侧数字 OFDM 边带: 在 ±f_off 处生成 ~bw 宽的多载波数字信号。

    CDR 数字部分采用类 OFDM 多载波调制 (见《CDR调制相关知识》), 谱呈"柱状"
    子载波梳状结构。baseband 生成 CP-OFDM 后再整体频移 f_off。
    """
    sub = dict(p)
    sub["ofdm_bw"] = float(bw)
    sub["occupied_bw"] = float(bw)
    # 独立随机种子, 使左右两侧数字边带不完全相同
    seed = p.get("seed")
    if isinstance(seed, str) and seed:
        sub["seed"] = seed + ("L" if f_off < 0 else "R")
    base = _build_ofdm(sub, sr, N, amp)
    if abs(f_off) > 1e-9 and base.size:
        n = np.arange(base.size)
        base = base * np.exp(2j * np.pi * f_off * n / sr)
    return base.astype(np.complex64)

def _build_cdr(p, sr, N, amp):
    """中国数字广播 (CDR) 标准结构 (依据《CDR调制相关知识》):

    3 段频谱 —— 中间为模拟 FM 广播, 左右两侧各一路 ~50 kHz 数字 OFDM 边带;
    总占用带宽约 400 kHz, 数字部分采用类 OFDM 多载波调制。

    参数 (p 内):
      ofdm_bw    : 总占用带宽 (默认 400e3)
      cdr_fm_dev : 中间模拟 FM 频偏 Hz (默认 0.4·half = 80k, Carson 带宽 ~±95k)
      cdr_side_bw: 单侧数字带宽 Hz (默认 ofdm_bw·0.125 = 50 kHz)
      cdr_fm_bw  : 中间 FM 音频带宽 Hz (默认 12k)
    """
    bw = float(p.get("ofdm_bw") or 400e3)
    if not (bw > 0):
        bw = 400e3
    p["occupied_bw"] = bw

    half = bw / 2.0                          # 占用半宽 (±200 kHz)
    side_bw = float(p.get("cdr_side_bw") or bw * 0.125)   # 单侧数字带宽 (50 kHz)
    side_off = half - side_bw / 2.0         # 边带中心距载波 (175 kHz)

    # 1) 中间模拟 FM 广播 (窄带, 留出两侧数字边带的空间)
    fm_p = dict(p)
    fm_p["mod"] = "FM"
    fm_p["dev"] = float(p.get("cdr_fm_dev") or 0.3 * half)   # 默认 60 kHz (Carson ~±72k)
    fm_p["msg_mode"] = "noise"
    fm_p["msg_norm"] = "rms"
    fm_p["msg_bw"] = float(p.get("cdr_fm_bw") or 12e3)
    fm_p["msg_pk"] = float(p.get("cdr_fm_pk") or 0.0)          # 不预加重, FM 宽度≈Carson
    fm_p["msg_order"] = 4
    fm_p["carrier"] = 0.0
    fm = _build_analog("FM", fm_p, sr, N, amp * 0.5)

    # 2) 左右两侧数字 OFDM 边带
    left = _cdr_ofdm_side(p, sr, N, amp * 0.5, -side_off, side_bw)
    right = _cdr_ofdm_side(p, sr, N, amp * 0.5, +side_off, side_bw)

    iq = (np.asarray(fm, dtype=complex)
          + np.asarray(left, dtype=complex)
          + np.asarray(right, dtype=complex))

    peak = float(np.max(np.abs(iq))) if N else 1.0
    if peak > 1e-9:
        iq = iq * (amp / peak)
    return iq.astype(np.complex64)

def _build_bridge(p, sr, N, amp):
    """无线网桥 (802.11 系列 OFDM) 信号。

    依据《无线网桥信号的调制方式与频谱知识》:
    - 核心调制 OFDM (802.11a/g/n/ac/ax 均为 OFDM / OFDMA)
    - 信道带宽 20 / 40 / 80 / 160 MHz (信道绑定), 子载波间隔固定 312.5 kHz
    - 自适应调制 BPSK / QPSK / 16QAM / 64QAM / 256QAM (链路质量越好阶数越高)
    - 2.4GHz 与 5.8GHz ISM 免执照频段 (中心频率偏移由 foff 体现)
    - 突发(TDD)帧结构: 帧间存在静默间隔
    OFDM 参数 (标准 802.11):
      信道带宽    IFFT 点数 Nfft   子载波间隔 Δf=312.5kHz   占用子载波
      20 MHz      64               52 (48 数据 + 4 导频)
      40 MHz      128              108
      80 MHz      256              234
      160 MHz     512              468
    占用带宽 ≈ N_used · Δf (20M->16.25M, 40M->33.75M, 80M->73.1M, 160M->146.25M)。
    """
    df = 312.5e3                       # 802.11 子载波间隔 (固定)
    # 信道带宽 -> IFFT 点数 (标准 20M 信道 Nfft=64)
    bw_in = float(p.get("bridge_bw") or 0.0)
    if bw_in <= 0:
        bw_in = sr * 0.8               # 未指定时按采样率的 80% 估算
    bw_in = min(bw_in, sr * 0.95)      # 不超过奈奎斯特
    bw_in = max(df * 8, bw_in)
    Nfft = max(8, int(round(bw_in / df)))
    Nfft = 1 << int(np.ceil(np.log2(Nfft)))           # 取 2 的幂 (64/128/256/512)
    cp = max(4, int(round(Nfft * 0.0625)))            # 循环前缀 ~1/16
    sym_len = Nfft + cp

    # 占用子载波: 标准约 81% (20M 信道 52/64)
    n_used = max(8, int(round(Nfft * 0.81)))
    n_used = min(int(n_used), Nfft - 2)

    # 调制阶数 -> 星座点 (自适应调制: BPSK..256QAM)
    order = str(p.get("bridge_mod") or "QPSK").upper()
    order_tbl = {"BPSK": 1, "QPSK": 2, "16QAM": 4, "64QAM": 6, "256QAM": 8,
                 "1024QAM": 10, "4096QAM": 12}
    if order not in order_tbl:
        order = "QPSK"
    if order == "BPSK":
        pts = np.array([1.0 + 0j, -1.0 + 0j], dtype=complex)
    elif order == "QPSK":
        pts = np.array([1 + 1j, -1 + 1j, -1 - 1j, 1 - 1j],
                       dtype=complex) / math.sqrt(2)
    elif order in ("16QAM", "64QAM"):
        pts = qam_constellation(order)
    elif order == "256QAM":
        pts = _qam_square(16)
    elif order == "1024QAM":                 # Wi-Fi 6 (802.11ax)
        pts = _qam_square(32)
    elif order == "4096QAM":                 # Wi-Fi 7 (802.11be)
        pts = _qam_square(64)
    else:
        pts = np.array([1 + 1j, -1 + 1j, -1 - 1j, 1 - 1j],
                       dtype=complex) / math.sqrt(2)
    pts = np.asarray(pts, dtype=complex)

    rng = _rng(p.get("seed"))
    half = n_used // 2
    n_pilot = 4

    def one_ofdm():
        freq = np.zeros(Nfft, dtype=complex)
        # 数据子载波: 随机取星座符号 (占满 n_used 个可用子载波)
        sym_all = pts[rng.integers(0, len(pts), n_used).astype(int)]
        freq[1:1 + half] = sym_all[:half]
        freq[Nfft - (n_used - half):Nfft] = sym_all[half:n_used]
        # 导频子载波: 仿 802.11 的 ±1 伪随机导频 (覆盖在固定位置)
        pilot_pos = np.linspace(1, Nfft - 2, n_pilot).astype(int)
        freq[pilot_pos] = (rng.integers(0, 2, n_pilot) * 2 - 1).astype(complex)
        time = np.fft.ifft(freq) * math.sqrt(Nfft)
        return np.concatenate([time[Nfft - cp:], time])

    # 突发(TDD)结构: 每帧连续若干 OFDM 符号, 帧间按占空比留空
    duty = float(p.get("bridge_duty") or 1.0)
    duty = min(1.0, max(0.05, duty))
    n_frame = int(p.get("bridge_frame") or 48)         # 每帧 OFDM 符号数
    n_frame = max(1, n_frame)
    gap = 0 if duty >= 1.0 else max(1, int(round(n_frame * (1.0 - duty) / duty)))

    out = np.zeros(N, dtype=np.complex64)
    pos = 0
    k = 0
    period = n_frame + gap
    while pos + sym_len <= N:
        if gap == 0 or (k % period) < n_frame:
            out[pos:pos + sym_len] = one_ofdm()[:sym_len]
        pos += sym_len
        k += 1
    if pos == 0:                                    # 连一个符号都放不下
        return _build_cw_like(p, sr, N, amp)

    p["occupied_bw"] = float(n_used * df)
    peak = float(np.max(np.abs(out))) if N else 1.0
    if peak > 1e-9:
        out = out * (amp / peak)
    return out.astype(np.complex64)

def _build_ais(p, sr, N, amp):
    """AIS: 实际为 GMSK (9600 bps, BT=0.5) 窄带调制。

    素材 ais 实测谱: 主瓣 0~10 kHz + 更宽处的次瓣 (约 50 kHz 处的旁瓣),
    这正是 GMSK 的谱形。原先的 AIS_MASK 把旁瓣全压成 -65 dB 地板, 与素材不符。
    改为真正的 GMSK 建模后, 主瓣+旁瓣结构与素材一致。"""
    p = dict(p)
    p["gmsk_sym"] = float(p.get("ais_sym") or 9600.0)
    p["gmsk_bt"] = 0.5
    return _build_gmsk(p, sr, N, amp)

def _build_dscdma(p, sr, N, amp):
    """DS-CDMA 直接序列扩频 (WCDMA / IS-95 CDMA2000)。

    真实参数:
      WCDMA (3G UMTS)      : 码片 3.84 Mcps, 信道 5 MHz, RRC 滚降 0.22
      CDMA (IS-95/cdma2000): 码片 1.2288 Mcps, 信道 1.25 MHz
    下行用 QPSK 扩频 (I/Q 各一路独立 ±1 码片), 谱是 (1+α)·Rc 宽的类噪声平台,
    这正是 3G 信号在频谱仪上"抬高噪底一大块"的观感来源。
    """
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)

    chip_rate = float(p.get("chip_rate") or 3.84e6)
    if chip_rate <= 0:
        chip_rate = 3.84e6
    ro = float(p.get("rolloff") or 0.22)
    if not (0.0 <= ro <= 1.0):
        ro = 0.22
    sps = max(2, int(round(sr / chip_rate)))

    rng = _rng(p.get("seed"))
    n_chip = max(16, int(N // sps) + 8)
    ci = rng.integers(0, 2, n_chip) * 2 - 1
    cq = rng.integers(0, 2, n_chip) * 2 - 1
    if str(p.get("cdma_qpsk") or "1").lower() in ("0", "false", "no"):
        chips = ci.astype(np.float64)               # 仅 BPSK 扩频
    else:
        chips = (ci + 1j * cq) / math.sqrt(2.0)     # QPSK 扩频 (标准方式)

    base = np.zeros(n_chip * sps, dtype=complex)
    base[::sps] = chips
    full = fast_convolve(base, rrc_filter(sps, ro))

    out = np.zeros(N, dtype=complex)
    m = min(N, full.size)
    out[:m] = full[:m]

    pk = float(np.max(np.abs(out))) if N else 0.0
    if pk > 1e-12:
        out = out * (amp / pk)
    return out.astype(np.complex64)

def _build_zigbee(p, sr, N, amp):
    """ZigBee / IEEE 802.15.4 (2.4 GHz): O-QPSK + 半正弦脉冲成形。

    标准参数: 码片速率 2 Mchip/s, 每 4 bit 映射 32 码片 (-> 250 kbit/s),
    O-QPSK + 半正弦成形 —— 这正是 MSK (h=0.5, 频偏 ±500 kHz, 音隔 1 MHz)。
    -20 dB 占用带宽约 2.2 MHz, 信道间隔 5 MHz。

    关键实现细节 (第一版在这里错了): 偶数码片走 I、奇数码片走 Q,
    Q 相对 I 延时一个码片 Tc; 每个支路脉冲宽度是 **2 个码片** (不是 1 个),
    即支路码率只有总码率的一半 (1 Mchip/s), 两支互相重叠半个脉冲 ——
    这样合成的包络才是恒定的 (MSK 的本质)。若按"每支路 2 Mchip/s + 半个
    码片宽脉冲"生成, 占用带宽会整整宽一倍。
    """
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)

    chip_rate = float(p.get("chip_rate") or 2.0e6)
    if chip_rate <= 0:
        chip_rate = 2.0e6
    sps = max(2, int(round(sr / chip_rate)))     # 一个码片的样本数
    per = 2 * sps                                 # 支路码片间隔 = 2 个码片

    rng = _rng(p.get("seed"))
    n_pair = max(8, int(N // per) + 4)
    ci = (rng.integers(0, 2, n_pair) * 2 - 1).astype(np.float64)   # 偶数码片
    cq = (rng.integers(0, 2, n_pair) * 2 - 1).astype(np.float64)   # 奇数码片

    # 半正弦脉冲, 宽度 = 2 个码片: p(t) = sin(pi*t/(2Tc)), t in [0, 2Tc]
    tt = (np.arange(per) + 0.5) / float(per)
    pulse = np.sin(np.pi * tt)

    up_i = np.zeros(n_pair * per, dtype=np.float64)
    up_i[::per] = ci
    up_q = np.zeros(n_pair * per, dtype=np.float64)
    up_q[::per] = cq

    fi = fast_convolve(up_i, pulse, "full")
    fq = fast_convolve(up_q, pulse, "full")
    fq = np.concatenate([np.zeros(sps), fq])[:fi.size]   # Q 路延时 Tc

    iq = fi + 1j * fq
    if iq.size < N:
        iq = np.tile(iq, int(math.ceil(N / float(max(iq.size, 1)))))
    iq = iq[:N]

    pk = float(np.max(np.abs(iq))) if N else 0.0
    if pk > 1e-12:
        iq = iq * (amp / pk)
    return iq.astype(np.complex64)

def _build_ssb(p, sr, N, amp):
    """单边带话音 SSB (USB 上边带 / LSB 下边带)。

    短波 (HF) 通信里最常见的语音方式: 抑制载波 + 只发送一个边带,
    占用带宽仅 2.7 kHz (300~3000 Hz), 与 AM 的双边带 + 载波完全不同。
    实现: 带限语音 m(t) -> 希尔伯特变换得解析信号 m + j·H{m} (USB);
    取共轭即得 LSB。希尔伯特用 FFT 实现 (不依赖 scipy)。
    """
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)

    pp = dict(p)
    pp["msg_mode"] = "noise"
    pp["msg_norm"] = "rms"
    pp["msg_bw"] = float(p.get("msg_bw") or p.get("ssb_fhi") or 3000.0)
    pp["msg_hp"] = float(p.get("ssb_flo") or 300.0)
    # 阶数要高: 真实 SSB 发射机有陡峭的晶体/机械滤波器边带,
    # 用 4 阶的话裙边拖到 5 kHz 开外, 占用带宽会明显偏宽。
    pp["msg_order"] = 12
    m = np.asarray(_message(pp, sr, N), dtype=np.float64)
    if m.size < N:
        m = np.tile(m, int(math.ceil(N / float(max(m.size, 1)))))
    m = m[:N]

    # 希尔伯特变换: 正频加倍、负频置零 -> m + j·H{m}
    F = np.fft.fft(m)
    Hmask = np.zeros(N)
    if N % 2 == 0:
        Hmask[0] = 1.0
        Hmask[N // 2] = 1.0
        Hmask[1:N // 2] = 2.0
    else:
        Hmask[0] = 1.0
        Hmask[1:(N + 1) // 2] = 2.0
    analytic = np.fft.ifft(F * Hmask)

    iq = np.conj(analytic) if str(p.get("ssb_side") or "USB").upper() == "LSB" \
        else analytic

    fc = float(p.get("carrier") or 0.0)
    if fc != 0.0:
        t = np.arange(N) / float(sr)
        iq = iq * np.exp(1j * 2 * np.pi * fc * t)

    pk = float(np.max(np.abs(iq))) if N else 0.0
    if pk > 1e-12:
        iq = iq * (amp / pk)
    return iq.astype(np.complex64)

def _build_fmcw(p, sr, N, amp):
    """FMCW 调频连续波 (车载雷达 / 无线电高度表 / 测距雷达)。

    与 RADAR (脉冲式) 不同: FMCW 全程连续发射, 瞬时频率在 [f0, f0+BW]
    之间周期性线性扫频, 靠收发频差测距, 因此在频谱上是一条被"抹平"的
    宽带平台 (不是脉冲谱线)。
    典型: 车载 77 GHz 雷达 BW 150~600 MHz, 扫频周期数十 us ~ 数 ms。
    基带生成时中心取 0 (扫频范围 -BW/2 ~ +BW/2)。
    fmcw_shape: "saw" 锯齿 (单向扫) / "tri" 三角 (上下扫)。
    """
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)

    bw = float(p.get("fmcw_bw") or 150e6)
    pri = float(p.get("fmcw_pri") or 1e-3)
    if pri <= 0:
        pri = 1e-3
    bw = float(min(bw, 0.95 * sr))                # 不超出奈奎斯特
    shape = str(p.get("fmcw_shape") or "saw").lower()

    t = np.arange(N) / float(sr)
    tm = np.mod(t, pri)                           # 扫频周期内的时间
    f0 = -bw / 2.0

    if shape.startswith("tri"):
        half = pri / 2.0
        seg1 = np.minimum(tm, half)
        d = np.maximum(tm - half, 0.0)
        ph = (f0 * seg1 + bw * seg1 * seg1 / pri
              + np.where(tm > half, (f0 + bw) * d - bw * d * d / pri, 0.0))
    else:
        ph = f0 * tm + bw * tm * tm / (2.0 * pri)

    iq = amp * np.exp(1j * 2 * np.pi * ph)
    return iq.astype(np.complex64)

MORSE_TABLE = {
    'A': '.-', 'B': '-...', 'C': '-.-.', 'D': '-..', 'E': '.', 'F': '..-.',
    'G': '--.', 'H': '....', 'I': '..', 'J': '.---', 'K': '-.-', 'L': '.-..',
    'M': '--', 'N': '-.', 'O': '---', 'P': '.--.', 'Q': '--.-', 'R': '.-.',
    'S': '...', 'T': '-', 'U': '..-', 'V': '...-', 'W': '.--', 'X': '-..-',
    'Y': '-.--', 'Z': '--..',
    '0': '-----', '1': '.----', '2': '..---', '3': '...--', '4': '....-',
    '5': '.....', '6': '-....', '7': '--...', '8': '---..', '9': '----.',
    '/': '-..-.', '?': '..--..', '.': '.-.-.-', ',': '--..--', '=': '-...-',
    '-': '-....-',
}

def _build_morse(p, sr, N, amp):
    """莫尔斯等幅报 (CW / A1A): OOK 键控单音。

    标准 PARIS 计时: 点长 = 1.2/WPM 秒; 划 = 3 点; 符号内间隔 1 点,
    字符间隔 3 点, 词间隔 7 点。差拍音调典型 600~1000 Hz。
    键控边沿做 ~5 ms 余弦整形 (真实发射机都有上升时间, 否则键控火花
    会把频谱展得很宽)。
    """
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)

    wpm = float(p.get("morse_wpm") or 20.0)
    if wpm <= 0:
        wpm = 20.0
    unit = 1.2 / wpm                               # 一个"点"的时长 (s)
    text = str(p.get("morse_text") or "CQ CQ DE BG1ABC")

    seq = []
    for ch in text.upper():
        if ch == " ":
            seq.append((0, 4))                     # 字符已补 3 点 + 4 点 = 词间 7 点
            continue
        code = MORSE_TABLE.get(ch)
        if code is None:
            continue
        for k, s in enumerate(code):
            if k:
                seq.append((0, 1))                 # 符号内间隔
            seq.append((1, 1 if s == '.' else 3))  # 点 1 单位 / 划 3 单位
        seq.append((0, 3))                         # 字符间隔
    if not seq:
        seq = [(1, 1), (0, 1)]

    env = []
    for on, n in seq:
        env.append(np.full(max(1, int(round(n * unit * sr))), 1.0 if on else 0.0))
    env = np.concatenate(env) if env else np.ones(N)

    # 键控边沿整形 (余弦升/降, 约 5 ms, 不超过半个点长)
    rise_s = min(0.005, unit * 0.5)
    n_r = max(1, int(round(rise_s * sr)))
    if n_r > 1 and env.size > n_r:
        w = 0.5 * (1 - np.cos(np.pi * np.arange(n_r) / float(n_r)))
        wsum = float(w.sum())
        if wsum > 1e-9:
            env = fast_convolve(env, w / wsum, "same")

    if env.size < N:
        env = np.tile(env, int(math.ceil(N / float(max(env.size, 1)))))
    env = env[:N]

    fc = float(p.get("morse_fc") or p.get("carrier") or 800.0)
    fc = max(-0.45 * sr, min(fc, 0.45 * sr))
    t = np.arange(N) / float(sr)
    iq = amp * env * np.exp(1j * 2 * np.pi * fc * t)

    pk = float(np.max(np.abs(iq))) if N else 0.0
    if pk > 1e-12:
        iq = iq * (amp / pk)
    return iq.astype(np.complex64)

def _build_gmsk(p, sr, N, amp):
    """GSM / GMSK: 高斯滤波 MSK, 调制指数 0.5 (频偏 Rs/4)。

    修复记录: 原实现把卷积核直接卷到稀疏的符号序列上,
    由于高斯核比符号序列还长, 'same' 卷积结果长度不对,
    导致 out 长度远小于 N。
    """
    sym_rate = float(p.get("gmsk_sym") or 270833.0)
    if sym_rate <= 0:
        sym_rate = 270833.0
    bt = float(p.get("gmsk_bt") or 0.5)
    # 调制指数 h: GMSK 规定 h=0.5 (每符号相位步进 ±pi/2);
    # 普通 GFSK (蓝牙 BR/EDR h=0.28~0.35, DECT h≈0.5) 用 gmsk_h 指定。
    h_idx = float(p.get("gmsk_h") or 0.5)
    if h_idx <= 0:
        h_idx = 0.5

    sps = max(2, int(round(sr / sym_rate)))
    n_sym = max(16, int(N // sps) + 8)
    rng = _rng(p.get("seed"))
    syms = rng.integers(0, 2, n_sym) * 2 - 1

    # 高斯成形滤波器 (单位增益)
    span = 4
    t = np.arange(-span * sps, span * sps + 1) / float(sr)
    sigma = bt / (2.0 * math.sqrt(2.0 * math.log(2)) * sym_rate)
    h = np.exp(-(t ** 2) / (2 * sigma ** 2))
    h = h / h.sum()

    # NRZ 矩形 -> 高斯滤波 -> 相位积分 (每符号相位步进 ±pi/2)
    nrz = np.repeat(syms.astype(np.float64), sps)
    shaped = fast_convolve(nrz, h, "same")
    phase = np.cumsum(shaped) * (math.pi * h_idx / float(sps))
    iq = amp * np.exp(1j * phase)

    if len(iq) < N:
        reps = int(math.ceil(N / float(max(len(iq), 1))))
        iq = np.tile(iq, reps)
    return iq[:N].astype(np.complex64)

def _build_atv(p, sr, N, amp):
    """模拟电视 (VSB-AM): 限带视频噪声 + 残留载波。修复: rng 未定义。"""
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)

    bw = float(p.get("atv_bw") or 8e6)
    fc = float(p.get("atv_fc") or 0.0)
    rng = _rng(p.get("seed"))

    noise = rng.standard_normal(N)
    F = np.fft.fft(noise)
    f = np.fft.fftfreq(N, 1.0 / float(sr))
    F = F * (np.abs(f) <= (bw / 2.0))
    video = np.real(np.fft.ifft(F)) + 0.6

    pk_v = float(np.max(np.abs(video)))
    if pk_v > 1e-12:
        video = video / pk_v

    t = np.arange(N) / float(sr)
    iq = amp * video * np.exp(1j * 2 * np.pi * fc * t)
    return iq.astype(np.complex64)

def _build_adsb(p, sr, N, amp):
    """ADS-B: 1 Mbps PPM (Mode S)。

    用截断高斯脉冲替换矩形 chip: 高斯脉冲的谱没有精确零点, 能把矩形 PPM 在 1 MHz 处的
    深 sinc 零点填充为'浅零点' (素材实测在 1/2/3 MHz 处只有 -24/-9.5/-39 dB 的浅凹陷),
    同时保持主瓣平坦、带外平滑滚降。"""
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)

    rate = float(p.get("adsb_rate") or 1e6)
    rng = _rng(p.get("seed"))

    chip_samp = max(2, int(round(sr / rate)))     # 半比特宽度 (1 us @8Msps -> 8)
    sym_samp = 2 * chip_samp                       # 一个比特 = 前后两个 chip
    n_bits = max(1, int(N // sym_samp))
    bits = rng.integers(0, 2, n_bits)

    # 截断高斯脉冲 (宽度 = 一个半比特); sigma 取 0.4*chip_samp 时,
    # 其谱在 1 MHz (PPM 周期) 处约 -22~-24 dB, 与素材的浅凹陷一致。
    sigma = chip_samp * 0.4
    g = np.exp(-0.5 * ((np.arange(chip_samp) - (chip_samp - 1) / 2.0) / max(sigma, 1e-6)) ** 2)
    g = g / g.sum()

    env = np.zeros(n_bits * sym_samp, dtype=np.float64)
    for i, b in enumerate(bits):
        base = i * sym_samp
        if b == 1:
            env[base:base + chip_samp] += g
        else:
            env[base + chip_samp:base + sym_samp] += g

    pk = float(np.max(np.abs(env))) if env.size else 1.0
    if pk > 1e-12:
        env = env / pk

    out = np.zeros(N, dtype=np.complex64)
    m = min(N, env.size)
    out[:m] = env[:m]
    if m < N:                                     # 不足一帧 -> 回卷补齐
        out[m:] = out[:N - m]

    peak = float(np.max(np.abs(out))) if N else 1.0
    if peak > 1e-9:
        out = out * (amp / peak)
    return out.astype(np.complex64)

def _build_freqhop(p, sr, N, amp):
    """跳频信号。修复: idx 从未初始化 -> NameError。"""
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)

    band = float(p.get("fh_band") or 5e6)
    rate = float(p.get("fh_rate") or 1000.0)
    rng = _rng(p.get("seed"))

    hop_samp = max(2, int(round(sr / rate)))
    out = np.zeros(N, dtype=np.complex64)
    idx = 0

    while idx + hop_samp <= N:
        f = (rng.random() - 0.5) * band
        ph = 2 * np.pi * f * (np.arange(hop_samp) / float(sr))
        out[idx:idx + hop_samp] = amp * (np.cos(ph) + 1j * np.sin(ph))
        idx += hop_samp

    if idx == 0:
        # 修复: N 连一个跳频段都放不下时, 原实现会静默返回全零信号;
        # 至少放一段部分跳频, 保证输出非零。
        m = int(N)
        f = (rng.random() - 0.5) * band
        ph = 2 * np.pi * f * (np.arange(m) / float(sr))
        out[:m] = amp * (np.cos(ph) + 1j * np.sin(ph))
    elif idx < N:
        out[idx:] = out[:N - idx]

    peak = float(np.max(np.abs(out))) if N else 1.0
    if peak > 1e-9:
        out = out * (amp / peak)
    return out.astype(np.complex64)

def _lchirp(n, sr, fc, bw):
    n = int(max(n, 1)); t = np.arange(n) / sr; f0 = fc - bw / 2.0
    if n > 1:
        k = bw * sr / n
        phase = 2 * np.pi * (f0 * t + 0.5 * k * t ** 2)
    else: phase = 2 * np.pi * f0 * t
    return np.exp(1j * phase)

def _radar_spec(pattern):
    out = []
    for s in pattern.get("segments", []):
        pw = float(s["pw"]) * 1e-6
        if "pri" in s: pri = float(s["pri"]) * 1e-6
        else: pri = pw + (float(s.get("gap", 0)) * 1e-6)
        f0 = float(s.get("f_lo", 0.0)) * 1e6; f1 = float(s.get("f_hi", 0.0)) * 1e6
        out.append(dict(pw=pw, pri=pri, count=int(s.get("count", 1)), f0=f0, f1=f1))
    return out

def _build_radar(p, sr, N, amp):
    spec = p.get("radar_spec")
    if not spec:
        pat = RADAR_PATTERNS.get(p.get("radar", "agile1"), RADAR_PATTERNS["agile1"])
        spec = _radar_spec(pat)

    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)

    tp = float(p.get("radar_taper") or 0.0)
    out = np.zeros(N, dtype=np.complex64)
    idx = 0
    guard = 0

    while idx < N and guard < 100000:
        guard += 1
        moved = False

        for g in spec:
            n_pw = max(2, int(round(g["pw"] * sr)))
            n_pri = max(n_pw, int(round(g["pri"] * sr)))

            for _ in range(int(g["count"])):
                if idx >= N:
                    break
                seg = _lchirp(n_pw, sr, (g["f0"] + g["f1"]) / 2.0, g["f1"] - g["f0"])

                if tp > 0 and n_pw > 4:
                    k = min(n_pw // 2, max(2, int(round(tp * n_pw / 2.0)) * 2))
                    w = np.hanning(k + 2)[1:-1]
                    core = np.ones(n_pw, dtype=np.float64)
                    core[:k // 2] = w[:k // 2]
                    core[-k // 2:] = w[-k // 2:]
                    seg = seg * core

                m = min(n_pw, N - idx)
                out[idx:idx + m] = amp * seg[:m]

                idx += n_pri          # 每个脉冲都要推进, 否则会互相覆盖
                moved = True

            if idx >= N:
                break

        if not moved:
            break

    if not out.any() and spec:        # N 太小导致一个脉冲都没落下 -> 至少放一个
        g = spec[0]
        n_pw = min(N, max(2, int(round(g["pw"] * sr))))
        if n_pw > 0:
            seg = _lchirp(n_pw, sr, (g["f0"] + g["f1"]) / 2.0, g["f1"] - g["f0"])
            out[:n_pw] = amp * seg[:n_pw]

    peak = float(np.max(np.abs(out))) if len(out) else 1.0
    if peak > 1e-9:
        out = out * (amp / peak)
    return out.astype(np.complex64)

def _anti_clip(iq, limit=0.95):
    if len(iq) == 0: return iq
    m = float(np.max(np.abs(iq)))
    if m > limit and np.isfinite(m) and m > 0:
        iq = iq * (limit / m)
    return iq

def _orig_sample_rate(path):
    stem = path
    if stem.lower().endswith(".cs16"): stem = stem[:-5]
    for cand in (path + ".xml", stem + ".xml"):
        if not os.path.exists(cand): continue
        try:
            with open(cand, "r", encoding="utf-8", errors="ignore") as f: txt = f.read()
        except Exception: continue

        for line in txt.splitlines():
            if 'name="sample_rate"' not in line: continue
            try:
                seg = line.split('val="', 1)[1].split('"', 1)[0]
                return float(seg)
            except Exception: pass
    return 0.0

def _resample_iq(x, src_sr, dst_sr, n_out):
    L = int(np.asarray(x).size); K = max(1, int(round(L * dst_sr / float(src_sr))))
    Y = np.fft.fft(x)
    f_src = np.fft.fftfreq(L) * src_sr
    order = np.argsort(f_src)
    f_src = f_src[order]; Ys = Y[order]
    f_dst = np.fft.fftfreq(K) * dst_sr

    keep = np.abs(f_dst) <= src_sr / 2.0 * 0.999999
    Z = np.zeros(K, dtype=complex)

    if keep.any():
        fd = f_dst[keep]
        Z[keep] = (np.interp(fd, f_src, Ys.real, left=0.0, right=0.0) +
                   1j * np.interp(fd, f_src, Ys.imag, left=0.0, right=0.0))

    y = np.fft.ifft(Z) * (K / float(L))
    if y.size < n_out: y = np.concatenate([y, np.zeros(n_out - y.size, dtype=complex)])
    return y[:n_out]

def _orig_file(p):
    """在 ORIG_DIR 下定位与当前信号同名的原始素材 (.cs16)。"""
    for base in (p.get("replay_src"), p.get("name")):
        if not base:
            continue
        b = str(base)
        for path in (os.path.join(ORIG_DIR, b), os.path.join(ORIG_DIR, b) + ".cs16"):
            if os.path.isfile(path):
                return path
    return None

def _replay_block(p, N=None):
    """回放 E:\\NEW\\signalwave 下的原始素材 (循环拼接 + 必要时重采样)。

    修复记录: 原实现把字符串路径拿去 len()/`np.fromfile`/
    `float(...) and bool` 当采样率, 还在未定义的 N / src_sr / rep 上做算术,
    只要走进这条分支必然抛异常。
    """
    path = _orig_file(p)
    if not path:
        return None

    # ---- 先算"需要多少样本", 再去读文件 ----
    # 性能要点: 素材目录(E:\NEW\signalwave)里单个文件最大 614 MB(lte_20m),
    # 而预览只要 26 万样本。原实现在这里无条件 np.fromfile 整个文件 ——
    # 384 MB 的 analogTV 素材要 1.0 s / 峰值 2.3 GB, 614 MB 的要 1.6 s / 3.7 GB,
    # 点在信号库里的 analogTV 上界面就会明显卡一下(还可能触发内存换页)。
    # 现在改成只读需要的那一段(count=), 同样的预览降到 10 ms 量级。
    src_sr = _orig_sample_rate(path)
    dst_sr = float(p.get("sample_rate") or 0.0)
    amp = float(p.get("amplitude") or 0.8)

    # 回放模式沿用素材自身采样率, 保证频谱形状与原始信号一致
    if src_sr > 0:
        dst_sr = src_sr
        p["sample_rate"] = src_sr

    if N is None:
        N = int(round(float(p.get("duration") or 1.0) * max(dst_sr, 1.0)))
    N = int(max(1, min(int(N), MAX_SAMPLES)))

    need = N
    if src_sr > 0 and dst_sr > 0 and abs(src_sr - dst_sr) > 1e-6:
        need = int(math.ceil(N * src_sr / float(dst_sr)))
    need = int(min(need + 8192, MAX_SAMPLES))       # +8192 是重采样/拼接的余量

    try:
        n_pairs = int(os.path.getsize(path) // 4)   # cs16: I/Q 各 1 个 int16
    except OSError:
        return None
    if n_pairs <= 0:
        return None

    try:
        # 只读需要的样本对; 素材比需求短时才读满整个文件(交给下面 tile 循环拼接)
        raw = np.fromfile(path, dtype=np.int16, count=int(min(need, n_pairs)) * 2)
    except Exception:
        return None
    raw = raw[:raw.size - (raw.size % 2)]
    if raw.size < 2:
        return None

    # 交错 int16 的 I/Q -> float32 后, 内存布局恰好等于 complex64,
    # 直接 view 成复数即可, 省掉原来的两个 astype 临时数组。
    blk = (raw.astype(np.float32) / 32768.0).view(np.complex64).ravel()

    if blk.size < need:
        reps = int(math.ceil(need / float(blk.size)))
        blk = np.tile(blk, reps)
    seg = blk[:need]

    if src_sr > 0 and dst_sr > 0 and abs(src_sr - dst_sr) > 1e-6:
        try:
            seg = _resample_iq(seg, src_sr, dst_sr, N)
        except Exception:
            seg = blk[:N]

    # 用 complex64 而不是 complex: 4000 万样本下 complex128 会多占 640 MB
    seg = np.asarray(seg, dtype=np.complex64)
    if seg.size < N:
        seg = np.concatenate([seg, np.zeros(N - seg.size, dtype=np.complex64)])
    seg = seg[:N]

    # 峰值分块求 —— 在 4000 万点上做 abs() 会多开一个 160 MB 的临时数组
    pk = 0.0
    step = 1 << 22
    for s in range(0, int(seg.size), step):
        m = float(np.abs(seg[s:s + step]).max())
        if m > pk:
            pk = m
    if pk > 1e-9:
        seg = seg * (amp / pk)
    return np.asarray(seg, dtype=np.complex64)

def _slot_mask(tx, count):
    """解析"发射时隙"字符串 -> 长度 count 的布尔列表。

    支持:
      ""/"all"/"full"/"满"/"满时隙"  -> 全部时隙发射 (连续, 相当于不分时隙)
      纯 0/1 且长度 == 时隙数         -> 位图 (如 DMR 半时隙 "10"、GSM 单突发 "10000000")
      索引/区间 (1 起)               -> 如 "1" / "1,2" / "1-2" / "1 3"
    解析失败时按"全发"处理, 保证不会误伤信号。
    """
    count = int(count)
    if count <= 0:
        return None
    s = str(tx or "").strip().lower()
    if s in ("", "all", "full", "满", "满时隙", "连续"):
        return [True] * count
    compact = s.replace(" ", "")
    if len(compact) == count and all(c in "01" for c in compact):
        return [c == "1" for c in compact]
    mask = [False] * count
    ok = False
    for tok in re.split(r"[,\s]+", compact):
        if not tok:
            continue
        m = re.match(r"^(\d+)-(\d+)$", tok)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            for i in range(min(a, b), max(a, b) + 1):
                if 1 <= i <= count:
                    mask[i - 1] = True
                    ok = True
            continue
        if tok.isdigit():
            i = int(tok)
            if 1 <= i <= count:
                mask[i - 1] = True
                ok = True
    if not ok:
        return [True] * count
    return mask

def _slot_period_window(sr, count, dur, mask, ramp=None):
    """构造一个"帧"周期的门控窗 (长度 = count*dur*sr 样本), 含升余弦斜坡。

    斜坡用于削弱开关瞬间的频谱扩展; 由于按周期循环平滑, 帧边界处保持连续。
    """
    sps_f = float(dur) * float(sr)                  # 每时隙样本数 (可能非整数)
    P = int(round(sps_f * count))
    if P < 2:
        return None, 0
    pw = np.zeros(P, dtype=np.float32)
    for i in range(count):
        if not mask[i]:
            continue
        a = int(round(i * sps_f))
        b = int(round((i + 1) * sps_f))
        a = max(0, min(a, P))
        b = max(a, min(b, P))
        if b > a:
            pw[a:b] = 1.0
    if ramp is None:
        ramp = int(min(sps_f * 0.05, sr * 5e-5))     # 默认 ~5% 时隙 或 50us
    ramp = int(max(0, min(ramp, P // 4)))
    if ramp >= 1:
        k = 0.5 * (1.0 - np.cos(2.0 * np.pi * np.arange(2 * ramp + 1) / (2 * ramp)))
        k = k / k.sum()
        pw = fast_convolve(np.r_[pw[-ramp:], pw, pw[:ramp]], k, "same")[ramp:ramp + P]
        pw = np.clip(pw, 0.0, 1.0).astype(np.float32)
    return pw, P

def _apply_tdma_slots(iq, sr, p):
    """按"时隙(TDMA)"配置对已合成信号做门控 (就地对 iq 生效后返回)。

    参数 (p 内):
      slot_count : 每帧时隙数 (<=1 或未配置 -> 不分时隙)
      slot_dur   : 每时隙时长 (秒)
      slot_tx    : 发射时隙, 见 _slot_mask (""/"all" = 满时隙/连续)
      slot_ramp  : 可选, 斜坡样本数覆盖
    满时隙 -> 原样返回; 半时隙/位图 -> 发指定时隙、其余时隙静默 (发30ms停30ms)。
    """
    n = int(iq.size)
    if n == 0 or sr <= 0:
        return iq
    count = int(p.get("slot_count") or 0)
    dur = float(p.get("slot_dur") or 0.0)
    if count <= 1 or dur <= 0:
        return iq
    mask = _slot_mask(p.get("slot_tx"), count)
    if mask is None or all(mask):
        return iq                                   # 满时隙 -> 连续, 不变

    ramp = p.get("slot_ramp")
    if ramp in ("", None):
        ramp = None
    else:
        try:
            ramp = int(float(ramp))
        except (TypeError, ValueError):
            ramp = None

    pw, P = _slot_period_window(sr, count, dur, mask, ramp)
    if pw is None or P < 2:
        return iq

    CH = 1 << 20                                    # 分块, 避免构造整段大窗
    for s in range(0, n, CH):
        e = min(n, s + CH)
        idx = np.arange(s, e) % P
        iq[s:e] *= pw[idx]
    return iq

_MIX_INHERIT = ("symbol_rate", "rolloff", "M", "mu", "dev", "carrier", "tone",
                "amplitude")

def _resample_complex(iq, sr_from, sr_to):
    """把复基带信号从 sr_from 重采样到 sr_to (scipy 优先, 否则线性插值)。"""
    iq = np.asarray(iq)
    if iq.size == 0 or sr_from <= 0 or abs(sr_from - sr_to) < 1e-6:
        return iq.astype(np.complex64)
    up = int(round(float(sr_to)))
    dn = int(round(float(sr_from)))
    if up <= 0 or dn <= 0:
        return iq.astype(np.complex64)
    g = math.gcd(up, dn)
    up //= g
    dn //= g
    try:
        from scipy.signal import resample_poly
        out = resample_poly(iq, up, dn)
        return np.asarray(out, dtype=np.complex64)
    except Exception:
        n_out = max(1, int(round(iq.size * float(sr_to) / float(sr_from))))
        xi = np.arange(iq.size)
        xo = np.linspace(0.0, iq.size - 1, n_out)
        out = (np.interp(xo, xi, np.real(iq))
               + 1j * np.interp(xo, xi, np.imag(iq)))
        return out.astype(np.complex64)

def _mix_spec_of(c):
    """组件 dict -> 规格字符串片段 (供 XML 记录)。"""
    c = dict(c or {})
    tok = c.get("name") or c.get("mod") or "CW"
    parts = [str(tok)]
    if c.get("amp") not in (None, ""):
        parts.append("amp=%s" % c["amp"])
    if c.get("foff") not in (None, ""):
        parts.append("foff=%s" % c["foff"])
    if c.get("slot_count") not in (None, ""):
        parts.append("slots=%s" % c["slot_count"])
    if c.get("slot_dur") not in (None, ""):
        try:
            parts.append("dur=%g" % (float(c["slot_dur"]) * 1000.0))
        except Exception:
            parts.append("dur=%s" % c["slot_dur"])
    if c.get("slot_tx") not in (None, ""):
        parts.append("tx=%s" % c["slot_tx"])
    return ",".join(parts)

def _mix_component_params(c, top):
    """把单个混频组件展开为完整的信号参数字典。"""
    c = dict(c or {})
    amp = c.pop("amp", None)
    foff = c.pop("foff", None)
    sc = c.pop("slot_count", None)
    sd = c.pop("slot_dur", None)
    stx = c.pop("slot_tx", None)
    token = str(c.pop("name", "") or c.pop("mod", "") or "").strip()

    base = {}
    if token:
        try:
            base = dict(resolve_signal(token))
        except Exception:
            base = {}
    if not base or not base.get("mod") or base.get("mod") == "REPLAY":
        up = token.upper()
        if up not in [x.upper() for x in ALL_MODS] and up != "REPLAY":
            up = "CW"
        base = dict(base) if base else {}
        if not base.get("replay_src"):
            base["mod"] = up

    # 继承顶层参数的合理默认
    for k in _MIX_INHERIT:
        if k not in base and top.get(k) not in (None, ""):
            base[k] = top[k]
    # 组件显式参数覆盖
    for k, v in c.items():
        if v not in (None, ""):
            base[k] = v

    if amp not in (None, ""):
        try:
            base["amp"] = float(amp)
        except (TypeError, ValueError):
            pass
    if foff not in (None, ""):
        try:
            base["foff"] = float(foff)
        except (TypeError, ValueError):
            pass
    if sc not in (None, ""):
        try:
            base["slot_count"] = int(float(sc))
        except (TypeError, ValueError):
            pass
    if sd not in (None, ""):
        try:
            base["slot_dur"] = float(sd)
        except (TypeError, ValueError):
            pass
    if stx not in (None, ""):
        base["slot_tx"] = str(stx)
    base.setdefault("mod", "CW")
    return base

def _build_mfsk_tones(p, sr, N, amp):
    """通用 N 音 MFSK (相位连续, 无包络整形)。

    业余无线电 / 遥测里的一整类"多音"制式都用它:
      WSPR   : 4 音,  音隔 = 符号率 = 1.4648 Hz   (弱信号信标)
      JT65   : 65 音, 音隔 = 符号率 = 2.6917 Hz   (月面反射/弱信号通联)
      Olivia : 8/16/32 音, 音隔 = 符号率          (如 32/1000 -> 31.25 Hz)
      16FSK  : 16 音, 音隔 = 2·dev
    相位是逐样本累积出来的 (真实发射机的做法), 所以符号交界处相位连续、
    包络恒定 —— 谱是一簇离散音线而不是 sinc 包络, 这正是弱信号模式能扛住
    多普勒频移和衰落的原因。
    """
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)
    M = max(2, int(p.get("mfsk_tones") or p.get("M") or 4))
    sp = float(p.get("tone_spacing") or 0.0)
    rs = float(p.get("symbol_rate") or 0.0)
    if rs <= 0:
        rs = sp
    if sp <= 0:
        sp = rs
    if rs <= 0:
        rs = sp = max(1.0, float(sr) / 512.0)

    rng = _rng(p.get("seed"))
    sps = max(2, int(round(sr / rs)))
    ns = max(2, int(N // sps) + 2)
    syms = rng.integers(0, M, ns)
    f = (syms.astype(np.float64) - (M - 1) / 2.0) * sp
    freq = np.repeat(f, sps)
    if freq.size < N:
        freq = np.concatenate([freq, np.full(N - freq.size, float(f[-1]))])
    freq = freq[:N]
    iq = np.exp(1j * (2 * np.pi * np.cumsum(freq) / float(sr)))
    return (float(amp) * iq).astype(np.complex64)

def _build_subcarrier(p, sr, N, amp):
    """副载波调制: 图像 / 传真类信号 (AM 副载波 或 FM 副载波)。

      NOAA APT (极轨气象卫星云图): AM 副载波 2400 Hz, 图像 0~2 kHz, 2 行/秒
      SSTV     (业余慢扫描电视)  : FM 副载波 1500 Hz, 频偏 ±800 Hz
                                   (1200 Hz=同步/黑, 2300 Hz=白)
      FAX      (无线气象传真)    : FM 副载波 1500 Hz, 频偏 ±400 Hz, 120 行/分
    基带信源用 _message 生成 (带限噪声 ≈ 图像内容), 再搬到一个副载波上。
    """
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)
    m = _message(p, sr, N)
    t = np.arange(N) / float(sr)
    sub_fc = float(p.get("sub_fc") or 1500.0)
    typ = str(p.get("sub_type") or "fm").lower()

    if typ == "am":
        # AM 副载波: 载波 + 双边带 (NOAA APT 就是这种)
        depth = float(p.get("sub_depth") or 0.8)
        depth = min(max(depth, 0.0), 1.0)
        iq = (1.0 + depth * m) * np.exp(1j * 2 * np.pi * sub_fc * t)
    else:
        # FM 副载波: 瞬时频率 = sub_fc + dev·m(t)   (SSTV / 气象传真)
        dev = float(p.get("sub_dev") or 800.0)
        iq = np.exp(1j * 2 * np.pi * np.cumsum(sub_fc + dev * m) / float(sr))

    return (float(amp) * iq).astype(np.complex64)

def _build_soqpsk(p, sr, N, amp):
    """SOQPSK (成形偏移 QPSK) —— MIL-STD / IRIG-106 遥测标准调制。

    与 ZigBee 的 O-QPSK 半正弦是同一套机制: I/Q 各传一半符号、相互错开
    半个符号, 支路脉冲宽度 = 2 个符号周期。因此包络恒定 (可用饱和功放),
    占用带宽 ≈ 1.2·Rs, 明显窄于同码率的普通 QPSK。
    """
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)
    rs = float(p.get("symbol_rate") or 0.0)
    if rs <= 0:
        rs = float(sr) / 8.0
    sps = max(2, int(round(sr / rs)))       # 一个符号的样本数
    per = 2 * sps                            # 支路符号间隔 = 2 个符号

    rng = _rng(p.get("seed"))
    n_pair = max(8, int(N // per) + 4)
    ci = (rng.integers(0, 2, n_pair) * 2 - 1).astype(np.float64)
    cq = (rng.integers(0, 2, n_pair) * 2 - 1).astype(np.float64)

    tt = (np.arange(per) + 0.5) / float(per)
    pulse = np.sin(np.pi * tt)               # 半正弦, 宽 2 个符号
    up_i = np.zeros(n_pair * per, dtype=np.float64)
    up_q = np.zeros(n_pair * per, dtype=np.float64)
    up_i[::per] = ci
    up_q[::per] = cq
    fi = fast_convolve(up_i, pulse, "full")
    fq = fast_convolve(up_q, pulse, "full")
    fq = np.concatenate([np.zeros(sps), fq])[:fi.size]      # Q 路延时 T/2

    iq = fi + 1j * fq
    if iq.size < N:
        iq = np.tile(iq, int(math.ceil(N / float(max(iq.size, 1)))))
    iq = iq[:N]
    pk = float(np.max(np.abs(iq))) if N else 0.0
    if pk > 1e-12:
        iq = iq * (float(amp) / pk)
    return iq.astype(np.complex64)

def _build_thss(p, sr, N, amp):
    """跳时扩频 THSS: 每帧只在伪随机选中的一个时隙里发一段突发。

    与 FHSS 的区别 —— FHSS 换的是"频率", THSS 换的是"时间"。
    时隙化发送把频谱打散 (门控谱), 且非协作接收机看不到发送时刻。
    """
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)
    frame = float(p.get("th_frame") or 2e-3)
    nslot = max(2, int(p.get("th_slot") or 8))
    duty = min(max(float(p.get("th_duty") or 0.25), 0.02), 1.0)
    rs = float(p.get("th_rate") or 0.0)
    if rs <= 0:
        rs = 1.0 / max(frame / nslot * duty, 1e-12)
    rng = _rng(p.get("seed"))

    fr_n = max(2, int(round(frame * sr)))
    sl_n = max(2, fr_n // nslot)
    bn = max(2, int(sl_n * duty))

    env = np.zeros(N, dtype=np.float64)
    pos = 0
    while pos + fr_n <= N:
        s = int(rng.integers(0, nslot))
        a = pos + s * sl_n
        b = min(a + bn, pos + fr_n, N)
        if a < N:
            env[a:b] = 1.0
        pos += fr_n
    if not env.any():
        env[:max(2, N // 4)] = 1.0

    # 突发内是 BPSK 码流
    sps = max(2, int(round(sr / rs)))
    ns = max(4, int(N // sps) + 2)
    bits = (rng.integers(0, 2, ns) * 2 - 1).astype(np.float64)
    base = np.repeat(bits, sps)
    if base.size < N:
        base = np.concatenate([base, np.full(N - base.size, float(bits[-1]))])
    base = base[:N]

    # 门控边沿整形 (避免矩形门控把谱展到无限宽)
    n_r = max(1, int(sps // 8))
    w = np.hanning(max(3, n_r * 2) + 1)[1:-1] if n_r > 1 else np.ones(1)
    if w.size > 1:
        env = fast_convolve(env, w / float(w.sum()), "same")

    iq = (base * env).astype(np.complex128)
    pk = float(np.max(np.abs(iq))) if N else 0.0
    if pk > 1e-12:
        iq = iq * (float(amp) / pk)
    return iq.astype(np.complex64)

def _build_pulse_mod(p, sr, N, amp):
    """脉冲调制 PAM / PWM / PPM: 周期性射频脉冲串, 参数逐周期随信源变化。

    PAM 脉幅调制: 脉冲幅度随信源变 (功率控制 / RC 模拟量 / 发射机包络)
    PWM 脉宽调制: 脉冲宽度随信源变 (D 类开关功放、舵机、调光)
    PPM 脉位调制: 脉冲位置随信源变 (光通信、UWB、RC 遥控标准 1~2 ms 帧)
    参数: pulse_pri 重复周期(s), pulse_pw 基准脉宽(s), pulse_fc 脉内载频(Hz)
    """
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)
    mod = str(p.get("mod") or "PAM").upper()
    pri = float(p.get("pulse_pri") or 1e-3)
    pw = float(p.get("pulse_pw") or 1e-4)
    fc = float(p.get("pulse_fc") or 0.0)
    m = _message(p, sr, N)

    npri = max(4, int(round(pri * sr)))
    npw = max(1, min(int(round(pw * sr)), npri - 2))
    ncyc = int(math.ceil(N / float(npri)))

    # 每个重复周期取一个信源样点
    v = m[np.minimum(np.arange(ncyc) * npri, N - 1)]
    if mod == "PWM":
        widths = np.maximum(1, np.round(npw * (0.5 + 0.45 * v))).astype(np.int64)
        amps = np.ones(ncyc)
        offs = np.zeros(ncyc, dtype=np.int64)
    elif mod == "PPM":
        widths = np.full(ncyc, npw, dtype=np.int64)
        amps = np.ones(ncyc)
        offs = np.round(0.35 * npri * v).astype(np.int64)
    else:                                     # PAM
        widths = np.full(ncyc, npw, dtype=np.int64)
        amps = 0.5 + 0.5 * v
        offs = np.zeros(ncyc, dtype=np.int64)

    env = np.zeros(N, dtype=np.float64)
    for k in range(ncyc):
        a = k * npri + int(offs[k])
        b = a + int(widths[k])
        a = max(0, min(a, N))
        b = max(0, min(b, N))
        if b > a:
            env[a:b] = amps[k]

    t = np.arange(N) / float(sr)
    iq = (env * np.exp(1j * 2 * np.pi * fc * t)).astype(np.complex128)
    pk = float(np.max(np.abs(iq))) if N else 0.0
    if pk > 1e-12:
        iq = iq * (float(amp) / pk)
    return iq.astype(np.complex64)

def _build_analog_ext(mod, p, sr, N, amp):
    """模拟调制补充族: DSB-SC / VSB / PM / NBFM / WBFM。

    DSB-SC  抑制载波双边带: y = m(t)              -> 带宽 2·fmax, 无载波分量
    VSB     残留边带      : 一个边带完整 + 另一边带只留一小段过渡带
                            (模拟电视图像 / ATSC 8-VSB 用的就是这个思路)
    PM      调相          : y = exp(j·β·m(t)), β 为调制指数 (rad)
    NBFM    窄带调频      : 对讲机 12.5 kHz 信道, 频偏 ±2.5 kHz
    WBFM    宽带调频广播  : 频偏 ±75 kHz, 15 kHz 立体声复合基带
    """
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)
    m = _message(p, sr, N)
    t = np.arange(N) / float(sr)

    if mod == "DSBSC":
        iq = m.astype(np.complex128)

    elif mod == "VSB":
        up_edge = float(p.get("msg_bw") or 0.0)
        fv = float(p.get("vsb_vest") or 0.0)
        if fv <= 0:
            fv = max(up_edge * 0.1, 1.0)
        M = np.fft.fft(m.astype(np.complex128))
        f = np.fft.fftfreq(N, 1.0 / float(sr))
        H = np.zeros(N)
        # 上边带: 保留到 msg_bw, 然后短过渡截止 (否则信源滤波器的裙边
        # 会一路拖到几倍带宽外, 出来的根本不是 6 MHz 电视频道的形状)
        if up_edge > 0:
            H[(f > 0) & (f <= up_edge)] = 1.0
            tr = (f > up_edge) & (f < 1.15 * up_edge)
            H[tr] = 0.5 * (1 + np.cos(np.pi * (f[tr] - up_edge)
                                      / (0.15 * up_edge)))
        else:
            H[f > 0] = 1.0
        H[(f <= 0) & (f > -fv)] = 1.0                   # 下边带残留
        mid = (f <= -fv) & (f > -1.3 * fv)              # 残留过渡带
        if fv > 0:
            H[mid] = 0.5 * (1 + np.cos(np.pi * (-f[mid] - fv) / (0.3 * fv)))
        iq = np.fft.ifft(M * H)

    elif mod == "PM":
        beta = float(p.get("pm_index") or 1.0)
        iq = np.exp(1j * beta * m)

    else:                                               # NBFM / WBFM
        dev = float(p.get("dev") or (2500.0 if mod == "NBFM" else 75000.0))
        iq = np.exp(1j * 2 * np.pi * dev * np.cumsum(m) / float(sr))

    pk = float(np.max(np.abs(iq))) if N else 0.0
    if pk > 1e-12:
        iq = iq * (float(amp) / pk)
    return iq.astype(np.complex64)

def _build_atsc(p, sr, N, amp):
    """ATSC 8-VSB (美/韩/墨 地面数字电视)。

    标准参数: 8 电平 PAM, 符号率 10.762 MSym/s (-> 32.28 Mbit/s),
    滚降 0.115, 残留边带 0.31 MHz -> 总占用 5.69 MHz, 放进 6 MHz 电视频道。
    导频位于被抑制的载波频率处 (频道低端 +0.31 MHz), 功率约占 0.3 dB。
    """
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)
    rs = float(p.get("symbol_rate") or 10.762e6)
    ro = float(p.get("rolloff") or 0.115)
    sps = max(2, int(round(sr / rs)))
    rng = _rng(p.get("seed"))
    ns = max(16, int(N // sps) + 16)
    lv = (2.0 * rng.integers(0, 8, ns) - 7.0) / 7.0     # ±1,±3,±5,±7 归一化
    base = np.zeros(ns * sps, dtype=np.float64)
    base[::sps] = lv
    full = fast_convolve(base, rrc_filter(sps, ro))
    if full.size < N:
        full = np.concatenate([full, np.zeros(N - full.size)])
    sig = full[:N]

    # VSB 成形: 上边带保留到奈奎斯特 Rs/2, 下边带只留 fv 的残留
    # (硬截止到 Rs/2 是 ATSC 发射滤波器的做法 —— 8-VSB 只占 5.38 MHz,
    #  加 0.31 MHz 残留 = 5.69 MHz, 才能塞进 6 MHz 电视频道)
    fv = float(p.get("vsb_vest") or 0.31e6)
    nyq = rs / 2.0
    lo_edge = nyq * (1.0 - ro)
    F = np.fft.fft(sig.astype(np.complex128))
    f = np.fft.fftfreq(N, 1.0 / float(sr))
    H = np.zeros(N)
    H[(f > 0) & (f <= lo_edge)] = 1.0
    up = (f > lo_edge) & (f < nyq)
    if nyq > lo_edge:
        H[up] = 0.5 * (1 + np.cos(np.pi * (f[up] - lo_edge) / (nyq - lo_edge)))
    H[(f <= 0) & (f >= -fv)] = 1.0
    mid = (f < -fv) & (f > -1.3 * fv)
    if fv > 0:
        H[mid] = 0.5 * (1 + np.cos(np.pi * (-f[mid] - fv) / (0.3 * fv)))
    out = np.fft.ifft(F * H)

    # 导频放在被抑制的载波处 (频道中心 -2.69 MHz)
    t = np.arange(N) / float(sr)
    pilot_f = float(p.get("atsc_pilot") or -2.69e6)
    out = out * np.exp(1j * 2 * np.pi * pilot_f * t)
    out = out + 0.12 * np.exp(1j * 2 * np.pi * pilot_f * t)

    pk = float(np.max(np.abs(out))) if N else 0.0
    if pk > 1e-12:
        out = out * (float(amp) / pk)
    return out.astype(np.complex64)

def _build_vor(p, sr, N, amp):
    """VOR 甚高频全向信标 (108~118 MHz 航空导航)。

    复合调制 (全部叠加在对载波的调幅上):
      a) 30 Hz 基准相位 : 直接 30 Hz 调幅 (全向, 相位不随方位变)
      b) 30 Hz 可变相位 : 先对 9960 Hz 副载波调频 (频偏 ±480 Hz),
                          再用这个已调副载波对主载波调幅; 解调出的 30 Hz
                          相位 = 飞机相对台的方位角
      c) 1020 Hz 台站识别 (摩尔斯码, 约 10% 调幅)
    接收机比较 a 与 b 两个 30 Hz 的相位差 -> 径向方位。
    """
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)
    t = np.arange(N) / float(sr)
    az = float(p.get("vor_azimuth") or 0.0) * math.pi / 180.0

    m30a = np.sin(2 * np.pi * 30.0 * t)                 # 基准 (全向)
    m30b = np.sin(2 * np.pi * 30.0 * t + az)            # 可变 (方位)
    sub = np.cos(2 * np.pi * 9960.0 * t
                 + 2 * np.pi * 480.0 * np.cumsum(m30b) / float(sr))

    y = (1.0 + 0.30 * m30a + 0.30 * sub
         + 0.10 * np.sin(2 * np.pi * 1020.0 * t))       # 1020 Hz 识别音
    y = y.astype(np.complex128)
    return (y * (float(amp) / float(np.max(np.abs(y))))).astype(np.complex64)

def _build_ils(p, sr, N, amp):
    """ILS 仪表着陆系统 (航向道 LOC / 下滑道 GS): 90 Hz 与 150 Hz 双音调幅。

    两个音调幅深度各 20% (合计 40%); 接收机比较二者的调幅深度差 DDM 判断
    是否偏离跑道中心线/下滑道。另有 1020 Hz 识别音与可选话音。
    """
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)
    t = np.arange(N) / float(sr)
    f1 = float(p.get("ils_f90") or 90.0)
    f2 = float(p.get("ils_f150") or 150.0)
    d1 = float(p.get("ils_d90") or 0.20)
    d2 = float(p.get("ils_d150") or 0.20)
    y = (1.0 + d1 * np.sin(2 * np.pi * f1 * t)
         + d2 * np.sin(2 * np.pi * f2 * t)
         + float(p.get("ils_ident") or 0.15) * np.sin(2 * np.pi * 1020.0 * t)
         ).astype(np.complex128)
    return (y * (float(amp) / float(np.max(np.abs(y))))).astype(np.complex64)

def _build_pulse_pair(p, sr, N, amp):
    """脉冲对信号: DME / TACAN / SSR (二次监视雷达)。

    DME   : 高斯形脉冲对, 间隔 12 us (X 通道) / 36 us (Y 通道),
            半幅脉宽 3.5 us, 应答到达是随机抖动的 (平均 ~1200 对/秒, 上限 2700)
    TACAN : 同 DME, 脉冲对再被 15 Hz / 135 Hz 包络调制
    SSR   : Mode A/C 应答, 脉冲宽 0.45 us, P1-P3 间隔 8 us (A) / 21 us (C)
    参数: pp_spacing 脉冲间隔(s), pp_pw 半幅脉宽(s), pp_rate 平均到达率(对/s)
    """
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)
    spacing = float(p.get("pp_spacing") or 12e-6)
    pw = float(p.get("pp_pw") or 3.5e-6)
    rate = float(p.get("pp_rate") or 1200.0)
    rng = _rng(p.get("seed"))

    dur = float(N) / float(sr)
    n_mean = int(max(2, dur * rate * 1.2))
    gaps = rng.exponential(1.0 / max(rate, 1e-9), n_mean)
    samp = np.cumsum(gaps * float(sr)).astype(np.int64)
    samp = samp[(samp >= 0) & (samp < N)]
    if samp.size == 0:
        samp = np.arange(0, N, max(1, int(sr / max(rate, 1.0))), dtype=np.int64)

    # 高斯脉冲核 (半幅宽 pw -> sigma = pw/2)
    nk = max(3, int(round(8.0 * pw * sr)))
    tk = (np.arange(nk) - (nk - 1) / 2.0) / float(sr)
    sig = max(pw / 2.0, 1.0 / (4.0 * sr))
    kern = np.exp(-(tk ** 2) / (2.0 * sig ** 2))
    off = max(1, int(round(spacing * sr)))
    comb = np.zeros(nk + off)
    comb[:nk] = kern
    comb[off:off + nk] = kern

    imp = np.zeros(N, dtype=np.float64)
    np.add.at(imp, samp, 1.0)
    env = np.fft.ifft(np.fft.fft(imp) * np.fft.fft(comb, N)).real
    env = np.maximum(env, 0.0)

    t = np.arange(N) / float(sr)
    iq = (env * np.exp(1j * 2 * np.pi * float(p.get("pp_fc") or 0.0) * t)
          ).astype(np.complex128)
    pk = float(np.max(np.abs(iq))) if N else 0.0
    if pk > 1e-12:
        iq = iq * (float(amp) / pk)
    return iq.astype(np.complex64)

def _build_pulsedoppler(p, sr, N, amp):
    """脉冲多普勒雷达: 相干脉冲串 (脉内可选线性调频)。

    与 _build_radar 的区别: 这里脉间相位是连续的 (同一本振), 所以可以做
    多普勒滤波 / 脉冲对消 (MTI), 谱是一根根离散的 PRF 谱线。
    参数: pd_pri 重复周期(s), pd_pw 脉宽(s), pd_fc 载频(Hz),
          pd_chirp 脉内线性调频带宽(Hz, 0 = 单载频)
    """
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)
    pri = float(p.get("pd_pri") or 200e-6)
    pw = float(p.get("pd_pw") or 2e-6)
    fc = float(p.get("pd_fc") or 0.0)
    chirp = float(p.get("pd_chirp") or 0.0)

    t = np.arange(N) / float(sr)
    tp = np.mod(t, pri)
    on = tp < pw
    ramp = np.where(on, tp / max(pw, 1e-12), 0.0)
    inst = fc + (chirp * (ramp - 0.5) * on if chirp > 0 else 0.0)
    phase = 2 * np.pi * np.cumsum(inst) / float(sr)

    # 脉冲前后沿做升余弦整形 (真实发射机上升时间 ~0.1 us, 否则谱无限宽)
    n_r = max(1, int(round(0.05 * pw * sr)))
    env = on.astype(np.float64)
    if n_r > 1:
        w = 0.5 * (1 - np.cos(np.pi * np.arange(n_r) / float(n_r)))
        env = fast_convolve(env, w / float(w.sum()), "same")
    iq = (env * np.exp(1j * phase)).astype(np.complex128)
    pk = float(np.max(np.abs(iq))) if N else 0.0
    if pk > 1e-12:
        iq = iq * (float(amp) / pk)
    return iq.astype(np.complex64)

def _build_uwb(p, sr, N, amp):
    """HRP-UWB (IEEE 802.15.4a/z): 纳秒级极窄脉冲 + BPM-BPSK + 跳时。

    真实参数: 脉冲 -10 dB 带宽 499.2 MHz, 脉冲重复频率 64 MHz (或 499.2 MHz),
    符号率 6.8 / 27.2 Mbps (BPM-BPSK: 突发位置调制 + 二进制相移键控)。
    采样率限制: 499.2 MHz 带宽需要 ≥1 GS/s 才能无混叠表示, 本生成器最高
    200 MHz, 因此默认按 uwb_bw (默认 80 MHz) 缩比建模 —— 波形结构
    (极窄脉冲 / 随机极性 / 跳时抖动) 与真实一致, 绝对带宽可自行调大。
    """
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)
    bw = float(p.get("uwb_bw") or 80e6)
    prf = float(p.get("uwb_prf") or 64e6)
    rng = _rng(p.get("seed"))

    # 高斯单脉冲: 单双边 -10 dB 带宽 = bw -> sigma = 0.483/bw
    sig = 0.483 / max(bw, 1.0)
    nk = max(3, int(round(8.0 * sig * sr)))
    tk = (np.arange(nk) - (nk - 1) / 2.0) / float(sr)
    kern = np.exp(-(tk ** 2) / (2.0 * sig ** 2))

    dur = float(N) / float(sr)
    n_p = int(max(2, dur * prf * 1.2))
    gaps = rng.exponential(1.0 / max(prf, 1e-9), n_p)
    samp = np.cumsum(gaps * float(sr)).astype(np.int64)
    samp = samp[(samp >= 0) & (samp < N)]
    if samp.size == 0:
        samp = np.arange(0, N, max(1, int(sr / max(prf, 1.0))), dtype=np.int64)
    pol = (rng.integers(0, 2, samp.size) * 2 - 1).astype(np.float64)   # BPSK

    imp = np.zeros(N, dtype=np.float64)
    np.add.at(imp, samp, pol)
    iq = np.fft.ifft(np.fft.fft(imp) * np.fft.fft(kern, N)).astype(np.complex128)
    pk = float(np.max(np.abs(iq))) if N else 0.0
    if pk > 1e-12:
        iq = iq * (float(amp) / pk)
    return iq.astype(np.complex64)

def _build_rfid(p, sr, N, amp):
    """UHF RFID (EPC Gen2 / ISO 18000-6C): 读写器 PIE 编码 OOK + 标签反向散射。

    读写器 -> 标签: PIE (脉冲间隔编码) DSB-ASK/OOK,
      data-0 高电平 = 1 Tari, data-1 高电平 = 1.5 Tari (总符号时长 2 Tari);
      Tari 典型 6.25~25 us -> 40~160 ksym/s。帧前有前导码。
    标签 -> 读写器: FM0 / Miller 副载波反向散射, 比读写器弱 20 dB 以上,
      副载波频率 BLF = 40~640 kHz (默认 160 kHz)。
    """
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)
    tari = float(p.get("rfid_tari") or 12.5e-6)
    blf = float(p.get("rfid_blf") or 160e3)
    rng = _rng(p.get("seed"))
    t = np.arange(N) / float(sr)

    # ---- 读写器: PIE 编码 OOK ----
    sps = max(2, int(round(2.0 * tari * sr)))          # 一个 PIE 符号
    ns = max(4, int(N // sps) + 2)
    bits = rng.integers(0, 2, ns)
    hi = np.where(bits == 1, 1.5, 1.0) * tari * float(sr)
    env = np.zeros(ns * sps)
    for k in range(ns):
        a = k * sps
        b = a + int(round(hi[k]))
        env[a:min(b, a + sps)] = 1.0
    env = env[:N] if env.size >= N else np.concatenate([env, np.zeros(N - env.size)])
    env = env[:N]

    # ---- 标签: 副载波 (±BLF 的一对边带) 反向散射 ----
    tag_rate = max(blf / 8.0, 1.0)
    sps2 = max(2, int(round(sr / tag_rate)))
    ns2 = max(4, int(N // sps2) + 2)
    tb = (rng.integers(0, 2, ns2) * 2 - 1).astype(np.float64)
    tb = np.repeat(tb, sps2)[:N]
    if tb.size < N:
        tb = np.concatenate([tb, np.full(N - tb.size, float(tb[-1]))])
    tag = 0.10 * tb * 2.0 * np.cos(2 * np.pi * blf * t)

    iq = (env + tag).astype(np.complex128)
    pk = float(np.max(np.abs(iq))) if N else 0.0
    if pk > 1e-12:
        iq = iq * (float(amp) / pk)
    return iq.astype(np.complex64)

def _build_nfc(p, sr, N, amp):
    """NFC / RFID ISO 14443A (13.56 MHz): 读写器 100% ASK + 标签负载调制。

    读写器 -> 标签: 改进 Miller 编码 106 kbit/s (= fc/128), 100% 调幅
                    (所谓"暂停 pause", 载波被短暂拉低)
    标签 -> 读写器: 负载调制在载波两侧产生 ±848 kHz (= fc/16) 的一对边带,
                    Manchester 编码, 幅度比载波低 ~20 dB
    复基带表示: 载波在 DC, 标签响应就是 ±848 kHz 的两个边带。
    """
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)
    rate = float(p.get("nfc_rate") or 106e3)
    blf = float(p.get("nfc_blf") or 848e3)
    rng = _rng(p.get("seed"))
    t = np.arange(N) / float(sr)

    # 读写器: Miller 近似 = 占空比 ~25% 的 OOK 码流
    sps = max(2, int(round(sr / rate)))
    ns = max(4, int(N // sps) + 2)
    bits = rng.integers(0, 2, ns)
    env = np.zeros(ns * sps)
    pw = max(1, int(round(0.25 * sps)))                 # Miller 暂停宽 ≈ 1/4 符号
    for k in range(ns):
        if bits[k]:
            a = k * sps
            env[a:a + pw] = 1.0
    env = env[:N] if env.size >= N else np.concatenate([env, np.zeros(N - env.size)])
    env = env[:N]
    reader = 1.0 - 0.95 * env                            # 100% ASK (低电平=暂停)

    # 标签: 负载调制 -> ±BLF 边带
    sps2 = max(2, int(round(sr / (blf / 8.0))))
    ns2 = max(4, int(N // sps2) + 2)
    tb = (rng.integers(0, 2, ns2) * 2 - 1).astype(np.float64)
    tb = np.repeat(tb, sps2)[:N]
    if tb.size < N:
        tb = np.concatenate([tb, np.full(N - tb.size, float(tb[-1]))])
    tag = 0.10 * tb * 2.0 * np.cos(2 * np.pi * blf * t)

    iq = (reader + tag).astype(np.complex128)
    pk = float(np.max(np.abs(iq))) if N else 0.0
    if pk > 1e-12:
        iq = iq * (float(amp) / pk)
    return iq.astype(np.complex64)

def _build_burst(p, sr, N, amp):
    """TDMA 突发封装: 把任意内层调制切成"帧周期 + 突发窗口"的形式。

    DECT (数字无绳电话) : 帧 10 ms / 24 时隙 (每时隙 417 us, 突发 366 us +
                          保护间隔), 内层 GFSK 1.152 Msym/s, 1728 kHz 信道
    Iridium (铱星)      : 帧 90 ms, 突发约 8.28 ms, 内层 QPSK 25 ksym/s,
                          41.67 kHz 信道, L 波段 1616~1626.5 MHz
    """
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)
    inner_mod = str(p.get("burst_mod") or "QPSK").upper()
    frame = float(p.get("burst_frame") or 10e-3)
    nslot = max(1, int(p.get("burst_nslot") or 24))
    duty = min(max(float(p.get("burst_duty") or 0.85), 0.02), 1.0)
    slot_i = int(p.get("burst_slot") or 0) % nslot

    # ---- 内层调制 ----
    if inner_mod in ("GFSK", "GMSK"):
        inner = _build_gmsk(p, sr, N, 1.0)
    elif inner_mod in ("DSSS",):
        inner = _build_dscdma(p, sr, N, 1.0)
    else:
        inner = _build_digital(inner_mod, p, sr, N, 1.0)
    inner = np.asarray(inner)
    if inner.size < N:
        inner = np.concatenate([inner, np.zeros(N - inner.size, dtype=complex)])

    # ---- 时隙门控 ----
    fr_n = max(4, int(round(frame * sr)))
    sl_n = max(2, fr_n // nslot)
    bn = max(2, int(sl_n * duty))
    n_r = max(1, int(sl_n * 0.02))

    env = np.zeros(N, dtype=np.float64)
    pos = 0
    while pos + fr_n <= N:
        a = pos + slot_i * sl_n
        b = min(a + bn, pos + fr_n, N)
        if a < N:
            env[a:b] = 1.0
        pos += fr_n
    if not env.any():
        env[:max(2, N // 2)] = 1.0
    if n_r > 1:
        w = np.hanning(2 * n_r + 1)[1:-1]
        env = fast_convolve(env, w / float(w.sum()), "same")

    iq = (inner[:N] * env).astype(np.complex128)
    pk = float(np.max(np.abs(iq))) if N else 0.0
    if pk > 1e-12:
        iq = iq * (float(amp) / pk)
    return iq.astype(np.complex64)

def _build_fasthop(p, sr, N, amp):
    """快速跳频 + 宽带内层调制 (Link-16 / JTIDS 类军用数据链)。

    Link-16: 51 个频点、3 MHz 间隔 (跨 153 MHz, 969~1206 MHz),
    跳速 76923 跳/s (每跳 13 us), 内层 MSK 5 Mchip/s (CCSK 32 片扩频),
    数据率 31.6 / 57.6 / 115.2 kbit/s, TDMA 时隙 7.8125 ms。
    """
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)
    band = float(p.get("fh_band") or 153e6)
    nch = max(2, int(p.get("fh_nch") or 51))
    rate = float(p.get("fh_rate") or 76923.0)
    rng = _rng(p.get("seed"))

    inner = _build_digital(str(p.get("fh_inner") or "MSK").upper(), p, sr, N, 1.0)
    inner = np.asarray(inner)
    if inner.size < N:
        inner = np.concatenate([inner, np.zeros(N - inner.size, dtype=complex)])

    hop_n = max(2, int(round(sr / rate)))
    nh = int(N // hop_n) + 1
    step = band / float(max(nch - 1, 1))
    freqs = (rng.integers(0, nch, nh) - (nch - 1) / 2.0) * step
    fh = np.repeat(freqs, hop_n)[:N]
    if fh.size < N:
        fh = np.concatenate([fh, np.full(N - fh.size, float(fh[-1]) if fh.size else 0.0)])

    iq = (inner[:N] * np.exp(1j * 2 * np.pi * np.cumsum(fh) / float(sr))
          ).astype(np.complex128)
    pk = float(np.max(np.abs(iq))) if N else 0.0
    if pk > 1e-12:
        iq = iq * (float(amp) / pk)
    return iq.astype(np.complex64)

def _build_hop_voice(p, sr, N, amp):
    """跳频话音 (HAVE QUICK / SINCGARS 类军用电台)。

    HAVE QUICK (军航 UHF 225~400 MHz): AM 话音 300~3000 Hz, 跳速约 10~100 跳/s
    SINCGARS  (陆军 VHF 30~88 MHz)  : FM 话音 (25 kHz 信道, 频偏 ±5 kHz),
                                       跳速约 100 跳/s (ECCM 慢跳)
    注意: 复基带要表示"跳频跨度", 所以 fh_band 直接决定所需采样率。
    默认给的是一小段跳频跨度; 想覆盖整个频段请把 fh_band 和采样率一起加大。
    """
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)
    typ = str(p.get("hop_voice_type") or "fm").lower()
    m = _message(p, sr, N)

    if typ == "am":
        mu = float(p.get("mu") if p.get("mu") is not None else 0.8)
        y = (1.0 + mu * m).astype(np.complex128)        # AM (含载波)
    else:
        dev = float(p.get("dev") or 5000.0)
        y = np.exp(1j * 2 * np.pi * dev * np.cumsum(m) / float(sr))

    band = float(p.get("fh_band") or 4e6)
    rate = float(p.get("fh_rate") or 100.0)
    rng = _rng(p.get("seed"))
    hop_n = max(2, int(round(sr / rate)))
    nh = int(N // hop_n) + 1
    freqs = (rng.random(nh) - 0.5) * band
    fh = np.repeat(freqs, hop_n)[:N]
    if fh.size < N:
        fh = np.concatenate([fh, np.full(N - fh.size, float(fh[-1]) if fh.size else 0.0)])

    iq = (y * np.exp(1j * 2 * np.pi * np.cumsum(fh) / float(sr))).astype(np.complex128)
    pk = float(np.max(np.abs(iq))) if N else 0.0
    if pk > 1e-12:
        iq = iq * (float(amp) / pk)
    return iq.astype(np.complex64)

def _build_time(p, sr, N, amp):
    """长波授时台 (BPC 68.5 kHz / DCF77 77.5 kHz / WWVB 60 kHz / MSF 60 kHz)。

    每秒发 1 bit: 秒首把载波幅度降到 25%, 持续 100 ms (bit 0) 或 200 ms
    (bit 1); 每分钟第 59 秒不降幅, 作为分钟标志。
    因此是极低速的 ASK/脉宽调制, 占用带宽只有几十 Hz。
    """
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)
    sr = float(sr)
    fc = float(p.get("time_fc") or 68.5e3)
    t = np.arange(N, dtype=np.float64) / sr
    env = np.ones(N, dtype=np.float64)
    rng = _rng(p.get("seed"))
    nsec = int(np.ceil(N / sr)) + 1
    for k in range(nsec):
        if k % 60 == 59:                      # 分钟标志: 该秒不降幅
            continue
        i0 = int(round(k * sr))
        if i0 >= N:
            break
        bit = int(rng.integers(0, 2))
        i1 = min(N, i0 + int(round((0.1 if bit == 0 else 0.2) * sr)))
        if i1 > i0:
            env[i0:i1] = 0.25
    # 边沿略做平滑 (约 2 ms), 避免理想矩形造成的无限宽旁瓣
    wlen = max(2, int(round(sr * 0.002)))
    if wlen > 2 and N > wlen * 2:
        w = np.hanning(wlen + 2)[1:-1]
        w = w / w.sum()
        env = fast_convolve(env, w, "same")
    return (amp * env * np.exp(1j * 2 * np.pi * fc * t)).astype(np.complex64)

def _build_rds(p, sr, N, amp):
    """RDS / RBDS: FM 广播的 57 kHz 副载波。

    1187.5 bps, 差分编码 + 双相码 (Manchester, 码片率 2375 chip/s),
    对 57 kHz 副载波做 BPSK(抑制载波) 调制, 占用约 ±2.4 kHz(共 4.8 kHz)。
    这里直接输出"57 kHz 副载波上的已调信号"这一基带表示, 便于看频谱形状。
    """
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)
    sr = float(sr)
    sub_fc = float(p.get("rds_fc") or 57000.0)
    rb = float(p.get("rds_rate") or 1187.5)
    chip_rate = 2.0 * rb                 # 双相码: 每 bit 两个码片
    sps = max(2, int(round(sr / chip_rate)))
    rng = _rng(p.get("seed"))
    n_bit = max(8, int(N // (2 * sps)) + 8)
    bits = rng.integers(0, 2, n_bit)
    # Manchester: bit 1 -> (+1, -1), bit 0 -> (-1, +1)
    chips = np.repeat((bits * 2 - 1).astype(np.float64), 2)
    chips[1::2] *= -1.0
    base = np.zeros(chips.size * sps, dtype=np.float64)
    base[::sps] = chips
    # 滚降 1.0: 双边带宽 = 2 x 码片率/2 x (1+1) = 2 x rb x 2 = 4.75 kHz
    shaped = fast_convolve(base, rrc_filter(sps, 1.0), "full")
    if shaped.size < N:
        shaped = np.concatenate([shaped, np.zeros(N - shaped.size)])
    shaped = shaped[:N]
    rms = float(np.sqrt(np.mean(shaped ** 2))) or 1.0
    shaped = shaped / rms
    t = np.arange(N, dtype=np.float64) / sr
    return (amp * shaped * np.exp(1j * 2 * np.pi * sub_fc * t)).astype(np.complex64)

def _build_selcal(p, sr, N, amp):
    """SELCAL 选择呼叫: 地面站呼叫飞机时发的调幅双音。

    SELCAL 从 12 个标准音 (312.6~1479.2 Hz) 中取 4 个, 分两对,
    每对两个音同时发约 1 s, 两对之间轮换 (如 "AB-CD")。
    这里生成基带表示 (载波在 0 频) 的双音调幅。
    """
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)
    sr = float(sr)
    TONES = (312.6, 346.7, 384.6, 426.6, 473.2, 524.8,
             582.1, 645.7, 716.1, 794.3, 881.0, 977.2)
    rng = _rng(p.get("seed"))
    f1 = float(p.get("selcal_f1") or TONES[int(rng.integers(0, len(TONES)))])
    f2 = float(p.get("selcal_f2") or TONES[int(rng.integers(0, len(TONES)))])
    f3 = float(p.get("selcal_f3") or TONES[int(rng.integers(0, len(TONES)))])
    f4 = float(p.get("selcal_f4") or TONES[int(rng.integers(0, len(TONES)))])
    seg = max(1, int(round(float(p.get("selcal_seg") or 1.0) * sr)))
    t = np.arange(N, dtype=np.float64) / sr
    m = np.zeros(N, dtype=np.float64)
    for k in range(0, N, seg):
        k2 = min(N, k + seg)
        tt = t[k:k2] - t[k]
        if (k // seg) % 2 == 0:
            m[k:k2] = 0.5 * (np.cos(2 * np.pi * f1 * tt)
                             + np.cos(2 * np.pi * f2 * tt))
        else:
            m[k:k2] = 0.5 * (np.cos(2 * np.pi * f3 * tt)
                             + np.cos(2 * np.pi * f4 * tt))
    mu = float(p.get("mu") or 0.7)
    return (amp * (1.0 + mu * m)).astype(np.complex64)

def _build_cwdoppler(p, sr, N, amp):
    """连续波多普勒雷达 (CW Doppler)。

    发射单频连续波, 回波被运动目标的多普勒频率调制, 等价于
    "单频 + 低频正弦调相/调频"。多个散射点各自有不同的多普勒频率
    与起伏速率, 叠加后就是实际测速雷达看到的谱。
    """
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)
    sr = float(sr)
    fc = float(p.get("dop_fc") or 0.0)
    fd = float(p.get("dop_freq") or 300.0)      # 多普勒频率 (Hz)
    fm = max(1e-6, float(p.get("dop_rate") or 2.0))   # 目标起伏速率 (Hz)
    n_sc = max(1, int(p.get("dop_scatter") or 3))
    t = np.arange(N, dtype=np.float64) / sr
    rng = _rng(p.get("seed"))
    ph0 = 2 * np.pi * fc * t
    sig = np.zeros(N, dtype=np.complex128)
    for _ in range(n_sc):
        fdk = fd * (1.0 + 0.6 * float(rng.standard_normal()))
        ak = (amp / float(n_sc)) * (0.5 + 0.5 * float(rng.random()))
        # 调相指数 = fdk / fm, 使瞬时频率偏移正好是 fdk
        sig += ak * np.exp(1j * (ph0 + (fdk / fm) * np.sin(2 * np.pi * fm * t)))
    return sig.astype(np.complex64)

def _mix_common_sr(p, comps=None):
    """同频混合的共用采样率 (Hz)。

    单一实现点: _build_mix 用它决定各路合成用的采样率, build_preview 的
    _sr_eff_for_params 也用它预测时长 —— 两处必须是同一个数, 否则预览会先
    合成一个超大数组再截掉(混合 + 跳频预览要 1.5 s 就是这个原因)。

    规则: 各路需求取最大, 每路再加上它自己的频偏(搬移后带宽 = 带宽 + 2×频偏);
    若顶层还开了跳频, 最终混合体整体还要跳到跳频网格上, 共用采样率就必须覆盖
    (跳频总带宽 + 2×起始偏移 + 混合体带宽)。漏掉这一步的后果不是"慢"而是
    "错": 旧实现共用采样率停在 96 kHz, 79 MHz 的跳频点被整体折叠进 ±48 kHz,
    频谱上完全看不出跳频特征。
    """
    sr_req = float(p.get("sample_rate") or 0.0) or float(DEFAULTS["sample_rate"])
    if comps is None:
        comps = [_mix_component_params(c, p) for c in (p.get("mix") or [])]

    need = sr_req
    for sub in comps:
        try:
            f = abs(float(sub.get("foff") or 0.0))
        except (TypeError, ValueError):
            f = 0.0
        sub_need = 0.0
        if sub.get("replay_src") or int(sub.get("replay_block") or 0) > 0:
            # 素材回放: 带宽由素材自身的采样率决定, 与声明值无关
            try:
                path = _orig_file(sub)
                sub_need = float(_orig_sample_rate(path) or 0.0) if path else 0.0
            except Exception:
                sub_need = 0.0
        if sub_need <= 0:
            m = str(sub.get("mod") or "CW").upper()
            m = _MOD_ALIAS.get(m, m)
            sub2 = _fill_mod_defaults(m, dict(sub))
            try:
                sub_need = float(_min_sample_rate(m, sub2) or 0.0)
            except Exception:
                sub_need = 0.0
        need = max(need, sub_need + 2.0 * f)

    if p.get("hop_on"):
        try:
            band = max(0.0, float(p.get("hop_band") or 0.0))
            foff = abs(float(p.get("hop_foff") or 0.0))
        except (TypeError, ValueError):
            band = foff = 0.0
        if band > 0 or foff > 0:
            need = max(need, (band + 2.0 * foff + need) * 1.05)
    return need

def _build_mix(p):
    """合成"同频混合"信号: 逐路合成 -> 各自时隙/频偏/幅度 -> 叠加。

    返回 complex64 数组 (长度 = duration * common_sr), 并就地更新 p["sample_rate"]。
    未配置 mix 时返回 None。
    """
    comps = p.get("mix") or []
    if not comps:
        return None

    dur = float(p.get("duration") or DEFAULTS["duration"])
    sr_req = float(p.get("sample_rate") or DEFAULTS["sample_rate"])

    norm = [_mix_component_params(c, p) for c in comps]
    common_sr = max(sr_req, _mix_common_sr(p, norm))

    # 样本数上限与 _build_core 保持一致: 混合 + 跳频时共用采样率会到 100 MHz,
    # 照 duration 硬算 0.5 s 就是 5000 万样本(单个累加数组 400 MB), 先不说
    # 后面的时隙/滤波要走几遍, 光分配就会触发换页/内存不足 —— 看上去就是"卡死"。
    n = int(round(common_sr * dur))
    if n > MAX_SAMPLES:
        n = MAX_SAMPLES
    if n <= 0:
        return None
    t = (np.arange(n, dtype=np.float64) / common_sr)
    acc = np.zeros(n, dtype=np.complex64)

    for sub in norm:
        sub["sample_rate"] = common_sr
        sub["duration"] = dur
        ciq = None
        if sub.get("replay_src") or int(sub.get("replay_block") or 0) > 0:
            ciq = _replay_block(sub, None)
        if ciq is None:
            try:
                ciq = _build_core(sub)
            except ValueError:
                ciq = _replay_block(sub, None)
        if ciq is None or len(ciq) == 0:
            continue

        csr = float(sub.get("sample_rate") or common_sr)
        if abs(csr - common_sr) > 1e-6 and csr > 0:
            ciq = _resample_complex(ciq, csr, common_sr)
        ciq = np.asarray(ciq, dtype=np.complex64)
        if ciq.size < n:
            ciq = np.concatenate([ciq, np.zeros(n - ciq.size, dtype=np.complex64)])
        elif ciq.size > n:
            ciq = ciq[:n]

        # 该路时隙门控
        ciq = _apply_tdma_slots(ciq, common_sr, sub)

        # 频率偏移 (相对主信号), 只对非零频偏做乘子, 省算力
        foff = 0.0
        try:
            foff = float(sub.get("foff") or 0.0)
        except (TypeError, ValueError):
            foff = 0.0
        if foff:
            ciq = ciq * np.exp(1j * 2.0 * np.pi * foff * t).astype(np.complex64)

        # 相对幅度
        a = 1.0
        try:
            if sub.get("amp") not in (None, ""):
                a = float(sub["amp"])
        except (TypeError, ValueError):
            a = 1.0
        if a != 0.0:
            acc += (ciq * a).astype(np.complex64)

    p["sample_rate"] = float(common_sr)
    p["mix_count"] = len(norm)
    return acc

def _apply_output_bpf(iq, sr, p):
    """输出带通滤波 (零相位)。

    参数 (p 内):
      bpf_bw    : 带宽 (Hz)。> 0 才生效; 未给/为 0 表示不滤波。
      bpf_fc    : 中心频率 (Hz), 默认 0。中心=0 时退化为低通 (0 ~ bw/2)。
      bpf_order : Butterworth 阶数, 默认 4。
      bpf_flo/bpf_fhi : 直接给定上下限 (Hz), 二者都给则以它为准, 覆盖 fc/bw。
    优先用 scipy 零相位 Butterworth (流式省内存); 无 scipy 时回退频域升余弦掩模。
    """
    iq = np.asarray(iq)
    n = int(iq.size)
    if n == 0 or sr <= 0:
        return iq

    def _f(key, d=0.0):
        try:
            v = p.get(key)
            return float(v) if v not in (None, "") else d
        except (TypeError, ValueError):
            return d

    flo = p.get("bpf_flo")
    fhi = p.get("bpf_fhi")
    use_edges = False
    if flo not in (None, "") and fhi not in (None, ""):
        try:
            flo = float(flo)
            fhi = float(fhi)
            use_edges = True
        except (TypeError, ValueError):
            use_edges = False
    if not use_edges:
        bw = _f("bpf_bw", 0.0)
        if bw <= 0:
            return iq                                   # 未配置 -> 不滤波
        fc = _f("bpf_fc", 0.0)
        flo = fc - bw / 2.0
        fhi = fc + bw / 2.0

    nyq = sr / 2.0
    flo = max(0.0, flo)
    fhi = min(nyq * 0.999, fhi)
    if not (fhi > flo >= 0.0):
        return iq
    try:
        order = int(float(p.get("bpf_order") or 4))
    except (TypeError, ValueError):
        order = 4
    order = max(1, min(order, 12))

    # 低通(含 DC): 实系数零相位滤波器即正确(频响左右对称)且流式省内存
    if flo <= 1e-9:
        try:
            from scipy.signal import butter, sosfiltfilt
            sos = butter(order, fhi / nyq, btype="low", output="sos")
            return np.asarray(sosfiltfilt(sos, iq), dtype=np.complex64)
        except Exception:
            pass

    # 带通(单边) / 无 scipy: 频域升余弦掩模。
    # 注意: 复数基带的带通必须用"非对称"频响 —— 实系数 FIR/IIR 的频响左右对称,
    #       会把 +fc 与 -fc 一起通过(镜像), 所以这里用单边频域掩模。
    f = np.fft.fftfreq(n, 1.0 / sr)
    m = np.zeros(n, dtype=np.float64)
    edge = max((fhi - flo) * 0.15, sr / max(n, 1) * 2.0)
    if flo <= 1e-9:
        af = np.abs(f)
        m[af <= fhi] = 1.0
        hm = (af > fhi) & (af <= fhi + edge)
        m[hm] = 0.5 * (1.0 + np.cos(np.pi * (af[hm] - fhi) / edge))
    else:
        m[(f >= flo) & (f <= fhi)] = 1.0
        lm = (f >= flo - edge) & (f < flo)
        hm = (f > fhi) & (f <= fhi + edge)
        if np.any(lm):
            m[lm] = 0.5 * (1.0 + np.cos(np.pi * (flo - f[lm]) / edge))
        if np.any(hm):
            m[hm] = 0.5 * (1.0 + np.cos(np.pi * (f[hm] - fhi) / edge))
    out = np.fft.ifft(np.fft.fft(iq) * m)
    return out.astype(np.complex64)

def build_iq(p):
    """生成最终 IQ。返回 complex64 数组 (长度可能受 MAX_SAMPLES 限制)。

    注: 本函数会就地更新 p["sample_rate"] (采样率可能被 _build_core 抬高,
    或回放时改为素材原始采样率), 调用方需在生成后再取一次。
    """
    iq = None

    # 0) 同频混合: 配置了多路信号 -> 逐路合成后叠加 (各路自带频偏/幅度/时隙)
    if p.get("mix"):
        iq = _build_mix(p)

    # 1) 显式指定了原始素材 -> 优先回放
    if iq is None and (p.get("replay_src") or int(p.get("replay_block") or 0) > 0):
        iq = _replay_block(p, None)

    # 2) 合成
    if iq is None:
        try:
            iq = _build_core(p)
        except ValueError:
            iq = _replay_block(p, None)     # 名称无法解析 -> 退回原始素材回放
            if iq is None:
                raise

    sr = float(p.get("sample_rate") or 0.0)
    N = len(iq)
    idh = float(p.get("idle_head") or 0.0)
    idt = float(p.get("idle_tail") or 0.0)

    if (idh > 0 or idt > 0) and N > 0 and sr > 0:
        nh = max(0, min(int(round(idh * sr)), N))
        nt = max(0, min(int(round(idt * sr)), max(0, N - nh)))
        # 空闲段应当是"静默"。原代码填的是常数量 (amplitude),
        # 会在 0 Hz 处凭空多出一根极强的 DC 谱线。
        if nh:
            iq[:nh] = 0.0
        if nt:
            iq[N - nt:] = 0.0

    # 2.5) 跳频发射: 把基带信号搬到跳变的载频上 (顺序/伪随机/自定义序列)
    #      放在时隙门控之前, 因为门控是在基带上做通断, 先跳频后门控
    #      等价于真实电台的"在工作时隙内跳频"。
    iq = _apply_freq_hop(iq, sr, p)

    # 3) 时隙(TDMA)门控: 按配置在部分时隙发射、其余时隙静默
    #    (如 DMR 半时隙 = 发 30ms 停 30ms; 满时隙 = 连续不分时隙)
    iq = _apply_tdma_slots(iq, sr, p)

    # 4) 叠加加性高斯白噪声 (AWGN), 使合成信号更接近真实采集信号
    iq = _add_awgn(iq, p)

    # 5) 输出带通滤波: 把最终输出限制在设定带宽内 (零相位)
    iq = _apply_output_bpf(iq, sr, p)

    return _anti_clip(iq)

def _add_awgn(iq, p):
    """按给定载噪比叠加复高斯白噪声。

    参数 (p 内):
      snr_db   : 信噪比 (dB)。None / 空 / "off" 表示不加噪声。
                 定义为 10*log10(信号平均功率 / 噪声平均功率)。
      snr_mode : "avg"  (默认) 相对整段平均功率;
                 "peak" 相对峰值功率 (噪声功率 = 峰值功率 / 10^(SNR/10))。
    返回 complex64 数组。
    """
    iq = np.asarray(iq)
    if iq.size == 0:
        return iq.astype(np.complex64)

    v = p.get("snr_db", None)
    if v is None or v == "":
        return iq.astype(np.complex64)
    try:
        snr = float(v)
    except (TypeError, ValueError):
        return iq.astype(np.complex64)
    if not np.isfinite(snr):
        return iq.astype(np.complex64)

    mode = str(p.get("snr_mode") or "avg").lower()
    if mode == "peak":
        ref = float(np.max(np.abs(iq)))
        sig_p = ref ** 2
    else:
        sig_p = float(np.mean(np.abs(iq) ** 2))

    if sig_p <= 0:
        return iq.astype(np.complex64)

    noise_p = sig_p / (10.0 ** (snr / 10.0))
    sigma = math.sqrt(noise_p / 2.0)          # 复噪声: 实部虚部各占一半功率

    rng = _rng(p.get("seed"))
    n = iq.size
    noise = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) * sigma
    return (iq + noise).astype(np.complex64)

_MOD_ALIAS = {"SAT": "QPSK", "PI4PSK": "Pi4DQPSK", "PSKR": "QPSK", "REPLAY": "REPLAY"}

def _build_core(p):
    """按调制族分发到具体的波形构造函数。

    修复记录: 原来只处理了 CW/AWGN/AM/FM/数字族/LoRa/GNSS/CDR,
    LTE、DTMB、GSM、AIS、ATV、ADS-B、FREQHOP、RADAR 全部走不到分支,
    默默落到最后的 _build_ais; 并且引用了未定义的 MOD_REPLAY_SRC。
    """
    mod = str(p.get("mod") or "CW").strip()
    alias = _MOD_ALIAS.get(mod.upper())
    if alias:
        mod = alias

    # 补齐该族的默认参数 (采样率 / 带宽等)
    for k, v in (MOD_DEFAULTS.get(mod.upper()) or {}).items():
        if not p.get(k):
            p[k] = v

    try:
        need = _min_sample_rate(mod, p)
        if need > float(p.get("sample_rate") or 0.0) * 1.0001:
            p["sample_rate"] = nice_sr(need)
    except Exception:
        pass

    sr = float(p.get("sample_rate") or 0.0)
    if sr <= 0:
        sr = float(DEFAULTS["sample_rate"])
        p["sample_rate"] = sr

    dur = float(p.get("duration") or DEFAULTS["duration"])
    amp = float(p.get("amplitude") or DEFAULTS["amplitude"])
    N = int(round(dur * sr))
    if N <= 0:
        N = 1
    if N > MAX_SAMPLES:
        N = MAX_SAMPLES
        p["duration"] = N / sr

    if mod == "REPLAY":
        iq = _replay_block(p, N)
        if iq is not None:
            return iq
        raise ValueError("回放失败: 在 %s 下找不到对应素材" % ORIG_DIR)

    if mod == "CW":
        fc = float(p.get("carrier") or 0.0)
        t = np.arange(N) / sr
        return (amp * np.exp(1j * 2 * np.pi * fc * t)).astype(np.complex64)

    if mod == "AWGN":
        rng = _rng(p.get("seed"))
        iq = (rng.standard_normal(N) + 1j * rng.standard_normal(N)) * (amp / math.sqrt(2))
        return iq.astype(np.complex64)

    if mod in ("AM", "FM"):
        return _build_analog(mod, p, sr, N, amp)

    if mod in DIGITAL_FAMILIES:
        return _build_digital(mod, p, sr, N, amp)

    # ---- 第二批: 调制基元 (模拟补充 / 扩频 / 脉冲) ----
    if mod in ("DSBSC", "VSB", "PM", "NBFM", "WBFM"):
        return _build_analog_ext(mod, p, sr, N, amp)

    if mod == "SOQPSK":
        return _build_soqpsk(p, sr, N, amp)

    if mod in ("GFSK", "GMSK", "DSTAR"):
        return _build_gmsk(p, sr, N, amp)

    # ---- 第三批: 授时 / RDS / SELCAL / CW 多普勒 ----
    if mod == "TIME":
        return _build_time(p, sr, N, amp)
    if mod == "RDS":
        return _build_rds(p, sr, N, amp)
    if mod == "SELCAL":
        return _build_selcal(p, sr, N, amp)
    if mod == "CWDOPPLER":
        return _build_cwdoppler(p, sr, N, amp)

    if mod == "DSSS":
        return _build_dscdma(p, sr, N, amp)

    if mod == "THSS":
        return _build_thss(p, sr, N, amp)

    if mod in ("PAM", "PWM", "PPM"):
        return _build_pulse_mod(p, sr, N, amp)

    # ---- 第二批: 专用波形 (导航 / 雷达 / UWB / 近场) ----
    if mod == "ATSC":
        return _build_atsc(p, sr, N, amp)
    if mod == "VOR":
        return _build_vor(p, sr, N, amp)
    if mod == "ILS":
        return _build_ils(p, sr, N, amp)
    if mod in ("DME", "SSR"):
        return _build_pulse_pair(p, sr, N, amp)
    if mod == "PULSEDOPPLER":
        return _build_pulsedoppler(p, sr, N, amp)
    if mod == "UWB":
        return _build_uwb(p, sr, N, amp)
    if mod == "RFID":
        return _build_rfid(p, sr, N, amp)
    if mod == "NFC":
        return _build_nfc(p, sr, N, amp)

    # ---- 第二批: 突发 / 跳频 ----
    if mod in ("DECT", "IRIDIUM"):
        return _build_burst(p, sr, N, amp)
    if mod == "LINK16":
        return _build_fasthop(p, sr, N, amp)
    if mod in ("HAVEQUICK", "SINCGARS"):
        return _build_hop_voice(p, sr, N, amp)

    # ---- 第二批: 多音 MFSK / 副载波图像 ----
    if mod in ("WSPR", "JT65", "OLIVIA"):
        return _build_mfsk_tones(p, sr, N, amp)
    if mod in ("NOAAAPT", "SSTV", "FAX"):
        return _build_subcarrier(p, sr, N, amp)

    # ---- 第二批: OFDM 家族 ----
    if mod in ("NR5G", "DVBT", "ISDBT", "DRM", "NBIOT", "LTEM", "FREEDV"):
        return _build_ofdm(p, sr, N, amp)
    if mod == "HDRADIO":
        return _build_cdr(p, sr, N, amp)
    if mod in ("WIFI6", "WIFI7"):
        return _build_bridge(p, sr, N, amp)

    # ---- 第二批: 命名预设 (复用已有数字调制器 + 该制式的标准参数) ----
    _PRESET2 = {"DVBS2": "16APSK", "DVBC": "256QAM",
                "VDL2": "8PSK", "METEOR": "QPSK", "INMARSAT": "QPSK",
                "FLEX": "4FSK", "ZWAVE": "2FSK", "DSC": "2FSK",
                "NXDN": "4FSK", "DPMR": "4FSK", "C4FM": "4FSK", "M17": "4FSK",
                "RKE": "ASK", "TPMS": "ASK", "HELL": "ASK",
                "SIGFOX": "BPSK",
                # --- 第三批: 复用已有调制器 + 该制式的标准参数 ---
                "CCSDS": "BPSK",      # 卫星遥测 BPSK
                "DVBS": "QPSK",       # DVB-S 第一代: QPSK
                "VSAT": "QPSK",       # 卫星小站回传: QPSK
                "FT4": "4FSK",        # 4-FSK 音隔 20.833 Hz
                "FSK441": "4FSK",     # 流星散射 4-FSK 441 baud
                "ALE": "8FSK",        # 自动链路建立 8-FSK 125 baud
                "AMTOR": "2FSK",      # 业余电传 2-FSK 100 baud
                "WISUN": "2FSK",      # 802.15.4g 2-FSK
                "WMBUS": "2FSK"}      # 无线抄表 2-FSK
    if mod in _PRESET2:
        return _build_digital(_PRESET2[mod], p, sr, N, amp)

    if mod == "LORA":
        return _build_lora(p, sr, N, amp)

    if mod in ("GPS", "GLONASS", "BEIDOU", "GALILEO",
               "NAVIC", "QZSS", "SBAS"):
        return _build_gnss(p, sr, N, amp)

    if mod == "DAB":
        return _build_dab(p, sr, N, amp)
    if mod == "CDR":
        return _build_cdr(p, sr, N, amp)

    if mod in ("LTE", "DTMB"):
        return _build_ofdm(p, sr, N, amp)

    if mod == "BRIDGE":
        return _build_bridge(p, sr, N, amp)

    # ---- 新增: 真实世界信号 ----
    if mod in ("NR5G", "DVBT"):
        return _build_ofdm(p, sr, N, amp)

    if mod in ("WCDMA", "CDMA"):
        return _build_dscdma(p, sr, N, amp)

    if mod == "ZIGBEE":
        return _build_zigbee(p, sr, N, amp)

    if mod == "SSB":
        return _build_ssb(p, sr, N, amp)

    # ---- 第三批: 复用已有构造器 + 标准参数 ----
    if mod in ("THREAD",):
        return _build_zigbee(p, sr, N, amp)
    if mod in ("GSMR", "VDL4", "RADIOSONDE"):
        return _build_gmsk(p, sr, N, amp)
    if mod == "NDB":
        return _build_analog("AM", p, sr, N, amp)
    if mod in ("ATSC3", "DSRC"):
        return _build_ofdm(p, sr, N, amp)
    if mod == "SAR":
        return _build_pulsedoppler(p, sr, N, amp)
    if mod == "MT63":
        return _build_mfsk_tones(p, sr, N, amp)

    if mod == "MORSE":
        return _build_morse(p, sr, N, amp)

    if mod == "FMCW":
        return _build_fmcw(p, sr, N, amp)

    # 命名预设: 复用已有数字调制器, 只是把真实世界的标准参数固定下来
    # (DMR/PDT 4FSK 连续相位; P25 C4FM; POCSAG/NAVTEX/RTTY FSK;
    #  ACARS MSK; TETRA pi/4-DQPSK; PSK31 BPSK; FT8 8-FSK)
    _PRESET = {"DMR": "CP4FSK", "PDT": "CP4FSK", "P25": "4FSK",
               "POCSAG": "2FSK", "NAVTEX": "2FSK", "RTTY": "2FSK",
               "ACARS": "MSK", "TETRA": "Pi4DQPSK",
               "PSK31": "BPSK", "FT8": "8FSK"}
    if mod in _PRESET:
        return _build_digital(_PRESET[mod], p, sr, N, amp)

    if mod == "GSM":
        return _build_gmsk(p, sr, N, amp)

    if mod == "BT":
        q = dict(p)
        q["gmsk_sym"] = float(p.get("gmsk_sym") or 1e6)
        q["gmsk_bt"] = float(p.get("gmsk_bt") or 0.5)
        return _build_gmsk(q, sr, N, amp)

    if mod == "AIS":
        return _build_ais(p, sr, N, amp)

    if mod == "ATV":
        return _build_atv(p, sr, N, amp)

    if mod == "ADSB":
        return _build_adsb(p, sr, N, amp)

    if mod == "FREQHOP":
        return _build_freqhop(p, sr, N, amp)

    if mod == "RADAR":
        return _build_radar(p, sr, N, amp)

    # 未知的族: 名字能在素材库里对上就直接回放
    iq = _replay_block(p, N)
    if iq is not None:
        return iq

    raise ValueError("不支持的调制方式: %s" % mod)

def _message(p, sr, N):
    """调制信源。

    msg_mode: "tone" 单音 (教科书式, 谱是离散谱线);
              "noise" 带限噪声 (语音状, 谱是连续的 -- 素材 AM/FM 都是这种)。
    msg_norm: "peak" 峰值归一化 (默认); "rms" 均方根归一化。
              素材实测: AM 包络 std/mean ≈ mu, FM 瞬时频率 std ≈ dev,
              都对应 rms 归一化。
    """
    bw = float(p.get("msg_bw") or 0.0)
    mode = str(p.get("msg_mode") or ("noise" if bw > 0 else "tone")).lower()

    # 修复: 原实现里中间还有一个 `if bw > 0` 的噪声分支, 会在 msg_mode
    # 显式为 "tone" 但 msg_bw > 0 时把单音请求错误地换成噪声输出;
    # 且 `p["tone"]` 直接取键, 裸参数字典会 KeyError, tone=0 时还会除零出 NaN。
    if mode == "tone":
        fm_tone = float(p.get("tone") or DEFAULTS["tone"])
        t = np.arange(N) / sr
        m = np.sin(2 * np.pi * fm_tone * t)
        mx = float(np.max(np.abs(m))) if m.size else 0.0
        return m / mx if mx > 1e-12 else m

    # msg_mode != "tone": 一律带限噪声信源 (bw<=0 时用默认截止)
    rng = _rng(p.get("seed"))
    Ng = 1 << int(np.ceil(np.log2(max(N, 1024))))
    W = np.fft.rfft(rng.standard_normal(Ng))
    f = np.abs(np.fft.rfftfreq(Ng, 1.0 / sr))
    n = max(1, int(p.get("msg_order") or 4))
    cut = bw if bw > 0 else max(sr / 20.0, 300.0)

    with np.errstate(divide="ignore", invalid="ignore"):
        H = 1.0 / np.sqrt(1.0 + np.power(np.maximum(f, 1e-9) / cut, 2 * n))

    hp = float(p.get("msg_hp") or 0.0)
    if hp > 0:
        H = H / np.sqrt(1.0 + np.power(hp / np.maximum(f, 1e-9), 2 * n))

    pk = float(p.get("msg_pk") or 0.0)
    if pk > 0:
        H = H * np.power(np.maximum(f, 1e-9) / max(cut, 1.0), pk)
        H[f <= 0] = 0.0

    m = np.fft.irfft(W * H, Ng)[:N]
    if str(p.get("msg_norm") or "peak").lower() == "rms":
        r = float(np.sqrt(np.mean(m ** 2)))
        if r > 1e-12:
            m = m / r
    else:
        mx = float(np.max(np.abs(m)))
        if mx > 1e-12:
            m = m / mx
    return m

def _build_analog(mod, p, sr, N, amp):
    """AM / FM 调制。

    修复记录: AM 分支算完 y 以后没有 return, 必然落到函数末尾的
    raise ValueError("未支持的模拟调制")。
    """
    if p.get("source_mode") == "audio" and p.get("audio_path"):
        try:
            m = prepare_audio_source(p["audio_path"], sr, N)
        except Exception as e:
            raise RuntimeError("音频信源读取失败: %s" % e)
    else:
        m = _message(p, sr, N)

    m = np.asarray(m, dtype=np.float64).reshape(-1)
    if m.size < N:
        m = np.tile(m, int(math.ceil(N / float(max(m.size, 1)))))
    m = m[:N]

    if mod == "AM":
        mu = float(p.get("mu") or 0.5)
        if not (0.0 <= mu <= 1.0):
            mu = 0.5
        # 素材的 AM 是"基带录音": 载波就落在 0 Hz (DC), 不是中频。
        # 以前默认放到 sr/8, 谱心会整体偏出去, 与素材对不上。
        fc = float(p.get("carrier") or 0.0)
        fc = max(-0.95 * sr / 2.0, min(fc, 0.95 * sr / 2.0))     # 限幅到奈奎斯特内
        t = np.arange(N) / float(sr)
        env = (1.0 + mu * m)
        y = amp * env * np.exp(1j * 2 * np.pi * fc * t)

        if p.get("real_out"):           # 实输出 = 正交分量为 0
            y = np.asarray(y.real, dtype=np.float64)

        pk = float(np.max(np.abs(y))) if N else 0.0
        if pk > 1e-9:
            y = y * (amp / pk)
        if np.isrealobj(y):
            y = y.astype(np.float64) + 1j * np.zeros(N)
        return y.astype(np.complex64)

    if mod == "FM":
        # 真 FM: 瞬时频率 f_i(t) = dev * m(t)  =>  相位 φ(t) = 2π·dev·∫m(t)dt
        #
        # 修复记录: 之前写成 `phase = 2π*(msg_bw+dev)*m/sr`, 有两个致命错误 --
        #   1) 缺少对消息的积分 -> 实际是"窄带调相(PM)", 相位偏移被 sr 除成一个
        #      极小的常数(<0.3 rad), 没有 Carson 展宽, 窄带素材几乎退化成纯载波
        #      (实测 fm_25k 99% 占用带宽 0.0 kHz, 而素材是 11.5 kHz);
        #   2) 直接由 occupied_bw 反推 dev, 完全忽略了 ORIG_TRUTH 里标定好的
        #      p["dev"], 导致 13 个 FM 标定参数全部失效。
        # 现在改回标准 FM: 频偏由 dev 控制, 用 cumsum 做积分。
        dev = float(p.get("dev") or DEFAULTS["dev"])
        # 去掉直流再积分, 否则 cumsum 会随机游走把载波拖偏 (等效于交流耦合的音频)
        mm = m - float(np.mean(m))
        phase = 2.0 * np.pi * dev * np.cumsum(mm) / float(sr)
        iq = amp * np.exp(1j * phase)
        return iq.astype(np.complex64)

    raise ValueError("未支持的模拟调制: %s" % mod)

def _bits_per_symbol(mod, M):
    M = max(2, int(M))
    if mod in ("BPSK", "DPSK", "MSK"):
        return 1
    if mod in ("QPSK", "Pi4DQPSK"):
        return 2
    if mod == "8PSK":
        return 3
    if mod == "16PSK":
        return 4
    if mod in ("16QAM", "32QAM", "64QAM", "256QAM", "1024QAM", "4096QAM"):
        return int(round(math.log2(M)))
    if mod in ("16APSK", "32APSK", "64APSK"):
        return int(round(math.log2(M)))
    return int(round(math.log2(M)))          # ASK / MFSK

def _pi4_dqpsk(syms):
    """pi/4-DQPSK 差分编码: 相位跳变取自 {pi/4, 3pi/4, 5pi/4, 7pi/4}。"""
    syms = np.asarray(syms, dtype=np.int64) % 4
    dtheta = (2.0 * syms + 1.0) * (np.pi / 4.0)
    return np.exp(1j * np.cumsum(dtheta)).astype(complex)

def _build_digital(mod, p, sr, N, amp):
    """包装层: 保证符号速率精确等于设定值。

    素材的采样率不总是符号速率的整数倍 (如 tetra 108k/25k=4.32,
    wcdma 6.4M/5M=1.28)。若直接把 sps 取整, 实际符号速率会被改掉,
    占用带宽随之偏移。这里先在"整数倍采样率"上合成, 再重采样到
    目标采样率, 使符号速率与占用带宽都精确。
    """
    sym = float(p.get("symbol_rate") or 0.0)
    if sym > 0 and sr > 0:
        sps_f = sr / sym
        sps_i = int(round(sps_f))
        # sps 上限 4096 -> 65536: 上限卡太低时, "跳频把采样率抬到 100 MHz、
        # 符号率只有 6 kHz"(sps≈16667) 这种组合会被强行压成 4096, 于是
        # sr_g=24.576 MHz 与目标 100 MHz 差 4 倍, 白白触发一次 4000 万点的
        # FFT 重采样(十几秒, 看着像卡死)。放宽后 sps 取整误差只有 0.002%,
        # 达不到下面的重采样门槛, 直接合成即可。
        sps_i = max(2, min(sps_i, 1 << 16))
        sr_g = sym * sps_i
        # 只有当相对偏差超过 0.05% 才需要重采样
        if abs(sr_g - sr) > sr * 5e-4 and sps_i >= 2:
            N_g = int(round(N * sr_g / sr)) + 8
            q = dict(p)
            iq = _build_digital_raw(mod, q, sr_g, N_g, amp)
            if iq is None or len(iq) < 8:
                return iq
            out = _resample_iq(iq, sr_g, sr, N)
            pk = float(np.max(np.abs(out))) if out.size else 0.0
            if pk > 1e-9:
                out = out * (amp / pk)
            return out.astype(np.complex64)
    return _build_digital_raw(mod, p, sr, N, amp)

def _build_digital_raw(mod, p, sr, N, amp):
    """PSK / QAM / ASK / FSK / MSK 基类带生成 (在采样率 sr 上直接合成)。

    修复记录:
      * sps / rolloff / _pi4_dqpsk 从未定义;
      * int(N // x, 0) 这种 int() 双参数写法直接 TypeError;
      * len(sps) 对一个整数取长度;
      * 16/32/64QAM 和 CP4FSK 没有分支 -> 一律 raise ValueError;
      * DPSK 的差分逻辑写进了临时变量 out_sym, 实际用的还是未差分的 sym_c。
    """
    N = int(max(N, 0))
    if N <= 0:
        return np.zeros(0, dtype=np.complex64)

    # ---- 进制数 M ----
    forced = {"2FSK": 2, "4FSK": 4, "CP4FSK": 4, "8FSK": 8, "16FSK": 16, "MSK": 2,
              "BPSK": 2, "DPSK": 2, "QPSK": 4, "Pi4DQPSK": 4, "OQPSK": 4,
              "8PSK": 8, "16PSK": 16, "16QAM": 16, "32QAM": 32, "64QAM": 64,
              "256QAM": 256, "1024QAM": 1024, "4096QAM": 4096,
              "16APSK": 16, "32APSK": 32, "64APSK": 64}
    M = forced.get(mod, int(p.get("M") or 2))
    M = max(2, int(M))
    p["M"] = M

    rolloff = float(p.get("rolloff") or 0.35)
    if not (0.0 <= rolloff <= 1.0):
        rolloff = 0.35

    # ---- 每符号采样数 ----
    sym_rate = float(p.get("symbol_rate") or 0.0)
    if 0 < sym_rate < sr:
        sps = max(2, int(round(sr / sym_rate)))
    else:
        sps = max(2, int(p.get("sps") or 4))
        sym_rate = sr / float(sps)
    if sym_rate <= 0:
        sym_rate = sr / float(sps)
        p["symbol_rate"] = sym_rate

    # ---- 符号序列 ----
    bps = _bits_per_symbol(mod, M)
    n_symbols = max(16, int(math.ceil(N / float(sps))) + 16)
    syms = build_bits(p.get("source_mode") or "random", p.get("bits", ""),
                      n_symbols, bps, p.get("seed"))
    syms = np.asarray(syms, dtype=np.int64) % (2 ** bps)

    pat = str(p.get("sym_pat") or "").strip()
    if pat:
        digs = [int(c) for c in pat if c.isdigit()]
        if digs:
            syms = np.array([digs[i % len(digs)] for i in range(len(syms))],
                            dtype=np.int64) % (2 ** bps)

    per = int(p.get("sym_period") or 0)
    if per > 0 and len(syms) > per:
        syms = np.tile(np.asarray(syms)[:per],
                       int(math.ceil(len(syms) / float(per))))[:len(syms)]

    # ---- 脉冲成形 / 调制 ----
    if mod in ("BPSK", "DPSK", "QPSK", "8PSK", "16PSK",
               "16QAM", "32QAM", "64QAM", "256QAM", "1024QAM", "4096QAM",
               "16APSK", "32APSK", "64APSK"):
        pts, _ = constellation_points(mod, M)
        pts = np.asarray(pts)
        idx = syms % len(pts)

        if mod == "DPSK":                     # 差分编码: 相位相对前一符号
            sym_c = np.cumprod(pts[idx])
        else:
            sym_c = pts[idx]

        base = np.zeros(len(syms) * sps, dtype=complex)
        base[::sps] = sym_c
        full = fast_convolve(base, rrc_filter(sps, rolloff))

    elif mod == "OQPSK":
        # 偏移 QPSK: Q 路相对 I 路延时半个符号 (T/2), 避免 180° 相位跳变,
        # 包络起伏比 QPSK 小, 适合非线性功放。ZigBee 就是它的恒包络变体。
        pts, _ = constellation_points("QPSK", 4)
        pts = np.asarray(pts)
        sym_c = pts[syms % len(pts)]
        off = max(1, sps // 2)                       # Q 路延时 T/2
        n0 = len(syms) * sps
        bi = np.zeros(n0, dtype=np.float64)
        bq = np.zeros(n0 + off, dtype=np.float64)
        bi[::sps] = sym_c.real
        bq[off::sps] = sym_c.imag                    # Q 路整体后移 off 个样本
        bq = bq[:n0]
        h = rrc_filter(sps, rolloff)
        fi = fast_convolve(bi, h)
        fq = fast_convolve(bq, h)
        m = min(fi.size, fq.size)
        full = fi[:m] + 1j * fq[:m]

    elif mod == "Pi4DQPSK":
        sym_c = _pi4_dqpsk(syms)
        base = np.zeros(len(syms) * sps, dtype=complex)
        base[::sps] = sym_c
        full = fast_convolve(base, rrc_filter(sps, rolloff))

    elif mod == "ASK":
        # ASK/OOK 是"单极性"电平 (0..1), 因此谱里必然有一根很强的载波(DC)分量。
        # 素材实测: ask_25.8k_25k 的 99% 能量带宽只有 0.4*Rs, 正是因为能量
        # 集中在载波上; 若按双极性(±1)生成则无载波, 带宽会宽 3 倍。
        mu = float(p.get("mu") if p.get("mu") is not None else 1.0)
        if not (0.0 <= mu <= 1.0):
            mu = 1.0
        unipolar = np.arange(M, dtype=np.float64) / max(M - 1, 1)   # 0 .. 1
        levels = (1.0 - mu) + mu * unipolar                          # mu=1 -> OOK
        mx = float(np.max(levels))
        if mx > 1e-12:
            levels = levels / mx
        levels = levels[:2 ** bps] if len(levels) >= 2 ** bps else levels

        syms_f = levels[syms % len(levels)].astype(np.float64)
        base = np.zeros(len(syms) * sps, dtype=np.float64)
        base[::sps] = syms_f
        full = fast_convolve(base, rrc_filter(sps, rolloff))

    elif mod in ("2FSK", "4FSK", "8FSK", "16FSK", "CP4FSK", "MSK"):
        shape = float(p.get("dev_shape") or 0.0)
        full = _fsk_waveform(mod, syms, sr, sps, len(syms), M,
                             sym_rate, p.get("dev"), shape, p.get("mod_index"))

    else:
        raise ValueError("未支持的数字调制: %s" % mod)

    # ---- 截取到目标长度并归一化 ----
    target = N
    full = np.asarray(full)
    if full.size >= target:
        start = (full.size - target) // 2
        out = full[start:start + target]
    else:
        out = np.zeros(target, dtype=complex)
        out[:full.size] = full

    peak = float(np.max(np.abs(out))) if target else 1.0
    if peak > 1e-9:
        out = out * (amp / peak)
    return out.astype(np.complex64)

def _fsk_waveform(mod, syms, sr, sps, n_symbols, M, sym_rate=None, dev=None,
                  shape=0.0, mod_index=None):
    """连续相位 MFSK / MSK / CP4FSK。

    CP4FSK (连续相位 4-FSK, 标准见 DMR/TETRA):
      调制指数 h, 4 电平 a_i∈{±1,±3}, 相位连续, 恒包络;
      频率偏移 Δf_i = h·R_s·a_i/2, 音隔 = h·R_s, 最大频偏 D = 3h·R_s/2。
      DMR 标准 h≈0.27 (R_s=4.8k → 音隔 1.296k, 最大频偏 ±1.944k)。
    dev = 半音隔频偏 Hz (旧语义: dev = h·R_s/2, 兼容 ORIG_TRUTH, 如 DMR dev=648)。
    mod_index(h) 优先; 否则用 dev; 再否则 CP4FSK 默认 h=0.27, 其余按 R_s 或 MSK。
    """
    M = max(2, int(M))
    syms = np.asarray(syms, dtype=np.int64).reshape(-1) % M
    shape = float(shape or 0.0)

    try:
        dev_v = float(dev) if dev not in (None, "") else 0.0
    except (TypeError, ValueError):
        dev_v = 0.0
    try:
        sym_rate = float(sym_rate or 0.0)
    except (TypeError, ValueError):
        sym_rate = 0.0
    try:
        h = float(mod_index) if mod_index not in (None, "") else 0.0
    except (TypeError, ValueError):
        h = 0.0

    if mod == "CP4FSK":
        # 标准 CP4FSK: 调制指数 h 决定音隔; 默认 DMR h=0.27 (窄带、连续相位)
        if h > 0 and sym_rate > 0:
            spacing = h * sym_rate
        elif dev_v > 0:
            spacing = 2.0 * dev_v            # 兼容: dev = h·R_s/2
        elif sym_rate > 0:
            spacing = 0.27 * sym_rate        # DMR 标准默认
        else:
            spacing = float(sr) / float(max(sps, 1))
    elif dev_v > 0:
        spacing = 2.0 * dev_v
    elif sym_rate > 0 and mod == "MSK":
        spacing = 2.0 * (sym_rate / 4.0)
    elif sym_rate > 0:
        spacing = sym_rate
    else:
        spacing = float(sr) / float(max(sps, 1))

    freqs = (np.arange(M) - (M - 1) / 2.0) * spacing
    dev_seq = np.repeat(freqs[np.asarray(syms, dtype=np.int64) % M], sps).astype(np.float64)

    if shape > 0 and sps > 1:
        wlen = max(2, int(round(shape * sps)))
        if wlen > 1: w = np.hanning(wlen + 2)[1:-1]; w /= w.sum()
        dev_seq = fast_convolve(dev_seq, w, "same")

    if len(dev_seq):
        fmax = float(np.max(np.abs(dev_seq)))
        limit = 0.45 * sr
        if fmax > limit and limit > 0: dev_seq *= (limit / fmax)

    phase = 2 * np.pi * np.cumsum(dev_seq) / sr
    return (np.cos(phase) + 1j * np.sin(phase)).astype(np.complex64)

ORIG_TRUTH = {
    "analogtv_6.9m_8m": dict(replay_block=100000000, replay_src="analogTV_6.9m_8m",
                              sample_rate=19200000.0),
    "analogtv":         dict(replay_block=100000000, replay_src="analogTV_6.9m_8m",
                              sample_rate=19200000.0),
    "cdr":              dict(replay_block=100000000, replay_src="cdr",
                              sample_rate=816000.0),
    "2fsk_12.7k_4k":     dict(M=2, dev=4000.0, symbol_rate=1800.0, sample_rate=180000.0),
    "2fsk_18.2k_6k":     dict(M=2, dev=6000.0, symbol_rate=2400.0, sample_rate=192000.0),
    "2fsk_23.7k_9k":     dict(M=2, dev=9000.0, symbol_rate=1800.0, sample_rate=180000.0),
    "4fsk_23.4k_3.125k": dict(M=4, dev=3125.0, symbol_rate=2400.0, sample_rate=192000.0),
    "4fsk_42.8k_6.25k":  dict(M=4, dev=6250.0, symbol_rate=4800.0, sample_rate=192000.0),
    "8fsk_47.5k_6.25k":  dict(M=8, dev=3125.0, symbol_rate=2400.0, sample_rate=192000.0),
    "msk_6.2k_5k":       dict(M=2, dev=1250.0, symbol_rate=5000.0, sample_rate=100000.0),
    "dmr_7.3k_12.5k":    dict(M=4, dev=648.0, symbol_rate=3600.0, sample_rate=120000.0),
    "pdt_7.3k_12.5k":    dict(M=4, dev=648.0, symbol_rate=3600.0, sample_rate=120000.0),
    "dpmr_3.2k_6.25k":   dict(M=4, dev=300.0, symbol_rate=1800.0, sample_rate=153600.0),
    "dpmr_6.4k_12.5k":   dict(M=4, dev=600.0, symbol_rate=3600.0, sample_rate=153600.0),
    "nxdn_3.2k_6.25k":   dict(M=4, dev=300.0, symbol_rate=1800.0, sample_rate=153600.0),
    "nxdn_6.4k_12.5k":   dict(M=4, dev=600.0, symbol_rate=3600.0, sample_rate=153600.0),

    # ---- 以下为"频谱比对"后按素材实测标定的条目 ----
    # TETRA: 25 kHz 是信道间隔, 实际符号速率 18 ksym/s (36 kbps / 2bit)
    "tetra_20.8k_25k":   dict(M=4, rolloff=0.35, symbol_rate=18000.0,
                              sample_rate=108000.0),
    # WCDMA: 5 MHz 是信道间隔, 码片速率 3.84 Mcps, 根升余弦滚降 0.22
    "wcdma_4.2m_5m":     dict(rolloff=0.22, symbol_rate=3840000.0,
                              sample_rate=6400000.0),
    # DPSK 素材实测有效滚降 ≈0.02, 对应真实滚降 0.1 (名称中未给出)
    "dpsk_6.2k_6k":      dict(rolloff=0.1, symbol_rate=6000.0, sample_rate=120000.0),
    "dpsk_30.5k_30k":    dict(rolloff=0.1, symbol_rate=30000.0, sample_rate=120000.0),
    "dpsk_121.2k_120k":  dict(rolloff=0.1, symbol_rate=120000.0, sample_rate=480000.0),

    # ---- FM 素材标定 (2026-09-19 重做) ----
    # 结论: 真 FM 下 边带谱 ∝ S_m(f)/f² (积分带来的 1/f²), 而素材实测是"载波 + 平顶宽带基座"。
    # 因此消息需要 +6 dB/oct 预加重来抵消 -> msg_pk≈1 (配合 msg_bw 定基座带宽,
    # msg_order 定滚降陡度, dev 定边带相对电平)。逐文件网格标定, 多数 3~5 dB。
    "fm_8k":          dict(mod="FM", msg_mode="noise", msg_norm="rms", msg_bw=2500,  msg_order=4,  msg_pk=0.25, dev=150,  sample_rate=200000.0),
    "fm_9k":          dict(mod="FM", msg_mode="noise", msg_norm="rms", msg_bw=1800,  msg_order=4,  msg_pk=0.5,  dev=108,  sample_rate=200000.0),
    "fm_10k":         dict(mod="FM", msg_mode="noise", msg_norm="rms", msg_bw=2500,  msg_order=4,  msg_pk=0.25, dev=150,  sample_rate=200000.0),
    "fm_12k":         dict(mod="FM", msg_mode="noise", msg_norm="rms", msg_bw=1800,  msg_order=4,  msg_pk=0.5,  dev=396,  sample_rate=200000.0),
    "fm_15k":         dict(mod="FM", msg_mode="noise", msg_norm="rms", msg_bw=1800,  msg_order=8,  msg_pk=0.75, dev=810,  sample_rate=200000.0),
    "fm_20k":         dict(mod="FM", msg_mode="noise", msg_norm="rms", msg_bw=2500,  msg_order=6,  msg_pk=0.75, dev=1125, sample_rate=200000.0),
    "fm_25k":         dict(mod="FM", msg_mode="noise", msg_norm="rms", msg_bw=3500,  msg_order=16, msg_pk=0.5,  dev=1137, sample_rate=200000.0),
    "fm_25k_wxdgl1":  dict(mod="FM", msg_mode="noise", msg_norm="rms", msg_bw=5000,  msg_order=8,  msg_pk=0.5,  dev=2250, sample_rate=200000.0),
    "fm_25k_wxdgl2":  dict(mod="FM", msg_mode="noise", msg_norm="rms", msg_bw=3500,  msg_order=8,  msg_pk=1.25, dev=2275, sample_rate=200000.0),
    "fm_30k":         dict(mod="FM", msg_mode="noise", msg_norm="rms", msg_bw=3500,  msg_order=24, msg_pk=0.75, dev=1575, sample_rate=200000.0),
    "fm_36k":         dict(mod="FM", msg_mode="noise", msg_norm="rms", msg_bw=5000,  msg_order=6,  msg_pk=0.5,  dev=1625, sample_rate=200000.0),
    "fm_40k":         dict(mod="FM", msg_mode="noise", msg_norm="rms", msg_bw=3500,  msg_order=6,  msg_pk=0.75, dev=2275, sample_rate=200000.0),
    "fm_120k":        dict(mod="FM", msg_mode="noise", msg_norm="rms", msg_bw=14000, msg_order=12, msg_pk=1.0,  dev=6300, sample_rate=200000.0),

    # ---- ASK 素材标定: 素材 ASK 是"强载波 + 弱边带"(小调幅深度), 减小 mu 使边带能量与素材一致 ----
    "ask_45k_125k":   dict(mod="ASK", M=2, symbol_rate=95000, rolloff=0.50, mu=0.2, sample_rate=200000.0),
    "ask_9k_25k":     dict(mod="ASK", M=2, symbol_rate=20000, rolloff=0.35, mu=0.1, sample_rate=100000.0),

    "rs2009_75k":        dict(replay_block=2000000, replay_src="rs2009_75k",
                              sample_rate=200000.0),
    "nlora":             dict(lora_bw=58260.0, lora_sf=7,
                              idle_head=0.3125, sample_rate=320000.0),
    "exam_cloud6_1":     dict(lora_bw=50661.0, lora_sf=10, idle_head=0.3125,
                              sample_rate=320000.0),
    "exam_cloud8_1":     dict(lora_bw=205e3, lora_sf=10, idle_head=0.3125,
                              sample_rate=320000.0),
    "exam_tkf6_1":       dict(lora_bw=60e3, lora_sf=10, idle_head=0.3125,
                              sample_rate=320000.0),
    "exam_cloud5_2":     dict(replay_block=2000000, replay_src="exam_cloud5_2",
                              sample_rate=320000.0),  # 活动段也是方波调频
    "exam_cloud5_1":     dict(replay_block=100000000, replay_src="exam_cloud5_1",
                              sample_rate=320000.0, duration=1.768),
}

_NAME_MOD = {
    "fm": "FM", "am": "AM", "cw": "CW", "awgn": "AWGN",
    "bpsk": "BPSK", "qpsk": "QPSK", "8psk": "8PSK", "16psk": "16PSK",
    "dpsk": "DPSK", "pi4dqpsk": "Pi4DQPSK", "pi4psk": "Pi4DQPSK",
    "16qam": "16QAM", "32qam": "32QAM", "64qam": "64QAM",
    "ask": "ASK", "2fsk": "2FSK", "4fsk": "4FSK", "8fsk": "8FSK",
    "msk": "MSK", "cp4fsk": "CP4FSK",
    "dmr": "4FSK", "pdt": "4FSK", "dpmr": "4FSK", "nxdn": "4FSK",
    "tetra": "Pi4DQPSK", "wcdma": "QPSK", "nbiot": "QPSK",
    "gsm": "GSM", "gsmr": "GSM", "ais": "AIS", "adsb": "ADSB",
    "atv": "ATV", "analogtv": "ATV", "cdr": "CDR", "dab": "CDR",
    "dtmb": "DTMB", "lte": "LTE", "ltefdd": "LTE", "ltetdd": "LTE",
    "sat": "SAT", "satellite": "SAT",
    "gps": "GPS", "glonass": "GLONASS", "bd1": "BEIDOU", "beidou": "BEIDOU",
    "lora": "LORA", "nlora": "LORA", "lora1": "LORA", "lora2": "LORA",
    "lora3": "LORA", "freqs": "FREQHOP", "freqhop": "FREQHOP",
    "groupradar": "RADAR",
    "agileradar": "RADAR", "freqagileradar": "RADAR", "radar": "RADAR",
    "rs2009": "REPLAY", "pseudobasestation": "REPLAY", "xs": "REPLAY",
    "sz": "REPLAY", "exam": "REPLAY",
}

def _pnum(t):
    """'6.4k' -> 6400.0 ; '2.2m' -> 2.2e6"""
    t = str(t).lower().replace(" ", "")
    try:
        if t.endswith("k"):
            return float(t[:-1]) * 1e3
        if t.endswith("m"):
            return float(t[:-1]) * 1e6
        return float(t)
    except ValueError:
        return 0.0

def _mk(mod, occupied_bw, **kw):
    sr = nice_sr(max(float(occupied_bw or 0.0), 1.0) * 1.25)
    dur = _choose_dur(sr)
    p = {"mod": mod, "occupied_bw": float(occupied_bw or 0.0), "sample_rate": sr,
         "duration": dur, "amplitude": 0.8, "rolloff": 0.35,
         "symbol_rate": 0.0, "M": 2, "mu": 0.5, "dev": 0.0, "carrier": 0.0,
         "tone": 1000.0, "seed": "", "source_mode": "random", "bits": "",
         "audio_path": ""}
    p.update(kw)
    return p

def _mod_from_name(name):
    """信号名 -> 调制族; 解析不出来返回 None。"""
    low = str(name or "").strip().lower()
    if not low:
        return None
    if low in _NAME_MOD:
        return _NAME_MOD[low]
    kw = low.split("_")[0]
    if kw in _NAME_MOD:
        return _NAME_MOD[kw]
    for k in sorted(_NAME_MOD, key=len, reverse=True):
        if low.startswith(k):
            return _NAME_MOD[k]
    return None

def _radar_pattern_of(low):
    m = re.search(r"(\d+)", low)
    n = m.group(1) if m else "1"
    if "group" in low:
        key = "group" + n
    elif "agile" in low:
        key = "fagile" + n
    else:
        key = "agile" + n
    if key not in RADAR_PATTERNS:
        key = "group1" if "group" in low else "agile1"
    return key

def _parse_systematic(name):
    """从 'bpsk_6.4k_6k_0.1' 这类系统命名推断全部参数。

    命名惯例: <族>[_占用带宽][_符号速率|频偏][_滚降系数]
    """
    low = str(name or "").strip().lower()
    parts = [p for p in low.split("_") if p]
    mod = _mod_from_name(name)

    nums = []
    for p in parts[1:]:
        for tk in re.findall(r"[0-9]*\.?[0-9]+[mk]?", p):
            nums.append(_pnum(tk))

    rolloff = None
    if nums and 0.0 < nums[-1] < 1.0:            # 尾部小数 = 滚降系数
        rolloff = nums[-1]
        nums = nums[:-1]

    occ = float(nums[0]) if len(nums) >= 1 else 0.0
    second = float(nums[1]) if len(nums) >= 2 else 0.0

    if mod == "FM":
        # 与 ORIG_TRUTH 同一模型: 边带谱 ∝ S_m(f)/f² -> 用 msg_pk≈0.75~1 的预加重
        # 得到素材那种"载波 + 平顶宽带基座"; msg_bw 定基座带宽, dev 定边带相对电平。
        # (经验标定趋势: dev ≈ 0.045 * occ, msg_bw ≈ occ/5)
        bw = max(occ, 1e3)
        msg_bw = float(np.clip(bw / 5.0, 1200.0, 14000.0))
        return _mk("FM", bw, tone=msg_bw, dev=bw * 0.045, msg_mode="noise",
                   msg_norm="rms", msg_bw=msg_bw, msg_pk=0.75, msg_order=6,
                   carrier=0.0, sample_rate=nice_sr(bw * 3.0))

    if mod == "AM":
        # 双边带 AM 的占用带宽 = 2 * 最高调制频率
        bw = max(occ, 1e3)
        mu = rolloff if rolloff is not None else 0.3
        return _mk("AM", bw, tone=max(bw / 2.0, 300.0),
                   mu=mu, sample_rate=nice_sr(bw * 3.0))

    if mod in ("CW", "AWGN"):
        return _mk(mod, occ or 10e3)

    if mod in ("BPSK", "QPSK", "8PSK", "16PSK", "Pi4DQPSK", "DPSK"):
        sym = second or max(occ / 2.0, 1e3)
        return _mk(mod, occ or 10e3, symbol_rate=sym,
                   rolloff=rolloff if rolloff is not None else 0.35)

    if mod in ("16QAM", "32QAM", "64QAM"):
        sym = second or max(occ / 2.0, 1e3)
        return _mk(mod, occ or 10e3, symbol_rate=sym,
                   rolloff=rolloff if rolloff is not None else 0.3)

    if mod == "ASK":
        sym = second or max(occ / 2.0, 1e3)
        return _mk("ASK", occ or 10e3, symbol_rate=sym, M=2,
                   rolloff=rolloff if rolloff is not None else 0.35)

    if mod in ("2FSK", "4FSK", "8FSK", "CP4FSK"):
        M = {"2FSK": 2, "4FSK": 4, "8FSK": 8, "CP4FSK": 4}[mod]
        dev = second or max(occ / (2.0 * M), 1e3)
        sym = max(occ / 8.0, 1200.0)
        return _mk(mod, occ or 10e3, M=M, dev=dev, symbol_rate=sym,
                   rolloff=rolloff if rolloff is not None else 0.35)

    if mod == "MSK":
        sym = second or max(occ / 2.0, 1e3)
        return _mk("MSK", occ or 10e3, M=2, symbol_rate=sym, dev=sym / 4.0)

    if mod == "GSM":
        bw = occ or 250e3
        return _mk("GSM", bw, gmsk_sym=270833.0, gmsk_bt=0.5,
                   sample_rate=nice_sr(bw * 4.0))

    if mod == "AIS":
        bw = occ or 25e3
        return _mk("AIS", bw, ais_bw=bw, gmsk_sym=9600.0,
                   sample_rate=nice_sr(bw * 8.0))

    if mod == "ADSB":
        return _mk("ADSB", occ or 2e6, adsb_rate=1e6)

    if mod == "ATV":
        bw = occ or 8e6
        return _mk("ATV", bw, atv_bw=bw)

    if mod == "CDR":
        bw = occ or 400e3
        return _mk("CDR", bw, ofdm_bw=bw)

    if mod == "LTE":
        bw = occ or 10e6
        return _mk("LTE", bw, ofdm_bw=bw,
                   duplex="TDD" if "tdd" in low else "FDD")

    if mod == "DTMB":
        bw = occ or 75e5
        return _mk("DTMB", bw, ofdm_bw=bw)

    if mod in ("GPS", "GLONASS", "BEIDOU"):
        return _mk(mod, 2.046e6, chip_rate=1.023e6)

    if mod == "LORA":
        bw = occ or 125e3
        return _mk("LORA", bw, lora_bw=bw, lora_sf=7)

    if mod == "FREQHOP":
        band = occ or 5e6
        return _mk("FREQHOP", band, fh_band=band, fh_rate=1000.0,
                   sample_rate=nice_sr(band * 2.5))

    if mod == "RADAR":
        return _mk("RADAR", 20e6, radar=_radar_pattern_of(low),
                   sample_rate=nice_sr(20e6))


    if mod == "SAT":
        bw = occ or 2e6
        return _mk("SAT", bw, symbol_rate=second or max(bw / 2.0, 1e3))

    # 解析不出来 -> 直接回放同名原始素材
    return {"mod": "REPLAY", "name": name, "occupied_bw": occ,
            "sample_rate": 0.0, "duration": 1.0, "amplitude": 0.8}

def _apply_truth(name, p):
    """用 ORIG_TRUTH 里登记的真实参数覆盖推断值。"""
    t = ORIG_TRUTH.get(str(name or "").lower())
    if not t:
        return p

    p = dict(p)
    p.update(t)

    need = 0.0
    if t.get("dev"):
        M = int(t.get("M") or 2)
        need = 2.0 * (((M - 1) / 2.0) * 2.0 * float(t["dev"]))
    sym = float(t.get("symbol_rate") or 0.0)
    need = max(need, sym * 4.0)
    if need > 0:
        p["sample_rate"] = max(float(p.get("sample_rate") or 0.0), nice_sr(need * 1.2))
    return p

_SPECIAL = {}

_NOT_IQ_EXT = {
    ".xml", ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".ico",
    ".cs16", ".wav", ".mp3", ".mp4", ".avi", ".bin", ".hex", ".dat",
    ".py", ".pyc", ".pyd", ".ipynb", ".spec", ".exe", ".dll", ".bat",
    ".sh", ".service", ".ps1", ".lnk",
    ".md", ".txt", ".log", ".json", ".ini", ".cfg", ".yml", ".yaml",
    ".toml", ".csv", ".xls", ".xlsx", ".doc", ".docx", ".pdf",
    ".zip", ".rar", ".7z", ".tar", ".gz", ".db", ".sqlite", ".bak", ".tmp",
}

def _scan_orig_files():
    """扫描 E:\\NEW\\signalwave 下的原始素材文件名 (作为信号库名)。"""
    out = []
    if not os.path.isdir(ORIG_DIR):
        return out
    try:
        entries = sorted(os.listdir(ORIG_DIR))
    except Exception:
        return out
    for f in entries:
        if f.startswith((".", "~$")):
            continue
        if os.path.splitext(f)[1].lower() in _NOT_IQ_EXT:
            continue
        if os.path.isfile(os.path.join(ORIG_DIR, f)):
            out.append(f)
    return out

def _init_special():
    _SPECIAL.clear()
    for nm in _scan_orig_files():
        try:
            d = resolve_signal(nm)
        except Exception:
            d = {"mod": "REPLAY", "name": nm}
        d["name"] = nm
        _SPECIAL[nm.lower()] = d
    for nm in ORIG_TRUTH:                 # ORIG_TRUTH 里独有的条目也补进来
        if nm not in _SPECIAL:
            d = resolve_signal(nm)
            d["name"] = nm
            _SPECIAL[nm] = d

def _special(name):
    """按名称查信号库条目 (不存在返回 None)。"""
    return _SPECIAL.get(str(name or "").strip().lower())

def _scan_orig_sr():
    """扫描素材目录, 建立 信号名 -> 素材采样率 表。

    素材的实际采样率都是"符号速率的整数倍"(4/8/10/16/20/100...),
    用素材的真实采样率生成, 频谱横轴与旁瓣结构才能和素材重合。
    """
    out = {}
    if not os.path.isdir(ORIG_DIR):
        return out
    for f in sorted(os.listdir(ORIG_DIR)):
        if not f.lower().endswith(".xml"):
            continue
        stem = f[:-4]
        if stem.lower().endswith("_mapped"):
            continue
        if not os.path.isfile(os.path.join(ORIG_DIR, stem)):
            continue
        sr = _orig_sample_rate(os.path.join(ORIG_DIR, stem))
        if sr > 0:
            out[stem.lower()] = float(sr)
    return out

ORIG_SR = _scan_orig_sr()

def resolve_signal(name):
    """信号名 -> 完整参数字典。"""
    low = str(name or "").strip().lower()
    hit = _SPECIAL.get(low)
    d = dict(hit) if hit else _parse_systematic(name)
    d["name"] = name

    d = _apply_truth(low, d)

    # 与素材对齐: 凡素材目录里存在的信号, 一律采用素材的采样率
    osr = ORIG_SR.get(low)
    if osr and osr > 0:
        d["sample_rate"] = osr

    t = ORIG_TRUTH.get(low) or {}
    if t.get("replay_src"):
        d["mod"] = "REPLAY"
    elif t.get("lora_bw"):
        d["mod"] = "LORA"

    # 时隙(TDMA)默认值: 按信号名关键字自动带上"时隙数/每时隙时长"
    # (发射模式 slot_tx 默认空 = 满时隙/连续, 保持原有连续输出; 需半时隙时再指定)
    if not d.get("slot_count"):
        for _kw, (_sc, _sd) in SLOT_PRESETS.items():
            if _kw in low:
                d["slot_count"] = _sc
                d["slot_dur"] = _sd
                d.setdefault("slot_tx", "")
                break

    keep = BASE_KEYS | EXTRA_KEYS
    for k in list(d.keys()):
        if k not in keep:
            d.pop(k, None)
    d["name"] = name
    return d

def catalog_names():
    """GUI 下拉框使用的信号库清单。"""
    return sorted(_SPECIAL.keys())

def lib_signature():
    """素材目录指纹: ((文件名, 大小, mtime_ns), ...) 已排序。"""
    out = []
    try:
        with os.scandir(ORIG_DIR) as it:
            for e in it:
                try:
                    if not e.is_file():
                        continue
                    st = e.stat()
                    out.append((e.name, int(st.st_size), int(st.st_mtime_ns)))
                except OSError:
                    continue
    except Exception:
        return ()
    out.sort()
    return tuple(out)

def refresh_library():
    """重建信号库索引(_SPECIAL / ORIG_SR 就地更新, 保持对象引用不变)。

    返回 (条目数, 新增名列表, 删除名列表)。ORIG_SR 必须先更新:
    _init_special() -> resolve_signal() 会按素材采样率对齐参数。
    """
    old = set(_SPECIAL.keys())
    try:
        new_sr = _scan_orig_sr()
        ORIG_SR.clear()
        ORIG_SR.update(new_sr)
    except Exception:
        pass
    try:
        _init_special()
    except Exception:
        pass
    global LIB_SIG
    LIB_SIG = lib_signature()
    new = set(_SPECIAL.keys())
    return len(_SPECIAL), sorted(new - old), sorted(old - new)

LIB_SIG = lib_signature()

def peak_abs(iq, chunk=1 << 22):
    """分块求 |iq| 的最大值 (避免为几千万样本再开一个同尺寸的浮点临时数组)。

    np.max(np.abs(iq)) 在 4000 万样本上会临时分配 160 MB; 分块后与长度无关。
    """
    pk = 0.0
    x = np.asarray(iq)
    for s in range(0, int(x.size), int(chunk)):
        m = float(np.abs(x[s:s + chunk]).max())
        if m > pk:
            pk = m
    return pk

def power_and_peak(iq, chunk=1 << 22):
    """分块算 (平均功率, 峰值)。4000 万样本下 np.abs(iq)**2 会临时开 320 MB。"""
    x = np.asarray(iq)
    n = int(x.size)
    if n == 0:
        return 0.0, 0.0
    acc = 0.0
    pk = 0.0
    step = int(chunk)
    for s in range(0, n, step):
        a = np.abs(x[s:s + step])
        acc += float(np.sum(a * a))
        m = float(a.max())
        if m > pk:
            pk = m
    return acc / n, pk

def write_cs16(iq, path):
    """写出 cs16 (交错 int16 的 I/Q)。

    分块写: 原先一次性把 I/Q 转 float64 再交错, 4000 万样本要同时驻留
    i/q(float64, 各 320 MB) + i16/q16(各 80 MB) + inter(160 MB) ≈ 1.4 GB,
    而文件本身只有 160 MB —— 生成带跳频的大信号时很容易把内存顶爆(或换页卡住)。
    分块后峰值仅约 4 MB(与文件大小无关), 且逐元素算法完全一致(输出比特不变)。
    """
    x = np.asarray(iq)
    n = int(x.size)
    step = 1 << 22
    with open(path, "wb") as f:
        for s in range(0, n, step):
            seg = x[s:s + step]
            i16 = np.clip(np.round(np.asarray(seg.real, dtype=np.float64) * 32767.0),
                          -32768, 32767).astype(np.int16)
            q16 = np.clip(np.round(np.asarray(seg.imag, dtype=np.float64) * 32767.0),
                          -32768, 32767).astype(np.int16)
            inter = np.empty(2 * i16.size, dtype=np.int16)
            inter[0::2] = i16
            inter[1::2] = q16
            f.write(inter.tobytes())

def make_xml_content(sample_rate, p):
    """生成 <name>.xml 描述文件内容。

    修复记录: 第 1694 行多了一个右括号 (直接 SyntaxError),
    后面的 `if p["mod"] in DIGITAL_FAMILIES:` 被顶到行首又缩进回来
    (IndentationError); AM 的 mu 取值写法 `p.get("mu" or 0.5, 1.0)` 也是错的。
    """
    mod = p.get("mod") or ""

    lines = ['<?xml version="1.0" encoding="utf-8"?>',
             "<signalwave>", "  <configs>"]

    def add(name, val):
        lines.append('    <config name="%s" val="%s"/>' % (name, val))

    add("sample_rate", repr(float(sample_rate)))
    add("name", p.get("name") or mod)
    add("modulation", mod)

    snr = p.get("snr_db")
    if snr is not None and snr != "":
        try:
            add("snr_db", repr(float(snr)))
            add("noise", "AWGN")
        except (TypeError, ValueError):
            pass

    if p.get("occupied_bw"):
        add("occupied_bw", repr(float(p["occupied_bw"])))
    if mod not in ("CW", "AWGN", "REPLAY"):
        add("symbol_rate", repr(float(p.get("symbol_rate") or 0.0)))
    if mod in PSK_MODS:
        add("rolloff", repr(float(p.get("rolloff") or 0.0)))

    if mod == "AM":
        mu = p.get("mu")
        if mu is None:
            mu = 0.5
        add("modulation_index", repr(float(mu)))
    elif mod == "FM":
        add("freq_deviation", repr(float(p.get("dev") or 0.0)))

    if mod in DIGITAL_FAMILIES:
        add("M", repr(int(p.get("M") or 2)))
        if mod == "ASK":
            mu = p.get("mu")
            if mu is None:
                mu = 0.5
            add("mu", repr(float(mu)))

    for k in ("lora_sf", "lora_bw", "chip_rate", "gmsk_sym", "gmsk_bt",
              "ais_bw", "atv_bw", "adsb_rate", "fh_band", "fh_rate"):
        if k in p and p[k] not in (None, ""):
            try:
                add(k, repr(float(p[k])))
            except (TypeError, ValueError):
                add(k, repr(p[k]))

    if mod in ("LTE", "DTMB", "CDR"):
        add("ofdm_bw", repr(float(p.get("ofdm_bw") or 0.0)))
        if mod == "LTE":
            add("duplex", repr(p.get("duplex", "FDD")))

    if mod == "RADAR":
        add("radar_pattern", repr(p.get("radar", "agile1")))

    # 时隙(TDMA)配置
    if int(p.get("slot_count") or 0) > 1:
        add("slot_count", repr(int(p["slot_count"])))
        add("slot_dur", repr(float(p.get("slot_dur") or 0.0)))
        add("slot_tx", str(p.get("slot_tx") or "all"))
        _mk = _slot_mask(p.get("slot_tx"), int(p["slot_count"]))
        if _mk is not None:
            add("slot_pattern", "".join("1" if x else "0" for x in _mk))

    # 同频混合配置
    _mix = p.get("mix") or []
    if _mix:
        add("mix_count", repr(len(_mix)))
        for _i, _c in enumerate(_mix):
            add("mix_%d" % _i, _mix_spec_of(_c))

    # 输出带通滤波
    try:
        _bpf_bw = float(p.get("bpf_bw") or 0.0)
    except (TypeError, ValueError):
        _bpf_bw = 0.0
    if _bpf_bw > 0 or (p.get("bpf_flo") not in (None, "")
                       and p.get("bpf_fhi") not in (None, "")):
        add("bpf_fc", repr(float(p.get("bpf_fc") or 0.0)))
        add("bpf_bw", repr(_bpf_bw))
        add("bpf_order", repr(int(float(p.get("bpf_order") or 4))))

    add("amplitude", repr(float(p.get("amplitude") or 0.8)))
    add("duration", repr(float(p.get("duration") or 0.0)))

    lines.append("  </configs>")
    lines.append("</signalwave>\n")
    return "\n".join(lines)

def write_xml(iq_path, sample_rate, p):
    base = os.path.splitext(iq_path)[0]
    xml_main = base + ".xml"

    content = make_xml_content(sample_rate, p)
    with open(xml_main, "w", encoding="utf-8") as f: f.write(content)
    shim = iq_path + ".xml"
    if os.path.abspath(shim) != os.path.abspath(xml_main):
        with open(shim, "w", encoding="utf-8") as f: f.write(content)

    return xml_main, shim

def compute_spectrum(iq, sr, nfft=2048):
    """Welch 式平均周期图。返回 (freq_hz, psd_db), psd_db 已按峰值归一化 (峰值=0 dB)。"""
    x = np.asarray(iq).ravel()
    n = int(x.size)
    if n < 16 or not sr or sr <= 0:
        return np.zeros(0), np.zeros(0)

    if x.dtype != np.complex128:
        x = x.astype(np.complex128)

    # FFT 长度取 <= nfft 且不超过 n 的 2 的幂
    L = 1 << int(math.floor(math.log2(max(n, 1))))
    L = int(min(int(nfft), L))
    L = max(min(L, n), 64)

    w = np.hanning(L)
    w = w / math.sqrt(float(np.mean(w ** 2)))      # 噪声功率归一化窗

    step = max(1, L // 2)
    acc = np.zeros(L, dtype=np.float64)
    cnt = 0
    for s in range(0, n - L + 1, step):
        seg = x[s:s + L] * w
        acc += np.abs(np.fft.fft(seg)) ** 2
        cnt += 1
    if cnt == 0:                                    # n < L 的极端情况
        seg = np.zeros(L, dtype=np.complex128); seg[:n] = x[:n]
        acc = np.abs(np.fft.fft(seg * w)) ** 2
        cnt = 1

    psd = acc / float(cnt * L)
    peak = float(np.max(psd)) if psd.size else 0.0
    ref = peak if peak > 0 else 1.0
    psd_db = 10.0 * np.log10(psd / ref + 1e-20)

    f = np.fft.fftfreq(L, 1.0 / float(sr))
    order = np.argsort(f)
    return f[order], psd_db[order]

def occupied_bandwidth(iq, sr, frac=0.99, max_len=8192):
    """按能量占比估计占用带宽 (Hz)。frac=0.99 即 99% 能量带宽。

    max_len 是参与估计的样本数上限。默认 8192 对被测信号"时不变"的场合足够,
    而且频率分辨率 = sr/max_len 一直够细。但跳频信号是**时变**的: 8192 个样本
    在 100 MHz 采样率下只有 82 us, 连一个跳频周期(蓝牙 1/1600 s = 625 us)都不到,
    测出来的其实是"某一跳"的带宽(约 100 kHz), 与谱图上铺开的 79 MHz 自相矛盾。
    调用方知道跳频参数时应传入能覆盖若干个跳周期的窗口。
    """
    x = np.asarray(iq).ravel()
    n = int(x.size)
    if n < 64 or not sr or sr <= 0:
        return 0.0

    L = int(min(n, max(64, int(max_len))))
    w = np.hanning(L)
    seg = np.asarray(x[:L], dtype=np.complex128) * w
    P = np.abs(np.fft.fft(seg)) ** 2
    f = np.fft.fftfreq(L, 1.0 / float(sr))
    order = np.argsort(f)
    P = P[order]; f = f[order]

    c = np.cumsum(P)
    total = float(c[-1])
    if total <= 0:
        return 0.0
    lo = int(np.searchsorted(c, total * (1.0 - frac) / 2.0))
    hi = int(np.searchsorted(c, total * (1.0 + frac) / 2.0))
    lo = max(0, min(lo, L - 1)); hi = max(0, min(hi, L - 1))
    return float(abs(f[hi] - f[lo]))

def _sr_eff_for_params(p):
    """预测 build_iq 实际会用到的采样率 (Hz)。

    必须与 _build_core / 素材回放 / _build_mix 的"抬高采样率"逻辑保持一致:
      - 素材回放: _replay_block 一律改用素材 xml 里的采样率(而不是输入框的值);
      - 制式合成: _build_core 里 `p["sample_rate"] = nice_sr(_min_sample_rate(...))`;
      - 同频混合: _build_mix 取"各路需求的最大值"作为共用采样率。
    任何一条被漏掉, build_preview 就会按偏小的采样率估时长, 于是先合成出一个
    超大数组再截掉 —— 这正是"选蓝牙跳频后卡死"和"混合里含跳频路要 1.5 s"的成因。
    """
    q = dict(p)
    sr = float(q.get("sample_rate") or 0.0) or float(DEFAULTS["sample_rate"])
    # 先补制式默认参数 (与 _build_core 一致), 否则只看得到输入框里的那几个键
    _fill_mod_defaults(q.get("mod"), q)

    # 0) 同频混合优先: 只要配了 mix, build_iq 就先走 _build_mix, 共用采样率
    #    完全由它决定(与顶层 mod / 输入框里的采样率都无关)。
    if q.get("mix"):
        try:
            csr = float(_mix_common_sr(q) or 0.0)
            if csr > 0:
                return csr
        except Exception:
            pass

    # 1) 素材回放: 采样率由素材决定, 与声明的采样率无关
    try:
        path = _orig_file(q)
        if path:
            osr = float(_orig_sample_rate(path) or 0.0)
            if osr > 0:
                return osr
    except Exception:
        pass

    # 2) 制式自身带宽 + 跳频总带宽
    need = sr
    try:
        need = max(need, float(_min_sample_rate(str(q.get("mod") or ""), q) or 0.0))
    except Exception:
        pass

    return float(nice_sr(need)) if need > sr * 1.0001 else sr

def _preview_sample_cap(p, sr_eff):
    """预览的样本数上限。

    跳频信号必须放宽: 26 万样本在 SINCGARS(58 MHz 带宽 -> 61 MSps, 111 hop/s)
    下只有 4.3 ms, 连一个跳周期(9 ms)都装不下 —— 预览里就完全看不到跳频, 与
    "跳频发射"这个功能名不符。这里按"至少 3 个跳周期"放宽, 并封顶在
    PREVIEW_HOP_MAX_SAMPLES(200 万, 约 0.4 s 生成耗时), 避免慢跳预置把界面拖住。
    """
    cap = PREVIEW_MAX_SAMPLES
    try:
        if p.get("hop_on"):
            hr = float(p.get("hop_rate") or 0.0)
            if hr > 0 and sr_eff > 0:
                need = int(3.0 * float(sr_eff) / hr)
                cap = int(max(cap, min(need, PREVIEW_HOP_MAX_SAMPLES)))
    except Exception:
        pass
    return cap

def build_preview(p, max_dur=PREVIEW_DEFAULT_DUR,
                  max_samples=None):
    """按当前参数合成一段"预览用"短信号。

    返回 (iq, sr, note): note 说明预览时长/样本数是否被裁剪。

    重要: 预览时长必须按 **build_iq 抬高后的采样率** 来裁剪。
    build_iq -> _build_core 内部会把采样率抬到 _min_sample_rate()(奈奎斯特
    + 制式带宽 + 跳频总带宽)。跳频开了以后这个值会暴涨 —— 例如"蓝牙 FHSS"
    预置总带宽 79 MHz, 采样率从输入框里的 96 kHz 被抬到约 87 MHz(近 900 倍)。
    若仍按输入采样率算上限, 0.05 s 的上限在抬高后变成 4300 万样本, 预览要跑
    一分多钟且界面完全无响应 —— 表现就是"选中蓝牙跳频后程序卡死"。
    """
    q = dict(p)
    sr = float(q.get("sample_rate") or 0.0) or float(DEFAULTS["sample_rate"])
    dur = float(q.get("duration") or 0.0) or float(DEFAULTS["duration"])

    # 预测 build_iq 会用到的实际采样率(与 _build_core 里同一套判据)
    sr_eff = sr
    try:
        sr_eff = float(_sr_eff_for_params(q) or sr) or sr
    except Exception:
        sr_eff = sr

    note = ""
    if sr_eff > 0:
        cap = max_dur if (max_dur and max_dur > 0) else PREVIEW_DEFAULT_DUR
        # max_samples 为 None 时按信号类型自动定(跳频放宽到 3 个跳周期)
        mcap = max_samples if (max_samples and max_samples > 0) \
            else _preview_sample_cap(q, sr_eff)
        cap = min(cap, mcap / sr_eff)
        if dur > cap:
            dur = cap
            note = "预览已裁剪为 %.4f s (%d 样本)" % (
                dur, int(round(dur * sr_eff)))
    q["duration"] = dur

    iq = np.asarray(build_iq(q))
    sr_out = float(q.get("sample_rate") or sr)

    # 兜底: 万一实际样本数仍超出上限(实际采样率比预测的还高, 例如某个构造器
    # 自己改了采样率), 直接截断 —— 截断只丢时间, 不改变频谱形态; 抽点会混叠。
    lim = (max_samples if (max_samples and max_samples > 0)
           else _preview_sample_cap(q, sr_out))
    if lim and iq.size > lim:
        iq = iq[:int(lim)]
        note = (note + " " if note else "") + "预览已截断为 %d 样本" % int(lim)
    return iq, sr_out, note

def bw_window_for(p, sr, n=None):
    """按参数挑"占用带宽"估计窗口的样本数。

    跳频信号的占用带宽必须跨若干个跳周期来测, 否则测到的是单跳的带宽。
    非跳频信号保持 8192(与历史结果一致, 避免影响 147 种信号的带宽校核)。
    """
    L = 8192
    try:
        if p.get("hop_on"):
            hr = float(p.get("hop_rate") or 0.0)
            if hr > 0 and sr > 0:
                # 6 个跳周期, 上限 1M 点(1M 点 FFT 约 0.1 s, 再多会拖慢预览)
                L = int(max(8192, min(6.0 * float(sr) / hr, 1 << 20)))
    except Exception:
        pass
    if n:
        L = int(max(64, min(L, int(n))))
    return L

_FAMILY_FIELDS = {
    "symbol_rate": ("ASK", "2FSK", "4FSK", "8FSK", "CP4FSK", "MSK", "BPSK", "QPSK",
                    "8PSK", "16PSK", "DPSK", "Pi4DQPSK", "Pi4PSK",
                    "16QAM", "32QAM", "64QAM", "SAT"),
    "rolloff": tuple(PSK_MODS) + ("ASK", "2FSK", "4FSK", "8FSK", "CP4FSK",
                                  "16QAM", "32QAM", "64QAM"),
    "M": ("ASK", "2FSK", "4FSK", "8FSK", "CP4FSK"),
    "mod_index": ("CP4FSK",),
    "mu": ("AM", "ASK"),
    "dev": ("FM", "2FSK", "4FSK", "8FSK", "MSK"),
    "carrier": ("AM", "CW"),
    "tone": ("AM", "FM"),
    "bridge_bw": ("BRIDGE",),
    "bridge_mod": ("BRIDGE",),
    "bridge_duty": ("BRIDGE",),
    # --- 新增真实世界信号的专用字段 ---
    "ssb_side": ("SSB",),
    "morse_wpm": ("MORSE",),
    "fmcw_bw": ("FMCW",),
    "fmcw_pri": ("FMCW",),
    "ofdm_bw": ("NR5G", "DVBT", "ISDBT", "DRM", "NBIOT", "LTEM", "FREEDV",
                "HDRADIO", "LTE", "DTMB", "CDR", "DAB"),
    "ofdm_const": ("NR5G", "DVBT", "ISDBT", "DRM", "NBIOT", "LTEM", "FREEDV"),
    # --- 第二批信号的专用字段 ---
    "mfsk_tones": ("WSPR", "JT65", "OLIVIA", "16FSK"),
    "tone_spacing": ("WSPR", "JT65", "OLIVIA", "FT8"),
    "sub_fc": ("NOAAAPT", "SSTV", "FAX"),
    "sub_dev": ("SSTV", "FAX"),
    "pulse_pri": ("PAM", "PWM", "PPM", "PULSEDOPPLER", "DME", "SSR"),
    "pulse_pw": ("PAM", "PWM", "PPM", "PULSEDOPPLER", "DME", "SSR"),
    "fh_band": ("FREQHOP", "LINK16", "HAVEQUICK", "SINCGARS"),
    "uwb_bw": ("UWB",),
    "bridge_mod": ("BRIDGE", "WIFI6", "WIFI7"),
    "bridge_bw": ("BRIDGE", "WIFI6", "WIFI7"),
    "bridge_duty": ("BRIDGE", "WIFI6", "WIFI7"),
    "chip_rate": ("GPS", "GLONASS", "BEIDOU", "GALILEO", "ZIGBEE", "DSSS"),
    "mu": ("AM", "ASK", "RKE", "TPMS", "HELL", "HAVEQUICK"),
    "dev": ("FM", "NBFM", "WBFM", "2FSK", "4FSK", "8FSK", "MSK"),
    "M": ("ASK", "2FSK", "4FSK", "8FSK", "16FSK", "CP4FSK"),
    "symbol_rate": tuple(DIGITAL_FAMILIES) + (
        "SAT", "TETRA", "DMR", "PDT", "P25", "POCSAG", "NAVTEX", "RTTY",
        "PSK31", "FT8", "ACARS", "NXDN", "DPMR", "C4FM", "M17", "VDL2",
        "DSC", "METEOR", "INMARSAT", "ZWAVE", "FLEX", "HELL", "RKE", "TPMS",
        "SIGFOX", "SOQPSK", "LINK16", "IRIDIUM", "ATSC", "DVBS2", "DVBC",
        "WSPR", "JT65", "OLIVIA"),
    "rolloff": tuple(PSK_MODS) + (
        "ASK", "2FSK", "4FSK", "8FSK", "16FSK", "CP4FSK", "OQPSK",
        "16QAM", "32QAM", "64QAM", "256QAM", "1024QAM", "4096QAM",
        "16APSK", "32APSK", "64APSK", "SAT", "TETRA", "DMR", "PDT", "P25",
        "POCSAG", "NAVTEX", "RTTY", "PSK31", "FT8", "ACARS", "NXDN", "DPMR",
        "C4FM", "M17", "VDL2", "DSC", "METEOR", "INMARSAT", "ZWAVE", "FLEX",
        "HELL", "RKE", "TPMS", "SIGFOX", "ATSC", "DVBS2", "DVBC", "IRIDIUM"),
}

MIX_LIB_NONE = "（不使用·按调制方式合成）"

HOP_PATTERNS = ("顺序(循环)", "伪随机", "自定义序列")

HOP_PRESET_NONE = "（自定义）"

HOP_PRESETS = {
    "蓝牙 FHSS":  {"points": 79,   "band": 79.0e6, "rate": 1600.0},   # 79×1MHz, 1600 hop/s
    "GSM 跳频":   {"points": 64,   "band": 12.8e6, "rate": 217.0},    # 64×200kHz, 217 hop/s
    "SINCGARS":   {"points": 2320, "band": 58.0e6, "rate": 111.0},    # 2320 频点, 111 hop/s
    "HAVEQUICK":  {"points": 1000, "band": 25.0e6, "rate": 769.0},    # 快跳
    "慢跳(战术)": {"points": 20,   "band": 1.0e6,  "rate": 20.0},     # 慢速跳频
    "快跳(抗干扰)": {"points": 50, "band": 5.0e6,  "rate": 5000.0},
}

def hop_grid_freqs(points, band, foff):
    """把总带宽均分成 points 个频点, 返回各频点频率(Hz)。

    以中心为 0 左右对称展开, 这样频谱图上跳频跨度正好等于总带宽。
    """
    points = max(1, int(points))
    band = max(0.0, float(band))
    if points <= 1:
        return np.array([float(foff)])
    step = band / float(points - 1)
    k = np.arange(points, dtype=np.float64)
    return float(foff) + (k - (points - 1) / 2.0) * step

def _hop_parse_seq(text, points, band, foff):
    """解析自定义序列。

    支持两种写法:
      "0, 1.5e6, 3e6"   -> 直接给频率(Hz)
      "idx:0,3,1,2"     -> 给格子序号(0 起), 省得算频率
    """
    txt = str(text or "").strip()
    if not txt:
        return None
    by_index = txt.lower().startswith("idx:")
    if by_index:
        # 先把前缀摘掉再切分 —— 否则第一项 "idx:0" 会被当成非法值丢掉
        txt = txt[4:].strip()
    vals = [x for x in re.split(r"[,\s;]+", txt) if x != ""]
    grid = hop_grid_freqs(points, band, foff)
    try:
        if by_index:
            n = int(grid.size)
            idxs = [int(float(x)) for x in vals]
            if not idxs:
                return None
            # 越界取模: 手写序列时不必先数清楚有多少个格子
            return np.array([grid[int(i) % n] for i in idxs], dtype=np.float64)
        return np.array([float(x) for x in vals], dtype=np.float64)
    except Exception:
        return None

def hop_sequence(pattern, points, band, seed, seq_text, foff, n_hops):
    """生成长度 n_hops 的频率序列(Hz)。

    伪随机用「格点随机置换」而不是连续随机频率: 真实跳频电台就是在一张
    频率表里按伪随机序跳, 频率永远落在离散格点上, 频谱上是若干离散尖峰
    而不是一片糊。
    """
    n_hops = max(1, int(n_hops))
    grid = hop_grid_freqs(points, band, foff)
    pat = str(pattern or HOP_PATTERNS[0])

    if pat == "自定义序列":
        custom = _hop_parse_seq(seq_text, points, band, foff)
        if custom is not None and custom.size:
            reps = int(math.ceil(n_hops / float(custom.size)))
            return np.tile(custom, reps)[:n_hops]
        pat = HOP_PATTERNS[0]                # 序列为空 -> 退回顺序

    if pat == "伪随机":
        rng = _rng(seed if seed not in (None, "") else 12345)
        # 先分配再按整轮置换填充 —— 原来是 while + np.concatenate 拼接,
        # 在点数少(如 points=1)而跳数多时每轮只追加 1 个元素, 复杂度 O(n^2),
        # 跳到几十万次就会把界面卡死。
        out = np.empty(n_hops, dtype=np.float64)
        filled = 0
        while filled < n_hops:
            perm = grid[rng.permutation(grid.size)]
            take = min(n_hops - filled, perm.size)
            out[filled:filled + take] = perm[:take]
            filled += take
        return out

    reps = int(math.ceil(n_hops / float(grid.size)))
    return np.tile(grid, reps)[:n_hops]

def _apply_freq_hop(iq, sr, p):
    """把已生成的基带 IQ 搬到跳变的载频上。

    逐跳处理而不是整段 cumsum: 40M 样本连续累加相位会累积浮点误差,
    分段算还能顺手做"每跳重置相位"和保护间隔。
    """
    if not p.get("hop_on"):
        return iq
    iq = np.asarray(iq)
    if iq.size == 0:
        return iq
    sr = float(sr or 0.0)
    rate = float(p.get("hop_rate") or 0.0)
    if sr <= 0 or rate <= 0:
        return iq

    hop_samp = max(2, int(round(sr / rate)))
    n_hops = int(math.ceil(iq.size / float(hop_samp)))
    freqs = hop_sequence(p.get("hop_pattern"), p.get("hop_points") or 1,
                         p.get("hop_band") or 0.0, p.get("seed"),
                         p.get("hop_seq"), p.get("hop_foff") or 0.0, n_hops)

    guard_s = max(0.0, float(p.get("hop_guard") or 0.0)) * 1e-6
    guard_samp = int(round(guard_s * sr))
    guard_samp = max(0, min(guard_samp, hop_samp - 1))
    cphase = bool(p.get("hop_cphase", True))

    out = np.empty(iq.size, dtype=np.complex64)
    phi0 = 0.0
    for k in range(n_hops):
        s = k * hop_samp
        e = min(iq.size, s + hop_samp)
        if e <= s:
            break
        n = e - s
        t = np.arange(n, dtype=np.float64) / sr
        ph = 2.0 * np.pi * float(freqs[k]) * t
        if cphase:
            ph = ph + phi0
        # 全程走 complex64: 40M 样本下 complex128 的输出缓冲要 640 MB,
        # 叠加 iq 本身后内存峰值过 1 GB, 会触发换页 —— 看起来也像"卡死"。
        rot = np.empty(n, dtype=np.complex64)
        rot.real = np.cos(ph)
        rot.imag = np.sin(ph)
        seg = iq[s:e] * rot
        if guard_samp > 0:
            seg[:min(guard_samp, n)] = 0.0        # 换频期间静默
        out[s:e] = seg
        if cphase:
            phi0 = float(ph[-1]) + 2.0 * np.pi * float(freqs[k]) / sr
    return out


__all__ = ["_add_awgn", "_anti_clip", "_apply_freq_hop", "_apply_output_bpf", "_apply_tdma_slots", "_apply_truth", "_bits_per_symbol", "_build_adsb", "_build_ais", "_build_analog", "_build_analog_ext", "_build_atsc", "_build_atv", "_build_bridge", "_build_burst", "_build_cdr", "_build_core", "_build_cw_like", "_build_cwdoppler", "_build_dab", "_build_digital", "_build_digital_raw", "_build_dscdma", "_build_fasthop", "_build_fmcw", "_build_freqhop", "_build_gmsk", "_build_gnss", "_build_hop_voice", "_build_ils", "_build_lora", "_build_mfsk_tones", "_build_mix", "_build_morse", "_build_nfc", "_build_ofdm", "_build_pulse_mod", "_build_pulse_pair", "_build_pulsedoppler", "_build_radar", "_build_rds", "_build_rfid", "_build_selcal", "_build_soqpsk", "_build_ssb", "_build_subcarrier", "_build_thss", "_build_time", "_build_uwb", "_build_vor", "_build_zigbee", "_cdr_ofdm_side", "_choose_dur", "_fft_convolve_full", "_fill_mod_defaults", "_fsk_waveform", "_hop_parse_seq", "_init_special", "_lchirp", "_message", "_min_sample_rate", "_min_sample_rate_base", "_mix_common_sr", "_mix_component_params", "_mix_spec_of", "_mk", "_mod_from_name", "_next_fast_len", "_orig_file", "_orig_sample_rate", "_parse_systematic", "_pi4_dqpsk", "_pnum", "_preview_sample_cap", "_qam_square", "_radar_pattern_of", "_radar_spec", "_replay_block", "_resample_complex", "_resample_iq", "_rng", "_scan_orig_files", "_scan_orig_sr", "_slot_mask", "_slot_period_window", "_special", "_sr_eff_for_params", "apsk_constellation", "build_bits", "build_iq", "build_preview", "bw_window_for", "catalog_names", "compute_spectrum", "constellation_points", "fast_convolve", "hop_grid_freqs", "hop_sequence", "lib_signature", "make_xml_content", "nice_sr", "occupied_bandwidth", "peak_abs", "power_and_peak", "prepare_audio_source", "qam_constellation", "read_wav_mono", "refresh_library", "resolve_signal", "rrc_filter", "write_cs16", "write_xml"]
