# osu!mania 节奏轨迹

本地回放分析器：按轨显示实际击打相对预期时刻的偏差，并提供整曲预览、音乐同步和黑底视频导出。原项目：[Mania-Replay-Master](https://github.com/Mania-Visualization-Project/Mania-Replay-Master)，其判定和渲染参考见[原项目 README](https://github.com/Mania-Visualization-Project/Mania-Replay-Master/blob/master/README_CN.md)。上游源码不随本项目重复打包，授权说明见 [NOTICE](NOTICE)。

## 展示

界面总览：选择本地回放，逐轨观察击打偏差曲线，并定位整曲中的薄弱段落。

![界面总览与逐轨偏差曲线](<readme/屏幕截图 2026-10-13 074037.png>)

击打误差：悬停查看毫秒级 early / late 偏差，同时高亮点击与对应音符。

![击打误差与配对高亮](<readme/屏幕截图 2026-10-13 073926.png>)

early-Miss 提示：突出显示提前 Miss 及其音符归属，辅助观察可能的连崩起点。

![early-Miss 与可能连崩起点](<readme/屏幕截图 2026-10-13 074000.png>)

## 启动

1. 下载 [Python 3.12.10 Windows 64 位安装包](https://pythonlang.cn/ftp/python/3.12.10/python-3.12.10-amd64.exe)，运行安装程序，勾选 **Add python.exe to PATH**，然后点击 **Install Now**。
2. 下载并解压本项目，打开项目文件夹（能看到 `requirements.txt` 和 `启动.cmd`），在文件夹空白处右键选择“在终端中打开”。安装完成后重新打开终端，再执行：

   ```powershell
   py -3.12 -m pip install --only-binary=:all: -r requirements.txt -i https://mirrors.ctyun.cn/pypi/simple/
   ```

3. 启动程序。电脑只安装了 Python 3.12 时，双击 **启动.cmd** 即可；若同时安装了其他 Python 版本，用下面的命令确保安装依赖和启动使用同一个解释器：

   ```powershell
   py -3.12 src/analyzer.py
   ```

4. 浏览器打开后，点左侧“选择 osu! 文件夹”，选 osu! 根目录、`Songs` 或 `Replays` 均可；程序会自动定位根目录并记住选择。换电脑或路径变化时重新选择，否则无法找到本地回放或谱面。
5. 选择回放开始分析；需要视频时，在导出区域选择范围、分辨率并导出。

关闭启动用的命令窗口即可停止服务。页面只在本机运行，不会上传回放或修改 osu! 文件。若旧版服务仍占用默认端口，再次启动会自动切换端口并打开新版页面。

## 使用

- 左侧选回放；只有回放 MD5 与本地谱面一致时才分析。
- 右侧铺面观察器固定在页面顶部；其下方密度条可点击或拖动定位。轨道可上下拖动或滚轮浏览。
- **流速**沿用原项目的 `speed`：像素/帧。原项目默认 20 px/帧、60 fps、轨道高度 960 px，一屏覆盖 `960 ÷ (20 × 60) × 1000 = 800 ms`。本页用同一换算；播放速度另行控制实时播放。
- 偏差折线按列展示；空心点是预期时刻，带细槽方块是实际击打位置。击打方块在 0 ms 为中性白，偏差越大越向 early 蓝 / late 橙渐变，±40 ms 达到满色。预期判定区域显示白 MAX 与黄 300，不显示 200。
- 导出可选整曲、当前观察范围或自定义时间；视频包含竖轨、曲线、击打位置和音乐。支持自动选择 NVIDIA NVENC 或 CPU 编码。

## 说明

`.osr` 记录输入时序和全局判定总数，不包含可直接读取的逐音符官方判定。程序按原项目规则重建，并展示总数核对；结果不能视为游戏直接提供的逐音符判定。只绘制长条头部。Random、Co-op、键数转换暂不支持。

流速换算来源：原项目 [README_CN.md](https://github.com/Mania-Visualization-Project/Mania-Replay-Master/blob/master/README_CN.md) 和其公开渲染配置。上游采用 20 px/帧、60 fps、960 px 游戏区域；本项目的界面视窗按这组参数折算成时间范围。

依赖列于 [requirements.txt](requirements.txt)。本地设置写入 `.runtime/`，视频保存在 `exports/`。
