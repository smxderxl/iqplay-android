[app]

# ---------------------------------------------------------------------------
# IQ 信号综合分析仪 · 安卓版（Kivy）
#
# 打包（buildozer 只跑在 Linux / macOS / WSL / Docker，Windows 原生不行）：
#     pip install buildozer cython
#     bash 打包APK.sh                  # 推荐：先建干净暂存目录再构建
#     buildozer -v android debug        # 直接在干净目录里手跑，产物在 bin/*.apk
#     buildozer android logcat          # 看真机日志
# 首次构建会自动下载 Android SDK/NDK（约 3~5 GB，耗时较长）。
# ---------------------------------------------------------------------------

title = IQ信号综合分析仪
package.name = iqplay
package.domain = org.iqplay

source.dir = .
# 入口必须是 main.py（p4a 的约定）；main.py 只转发到 iqplay_android.py。
source.include_exts = py,png,jpg,jpeg,kv,atlas,ttf,ttc,otf,json
# 本目录很杂（几十个无关 .py / cs16 / 截图），下面这条尽量把噪声挡掉；
# 但 buildozer 只支持"扩展名白名单 + 排除 glob"，没有真正的白名单机制，
# 所以真正可靠的做法是**先建一个只有四个文件的干净目录再构建**——
# 本地用 `打包APK.sh`，云端由 GitHub Actions 的 "准备干净构建目录" 步骤完成。
#
# 注意：不要在这里排除 _*.py——_extract_dsp.py 只是抽取工具、进不进包无所谓，
# 但真正的源码一个都不能误伤。下面排的是数据文件与备份。
# 不要排除 *.png——APK 的 icon.png / presplash.png 就是 png。
source.exclude_patterns = iqplay0911.py,iqplay_android.py.bak,备份*,*.bak,(2).py,2.py,4.py,188665,6a7dd05c*.py,__pycache__,build,dist,bin,.buildozer,*.log,*.csv,*.cs16,*.c16,*.wav

version = 3.2

# p4a 官方推荐的组合。
#  - kivy 2.3.x 与 ndk 25b 是配套的（25b 之前/之后的 NDK 会有 sysroot 变更）
#  - 不要写裸的 `android`：它在 p4a 里不是独立 recipe 名，写了会构建失败
#  - 需要 pyjnius 用于 Toast / 权限申请，它会被 kivy 的 android 依赖自动带上，
#    这里显式列出只是为了固定版本解析
requirements = python3,kivy==2.3.0,numpy,pyjnius

orientation = portrait
# 留状态栏，避免刘海遮住顶部工具条
fullscreen = 0
android.presplash_color = #101216
android.archs = arm64-v8a, armeabi-v7a

# ---- Android SDK/NDK ----
android.api = 34
# 覆盖到 Android 7.0
android.minapi = 24
android.ndk = 25b
android.accept_sdk_license = True
# 允许 p4a 用预编译轮子，省掉 numpy 的长时间交叉编译
android.skip_update = False
p4a.branch = master

# 读 IQ 文件 / 导出 WAV·PNG 需要存储权限
android.permissions = READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,MANAGE_EXTERNAL_STORAGE

# 播放解调音频时不要让 CPU 休眠
android.wakelock = True

# 只在 logcat 里过滤自己的日志
android.logcat_filters = *:S python:D

[buildozer]
log_level = 2
warn_on_root = 0
