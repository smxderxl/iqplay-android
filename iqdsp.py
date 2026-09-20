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



def parse_xml_sample_rate(xml_path):
    if not os.path.exists(xml_path):
        return None
    data = None
    for enc in ["utf-8-sig", "utf-8", "gbk", "gb2312", "latin-1"]:
        try:
            with open(xml_path, "r", encoding=enc, errors="ignore") as f:
                data = f.read()
            break
        except Exception:
            continue
    if data is None:
        return None
    pattern_list = [
        re.compile(r'sample_rate\s*=\s*["\']?([0-9.eE+-]+)["\']?', re.IGNORECASE),
        re.compile(r'name\s*=\s*["\']sample_rate["\']\s+val\s*=\s*["\']([0-9.eE+-]+)["\']', re.IGNORECASE),
    ]
    for pat in pattern_list:
        res = pat.search(data)
        if res:
            try:
                val_str = res.group(1).strip()
                sr = float(val_str)
                if sr > 0:
                    return sr
            except ValueError:
                continue
    return None

def load_iq_file(filepath, max_samples=int(4*10**6)):
    raw = np.fromfile(filepath, dtype=np.int16, count=max_samples*2)
    # =========【修复IQ截断：I/Q成对不匹配】=========
    if len(raw) % 2 != 0:
        warn_msg = f"【文件警告】IQ文件截断，检测到奇数样本，自动丢弃末尾1个样本继续加载！\n原始长度：{len(raw)}，修复后：{len(raw)-1}"
        print(warn_msg)
        notify(warn_msg, "warn")
        raw = raw[:-1]
        if len(raw) < 2:
            raise ValueError("IQ文件有效数据过短，修复后不足一对I/Q样本")
    # =============================================
    I = raw[0::2].astype(np.float64)
    Q = raw[1::2].astype(np.float64)
    I /= 32768.0
    Q /= 32768.0
    iq_complex = I + 1j * Q
    del raw, I, Q
    gc.collect()
    return iq_complex

def calc_fft_block(signal_block, N_fft):
    if len(signal_block) < N_fft:
        pad = np.zeros(N_fft - len(signal_block), dtype=np.complex128)
        signal_block = np.concatenate([signal_block, pad])
    win = np.hanning(N_fft).astype(np.float64)
    fft_out = np.fft.fft(signal_block * win, N_fft)
    mag = np.abs(fft_out)
    mag = np.clip(mag, 1e-12, None)
    amp_db = 20 * np.log10(mag)
    amp_db = np.nan_to_num(amp_db, nan=-200, posinf=-10, neginf=-200)
    freq_raw = np.fft.fftfreq(N_fft)
    freq_raw = np.fft.fftshift(freq_raw)
    amp_db = np.fft.fftshift(amp_db)
    del win, fft_out, mag
    return freq_raw, amp_db

def calc_single_band(freq_hz, amp_db):
    power = 10 ** (amp_db / 10.0)
    total_p = np.sum(power)
    target_99 = 0.99 * total_p
    n = len(freq_hz)
    peak_idx = int(np.argmax(amp_db))
    left = peak_idx
    right = peak_idx
    sum_p = power[peak_idx]
    while sum_p < target_99:
        dl = left - 1
        dr = right + 1
        pl = power[dl] if dl >= 0 else 0
        pr = power[dr] if dr < n else 0
        if dl < 0 and dr >= n:
            break
        if pl >= pr and dl >= 0:
            left -= 1
            sum_p += pl
        elif dr < n:
            right += 1
            sum_p += pr
        else:
            break
    bw99_hz = abs(freq_hz[right] - freq_hz[left])
    peak_db = np.max(amp_db)
    thresholds = {"3dB": peak_db - 3, "6dB": peak_db - 6, "9dB": peak_db - 9, "26dB": peak_db - 26}
    bw_dict = {"99%": bw99_hz}
    for name, thresh in thresholds.items():
        mask = amp_db >= thresh
        if not np.any(mask):
            bw_dict[name] = None
            continue
        idx = np.where(mask)[0]
        bw_dict[name] = abs(freq_hz[idx[-1]] - freq_hz[idx[0]])
    del power
    return bw_dict

def bandpass_filter_complex(signal, fs_hz, bw_hz, center_hz=0.0):
    """频域带通滤波（复信号）。bw_hz<=0 表示不滤波，直接返回。"""
    if bw_hz is None or bw_hz <= 0:
        return signal
    n = len(signal)
    if n < 4:
        return signal
    spec = np.fft.fftshift(np.fft.fft(signal))
    freqs = np.fft.fftshift(np.fft.fftfreq(n, 1.0 / fs_hz))
    mask = np.abs(freqs - center_hz) <= (bw_hz / 2.0)
    spec[~mask] = 0.0
    out = np.fft.ifft(np.fft.ifftshift(spec))
    return out

def am_demod(signal):
    """AM解调：包络（去直流）。"""
    env = np.abs(signal)
    env = env - np.mean(env)
    return env

def fm_demod(signal):
    """FM解调：相位差分得到瞬时频率（rad/sample）。"""
    phase = np.unwrap(np.angle(signal))
    return np.diff(phase)

AUDIO_OUT_RATE = 48000  # 声卡输出采样率

def _fir_lowpass_real(x, fs_hz, cutoff_hz, taps=129):
    """实信号 FIR 低通（sinc×hamming，线性相位）。用于解调音频链路，
    避免在长数组上做大 FFT（内存峰值不可控）。cutoff 超奈奎斯特则直通。"""
    n = len(x)
    if cutoff_hz is None or cutoff_hz <= 0 or cutoff_hz >= fs_hz / 2.0 or n < 16:
        return x
    m = np.arange(taps, dtype=np.float64) - (taps - 1) / 2.0
    h = np.sinc(2.0 * cutoff_hz / fs_hz * m) * np.hamming(taps)
    h /= h.sum()
    return np.convolve(x, h, mode="same")

def _fir_decimate_stage(x, fs_hz, D, cutoff_hz, taps=65):
    """抗混叠低通 + 整数抽取（单级，线性相位零群时延）。返回 (y, fs/D)。"""
    if D <= 1 or len(x) < 4 * D:
        return x, float(fs_hz)
    m = np.arange(taps, dtype=np.float64) - (taps - 1) / 2.0
    h = np.sinc(2.0 * cutoff_hz / fs_hz * m) * np.hamming(taps)
    h /= h.sum()
    y = np.convolve(x, h, mode="same")     # 复数输入直接支持
    return y[::D], fs_hz / float(D)

def _decimate_for_audio(x, fs_hz, bw_hz):
    """把采样率降到约 2.2·bw 以内（多级抽取，每级 ≤8）。

    替代整段 FFT 带通滤波：4M 样本全长度 FFT 峰值内存 ~400MB，内存紧张时
    直接 MemoryError；多级 FIR 抽取后所有后续数组都缩小一个数量级以上。
    信号内容带宽为 ±bw/2，抽取后奈奎斯特 ≥1.1·bw，不损失带内信息。
    """
    cur = np.asarray(x)
    cur_fs = float(fs_hz)
    if not (bw_hz > 0):
        return cur, cur_fs
    for _ in range(4):
        if cur_fs <= 2.2 * bw_hz:
            break
        D = int(cur_fs // (2.2 * bw_hz))
        if D < 2:
            break
        D = min(D, 8)
        # 截止取"新奈奎斯特的 0.9"与"1.1·bw/2 保带宽"的较小者，
        # 保证带内(≤bw/2)无损且对新奈奎斯特附近有足够抑制
        cutoff = min(0.45 * cur_fs / D, 0.55 * bw_hz)
        cur, cur_fs = _fir_decimate_stage(cur, cur_fs, D, cutoff)
    return cur, cur_fs

def demod_to_audio(iq, fs_hz, mode, offset_hz, bw_hz, audio_rate=AUDIO_OUT_RATE):
    """把IQ数据解调为可播放的音频（限内存实现）。

    参数:
        iq        : 复数IQ数组
        fs_hz     : IQ采样率(Hz)
        mode      : "FM" 或 "AM"
        offset_hz : 解调信道中心相对IQ零频的偏移(Hz，带符号)
        bw_hz     : 解调带宽(Hz)
    返回:
        (audio float32 单声道[-1,1]@audio_rate, info dict)
    """
    iq = np.asarray(iq, dtype=np.complex128)
    n = len(iq)
    if n < 8:
        return np.zeros(0, dtype=np.float32), {"dur_s": 0.0}
    t = np.arange(n, dtype=np.float64) / fs_hz
    if abs(offset_hz) > 0.0:
        iq = iq * np.exp(-2j * np.pi * offset_hz * t)
    del t
    bw_hz = float(max(200.0, min(bw_hz, fs_hz * 0.45)))
    # 多级抗混叠抽取代替整段 FFT 滤波（内存 O(N/8) 而非 O(4N)）
    iq, fs2 = _decimate_for_audio(iq, fs_hz, bw_hz)
    n2 = len(iq)
    if str(mode).upper() == "FM":
        ph = np.unwrap(np.angle(iq))
        f_inst = np.empty(n2, dtype=np.float64)
        f_inst[0] = 0.0
        f_inst[1:] = np.diff(ph) * fs2 / (2.0 * np.pi)   # 瞬时频率 Hz
        audio = f_inst / (bw_hz * 0.5)                     # 归一化: 最大频偏=带宽/2 -> ±1
        del ph, f_inst
    else:  # AM 包络检波
        env = np.abs(iq)
        audio = env - np.mean(env)                         # 去直流(载波分量)
        del env
    del iq
    # 统一自动增益：去直流后按 99.5% 分位归一化到 0.85 峰值。
    # （FM 电平正比于 频偏/带宽，若不定标，带宽调大时声音会小到听不见）
    audio = audio - np.mean(audio)
    ref = float(np.percentile(np.abs(audio), 99.5)) if n2 > 8 else 0.0
    if ref > 1e-9:
        audio = audio / ref * 0.85
    audio = np.clip(audio, -1.0, 1.0)
    # 音频抗混叠低通：限制到 min(15kHz, 带宽/2)。
    # 用 FIR 在抽取后的低速率上做（长数组大 FFT 内存峰值不可控，改用卷积）
    audio = _fir_lowpass_real(audio, fs2, min(15000.0, bw_hz / 2.0))
    n_out = max(1, int(round(n2 * audio_rate / fs2)))
    audio48 = np.interp(np.linspace(0.0, n2 - 1, n_out),
                        np.arange(n2, dtype=np.float64), audio)
    audio48 = audio48.astype(np.float32)
    # 首尾 10ms 淡入淡出，消除咔哒声
    fade = min(480, n_out // 4)
    if fade > 0:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        audio48[:fade] *= ramp
        audio48[-fade:] *= ramp[::-1]
    info = {"dur_s": n_out / float(audio_rate), "bw_hz": bw_hz,
            "offset_hz": offset_hz, "mode": str(mode).upper(),
            "peak_db": 20 * np.log10(max(float(np.max(np.abs(audio48))), 1e-6)),
            "rms_db": 20 * np.log10(max(float(np.sqrt(np.mean(audio48.astype(np.float64) ** 2))), 1e-6))}
    return audio48, info

def float32_to_wav_bytes(x, rate=AUDIO_OUT_RATE):
    """float32单声道 -> 内存中的16bit PCM WAV 字节串（winsound 用）。"""
    pcm = np.clip(np.asarray(x, dtype=np.float64) * 32767.0,
                  -32768.0, 32767.0).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(pcm)
    return buf.getvalue()

def _kmeans_levels_1d(x, max_levels=8, n_iter=10):
    """对归一化一维数据 x∈[0,1] 做 k-means，返回最优离散电平数 M。
    遍历所有候选 M，使用 elbow 方法选择最优簇数：
    1. 找到 ratio（簇内方差/总方差）低于阈值的最小 M
    2. 对后续更大的 M，检查改善是否显著（改善比 ≥ 3.0）
    3. 若改善不显著或前一个 ratio 已极小（< 0.001），停止增加 M
    """
    n = x.size
    total_var = float(np.var(x))
    if total_var < 1e-15:
        return 1
    best_m = 1
    best_ratio = 0.0
    for M in range(2, max_levels + 1):
        centers = np.linspace(0.0, 1.0, M)
        assign = np.zeros(n, dtype=np.int64)
        for _ in range(n_iter):
            d = np.abs(x[:, None] - centers[None, :])
            assign = np.argmin(d, axis=1)
            new_centers = centers.copy()
            for k in range(M):
                members = x[assign == k]
                if members.size > 0:
                    new_centers[k] = float(np.mean(members))
            if np.max(np.abs(new_centers - centers)) < 1e-5:
                centers = new_centers
                break
            centers = new_centers
        within = 0.0
        for k in range(M):
            members = x[assign == k]
            if members.size > 0:
                within += members.size * float(np.var(members))
        within /= n
        counts = np.bincount(assign, minlength=M)
        # 最小簇占比 5%，排除离群尖峰（如 PSK 相位跳变产生的频率尖峰）
        if float(np.min(counts)) / n < 0.05:
            continue
        ratio = within / total_var
        thresh = 0.5 / (M * M)
        # 额外检查：簇间分离度（相邻簇中心间距 / 簇内标准差）
        sorted_centers = np.sort(centers)
        min_gap = float(np.min(np.diff(sorted_centers))) if M >= 2 else 1.0
        within_std = float(np.sqrt(within)) if within > 0 else 0.0
        separation = min_gap / (within_std + 1e-12)
        sep_thresh = 1.5 if M <= 4 else 1.0
        if ratio < thresh and separation > sep_thresh:
            if best_m > 1:
                # Elbow 判定：检查从 best_m 到 M 的改善是否显著
                if best_ratio < 0.001:
                    # 前一个 M 的 ratio 已极小，无需增加
                    break
                improvement = best_ratio / (ratio + 1e-12)
                if improvement < 3.0:
                    # 改善不显著，保持当前 best_m
                    break
            best_m = M
            best_ratio = ratio
    return best_m

def _count_levels(values, max_levels=8):
    """估计离散电平数量（幅度/频率）。基于 k-means 簇内方差比判断。
    返回检测到的电平数（连续分布返回1）。
    """
    if values is None:
        return 1
    vals = np.asarray(values, dtype=np.float64).ravel()
    vals = vals[np.isfinite(vals)]
    if vals.size < 16:
        return 1
    # 用 3%/97% 分位数做稳健归一化，抑制离群尖峰（如 PSK 相位跳变产生的频率尖峰）
    lo = float(np.percentile(vals, 3))
    hi = float(np.percentile(vals, 97))
    span = hi - lo
    scale = abs(lo) + abs(hi) + 1.0
    # 跨度过小（相对幅度或绝对值）视为恒定，避免浮点噪声被放大成伪电平
    if span < 1e-6 * scale or span < 1e-9:
        return 1
    x = np.clip((vals - lo) / span, 0.0, 1.0)
    std = float(np.std(x))
    if std < 0.02:  # 近乎恒定
        return 1
    return _kmeans_levels_1d(x, max_levels=max_levels)

def _phase_levels_circular(phase_resid, max_levels=8):
    """在单位圆上对残余相位做 2D k-means，估计相位离散电平数（PSK 检测）。
    使用单位圆坐标 (cos,sin) 避免相位在 0/2π 处被错误拆分。
    """
    if phase_resid is None:
        return 1
    vals = np.asarray(phase_resid, dtype=np.float64).ravel()
    vals = vals[np.isfinite(vals)]
    if vals.size < 16:
        return 1
    cx = np.cos(vals)
    cy = np.sin(vals)
    pts = np.stack([cx, cy], axis=1)
    mean_vec = pts.mean(axis=0)
    R = float(np.linalg.norm(mean_vec))  # 合向量长度，越接近1越恒定
    if R > 0.90:
        return 1  # 相位近似恒定
    total_var = float(np.var(cx) + np.var(cy))
    if total_var < 1e-12:
        return 1
    n = pts.shape[0]
    for M in range(2, max_levels + 1):
        ang = 2.0 * np.pi * np.arange(M) / M
        centers = np.stack([np.cos(ang), np.sin(ang)], axis=1)
        assign = np.zeros(n, dtype=np.int64)
        for _ in range(10):
            d2 = ((pts[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
            assign = np.argmin(d2, axis=1)
            new_c = centers.copy()
            for k in range(M):
                members = pts[assign == k]
                if members.shape[0] > 0:
                    new_c[k] = members.mean(axis=0)
            if np.max(np.abs(new_c - centers)) < 1e-5:
                centers = new_c
                break
            centers = new_c
        within = 0.0
        for k in range(M):
            members = pts[assign == k]
            if members.shape[0] > 0:
                within += members.size * (float(np.var(members[:, 0])) + float(np.var(members[:, 1])))
        within /= n
        counts = np.bincount(assign, minlength=M)
        if float(np.min(counts)) / n < 0.05:
            continue
        ratio = within / total_var
        if ratio < 0.6 / (M * M):
            return M
    return 1

def _robust_std(arr):
    """抗离群点的标准差：截取 5%~95% 分位区间内的样本计算 std。
    用于抑制数字调制符号跳变产生的瞬时尖峰对 fm_dev/phase_std 的抬升。
    """
    a = np.asarray(arr, dtype=np.float64).ravel()
    a = a[np.isfinite(a)]
    if a.size < 4:
        return 0.0
    lo = float(np.percentile(a, 5))
    hi = float(np.percentile(a, 95))
    mask = (a >= lo) & (a <= hi)
    if int(mask.sum()) < 4:
        return float(np.std(a))
    return float(np.std(a[mask]))

def _smooth(arr, w=5):
    """一维移动平均平滑，w 为窗口半宽（总宽 2w+1）。
    用于抑制噪声对包络/瞬时频率统计量的影响，同时保留调制特征。
    """
    a = np.asarray(arr, dtype=np.float64).ravel()
    n = a.size
    if n < (2 * w + 1):
        return a.copy()
    kernel = np.ones(2 * w + 1) / (2 * w + 1)
    out = np.convolve(a, kernel, mode="same")
    # 边缘修正：用最近的有效值填充
    out[:w] = a[:w].mean()
    out[-w:] = a[-w:].mean()
    return out

def _count_clusters_2d(iq, max_clusters=32, n_sub=3000):
    """对复信号 iq 的 I-Q 散点做 2D k-means，估计星座点数。
    返回使簇内方差/总方差显著降低（低于 0.5/M²）的最小簇数 M；
    若无任何 M 满足条件则返回 1（连续分布）。
    用于 QAM 等 2D 星座调制的检测。
    """
    pts = np.stack([np.real(iq), np.imag(iq)], axis=1)
    n = pts.shape[0]
    if n < 64:
        return 1
    # 子采样加速
    if n > n_sub:
        step = max(1, n // n_sub)
        pts = pts[::step]
        n = pts.shape[0]
    # 归一化
    scale = float(np.max(np.abs(pts))) + 1e-12
    pts_n = pts / scale
    total_var = float(np.var(pts_n[:, 0]) + np.var(pts_n[:, 1]))
    if total_var < 1e-12:
        return 1
    best_m = 1
    ratios = {}  # 保存每个 M 的 ratio，用于 elbow 判定
    for M in [2, 4, 8, 16, 32, 64]:
        if M > max_clusters:
            break
        # 需要至少 3 个点每簇，否则 within-variance 恒为 0 导致过度聚类
        if n < 3 * M:
            break
        # 多次初始化取最优结果：随机 + 网格
        best_within_M = float('inf')
        best_assign_M = None
        for trial in range(3):
            if trial == 0 and M >= 16:
                # 网格初始化（适合 QAM 方阵星座）
                side = int(np.ceil(np.sqrt(M)))
                grid_1d = np.linspace(-1, 1, side)
                gi, gj = np.meshgrid(grid_1d, grid_1d)
                centers = np.stack([gi.ravel()[:M], gj.ravel()[:M]], axis=1).astype(np.float64)
            else:
                rng = np.random.RandomState(42 + trial * 100)
                idx_init = rng.choice(n, min(M, n), replace=False)
                centers = pts_n[idx_init].copy()
            assign = np.zeros(n, dtype=np.int64)
            for _ in range(20):
                d2 = ((pts_n[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
                assign = np.argmin(d2, axis=1)
                new_c = centers.copy()
                for k in range(M):
                    members = pts_n[assign == k]
                    if members.shape[0] > 0:
                        new_c[k] = members.mean(axis=0)
                if np.max(np.abs(new_c - centers)) < 1e-5:
                    break
                centers = new_c
            within = 0.0
            for k in range(M):
                members = pts_n[assign == k]
                if members.shape[0] > 0:
                    within += members.size * (float(np.var(members[:, 0])) + float(np.var(members[:, 1])))
            within /= n
            if within < best_within_M:
                best_within_M = within
                best_assign_M = assign
        within = best_within_M
        assign = best_assign_M
        counts = np.bincount(assign, minlength=M)
        # 高阶 QAM 每簇样本少，降低最小占比要求
        min_frac = 0.02 if M <= 16 else 0.005
        if float(np.min(counts)) / n < min_frac:
            ratios[M] = None  # 标记为不可用
            continue
        ratio = within / total_var
        ratios[M] = ratio
        # 离散星座点 ratio≈0（受噪声影响略大）；连续分布最优≈1/M²
        # 阈值取连续基线的50%，确保连续分布不会通过
        # 对小M(2,4,8)额外加0.01下限以容忍噪声
        # 对大M(16,32,64)加0.003下限，因为高阶星座点噪声更大
        baseline = 1.0 / (M * M)
        thresh = baseline * 0.5
        if M <= 8:
            thresh = max(thresh, 0.01)
        elif M <= 32:
            thresh = max(thresh, 0.003)
        else:
            thresh = max(thresh, 0.002)
        if ratio < thresh:
            # Elbow 判定：对于 M >= 32，检查从 M/2 到 M 的改善是否显著
            # 如果改善不显著（ratio 没有大幅下降），说明 M/2 已经是正确的簇数
            # 这防止噪声导致的过度分裂（如 16QAM 被误判为 32 或 64）
            # 仅对 M >= 32 应用，避免 16QAM 被停在 M=8（8个簇也能很好近似16QAM）
            if M >= 32 and M // 2 in ratios and ratios[M // 2] is not None:
                prev_ratio = ratios[M // 2]
                # 如果前一个 M 的 ratio 已经很小（< 0.001），说明已经找到正确的簇数
                # 此时不需要增加 M（避免 0/0 导致 improvement 虚高）
                if prev_ratio < 0.001:
                    continue
                improvement = prev_ratio / (ratio + 1e-12)
                if improvement < 3.0:
                    # 改善不显著，跳过此 M
                    continue
            best_m = M
    return best_m

def _linear_fit_r2(x, y):
    """线性回归 y=a*x+b，返回 (斜率, 截距, R²)。
    用于 LFM/chirp 检测：瞬时频率 vs 时间的线性拟合优度。
    """
    x = np.asarray(x, dtype=np.float64).ravel()
    y = np.asarray(y, dtype=np.float64).ravel()
    n = min(x.size, y.size)
    if n < 4:
        return 0.0, 0.0, 0.0
    x, y = x[:n], y[:n]
    xm, ym = float(np.mean(x)), float(np.mean(y))
    sxx = float(np.sum((x - xm) ** 2))
    sxy = float(np.sum((x - xm) * (y - ym)))
    syy = float(np.sum((y - ym) ** 2))
    if sxx < 1e-15:
        return 0.0, ym, 0.0
    slope = sxy / sxx
    intercept = ym - slope * xm
    if syy < 1e-15:
        r2 = 1.0
    else:
        r2 = (sxy ** 2) / (sxx * syy)
    r2 = max(0.0, min(1.0, r2))
    return slope, intercept, r2

def _detect_pulsed(env, fs_hz):
    """检测脉冲调制信号。
    返回 (is_pulsed, duty_cycle, burst_count)。
    脉冲信号的特征：包络呈 on/off 双峰分布，低电平近零，且脉冲数少（长脉冲）。
    与 OOK/ASK 的区别：脉冲信号的 on/off 周期长、脉冲数少，
    而 OOK 在符号率上频繁切换。
    """
    n = len(env)
    if n < 64:
        return False, 1.0, 0
    env_s = _smooth(env, w=10)
    env_max = float(np.max(env_s))
    if env_max < 1e-12:
        return False, 1.0, 0
    env_norm = env_s / env_max
    # 要求低电平真的接近零：5% 分位数 < 0.1
    env_p5 = float(np.percentile(env_norm, 5))
    env_p95 = float(np.percentile(env_norm, 95))
    if env_p5 > 0.1:
        # 低电平不够低，不是脉冲信号
        return False, 1.0, 0
    # 用 Otsu 思路：找最佳阈值将包络分为高/低两段
    hist, edges = np.histogram(env_norm, bins=64, range=(0, 1))
    hist = hist.astype(float) / n
    csum = np.cumsum(hist)
    cmean = np.cumsum(hist * (edges[:-1] + edges[1:]) / 2)
    total_mean = cmean[-1]
    best_var = -1.0
    best_t = 0.5
    for i in range(1, 63):
        w0 = csum[i]
        w1 = 1.0 - w0
        if w0 < 1e-6 or w1 < 1e-6:
            continue
        m0 = cmean[i] / w0
        m1 = (total_mean - cmean[i]) / w1
        var_between = w0 * w1 * (m0 - m1) ** 2
        if var_between > best_var:
            best_var = var_between
            best_t = (edges[i] + edges[i + 1]) / 2
    # 用最佳阈值二值化
    binary = (env_norm > best_t).astype(int)
    # 计算占空比
    duty = float(np.sum(binary)) / n
    # 计算脉冲个数（上升沿数）
    rising = np.diff(binary)
    burst_count = int(np.sum(rising == 1))
    # 计算平均脉冲长度（采样点数）
    if burst_count > 0:
        avg_burst_len = (n * duty) / burst_count
    else:
        avg_burst_len = n
    # 脉冲判定：占空比低 + 脉冲少(长脉冲) + 高低对比
    # avg_burst_len > 700 区分脉冲(1000+样本/脉冲)与 OOK(256样本/符号, 平均~512)
    # burst_count <= 8 进一步排除 OOK(OOK 通常有 8+ 个上升沿)
    # duty < 0.75 放宽以捕获脉冲 FSK 等较高占空比的脉冲信号
    on_env = env_norm[binary == 1]
    on_levels = 1
    if len(on_env) > 32:
        on_levels = _count_levels(on_env, max_levels=8)
    is_pulsed = (duty < 0.75) and (burst_count >= 1) and (avg_burst_len > 700) and \
                (env_p95 > 0.5) and (on_levels <= 2) and (burst_count <= 8)
    return is_pulsed, duty, burst_count

def _spectral_flatness(amp_db, n_fft):
    """计算谱平坦度（Spectral Flatness）。
    OFDM 信号的谱平坦度接近 1（各子载波功率均匀分布）；
    单载波信号的谱平坦度远低于 1（能量集中在少数频点）。
    使用 amp_db 的线性功率计算几何均值/算术均值比。
    """
    if len(amp_db) < 8:
        return 0.0
    # 取中心 80% 频段避免边缘效应
    n = len(amp_db)
    lo, hi = int(n * 0.1), int(n * 0.9)
    # amp_db 由 20*log10(|X|) 得到，它同时等于 10*log10(|X|^2)，即就是功率谱的 dB 值。
    # 因此还原为线性功率必须用 10**(amp_db/10)；用 /20 得到的是幅度(|X|)，
    # 会使平坦度被开方而系统性偏高（纯噪声 0.85 而非理论值 0.56），导致噪声被误判为 OFDM。
    power = 10.0 ** (amp_db[lo:hi] / 10.0)
    power = np.clip(power, 1e-15, None)
    geo_mean = np.exp(np.mean(np.log(power)))
    arith_mean = float(np.mean(power))
    if arith_mean < 1e-15:
        return 0.0
    sf = float(geo_mean / arith_mean)
    return max(0.0, min(1.0, sf))

def _detect_freq_hopping(inst_f, fs_hz, hop_window=1024):
    """检测跳频信号。
    将瞬时频率按时间分窗，统计各窗的主频率。
    若主频率在多个离散值间跳变，且各窗内频率稳定，判定为跳频。
    与 FSK 的区别：跳频的驻留时间长(1000+样本)、频率间隔大；
    FSK 的符号短(256样本)、频率间隔小。
    与 LFM 的区别：跳频的窗内频率恒定；LFM 的窗内频率线性变化。
    返回 (is_hopping, n_hops, hop_freqs)。
    """
    n = len(inst_f)
    if n < hop_window * 4:
        return False, 0, []
    inst_f_s = _smooth(inst_f, w=5)
    n_windows = n // hop_window
    freqs_per_window = []
    stds_per_window = []
    for i in range(n_windows):
        seg = inst_f_s[i * hop_window:(i + 1) * hop_window]
        freqs_per_window.append(float(np.median(seg)))
        stds_per_window.append(float(np.std(seg)))
    freqs_arr = np.array(freqs_per_window)
    stds_arr = np.array(stds_per_window)
    freq_range = float(np.max(freqs_arr) - np.min(freqs_arr))
    if freq_range < 0.02 * fs_hz:
        return False, 1, freqs_per_window
    # 窗内频率变化应小（排除 LFM）
    median_within_std = float(np.median(stds_arr))
    if median_within_std > 0.15 * freq_range:
        return False, 1, freqs_per_window
    # 用容差法统计离散频率值，并要求每个频率值至少出现 2 次
    tol = freq_range * 0.12  # 12% 容差（较大，合并相近的窗频率）
    sorted_freqs = np.sort(freqs_arr)
    # 聚类：将相近的频率合并
    clusters = [[sorted_freqs[0]]]
    for f in sorted_freqs[1:]:
        if abs(f - np.mean(clusters[-1])) < tol:
            clusters[-1].append(f)
        else:
            clusters.append([f])
    # 只保留出现 >= 1 次的频率值（大窗口下每个跳频驻留期约1窗）
    valid_clusters = [c for c in clusters if len(c) >= 1]
    n_levels = len(valid_clusters)
    # 跳频条件：
    # 1) 有>=3个离散频率值
    # 2) 频率跨度大 (>5% fs，较大跨度区分于 FSK)
    # 3) 窗内频率稳定 (排除 LFM 和 FSK)
    #    FSK 符号短(256样本)，在1024样本窗内频率变化大→std高→被排除
    #    跳频驻留长(1024+样本)，窗内频率恒定→std低→通过
    is_hopping = (n_levels >= 3) and (n_levels <= 16) and \
                 (freq_range > 0.05 * fs_hz) and \
                 (median_within_std < 0.15 * freq_range)
    return is_hopping, n_levels, freqs_per_window

def _differential_phase_analysis(signal, fs_hz, sym_len=None):
    """差分相位分析，用于 DQPSK / π/4-DQPSK 检测。
    计算相邻符号间的相位差，统计离散电平数和相位偏移。
    返回 (n_diff_levels, has_pi4_offset, diff_phases)。
    """
    n = len(signal)
    if n < 256:
        return 1, False, np.array([])
    best_n_diff = 1
    best_has_pi4 = False
    best_diff = np.array([])
    candidate_symlens = []
    env = np.abs(signal)
    ac_estimated = None
    if n >= 512:
        env_ac = np.correlate(env - env.mean(), env - env.mean(), mode='full')
        env_ac = env_ac[n - 1:]
        env_ac_norm = env_ac / (env_ac[0] + 1e-15)
        for lag in range(16, min(n // 4, 1024)):
            if env_ac_norm[lag] > 0.3:
                ac_estimated = lag
                candidate_symlens.append(lag)
                break
    for s in [64, 128, 256, 512]:
        if s < n // 4 and s != ac_estimated:
            candidate_symlens.append(s)
    if not candidate_symlens:
        candidate_symlens = [128]
    for sym_len in sorted(candidate_symlens):
        sym_len = max(8, sym_len)
        n_syms = n // sym_len
        if n_syms < 8:
            continue
        phases = np.zeros(n_syms)
        for i in range(n_syms):
            seg = signal[i * sym_len:(i + 1) * sym_len]
            phases[i] = float(np.angle(np.mean(seg)))
        diff_phases = np.diff(phases)
        diff_phases = np.arctan2(np.sin(diff_phases), np.cos(diff_phases))
        if len(diff_phases) < 8:
            continue
        cx = np.cos(diff_phases)
        cy = np.sin(diff_phases)
        pts = np.stack([cx, cy], axis=1)
        mean_vec = pts.mean(axis=0)
        R = float(np.linalg.norm(mean_vec))
        total_var = float(np.var(cx) + np.var(cy))
        if total_var < 1e-12 or R > 0.95:
            continue
        n_diff = 1
        for M_val in [2, 4, 8]:
            ang = 2.0 * np.pi * np.arange(M_val) / M_val
            centers = np.stack([np.cos(ang), np.sin(ang)], axis=1)
            assign = np.zeros(len(pts), dtype=np.int64)
            for _ in range(10):
                d2 = ((pts[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
                assign = np.argmin(d2, axis=1)
                new_c = centers.copy()
                for k in range(M_val):
                    members = pts[assign == k]
                    if members.shape[0] > 0:
                        new_c[k] = members.mean(axis=0)
                if np.max(np.abs(new_c - centers)) < 1e-5:
                    centers = new_c
                    break
                centers = new_c
            within = 0.0
            for k in range(M_val):
                members = pts[assign == k]
                if members.shape[0] > 0:
                    within += members.shape[0] * (float(np.var(members[:, 0])) + float(np.var(members[:, 1])))
            within /= len(pts)
            counts = np.bincount(assign, minlength=M_val)
            if float(np.min(counts)) / len(pts) < 0.05:
                continue
            ratio = within / total_var
            if ratio < 0.6 / (M_val * M_val):
                n_diff = M_val
                break
        has_pi4_this = False
        if n_diff == 4 and len(diff_phases) >= 16:
            multiples = np.round(diff_phases / (np.pi / 4)).astype(int)
            odd_count = int(np.sum(multiples % 2 != 0))
            even_count = int(np.sum(multiples % 2 == 0))
            has_pi4_this = odd_count > even_count * 2
        is_ac_result = (sym_len == ac_estimated)
        if is_ac_result and n_diff > best_n_diff:
            best_n_diff = n_diff
            best_diff = diff_phases
            best_has_pi4 = has_pi4_this
        elif not is_ac_result and best_n_diff <= 1 and n_diff >= 2:
            best_n_diff = n_diff
            best_diff = diff_phases
            best_has_pi4 = has_pi4_this
    return best_n_diff, best_has_pi4, best_diff

def _estimate_bandwidth(amp_db, freq_hz, fs_hz):
    """估计信号 3dB 带宽（Hz）。"""
    if len(amp_db) < 4:
        return 0.0
    peak_idx = int(np.argmax(amp_db))
    peak_val = amp_db[peak_idx]
    threshold = peak_val - 3.0
    above = np.where(amp_db >= threshold)[0]
    if len(above) < 2:
        return 0.0
    f_lo = float(freq_hz[above[0]])
    f_hi = float(freq_hz[above[-1]])
    bw = abs(f_hi - f_lo)
    return bw

def _estimate_bandwidth_20db(amp_db, freq_hz, fs_hz):
    """估计信号 20dB 带宽（Hz），用于扩频检测。
    20dB 带宽比 3dB 带宽更宽，能更好地反映扩频信号的实际带宽。
    """
    if len(amp_db) < 4:
        return 0.0
    peak_idx = int(np.argmax(amp_db))
    peak_val = amp_db[peak_idx]
    threshold = peak_val - 20.0
    above = np.where(amp_db >= threshold)[0]
    if len(above) < 2:
        return 0.0
    f_lo = float(freq_hz[above[0]])
    f_hi = float(freq_hz[above[-1]])
    bw = abs(f_hi - f_lo)
    return bw

def _constellation_radius_cv(block_corr, seg_len):
    """计算星座点半径的变异系数(CV)，用于区分 PSK（圆上等幅）与 QAM（网格变幅）。
    PSK 星座点在同一个圆上 → 半径 CV ≈ 0（仅噪声）
    QAM 星座点在网格上 → 半径 CV > 0.1（多个半径环）
    返回 (cv, has_multiple_radii)。
    """
    iq = block_corr[:seg_len]
    radius = np.abs(iq)
    r_max = float(np.max(radius)) if len(radius) else 1.0
    if r_max < 1e-12:
        return 0.0, False
    r_nz = radius[radius > 0.15 * r_max]
    if len(r_nz) < 16:
        return 0.0, False
    r_mean = float(np.mean(r_nz))
    if r_mean < 1e-12:
        return 0.0, False
    cv = float(np.std(r_nz) / r_mean)
    hist, _ = np.histogram(r_nz, bins=20, range=(0, r_max))
    hist_s = np.convolve(hist, np.ones(3) / 3, mode='same')
    peaks = 0
    for i in range(1, len(hist_s) - 1):
        if hist_s[i] > 2 and hist_s[i] >= hist_s[i - 1] and hist_s[i] >= hist_s[i + 1]:
            peaks += 1
    has_multi = (peaks >= 2) or (cv > 0.15)
    return cv, has_multi

def _histogram_levels(vals, max_levels=16):
    """基于直方图峰检测估计离散电平数。
    对平滑后的直方图找局部极大值，比 k-means 对多电平 FSK 更鲁棒。
    返回检测到的电平数。
    """
    vals = np.asarray(vals, dtype=np.float64).ravel()
    vals = vals[np.isfinite(vals)]
    if vals.size < 16:
        return 1
    lo = float(np.percentile(vals, 1))
    hi = float(np.percentile(vals, 99))
    span = hi - lo
    scale = abs(lo) + abs(hi) + 1.0
    if span < 1e-6 * scale or span < 1e-9:
        return 1
    x = np.clip((vals - lo) / span, 0.0, 1.0)
    if float(np.std(x)) < 0.02:
        return 1
    n_bins = min(200, max(32, vals.size // 8))
    hist, edges = np.histogram(x, bins=n_bins, range=(0, 1))
    kernel = np.exp(-np.arange(-4, 5) ** 2 / 8.0)
    kernel /= kernel.sum()
    hist_s = np.convolve(hist, kernel, mode='same')
    peaks = []
    for i in range(1, len(hist_s) - 1):
        if hist_s[i] > vals.size / (n_bins * 4) and \
           hist_s[i] >= hist_s[i - 1] and hist_s[i] > hist_s[i + 1]:
            peaks.append(i)
    merged = []
    for p in peaks:
        if merged and p - merged[-1] < 3:
            if hist_s[p] > hist_s[merged[-1]]:
                merged[-1] = p
        else:
            merged.append(p)
    n_levels = len(merged)
    if n_levels < 1:
        return 1
    return min(n_levels, max_levels)

def _estimate_symbol_len(signal, fs_hz, max_len=1024):
    """估计符号长度（采样点数）。
    使用信号幅度自相关和平方信号自相关两种方法。
    对于 PSK 等恒幅信号，幅度自相关无周期性，需用平方信号（去除 M-PSK 调制）。
    对于 ASK/FSK 信号，幅度或频率的变化会在自相关中产生峰值。
    返回估计的符号长度（采样点数），无法估计时返回 0。
    """
    n = len(signal)
    if n < 128:
        return 0
    best_len = 0
    best_score = 0.0
    env = np.abs(signal)
    env_c = env - env.mean()
    if np.std(env_c) > 1e-10:
        ac_env = np.correlate(env_c, env_c, mode='full')
        ac_env = ac_env[n - 1:]
        ac_env_norm = ac_env / (ac_env[0] + 1e-15)
        for lag in range(8, min(n // 2, max_len)):
            if ac_env_norm[lag] > 0.3 and ac_env_norm[lag] > best_score:
                best_score = ac_env_norm[lag]
                best_len = lag
    sig_sq = signal ** 2
    env_sq = np.abs(sig_sq)
    env_sq_c = env_sq - env_sq.mean()
    if np.std(env_sq_c) > 1e-10:
        ac_sq = np.correlate(env_sq_c, env_sq_c, mode='full')
        ac_sq = ac_sq[n - 1:]
        ac_sq_norm = ac_sq / (ac_sq[0] + 1e-15)
        for lag in range(8, min(n // 2, max_len)):
            if ac_sq_norm[lag] > 0.3 and ac_sq_norm[lag] > best_score:
                best_score = ac_sq_norm[lag]
                best_len = lag
    phase = np.unwrap(np.angle(signal))
    inst_f = np.diff(phase) * fs_hz / (2.0 * np.pi)
    inst_f_s = _smooth(inst_f, w=5)
    if_s_c = inst_f_s - inst_f_s.mean()
    if np.std(if_s_c) > 1e-6:
        ac_if = np.correlate(if_s_c, if_s_c, mode='full')
        ac_if = ac_if[len(if_s_c) - 1:]
        ac_if_norm = ac_if / (ac_if[0] + 1e-15)
        for lag in range(8, min(len(inst_f_s) // 2, max_len)):
            if ac_if_norm[lag] > 0.3 and ac_if_norm[lag] > best_score:
                best_score = ac_if_norm[lag]
                best_len = lag
    return best_len

def _constellation_at_centers(block_corr, sym_len):
    """在符号中心提取星座点（对每个符号周期取平均）。
    这可以消除符号间过渡样本的影响，并减少载频估计误差导致的星座旋转
    （因为旋转仅在一个符号周期内累积，而非整个信号）。
    返回复数数组，每个元素代表一个符号的星座点。
    """
    n = len(block_corr)
    if sym_len is None or sym_len <= 0 or sym_len > n // 4:
        return block_corr[:min(n, 2048)]
    n_syms = n // sym_len
    if n_syms < 4:
        return block_corr[:min(n, 2048)]
    pts = np.zeros(n_syms, dtype=np.complex128)
    for i in range(n_syms):
        seg = block_corr[i * sym_len:(i + 1) * sym_len]
        pts[i] = np.mean(seg)
    return pts

def _absolute_phase_levels(block_corr, sym_len, max_levels=8):
    """在符号中心检测绝对相位离散电平数（PSK 检测）。
    对去载频信号按符号周期取平均，然后在单位圆上做 k-means 检测相位电平数。
    尝试多种符号长度（自相关估计 + 常见值），按从短到长顺序尝试，
    返回第一个检测到 ≥2 电平的结果。
    注意：不能取最大值！过长的符号长度会将多个符号平均，
    产生虚假相位电平（如 QPSK 用 2x 符号长度会得到 8 个电平）。
    返回检测到的相位电平数。
    """
    n = len(block_corr)
    candidates = []
    if sym_len and sym_len > 0 and sym_len < n // 4:
        candidates.append(sym_len)
    for s in [64, 128, 256, 512, 1024]:
        if s < n // 4 and s not in candidates:
            candidates.append(s)
    if sym_len and sym_len > 0 and sym_len < n // 4:
        others = sorted([s for s in candidates if s != sym_len])
        candidates = [sym_len] + others
    else:
        candidates = sorted(candidates)
    for sl in candidates:
        pts = _constellation_at_centers(block_corr, sl)
        if len(pts) < 8:
            continue
        phases = np.angle(pts)
        n_lvl = _phase_levels_circular(phases, max_levels=max_levels)
        if n_lvl >= 2:
            return n_lvl
    return 1

def _best_constellation_points(block_corr, sym_len):
    """尝试多种符号长度，返回星座点最清晰的结果（用于 QAM 检测）。
    按从短到长顺序尝试，返回第一个得到 ≥2 个 2D 聚类的结果。
    避免过长符号长度将多个符号平均产生虚假星座点。
    """
    n = len(block_corr)
    candidates = []
    if sym_len and sym_len > 0 and sym_len < n // 4:
        candidates.append(sym_len)
    for s in [64, 128, 256, 512, 1024]:
        if s < n // 4 and s not in candidates:
            candidates.append(s)
    if sym_len and sym_len > 0 and sym_len < n // 4:
        others = sorted([s for s in candidates if s != sym_len])
        candidates = [sym_len] + others
    else:
        candidates = sorted(candidates)
    best_pts = block_corr[:min(n, 2048)]
    best_n = 1
    for sl in candidates:
        pts = _constellation_at_centers(block_corr, sl)
        if len(pts) < 8:
            continue
        n_2d = _count_clusters_2d(pts, max_clusters=64) if len(pts) > 16 else 1
        if n_2d > best_n:
            best_n = n_2d
            best_pts = pts
        if n_2d >= 4:
            return best_pts
    return best_pts

def estimate_modulation(signal, fs_hz, freq_hz, amp_db):
    """估计调制特征与调制方式，返回字典。
    综合幅度（包络）、瞬时频率、瞬时相位的统计量与离散电平数，
    对 AM/FM/CW/ASK/FSK/PSK/QAM 等常见调制方式进行判断与置信度评估。
    """
    info = {}
    n = len(signal)
    idx = int(np.argmax(amp_db))
    peak_freq = float(freq_hz[idx])
    info["peak_freq_hz"] = peak_freq
    env = np.abs(signal)
    env_smooth = _smooth(env, w=5)
    env_mean = float(np.mean(env)) if n else 0.0
    if env_mean > 1e-12:
        env_p95 = float(np.percentile(env_smooth, 95))
        env_p5 = float(np.percentile(env_smooth, 5))
        ac = (env_p95 - env_p5) / 2.0
        depth = ac / env_mean
        env_std_norm = float(np.std(env_smooth) / env_mean)
        noise_env = float(np.std(env - env_smooth) / env_mean)
    else:
        depth = 0.0
        env_std_norm = 0.0
        noise_env = 0.0
    info["am_depth"] = max(0.0, min(depth, 2.0))
    info["env_std_norm"] = env_std_norm
    info["noise_env"] = noise_env
    phase = np.unwrap(np.angle(signal))
    inst_f = np.diff(phase) * fs_hz / (2.0 * np.pi)
    inst_f_smooth = _smooth(inst_f, w=5)
    info["fm_dev_hz"] = _robust_std(inst_f_smooth) if len(inst_f_smooth) else 0.0
    info["fm_mean_hz"] = float(np.mean(inst_f)) if len(inst_f) else peak_freq
    noise_f = float(np.std(inst_f - inst_f_smooth)) if len(inst_f) else 0.0
    info["noise_f"] = noise_f
    t_axis = np.arange(n, dtype=np.float64)
    fc_est = float(np.median(inst_f)) if len(inst_f) else peak_freq
    if abs(fc_est) < 0.01 * fs_hz and peak_freq > 0.01 * fs_hz:
        fc_est = peak_freq
    phase_resid = phase - 2.0 * np.pi * fc_est * t_axis / fs_hz
    phase_std_init = _robust_std(phase_resid) if n else 0.0
    if phase_std_init > 1.0 and n >= 256:
        N_fc = min(n, 8192)
        N_zp = N_fc * 4
        for M_pow in [2, 4, 8]:
            try:
                sig_m = signal[:N_fc] ** M_pow
                win = np.hanning(N_fc)
                fft_m = np.fft.fftshift(np.fft.fft(sig_m * win, N_zp))
                mag_m = np.abs(fft_m)
                freq_m = np.fft.fftshift(np.fft.fftfreq(N_zp))
                pk_m = int(np.argmax(mag_m))
                if 0 < pk_m < len(mag_m) - 1:
                    a_, b_, g_ = mag_m[pk_m - 1], mag_m[pk_m], mag_m[pk_m + 1]
                    denom = a_ - 2.0 * b_ + g_
                    p_off = 0.5 * (a_ - g_) / denom if abs(denom) > 1e-12 else 0.0
                else:
                    p_off = 0.0
                fc_m = (freq_m[pk_m] + p_off / N_zp) * fs_hz / M_pow
                pr_m = phase - 2.0 * np.pi * fc_m * t_axis / fs_hz
                std_m = _robust_std(pr_m)
                if std_m < phase_std_init:
                    fc_est = fc_m
                    phase_resid = pr_m
                    phase_std_init = std_m
            except Exception:
                continue
    _lfm_r2_pre = 0.0
    if len(inst_f_smooth) >= 32:
        t_if_pre = np.arange(len(inst_f_smooth), dtype=np.float64) / fs_hz
        _, _, _lfm_r2_pre = _linear_fit_r2(t_if_pre, inst_f_smooth)
    _fm_dev_pre = _robust_std(inst_f_smooth) if len(inst_f_smooth) else 0.0
    if phase_std_init > 0.3 and n >= 512 and _lfm_r2_pre < 0.5 and \
       _fm_dev_pre < 0.01 * fs_hz:
        step_sub = max(1, n // 4096)
        phase_sub = phase[::step_sub]
        t_sub = t_axis[::step_sub]
        best_R_global = 0.0
        best_fc_R = fc_est
        best_M_R = 0
        for M_test in [2, 4, 8]:
            best_R_M = 0.0
            best_fc_M = fc_est
            for df in range(-800, 801, 40):
                fc_try = fc_est + df
                pr_try = phase_sub - 2.0 * np.pi * fc_try * t_sub / fs_hz
                R = float(abs(np.mean(np.exp(1j * M_test * pr_try))))
                if R > best_R_M:
                    best_R_M = R
                    best_fc_M = fc_try
            for df in range(-40, 41, 2):
                fc_try = best_fc_M + df
                pr_try = phase_sub - 2.0 * np.pi * fc_try * t_sub / fs_hz
                R = float(abs(np.mean(np.exp(1j * M_test * pr_try))))
                if R > best_R_M:
                    best_R_M = R
                    best_fc_M = fc_try
            if best_R_M > 0.5 and best_M_R == 0:
                best_M_R = M_test
                best_R_global = best_R_M
                best_fc_R = best_fc_M
                break
        if best_R_global > 0.5:
            fc_est = best_fc_R
            phase_resid = phase - 2.0 * np.pi * fc_est * t_axis / fs_hz
            phase_std_init = _robust_std(phase_resid)
            info["psk_order_hint"] = best_M_R
            info["psk_R"] = best_R_global
        else:
            best_fc = fc_est
            best_std = phase_std_init
            for df in range(-500, 501, 50):
                fc_try = fc_est + df
                pr_try = phase - 2.0 * np.pi * fc_try * t_axis / fs_hz
                std_try = _robust_std(pr_try)
                if std_try < best_std:
                    best_fc = fc_try
                    best_std = std_try
            for df in range(-50, 51, 5):
                fc_try = best_fc + df
                pr_try = phase - 2.0 * np.pi * fc_try * t_axis / fs_hz
                std_try = _robust_std(pr_try)
                if std_try < best_std:
                    best_fc = fc_try
                    best_std = std_try
            if best_std < phase_std_init:
                fc_est = best_fc
                phase_resid = phase - 2.0 * np.pi * fc_est * t_axis / fs_hz
                phase_std_init = best_std
    info["phase_std"] = phase_std_init
    info["carrier_freq_hz"] = fc_est
    sym_len_est = _estimate_symbol_len(signal, fs_hz)
    info["sym_len_est"] = sym_len_est
    t_corr = t_axis / fs_hz
    block_corr = signal * np.exp(-1j * 2.0 * np.pi * fc_est * t_corr)
    const_pts = _best_constellation_points(block_corr, sym_len_est)
    info["n_sym_pts"] = len(const_pts)
    info["n_amp_levels"] = _count_levels(env_smooth)
    env_max = float(np.max(env)) if len(env) else 1.0
    amp_mask = env > 0.1 * env_max
    if int(amp_mask.sum()) > n // 4:
        inst_f_masked = inst_f_smooth[amp_mask[1:]] if len(amp_mask) > 1 else inst_f_smooth
    else:
        inst_f_masked = inst_f_smooth
    info["n_freq_levels"] = _count_levels(inst_f_masked) if len(inst_f_masked) > 16 else _count_levels(inst_f_smooth)
    info["n_freq_levels_hist"] = _histogram_levels(inst_f_masked, max_levels=16) if len(inst_f_masked) > 16 else _histogram_levels(inst_f_smooth, max_levels=16)
    info["fm_dev_masked_hz"] = _robust_std(inst_f_masked) if len(inst_f_masked) > 16 else info["fm_dev_hz"]
    n_phase_sym = _absolute_phase_levels(block_corr, sym_len_est, max_levels=8)
    n_phase_raw = _phase_levels_circular(phase_resid)
    _std_psk = {2, 4, 8}
    if n_phase_sym in _std_psk:
        info["n_phase_levels"] = n_phase_sym
    elif n_phase_raw in _std_psk and n_phase_sym < 2:
        info["n_phase_levels"] = n_phase_raw
    else:
        info["n_phase_levels"] = max(n_phase_sym, n_phase_raw)
    info["n_phase_levels_sym"] = n_phase_sym
    info["n_phase_levels_raw"] = n_phase_raw
    seg_len = min(n, 2048)
    I_sym = np.real(const_pts)
    Q_sym = np.imag(const_pts)
    I_corr = _smooth(np.real(block_corr[:seg_len]), w=5)
    Q_corr = _smooth(np.imag(block_corr[:seg_len]), w=5)
    n_i_sym = _count_levels(I_sym) if len(I_sym) > 16 else 1
    n_q_sym = _count_levels(Q_sym) if len(Q_sym) > 16 else 1
    n_i_raw = _count_levels(I_corr)
    n_q_raw = _count_levels(Q_corr)
    info["n_i_levels"] = max(n_i_sym, n_i_raw)
    info["n_q_levels"] = max(n_q_sym, n_q_raw)
    info["n_const_points"] = info["n_i_levels"] * info["n_q_levels"]
    radius_cv_sym, has_multi_sym = _constellation_radius_cv(const_pts, len(const_pts))
    radius_cv_raw, has_multi_raw = _constellation_radius_cv(block_corr, seg_len)
    info["radius_cv"] = max(radius_cv_sym, radius_cv_raw)
    info["has_multi_radius"] = has_multi_sym or has_multi_raw
    var_i = float(np.var(I_sym)) if len(I_sym) > 4 else 0.0
    var_q = float(np.var(Q_sym)) if len(Q_sym) > 4 else 0.0
    dim_ratio = 0.0
    if len(I_sym) > 8 and max(var_i, var_q) > 1e-12:
        pts_pca = np.stack([I_sym, Q_sym], axis=1)
        cov = np.cov(pts_pca.T)
        try:
            eigvals = np.linalg.eigvalsh(cov)
            eigvals = np.sort(eigvals)[::-1]
            if eigvals[0] > 1e-12:
                dim_ratio = eigvals[1] / eigvals[0]
        except Exception:
            dim_ratio = 0.0
        info["constellation_2d"] = dim_ratio > 0.1
    elif len(I_corr) > 16 and max(float(np.var(I_corr)), float(np.var(Q_corr))) > 1e-12:
        pts_pca = np.stack([I_corr, Q_corr], axis=1)
        cov = np.cov(pts_pca.T)
        try:
            eigvals = np.linalg.eigvalsh(cov)
            eigvals = np.sort(eigvals)[::-1]
            if eigvals[0] > 1e-12:
                dim_ratio = eigvals[1] / eigvals[0]
        except Exception:
            dim_ratio = 0.0
        info["constellation_2d"] = dim_ratio > 0.1
    else:
        info["constellation_2d"] = False
        dim_ratio = 0.0
    info["const_dim_ratio"] = dim_ratio
    if len(I_sym) > 8 and var_i > 1e-12 and var_q > 1e-12:
        iq_corr = float(np.corrcoef(I_sym, Q_sym)[0, 1])
    elif len(I_corr) > 16 and float(np.var(I_corr)) > 1e-12 and float(np.var(Q_corr)) > 1e-12:
        iq_corr = float(np.corrcoef(I_corr, Q_corr)[0, 1])
    else:
        iq_corr = 0.0
    info["iq_correlation"] = iq_corr
    n_const_2d_sym = _count_clusters_2d(const_pts, max_clusters=64) if len(const_pts) > 16 else 1
    n_const_2d_raw = _count_clusters_2d(block_corr[:seg_len], max_clusters=64)
    _std_qam = {16, 32, 64, 128, 256}
    if n_const_2d_raw >= 4:
        if n_const_2d_raw in _std_qam and n_const_2d_sym in _std_qam and n_const_2d_raw != n_const_2d_sym:
            info["n_const_2d"] = min(n_const_2d_raw, n_const_2d_sym)
        else:
            info["n_const_2d"] = n_const_2d_raw
    else:
        info["n_const_2d"] = max(n_const_2d_sym, n_const_2d_raw)
    info["n_const_2d_sym"] = n_const_2d_sym
    if len(inst_f_smooth) >= 32:
        t_if = np.arange(len(inst_f_smooth), dtype=np.float64) / fs_hz
        lfm_slope, lfm_intercept, lfm_r2 = _linear_fit_r2(t_if, inst_f_smooth)
        info["lfm_slope_hz_s"] = lfm_slope
        info["lfm_r2"] = lfm_r2
        info["lfm_bw_hz"] = abs(lfm_slope) * (len(inst_f_smooth) / fs_hz)
    else:
        info["lfm_slope_hz_s"] = 0.0
        info["lfm_r2"] = 0.0
        info["lfm_bw_hz"] = 0.0
    is_pulsed, duty_cycle, burst_count = _detect_pulsed(env, fs_hz)
    info["is_pulsed"] = is_pulsed
    info["duty_cycle"] = duty_cycle
    info["burst_count"] = burst_count
    info["spectral_flatness"] = _spectral_flatness(amp_db, len(amp_db))

    # ---------- 信号存在性判据（用于识别"空采/纯噪声"） ----------
    # rms_dbfs：相对满量程(±1.0)的电平，反映本次录制的整体幅度是否过低
    # crest_db：谱峰高出频谱中位数的 dB 数；纯噪声的随机起伏约 17~20dB，有实际信号时明显更高
    rms_val = float(np.sqrt(np.mean(env ** 2))) if n else 0.0
    info["rms_dbfs"] = 20.0 * np.log10(rms_val + 1e-15) if rms_val > 1e-15 else -200.0
    if len(amp_db) >= 8:
        info["crest_db"] = float(np.max(amp_db) - np.median(amp_db))
    else:
        info["crest_db"] = 0.0

    power = env ** 2
    p_mean = float(np.mean(power)) if len(power) else 1.0
    p_peak = float(np.percentile(power, 99)) if len(power) else 1.0
    info["papr_db"] = 10.0 * np.log10(p_peak / (p_mean + 1e-15) + 1e-15) if p_mean > 1e-15 else 0.0

    # ---------- 跳频检测 ----------
    # 跳频信号幅度恒定；对幅度调制信号（ASK/OOK/AM）跳过跳频检测，
    # 因为零幅度样本的相位噪声会产生虚假的频率跳变
    if env_std_norm < 0.1 and depth < 0.15:
        is_hopping, n_hops, hop_freqs = _detect_freq_hopping(inst_f, fs_hz)
    else:
        is_hopping, n_hops, hop_freqs = False, 0, []
    info["is_hopping"] = is_hopping
    info["n_hop_freqs"] = n_hops

    # ---------- 差分相位分析（DQPSK / π/4-DQPSK） ----------
    n_diff_phase, has_pi4_offset, _ = _differential_phase_analysis(signal, fs_hz)
    info["n_diff_phase_levels"] = n_diff_phase
    info["has_pi4_offset"] = has_pi4_offset

    # ---------- 带宽估计 ----------
    info["bandwidth_hz"] = _estimate_bandwidth(amp_db, freq_hz, fs_hz)
    info["bandwidth_20db_hz"] = _estimate_bandwidth_20db(amp_db, freq_hz, fs_hz)

    # ---------- 调制方式判断 ----------
    guess, confidence, reason = _guess_modulation(info, env, inst_f, phase_resid, signal, fs_hz)
    info["guess"] = guess
    info["confidence"] = confidence
    info["reason"] = reason
    return info

def _guess_modulation(info, env, inst_f, phase_resid, signal, fs_hz):
    """根据统计特征推断调制方式，返回 (名称, 置信度0~1, 推断理由)。

    支持的调制类型：
    - 模拟：AM, FM, CW, AM+FM
    - 数字 ASK：OOK, 2ASK, 4ASK, 8ASK
    - 数字 FSK：2FSK, 4FSK, 8FSK
    - 数字 PSK：BPSK, QPSK, 8PSK, 16PSK, DQPSK, π/4-DQPSK
    - 数字 QAM：16QAM, 32QAM, 64QAM, 128QAM, 256QAM
    - 特殊：LFM(线性调频), OFDM, 脉冲, 跳频, 扩频
    """
    depth = info["am_depth"]
    env_std = info["env_std_norm"]
    fm_dev = info["fm_dev_hz"]
    phase_std = info["phase_std"]
    n_amp = info["n_amp_levels"]
    n_freq = info["n_freq_levels"]
    n_freq_hist = info.get("n_freq_levels_hist", 1)
    n_phase = info["n_phase_levels"]
    n_phase_sym = info.get("n_phase_levels_sym", 1)
    n_i = info.get("n_i_levels", 1)
    n_q = info.get("n_q_levels", 1)
    n_const = n_i * n_q
    n_const_2d = info.get("n_const_2d", 1)
    n_const_2d_sym = info.get("n_const_2d_sym", 1)
    const_is_2d = info.get("constellation_2d", False)
    iq_corr = info.get("iq_correlation", 0.0)
    radius_cv = info.get("radius_cv", 0.0)
    has_multi_radius = info.get("has_multi_radius", False)
    noise_env = info.get("noise_env", 0.0)
    noise_f = info.get("noise_f", 0.0)
    lfm_r2 = info.get("lfm_r2", 0.0)
    lfm_slope = info.get("lfm_slope_hz_s", 0.0)
    lfm_bw = info.get("lfm_bw_hz", 0.0)
    is_pulsed = info.get("is_pulsed", False)
    duty_cycle = info.get("duty_cycle", 1.0)
    spec_flat = info.get("spectral_flatness", 0.0)
    papr_db = info.get("papr_db", 0.0)
    is_hopping = info.get("is_hopping", False)
    n_hops = info.get("n_hop_freqs", 0)
    n_diff_phase = info.get("n_diff_phase_levels", 1)
    has_pi4 = info.get("has_pi4_offset", False)
    bw_hz = info.get("bandwidth_hz", 0.0)
    bw_20db_hz = info.get("bandwidth_20db_hz", 0.0)
    carrier_freq = info.get("carrier_freq_hz", 0.0)

    # ---- 噪声自适应阈值 ----
    amp_const_thresh = max(0.04, noise_env * 1.5)
    freq_const_thresh = max(50.0, noise_f * 1.5, 0.0002 * fs_hz)
    amp_const = (depth < 0.08) and (env_std < amp_const_thresh)
    freq_const = (fm_dev < freq_const_thresh)
    phase_const = (phase_std < 0.15)

    amp_mod = (depth > 0.12) or (env_std > max(0.06, noise_env * 2.0))
    # FM 判据：噪声自适应阈值。阈值取噪声的0.5倍而非1.5倍，
    # 避免高噪声时 FM 被误判为 PM（FM的频率偏差通常远大于噪声）
    freq_mod = (fm_dev > max(100.0, noise_f * 0.5, 0.0005 * fs_hz))
    phase_mod = (phase_std > 0.25) and not freq_mod and (fm_dev < max(200.0, noise_f * 0.3))

    amp_discrete = (n_amp >= 2)
    freq_discrete = (n_freq >= 2)
    phase_discrete = (n_phase >= 2)

    # FSK 电平数：k-means 为主，直方图为辅
    # 仅当 k-means 也检测到离散电平时才使用直方图辅助（避免连续调制的直方图伪峰）
    # 当 k-means 返回 1 但直方图检测到 2~8 个峰且频率调制显著时，可能是多电平 FSK
    if n_freq >= 2:
        n_freq_best = max(n_freq, min(n_freq_hist, 8))
    elif 2 <= n_freq_hist <= 8 and freq_mod:
        n_freq_best = n_freq_hist
    else:
        n_freq_best = n_freq
    freq_discrete_best = (n_freq_best >= 2)

    env_max = float(np.max(env)) if len(env) else 1.0
    env_min = float(np.min(env)) if len(env) else 0.0
    has_near_zero = (env_max > 1e-12) and ((env_min / env_max) < 0.15)

    # PSK 判据：星座在圆上(半径CV小) 且 相位离散
    # QAM 判据：星座在2D网格上(半径CV大/多环) 且 I/Q离散
    # 注意：ASK 信号也有多半径(不同幅度)但星座是1D，不能仅凭 radius_cv 判定 QAM
    is_psk_constellation = (radius_cv < 0.15) and (not has_multi_radius)
    is_qam_constellation = const_is_2d and ((radius_cv > 0.15) or has_multi_radius)

    # OOK 判据：2电平 + 低电平近零 + 非2D-QAM星座 + 非脉冲信号
    # OOK 的星座在原点和圆上，radius_cv 可能小（去除近零点后全在同一半径）
    # 排除脉冲调制信号（脉冲FSK等有on/off包络但内部有频率调制）
    # 通过 is_pulsed 标志区分：OOK 符号短(avg_burst_len<700)，脉冲信号符号长(>700)
    is_ook = (n_amp == 2) and has_near_zero and \
             (not const_is_2d or radius_cv < 0.15 or n_const_2d_sym <= 2 or abs(iq_corr) > 0.5) and \
             not is_pulsed

    # ASK 判据（非OOK）：幅度离散 + 非QAM星座（1D或低相关）
    # 排除 QAM 星座（radius_cv 高/多环），避免 16QAM 误判为 ASK
    # 排除 2D 聚类找到 ≥16 个簇的情况（QAM 星座，即使 PCA 判为 1D）
    is_ask = amp_discrete and not is_ook and not is_qam_constellation and \
             (not const_is_2d or has_near_zero or abs(iq_corr) > 0.5 or n_const_2d_sym <= 2) and \
             n_const_2d < 16

    # 扩频检测辅助：频率噪声与频率偏差的比值
    # 扩频信号的频率噪声远大于频率偏差，k-means 可能误检为 FSK
    freq_noise_ratio = noise_f / (fm_dev + 1e-6) if fm_dev > 0 else 999.0
    freq_noise_too_high = (noise_f > max(3.0 * fm_dev, 0.02 * fs_hz))

    # PSK 辅助：R 最大化搜索结果
    psk_hint = info.get("psk_order_hint", 0)
    psk_R = info.get("psk_R", 0.0)

    # QAM 辅助：星座维度比
    dim_ratio = info.get("const_dim_ratio", 0.0)

    reasons = []

    # ================================================================
    # 优先级 0: 空采/纯噪声（电平极低 且 频谱无结构）
    # 必须放在 OFDM 之前：白噪声同时具备"谱平坦"与"高 PAPR"两个特征，
    # 若无此判据会被高置信度误判为 OFDM。
    # 用 crest_db 兜底保护：GPS 等直扩信号电平也可能不高，
    # 但其幅度接近满量程且恒定，crest 判据与电平判据需同时成立才判噪声。
    # ================================================================
    rms_dbfs = info.get("rms_dbfs", -200.0)
    crest_db = info.get("crest_db", 0.0)
    if rms_dbfs < -40.0 and crest_db < 26.0:
        name = "噪声/无信号"
        conf = 0.15
        reasons.append(
            f"电平仅 {rms_dbfs:.1f} dBFS、谱峰高出中位 {crest_db:.1f} dB，未见明显信号")

    # ================================================================
    # 优先级 1: OFDM（高谱平坦度 + 高PAPR）
    # ================================================================
    elif spec_flat > 0.3 and papr_db > 6.0:
        name = "OFDM"
        conf = min(0.85, 0.5 + spec_flat * 0.3 + (papr_db - 8.0) * 0.02)
        reasons.append(f"谱平坦度={spec_flat:.2f}(高)、PAPR={papr_db:.1f}dB(高)")

    # ================================================================
    # 优先级 2: LFM / Chirp（瞬时频率线性变化且拟合优度高）
    # 在跳频之前检查：LFM 分窗后可能被误判为跳频
    # ================================================================
    elif lfm_r2 > 0.75 and lfm_bw > 0.005 * fs_hz:
        direction = "升频" if lfm_slope > 0 else "降频"
        name = f"LFM({direction})"
        conf = min(0.85, 0.5 + lfm_r2 * 0.35)
        reasons.append(f"瞬时频率线性{direction}(R²={lfm_r2:.2f}、扫频带宽={lfm_bw/1e3:.1f}kHz)")

    # ================================================================
    # 优先级 3: 跳频（瞬时频率在多个离散值间跳变，频率跨度大）
    # ================================================================
    elif is_hopping and n_hops >= 3:
        name = "跳频(FH)"
        conf = min(0.8, 0.5 + n_hops * 0.05)
        reasons.append(f"瞬时频率在{n_hops}个离散值间跳变")

    # ================================================================
    # 优先级 4: 扩频（带宽宽 + 频率噪声高 + 有相位特征）
    # 扩频信号的频率噪声远大于频率偏差，k-means 可能误检为 FSK
    # 当频率噪声/频率偏差 > 3 时，频率"电平"不可靠，不应阻止扩频判定
    # 使用 20dB 带宽（比 3dB 更准确反映扩频带宽）+ 谱平坦度联合判据
    # ================================================================
    elif (bw_20db_hz > 0.05 * fs_hz and spec_flat > 0.3 and
          noise_f > 0.01 * fs_hz and
          n_diff_phase >= 2 and not amp_discrete and
          (not freq_discrete_best or freq_noise_too_high) and
          lfm_r2 < 0.5):
        name = "扩频(SS)"
        conf = 0.55
        reasons.append(f"带宽{bw_20db_hz/1e3:.0f}kHz(20dB)、谱平坦度{spec_flat:.2f}、频率噪声高(噪声/偏差={freq_noise_ratio:.1f})、差分相位{n_diff_phase}电平")

    # ================================================================
    # 优先级 4.5: π/4-DQPSK（差分相位4电平 + π/4偏移 + 绝对相位电平数≥6）
    # π/4-DQPSK 的绝对相位有8个电平(因π/4旋转)，会被误判为8PSK。
    # 若差分相位检测到4电平且有π/4偏移，优先判定为π/4-DQPSK。
    # 要求 n_phase >= 6 以排除 QPSK+噪声（噪声可能导致 n_phase=5 和虚假π/4偏移）
    # ================================================================
    elif n_diff_phase == 4 and has_pi4 and n_phase >= 6 and amp_const and \
         not freq_discrete_best and not freq_noise_too_high:
        name = "π/4-DQPSK"
        conf = 0.72
        reasons.append(f"差分相位4电平且偏移π/4(绝对相位{n_phase}电平因旋转)")

    # ================================================================
    # 优先级 5: PSK（绝对相位离散 + 幅度恒定 + 星座在圆上）
    # psk_order_hint 来自 R 最大化载频搜索，R>0.5 说明信号有 M-PSK 结构
    # 注意：psk_hint 取最小 M，但绝对相位检测应优先使用更准确的电平数
    # ================================================================
    elif phase_discrete and amp_const and not freq_discrete_best and \
         not freq_noise_too_high and \
         (is_psk_constellation or n_phase_sym >= 2):
        table = {2: "BPSK", 4: "QPSK", 8: "8PSK", 16: "16PSK"}
        name = table.get(n_phase, f"{n_phase}PSK")
        conf = 0.75 if n_phase in table else 0.6
        reasons.append(f"相位呈{n_phase}个离散电平且幅度恒定(半径CV={radius_cv:.3f})")

    # R 最大化搜索发现的 PSK（载频已修正但绝对相位检测可能仍不显著）
    elif psk_hint >= 2 and psk_R > 0.5 and amp_const and not freq_discrete_best and \
         not is_qam_constellation and lfm_r2 < 0.5:
        table = {2: "BPSK", 4: "QPSK", 8: "8PSK"}
        name = table.get(psk_hint, f"{psk_hint}PSK")
        conf = min(0.78, 0.5 + psk_R * 0.3)
        reasons.append(f"R最大化载频搜索：M={psk_hint}、R={psk_R:.2f}(载频{carrier_freq/1e3:.1f}kHz)")

    # ================================================================
    # 优先级 6: 差分 PSK（DQPSK / π/4-DQPSK / DBPSK）
    # 当载频估计偏差导致绝对相位不显著时，差分相位仍可检测 PSK
    # ================================================================
    elif n_diff_phase >= 2 and not phase_discrete and not freq_discrete_best and \
         (amp_const or (env_std < 0.1 and not amp_discrete)) and \
         (is_psk_constellation or n_diff_phase <= 4):
        if n_diff_phase == 2:
            name = "DBPSK"
            conf = 0.65
            reasons.append("差分相位2电平(绝对相位不显著)")
        elif n_diff_phase == 4:
            if has_pi4:
                name = "π/4-DQPSK"
                conf = 0.70
                reasons.append("差分相位4电平且偏移π/4")
            else:
                name = "DQPSK"
                conf = 0.68
                reasons.append("差分相位4电平")
        elif n_diff_phase == 8:
            name = "D8PSK"
            conf = 0.62
            reasons.append("差分相位8电平")
        else:
            name = f"D{n_diff_phase}PSK"
            conf = 0.55
            reasons.append(f"差分相位{n_diff_phase}电平")

    # ================================================================
    # 优先级 7: OOK（幅度2电平 + 低电平近零）
    # ================================================================
    elif is_ook and not is_pulsed:
        name = "OOK"
        conf = 0.75
        reasons.append("幅度2电平且低电平近零")

    # ================================================================
    # 优先级 8: ASK（幅度离散 + 非QAM星座）
    # ================================================================
    elif is_ask and not phase_discrete and not freq_discrete_best and not is_pulsed:
        table = {2: "2ASK", 4: "4ASK", 8: "8ASK"}
        name = table.get(n_amp, f"{n_amp}ASK")
        conf = {2: 0.68, 4: 0.65, 8: 0.62}.get(n_amp, 0.58)
        reasons.append(f"幅度{n_amp}电平、I/Q相关{abs(iq_corr):.2f}")

    # ================================================================
    # 优先级 9: QAM（I/Q 均有离散电平 + 星座在2D网格上(多半径)）
    # 条件1: I/Q各≥2电平 + QAM星座 + 幅度离散
    # 条件2: 2D聚类≥4点 + 幅度离散 + 非恒幅 + QAM星座
    # 条件3: 2D聚类(sym)≥4点 + 星座明显2D(dim_ratio>0.3) + 非PSK星座
    # 条件4: I/Q各≥2电平 + 星座2D(dim_ratio>0.2) + 多半径/高CV + 非PSK（不要求幅度离散）
    #        覆盖高阶QAM(64QAM等)幅度电平过多导致 k-means 无法分辨的情况
    # 条件5: 2D聚类≥16点 + 非PSK星座（载频偏移导致PCA失效时仍可检测QAM）
    # 条件6: 星座2D(dim_ratio>0.3) + 多半径 + 高CV + 幅度调制（噪声使聚类失败时的回退）
    # 所有条件都排除 FSK 信号（频率离散或频率调制显著）
    # ================================================================
    elif (not freq_discrete_best and not freq_mod and
          ((n_i >= 2 and n_q >= 2 and is_qam_constellation and amp_discrete) or
          (n_const_2d >= 4 and amp_discrete and not amp_const and is_qam_constellation) or
          (n_const_2d_sym >= 4 and dim_ratio > 0.3 and not is_psk_constellation) or
          (n_i >= 2 and n_q >= 2 and const_is_2d and dim_ratio > 0.2 and
           radius_cv > 0.12 and not is_psk_constellation) or
          (n_const_2d >= 16 and not is_psk_constellation and amp_discrete) or
          (const_is_2d and dim_ratio > 0.3 and radius_cv > 0.2 and
           has_multi_radius and not is_psk_constellation and amp_mod and n_amp >= 3))):
        if n_const_2d >= 4:
            m = n_const_2d
            for std_m in [16, 32, 64, 128, 256]:
                if abs(n_const_2d - std_m) <= 2:
                    m = std_m
                    break
            reasons.append(f"2D星座聚类{n_const_2d}个点→{m}-QAM(半径CV={radius_cv:.3f})")
        elif n_const_2d_sym >= 4:
            m = n_const_2d_sym
            for std_m in [16, 32, 64, 128, 256]:
                if abs(n_const_2d_sym - std_m) <= 2:
                    m = std_m
                    break
            reasons.append(f"2D星座聚类(sym){n_const_2d_sym}个点→{m}-QAM(半径CV={radius_cv:.3f})")
        else:
            # 用 I/Q 电平数估计 QAM 阶数
            # 分别对 I 和 Q 电平数做最近标准值吸附
            std_levels = {2: 2, 3: 4, 4: 4, 5: 4, 6: 8, 7: 8, 8: 8}
            n_i_snap = std_levels.get(n_i, n_i)
            n_q_snap = std_levels.get(n_q, n_q)
            if n_i >= 2 and n_q >= 2:
                m = n_i_snap * n_q_snap
            elif n_i >= 4:
                # Q 电平检测失败（噪声），假设方形 QAM
                m = n_i_snap * n_i_snap
            elif n_q >= 4:
                m = n_q_snap * n_q_snap
            else:
                m = 16  # 默认 16-QAM
            # 吸附到最近的 QAM 标准阶数
            for std_m in [16, 32, 64, 128, 256]:
                if abs(m - std_m) <= 4:
                    m = std_m
                    break
            reasons.append(f"I/Q电平{n_i}×{n_q}(吸附{n_i_snap}×{n_q_snap}={m})→{m}-QAM(半径CV={radius_cv:.3f})")
        name = f"{m}-QAM"
        # 条件6（PCA+radius_cv回退）置信度较低
        if n_const_2d < 4 and n_const_2d_sym < 4:
            conf = 0.55
        else:
            conf = 0.78 if n_i >= 2 and n_q >= 2 else 0.7

    # ================================================================
    # 优先级 10: FSK（2FSK / 4FSK / 8FSK）
    # 排除频率噪声过高的情况（扩频信号可能被误检为 FSK）
    # ================================================================
    elif freq_discrete_best and amp_const and not freq_noise_too_high:
        table = {2: "2FSK", 4: "4FSK", 8: "8FSK"}
        name = table.get(n_freq_best, f"{n_freq_best}FSK")
        conf = {2: 0.72, 4: 0.70, 8: 0.68}.get(n_freq_best, 0.65)
        reasons.append(f"瞬时频率呈{n_freq_best}个离散电平且幅度恒定(k-means={n_freq}/直方图={n_freq_hist})")

    # ================================================================
    # 优先级 11: 脉冲调制（包络 on/off 且脉冲数少）
    # 使用 fm_dev_masked（仅 on 期间的频率偏差）判断内部调制
    # ================================================================
    elif is_pulsed and duty_cycle < 0.75:
        fm_dev_on = info.get("fm_dev_masked_hz", fm_dev)
        freq_mod_on = (fm_dev_on > max(100.0, noise_f * 0.5, 0.0005 * fs_hz))
        freq_const_on = (fm_dev_on < max(50.0, noise_f * 1.5, 0.0002 * fs_hz))
        inner_mod = "未知"
        # 脉冲内调制判断：on/off 模式的幅度离散(n_amp==2 + has_near_zero)不是 ASK
        is_onoff_only = (n_amp == 2 and has_near_zero)
        # on/off 脉冲信号：用 on 期间频率特征判断内部调制
        # phase_const 包含 off 期间噪声，不可靠；改用 freq_const_on + phase_discrete
        if is_onoff_only and freq_const_on and not phase_discrete:
            inner_mod = "脉冲CW"
        elif is_onoff_only and phase_discrete and not freq_mod_on:
            inner_mod = "脉冲PSK"
        elif is_onoff_only and freq_discrete_best and not freq_const_on:
            inner_mod = "脉冲FSK"
        elif is_onoff_only and freq_mod_on and not freq_discrete_best:
            inner_mod = "脉冲FM"
        elif amp_discrete and not is_onoff_only and not phase_discrete and not freq_discrete_best:
            inner_mod = "脉冲ASK"
        elif phase_discrete:
            inner_mod = "脉冲PSK"
        elif freq_discrete_best and not freq_const_on:
            inner_mod = "脉冲FSK"
        elif freq_mod_on:
            inner_mod = "脉冲FM"
        elif amp_mod and freq_const_on:
            inner_mod = "脉冲AM"
        elif freq_const_on and phase_const:
            inner_mod = "脉冲CW"
        if inner_mod == "未知" and freq_mod_on:
            inner_mod = "脉冲FM"
        if inner_mod == "未知" and freq_const_on:
            inner_mod = "脉冲CW"
        name = f"脉冲调制({inner_mod})"
        conf = 0.7
        reasons.append(f"包络on/off占空比{duty_cycle*100:.0f}%、脉冲数{info.get('burst_count',0)}、on期间频率偏差{fm_dev_on/1e3:.1f}kHz")

    # ================================================================
    # 优先级 12: 模拟调制
    # ================================================================
    elif amp_mod and not amp_discrete and not freq_discrete_best and not phase_discrete and not freq_mod:
        name = "AM"
        conf = 0.7
        reasons.append(f"包络连续调制(深度{depth*100:.0f}%)且频率稳定")

    elif freq_mod and not amp_mod and not freq_discrete_best:
        name = "FM"
        conf = 0.7
        reasons.append(f"瞬时频率连续调制(σ={fm_dev/1e3:.2f}kHz)且幅度恒定")

    elif not amp_mod and not freq_mod and not phase_mod and not amp_discrete and not freq_discrete_best and not phase_discrete:
        name = "CW"
        conf = 0.8
        reasons.append("幅度/频率/相位均近似恒定")

    elif amp_mod and freq_mod and not amp_discrete and not freq_discrete_best and not phase_discrete:
        name = "AM+FM"
        conf = 0.6
        reasons.append("幅度与频率均连续调制")

    elif phase_mod and amp_const and not phase_discrete and not freq_mod:
        name = "PM"
        conf = 0.45
        reasons.append("相位连续调制且幅度恒定")

    # ================================================================
    # 兜底
    # ================================================================
    else:
        if amp_mod:
            name = "AM(疑似)"
            conf = 0.4
            reasons.append("幅度有明显变化")
        elif freq_mod:
            name = "FM/FSK(疑似)"
            conf = 0.4
            reasons.append("频率有明显变化")
        elif phase_mod:
            name = "PSK/PM(疑似)"
            conf = 0.35
            reasons.append("相位有明显变化")
        else:
            name = "CW/未知"
            conf = 0.3
            reasons.append("特征不显著")

    reason_str = "；".join(reasons)
    return name, conf, reason_str

_IQ_EXT_MAP = {
    ".cs16": "cs16", ".c16": "cs16", ".sc16": "cs16", ".iq": "cs16", ".bin": "cs16",
    ".cf32": "cf32", ".fc32": "cf32", ".cfile": "cf32", ".complex": "cf32",
    ".cu8": "cu8", ".u8": "cu8", ".cs8": "cs8", ".s8": "cs8", ".wav": "wav",
}

def urh_detect_fmt(filepath):
    """按扩展名猜IQ文件格式（URH 也支持导入时手工指定格式）。"""
    return _IQ_EXT_MAP.get(os.path.splitext(str(filepath))[1].lower(), "cs16")

def load_iq_any(filepath, fmt="auto", max_samples=4000000):
    """多格式IQ载入：cs16 / cf32 / cu8 / cs8 / wav。返回 complex128 数组。"""
    if not filepath or not os.path.exists(filepath):
        raise FileNotFoundError("文件不存在：%s" % filepath)
    if fmt in (None, "", "auto"):
        fmt = urh_detect_fmt(filepath)
    fmt = str(fmt).lower()
    max_samples = int(max_samples)
    if fmt == "wav":
        with wave.open(filepath, "rb") as w:
            nch = w.getnchannels()
            sw = w.getsampwidth()
            n = min(w.getnframes(), max_samples * max(nch, 1))
            raw = w.readframes(n)
        if sw == 2:
            a = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
        elif sw == 1:
            a = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 127.5) / 127.5
        else:
            raise ValueError("WAV 位宽 %d bit 暂不支持（仅支持 8/16 bit）" % (sw * 8))
        if nch >= 2:
            return (a[0::nch] + 1j * a[1::nch]).astype(np.complex128)
        return (a + 1j * np.zeros_like(a)).astype(np.complex128)
    if fmt == "cf32":
        a = np.fromfile(filepath, dtype=np.float32, count=max_samples * 2)
        a = a[:len(a) // 2 * 2].astype(np.float64)
        return (a[0::2] + 1j * a[1::2]).astype(np.complex128)
    if fmt == "cu8":
        a = np.fromfile(filepath, dtype=np.uint8, count=max_samples * 2)
        a = a[:len(a) // 2 * 2].astype(np.float64)
        return ((a[0::2] - 127.5) + 1j * (a[1::2] - 127.5)).astype(np.complex128) / 127.5
    if fmt == "cs8":
        a = np.fromfile(filepath, dtype=np.int8, count=max_samples * 2)
        a = a[:len(a) // 2 * 2].astype(np.float64)
        return ((a[0::2] + 1j * a[1::2]) / 128.0).astype(np.complex128)
    # 默认 cs16（与原 load_iq_file 一致，含奇数样本修复）
    raw = np.fromfile(filepath, dtype=np.int16, count=max_samples * 2)
    if len(raw) % 2 != 0:
        raw = raw[:-1]
    if len(raw) < 2:
        raise ValueError("IQ数据不足一对样本")
    return ((raw[0::2].astype(np.float64) + 1j * raw[1::2].astype(np.float64))
            / 32768.0).astype(np.complex128)

def save_iq_cs16(path, iq, normalize=True):
    """保存为 cs16（int16 交错 I/Q），URH / 本程序发射页均可直接读取。"""
    iq = np.asarray(iq, dtype=np.complex128)
    scale = 1.0
    if normalize and iq.size:
        peak = float(np.max(np.abs(iq)))
        if peak > 1e-9:
            scale = 0.9 * 32767.0 / peak
    out = np.empty(iq.size * 2, dtype=np.int16)
    out[0::2] = np.clip(np.real(iq) * scale, -32768, 32767).astype(np.int16)
    out[1::2] = np.clip(np.imag(iq) * scale, -32768, 32767).astype(np.int16)
    out.tofile(path)
    return int(iq.size)

def save_iq_wav(path, iq, fs_hz):
    """保存为 16bit 立体声 WAV（左=I，右=Q），便于 URH / Audacity 导入。"""
    iq = np.asarray(iq, dtype=np.complex128)
    scale = 1.0
    if iq.size:
        peak = float(np.max(np.abs(iq)))
        if peak > 1e-9:
            scale = 0.9 * 32767.0 / peak
    inter = np.empty(iq.size * 2, dtype=np.int16)
    inter[0::2] = np.clip(np.real(iq) * scale, -32768, 32767).astype(np.int16)
    inter[1::2] = np.clip(np.imag(iq) * scale, -32768, 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(int(fs_hz))
        w.writeframes(inter.tobytes())
    return int(iq.size)

def parse_hex_str(s):
    """把 "A5 01 FF" / "a501ff" / "0xA5,0x01" 解析为 bytes。"""
    if s is None:
        return b""
    txt = str(s).strip()
    if not txt:
        return b""
    txt = txt.replace(",", " ").replace(";", " ").replace("0x", "").replace("0X", "")
    txt = txt.replace("\\x", " ").replace(":", " ").replace("-", " ")
    parts = [p for p in re.split(r"[\s]+", txt) if p]
    out = bytearray()
    for p in parts:
        if len(p) % 2:
            p = "0" + p
        try:
            out.extend(bytes.fromhex(p))
        except ValueError:
            raise ValueError("非法HEX片段：%s" % p)
    return bytes(out)

def make_sine_wave(sample_rate, freq_hz, amp, n_samples=8192):
    """生成可无缝循环的复正弦。

    关键：把频率微调到"整周期数 / 缓冲长度"，否则每次循环都会在
    拼接点产生相位跳变，在频谱上表现为一堆杂散。
    返回 (波形, 实际频率Hz)。
    """
    n = max(64, int(n_samples))
    sr = float(sample_rate)
    cycles = max(1, int(round(freq_hz * n / sr)))
    cycles = min(cycles, n // 2)          # 至少每周期 2 点
    f_actual = cycles * sr / n
    t = np.arange(n, dtype=np.float64) / sr
    wave = (float(amp) * np.exp(2j * np.pi * f_actual * t)).astype(np.complex64)
    return wave.reshape(1, -1), f_actual


__all__ = ["_absolute_phase_levels", "_best_constellation_points", "_constellation_at_centers", "_constellation_radius_cv", "_count_clusters_2d", "_count_levels", "_decimate_for_audio", "_detect_freq_hopping", "_detect_pulsed", "_differential_phase_analysis", "_estimate_bandwidth", "_estimate_bandwidth_20db", "_estimate_symbol_len", "_fir_decimate_stage", "_fir_lowpass_real", "_guess_modulation", "_histogram_levels", "_kmeans_levels_1d", "_linear_fit_r2", "_phase_levels_circular", "_robust_std", "_smooth", "_spectral_flatness", "am_demod", "bandpass_filter_complex", "calc_fft_block", "calc_single_band", "demod_to_audio", "estimate_modulation", "float32_to_wav_bytes", "fm_demod", "load_iq_any", "load_iq_file", "make_sine_wave", "parse_hex_str", "parse_xml_sample_rate", "save_iq_cs16", "save_iq_wav", "urh_detect_fmt"]
