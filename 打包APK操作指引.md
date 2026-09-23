# 怎么把 APK 造出来（云端构建，本地零依赖）

> **为什么不能在这台电脑上直接打包**
> buildozer 只能在 Linux 上跑，而本机是 Windows、WSL 又被安全策略拦住
> （`wsl.exe` 在黑名单里），且本机只有 JDK 1.8、没有 Android SDK/NDK。
> 所以改用 GitHub 的云端 Ubuntu 机器构建，**本机不需要装任何东西**。

---

## 一、准备工作：建一个 GitHub 空仓库

1. 打开 https://github.com/new
2. Repository name 填 `iqplay-android`（名字随便）
3. **选 Private（私有）或 Public 都行**，云端构建对私有仓库同样免费
4. **不要**勾 "Add a README file" / ".gitignore" / "license"（保持空仓库）
5. 点 Create repository，然后在下一页记下仓库地址，形如：
   `https://github.com/你的用户名/iqplay-android.git`

## 二、把本机已提交好的代码推上去

在终端（Git Bash 或 PowerShell 都行）执行，**把地址换成你自己的**：

```bash
cd /e/b210chegnxu

git remote add origin https://github.com/你的用户名/iqplay-android.git
git push -u origin main
```

> 首次 push 会弹窗要求登录 GitHub。若提示需要密码，请用
> **Personal Access Token**（头像 → Settings → Developer settings →
> Personal access tokens → Tokens (classic) → 勾 `repo` 权限），
> 别用账号密码（GitHub 早就不支持了）。

本机已完成的准备工作（不用你管）：
- `git init` + 分多次 commit（源码 + 云端构建配置 + 两个准备脚本）
- `.gitignore`：**默认忽略一切，只放行打 APK 必需的文件**
  —— 所以那几百 MB 的 `.cs16` 和几十个无关 `.py` 不会被传上去
- `.gitattributes`：锁定 `*.sh` / `*.yml` / `*.py` 为 LF 换行
  （Windows 的 CRLF 到 Linux 上会让脚本报 `bad interpreter`）
- `prepare_p4a.sh` + `patch_p4a_pip.py`：构建前把 python-for-android 克隆到
  指定的 release tag，并给它打一个 pip 加固补丁（不这么做会在编译早期报
  `ImportError: cannot import name 'open_rich_spinner' / 'RequirementInformation'`）

## 三、等云端自动构建

push 完成后：

1. 打开仓库页面 → 顶部 **Actions** 标签
2. 会看到一条 **"Build Android APK"** 正在跑（黄点）
3. 点进去可以看实时日志。**首次约 20~40 分钟**（要装系统依赖 + 下载
   Android SDK/NDK + 完整编译 hostpython3 / python3 / numpy；
   之后有缓存会快很多）
4. 跑完变成绿勾 ✅

> 想手动重跑：Actions → 左侧 "Build Android APK" → 右侧 **Run workflow** 按钮。
> 改了 `iqplay_android.py` 等文件再 push，也会自动触发。

## 四、下载 APK

构建成功后，在 **那次运行的页面底部**，找到 **Artifacts** 区域：

- 名字形如 **`iqplay-apk-<提交短哈希>`**（带哈希是为了区分不同次构建），点它下载一个 zip
- 解压后里面有：
  - `iqplay-3.2-arm64-v8a_armeabi-v7a-debug.apk` ← **这就是安装包，装它**
  - `build.log`（构建日志，真机出问题时发我）

> **正常只会有一个 .apk。** 如果看到多个名字不同的 apk，它们是**同一次 gradle 构建
> 产出的同一份文件**（buildozer 的最终产物 + p4a/gradle 在中间目录留的副本），
> 二进制完全相同，此时**装文件名里带包名+版本+架构的那个**即可。
> （workflow 已改为只收集 `bin/` 下的最终产物，所以正常情况不会再出现多个。）

## 五、装到手机

1. 把 APK 传到手机（微信文件传输 / QQ / 数据线都行）
2. 手机上点安装，会提示"未知来源"——到设置里允许一次即可
3. 首次启动会申请**存储权限**，一定要给，否则选不了 IQ 文件
4. 把 IQ 文件（`.cs16` / 无扩展名的 signalwave 文件）和同名 `.xml`
   （存 `sample_rate` 的那个）放进手机存储，例如 `Download/` 或自建目录
5. 打开 App → 点左上角「文件」 → 浏览到那个目录 → 选中 IQ 文件

> **这是 debug 版 APK**，可以直接装、可以正常用，只是签名是调试密钥。
> 想长期自用没问题；要上架应用商店才需要正式签名的 release 版，到时告诉我。

---

## 六、出问题怎么办

| 现象 | 原因 / 处理 |
|---|---|
| Actions 里没有 workflow 跑 | 确认 `.github/workflows/build-apk.yml` 推上去了（`git ls-files .github` 能看到） |
| 构建失败在 "构建 APK" 这步 | 把**报错行往上 30~60 行的日志**发我。**不要急着改 `requirements`**：日志里那批 `No matching distribution found for numpy==2.2.6 / pyjnius==1.7.0 / kivy==2.3.0` 是 p4a 的**探针噪音**（它用 `--dry-run --only-binary=:all:` 试探有没有现成 wheel，异常被 `except` 吞掉后继续），不是失败原因 |
| 日志里看不到 `===== [prepare_p4a] tag = ... =====` | 跑的还是旧版代码（改动没推上去）。看到什么报错都不能说明新方案有问题，先把最新 commit 推上去 |
| 报 `Path for p4a.source_dir does not exist` | p4a 没准备好。**不要直接跑 `buildozer android debug`**，改用 `bash 打包APK.sh`（它会先跑 `prepare_p4a.sh`） |
| 装完打开闪退 | 抓真机日志：手机开 USB 调试，或把 APK 里那个 `build.log` 发我；大概率是缺少中文字体或权限 |
| 界面汉字是方框 | 手机没有 NotoSansCJK 字体。把任意中文字体改名 `font.ttf` 放到 `E:\b210chegnxu\`，重新 push 触发构建即可 |
| 想改功能后重新出包 | 改 `iqplay_android.py` → `git add -A && git commit -m "改了什么" && git push`，云端自动重打 |

---

## 七、算法改动怎么同步（重要）

`iqdsp.py` 是**自动生成**的，不要手改。桌面版 `iqplay0911.py` 才是算法唯一来源：

```bash
cd /e/b210chegnxu
python _extract_dsp.py          # 重新生成 iqdsp.py
python _smoke_dsp.py            # 22 项，确认与桌面版逐位一致
git add -A && git commit -m "同步算法层" && git push
```
