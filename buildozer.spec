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
# 所以**推荐用 打包APK.sh**——它先把该进包的三个文件摆进干净的 _apk_stage/
# 再构建，效果最可靠。
source.exclude_patterns = _*,IQ*,iqplay0911.py,iqplay_android.py.bak,*.spec,备份*,*.bak,(2).py,2.py,4.py,188665,6a7dd05c*.py,__pycache__,build,dist,bin,.buildozer,*.log,*.csv

version = 3.2

# numpy 必须显式写；kivy 卡在 2.3.x（2.2 与新版 numpy 有 ABI 冲突）
requirements = python3,kivy==2.3.0,numpy,pyjnius,android

orientation = portrait
fullscreen = 0                     # 留状态栏，避免刘海遮住顶部工具条
android.presplash_color = #101216
android.archs = arm64-v8a, armeabi-v7a

# ---- Android SDK/NDK（首次构建要下载，版本可随本机 SDK 改）----
android.api = 34
android.minapi = 24                # 覆盖到 Android 7.0
android.ndk = 25b
android.accept_sdk_license = True

# 读 IQ 文件 / 导出 WAV·PNG 需要存储权限
android.permissions = READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,MANAGE_EXTERNAL_STORAGE

# 播放解调音频时不要让 CPU 休眠
android.wakelock = True

# 只在 logcat 里过滤自己的日志
android.logcat_filters = *:S python:D

[buildozer]
log_level = 2
warn_on_root = 0
