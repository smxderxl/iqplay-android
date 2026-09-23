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

# 必须把 python3 和 numpy 都锁死版本，否则会构建失败（已实测踩过）：
#  - python3 不锁 -> p4a(develop) 的 python3 recipe 默认 version = 3.14.2，
#    太新，与 NDK 25b 交叉编译 numpy 会炸（2026-09 云端构建实测）
#  - numpy 不锁 -> recipe 默认 version = v2.3.0，编译报
#    "no template named 'unordered_map' in namespace 'std'"（C++ 标准库版本不匹配）
#
# 版本写法有坑，两者格式不一样：
#  - python3 用 tarball：url = .../cpython/archive/refs/tags/v{version}.tar.gz
#    所以写 3.11.9（不带 v），拼出来才是 v3.11.9.tar.gz
#  - numpy 用 git：recipe.py 里是 sh.git('checkout', self.version)，
#    版本号被原样当 git ref，而 numpy 的 tag 是 v2.2.6（带 v），
#    所以必须写 v2.2.6；写成 2.2.6 会 checkout 失败
#    （recipe.py 的 get_pip_name() 会自动 lstrip('v')，转成 pip 的 numpy==2.2.6）
#
#  - hostpython3 也必须写成同一个版本！它的 downloads 里有硬性守卫：
#      recipe.py: if python_recipe.version != self.version: raise
#      "python3 should have same version as hostpython3, X != Y"
#    两边都默认是 3.14.2，只改 python3 会直接构建失败（p4a 官方 issue #2568 同款）
#  - 不要写裸的 `android`：它在 p4a 里不是独立 recipe 名，写了会构建失败
#  - pyjnius 用于 Toast / 权限申请，会被 kivy 的 android 依赖自动带上
requirements = python3==3.11.9,hostpython3==3.11.9,kivy==2.3.0,numpy==v2.2.6,pyjnius

# 必须写 all：程序顶部有「横屏/竖屏」按钮，运行时用
# Activity.setRequestedOrientation 切换（见 iqplay_android.set_screen_orientation）。
# 若这里锁成 portrait，manifest 会声明竖屏，和运行时的切换请求打架。
orientation = all
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
android.skip_update = False

# 不要用 stable 分支！它多年没更新了：numpy recipe 还停在 1.9.2（pypi.python.org
# 的老地址），连 python3 / hostpython3 recipe 都不存在，一用就崩。
#
# 锁到**具体 tag**而不用 master：master 的 recipe 默认值会随时间漂移
# （python3 曾默认 3.14.2、numpy 曾默认 v2.3.0），同一个 commit 今天能过、
# 明天可能就挂。v2026.05.09 是本项目验证过的写法组合：
#   python3  recipe: version 3.14.2, url = cpython github tarball v{version}
#   numpy    recipe: version v2.3.0,  url = git+https://github.com/numpy/numpy
# 两者与我们下面 requirements 的写法规则一致（python3 不带 v、numpy 带 v）。
# 说明：buildozer 是通过 p4a.branch 去 git clone p4a 的
#       （targets/android.py: p4a.url / p4a.branch），
#       所以 pip 上的 python-for-android 包根本不会被用到，别去 pip 装它。
#
# p4a.branch 在这里还有第二个用途：**我们自己**的 prepare_p4a.sh 会读这一行
# 决定克隆哪个 tag。设了下面的 p4a.source_dir 之后 buildozer 会忽略它
# （见 _install_p4a：有 source_dir 时只检查目录是否存在，不 clone 也不改）。
p4a.branch = v2026.05.09

# 指向我们自己准备的 p4a 检出（prepare_p4a.sh 克隆到该位置并打 pip 加固补丁）。
# 为什么必须这么做：buildozer 每次构建都会检查已存在的 p4a 目录，并且
#   - 用 tag 克隆时 HEAD 是 detached，它从 `git branch -vv` 解析出 "(HEAD"
#     与 p4a.branch 不等 -> 直接 rmdir 重克隆；
#   - 分支名恰好匹配时又会 `git clean -dxf` + `git pull`，p4a.commit 非 HEAD
#     还会 `git reset --hard`。
# 这几种都会抹掉我们打的补丁。设了 p4a.source_dir 之后 buildozer 完全不碰它。
# 路径相对于"运行 buildozer 时的当前目录"（我们固定先 cd 到 _apk_stage）。
# 放在 .buildozer/ 下还有个好处：buildozer 打包时会跳过任何以 "." 开头的路径
# （buildozer/__init__.py "avoid hidden directory"），所以不会进 APK。
p4a.source_dir = .buildozer/android/platform/python-for-android

# 读 IQ 文件 / 导出 WAV·PNG 需要存储权限
android.permissions = READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,MANAGE_EXTERNAL_STORAGE

# 播放解调音频时不要让 CPU 休眠
android.wakelock = True

# 只在 logcat 里过滤自己的日志
android.logcat_filters = *:S python:D

[buildozer]
log_level = 2
warn_on_root = 0
