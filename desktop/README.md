# FLA 桌面端放映助手 (Windows Client)

FLA 桌面助手专为学校多媒体讲台与课堂教学打造，彻底解决传统教学软件与希沃白板5的冲突问题。

## 核心特性
1. **轻量现代 Fluent 质感 UI**：
   - 采用系统原生 Edge WebView2 现代图形界面（无依赖时自动无缝降级为原生深色卡片 GUI），极简高效，无浮夸冗余。
2. **免登录下载与单 EXE 运行**：
   - 服务端提供公开直链 `/api/desktop/download`，访问官网即可直接免登录一键下载 `FLA.exe`；
   - 单文件独立运行，无需安装，无捆绑无全家桶。
3. **版本自动检测与原地静默更新**：
   - 客户端每次启动自动比对服务端 `/api/desktop/version`；
   - 发现新版本时在后台自动原地热替换更新，用户**永无需**再次从官网手动重新下载。
4. **希沃白板5 (EasiNote / PPTService) 智能拦截**：
   - 自动检测并压制希沃白板5在 PowerPoint 放映时霸屏注入的工具条；
   - 自动拦截希沃 COM 注入插件，还原原生纯净放映界面。
5. **FLA 专属悬浮教学工具栏**：
   - 包含：鼠标指针、激光笔、原笔迹画笔、几何图形、独立橡皮、幕布黑屏；
   - **一键手机投屏 / 遥控**：手机扫码即可实时遥控电脑 PPT 翻页与激光指示。
6. **右下角优雅 FLA 水印**：
   - 采用现代化微透明文字水印，彰显品质感。
7. **画布随 PPT 翻页严格同步**：
   - 通过 COM 接口监听 PowerPoint / WPS 的 `SlideShowNextSlide` 及实时页码；
   - 板书笔迹自动与当前幻灯片绑定存储，翻页时笔迹自动随幻灯片移动与归位。
8. **网页端直接调用本地 PowerPoint / WPS**：
   - 自动判断系统默认 Office 软件打开课件；
   - 本地提供 `http://127.0.0.1:8307/api/open` 桥接服务及 `fla://` 自定义协议支持。
9. **实验扩展中心**：
   - FLA 在后台下载并校验适配当前系统的离线模拟或桌面实验工具，安装到用户级 `FLA/Extensions` 目录后直接打开；
   - 支持在 FLA 内打开或卸载，PhET 单文件模拟下载后可离线使用，不再让用户前往项目官网自行找安装包。

## 运行与分发

### 方式 1：直接运行单文件客户端
在 Windows 上直接双击 `FLA.exe` 即可运行。

### 方式 2：双击开发脚本
双击 `run.bat`，即可自动安装依赖并以开发模式启动桌面助手。

### 方式 3：重新打包 EXE
Linux 上交叉打包（推荐，无需 Windows）：

```bash
bash desktop/qt/build_bundle.sh      # 产物直接写回 desktop/bin/FLA.exe
git add desktop/bin/FLA.exe && git commit -m "build: FLA.exe 3.6.x" && git push
```

Windows 上打包：运行 `desktop/build.bat` 或 `python desktop/package_exe.py`，生成 `desktop/dist/FLA.exe`。

> **3.6.0 → 3.6.1 事故复盘**：3.6.0 的 `SystemPage` 用了 `FIF.SHIELD`，而 qfluentwidgets 1.11.3 没有这个图标，
> `MainWindow.__init__` 直接 `AttributeError`，用户双击毫无反应。现在：① 图标名有回归测试
> （`desktop/qt/tests/test_icons.py`，不依赖 PyQt）；② `SystemPage` 构造单独 try/except，单个页面出错
> 不会再拖垮整个主窗口；③ `desktop/qt/tests/test_ui_offscreen.py` 会在 offscreen 下真正 new 出 MainWindow。
> 改了 UI 之后建议跑一次：
>
> ```bash
> QT_QPA_PLATFORM=offscreen python -m pytest desktop/qt/tests -q
> ```

> ⚠ **改了 `fla_qt/core.py` 的 `VERSION` 就必须重新打包**。`/api/desktop/version` 现在以
> **exe 里真实打包的版本** 为准（`server/desktop_dist.py::binary_version`），只改代码没打包时
> 接口会继续返回旧版本号并在日志里报警，避免"官网说 3.6.0、下载到的还是 3.5.6、更新完版本号不变"
> 这种死循环。`bash bt.sh doctor` 也会显示 `代码版本 ≠ exe 打包版本`。

## v3.6.2 新增：桌面原生课堂工具

### 课堂工具（首页「课堂工具」或导航第三项）
桌面版六件套由 Qt 原生组件实现，**独立于网页课堂页、无需浏览器、断网可用**。随机名单保存在本机；函数图像支持笛卡尔/隐式方程/不等式/极坐标/参数曲线/点、平移缩放、动态参数滑块及常见根和交点分析。网页版课堂工具仍可单独使用。

| 工具 | 说明 |
|---|---|
| 随机抽人 | 滚动动画抽取，1 / 2 / 3 / 5 人，不重复模式，历史记录，空格开始/停止 |
| 随机分组 | 按组数或每组人数均分，可复制结果发群里 |
| 计时器 | **iOS 式滚轮**拖时/分/秒 + ± 按钮 + 快捷预设 + 全屏投影 |
| 计分板 | 分组加减分、自动排序、大屏显示 |
| 表决器 | A/B/C/D/弃权，键盘 1–5 计票 |
| 函数图像 | 输入表达式即画图，拖动平移、滚轮缩放（自写解析器，无 eval） |

桌面版抽人/分组共用本地名单（`%LOCALAPPDATA%/FLA/classroom.json`）；网页版仍使用浏览器本地名单，与桌面版互不依赖。

### 2. 希沃全面消杀（系统维护页）
不只是隐藏窗口，而是真正清干净 9 类残留：进程、服务、驱动、注册表、计划任务、开机自启、
安装目录、残留文件、PPT 加载项与 COM CLSID。

- 先「体检」列出全部命中项，再决定是否消杀；
- 删除前自动备份（注册表 `reg export`、目录整体移入隔离区），页面里可一键还原；
- 被占用的驱动/文件走 `PendingFileRenameOperations`，重启后由内核删除，不强杀；
- 需要管理员权限：页面上有「以管理员身份重启 FLA」按钮（UAC 提权）。

命令行同样可用：`python -m fla_qt.purge --scan` / `--purge` / `--restore <隔离区目录>`。

### 3. 重启还原（影子系统）
开启后，学生装软件、改设置、删文件，重启即全部还原。实现只用系统底层机制：

- **方案 A（首选）UWF 统一写入筛选器**：`uwfmgr overlay set-type disk` → `filter enable` → `volume protect C:`；
  只有 **Windows 企业版 / 教育版 / IoT 企业版** 带这个组件，家庭版和专业版没有；
- **方案 B（备选）VHD 差分盘 + 独立 BCD 引导项**：仅当系统本身运行在 VHD/VHDX 上时可用；
- **方案 C 明确拒绝**：两个方案都不满足时直接说明缺什么，不做假影子（监控/回滚脚本挡不住驱动层写入）。

开启前默认**自动执行希沃消杀**（希沃驱动若被写进快照，重启也清不掉），可在页面勾选「强制跳过消杀」。
关闭时会先 `filter commit` 提交现有改动，再取消保护，避免"关掉就丢东西"。

### 4. 严格单实例
- `launcher.c`：命名互斥体 `Local\FLA.SingleInstance.v1`，第 2..N 个实例把参数写入 `pending.json` 并
  `SetEvent` 唤醒已运行实例后静默退出；
- `fla_qt/single.py`：PyQt 进程同样持有互斥体并后台等待唤醒事件，收到后 `bring_up()` + 打开对应课件；
- 互斥体由内核持有，进程崩溃/被强杀会自动释放，不会出现"再也打不开"。

