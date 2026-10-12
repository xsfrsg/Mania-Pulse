# osu!mania 节奏轨迹

本地回放分析器：按轨显示实际击打相对预期时刻的偏差，并提供整曲预览、音乐同步和黑底视频导出。原项目：[Mania-Replay-Master](https://github.com/Mania-Visualization-Project/Mania-Replay-Master)，其判定和渲染参考见[原项目 README](https://github.com/Mania-Visualization-Project/Mania-Replay-Master/blob/master/README_CN.md)。上游源码不随本项目重复打包，授权说明见 [NOTICE](NOTICE)。

## 启动

1. 安装 Python 3.10 或更新版本，并在安装时勾选 **Add Python to PATH**。
2. 在项目目录打开终端，安装依赖：

   ```powershell
   python -m pip install -r requirements.txt
   ```

3. 双击唯一入口 **启动.cmd**。浏览器打开后，点左侧“选择 osu! 文件夹”，选 osu! 根目录、`Songs` 或 `Replays` 均可；程序会自动定位根目录并记住选择。换电脑或路径变化时重新选择。

关闭启动用的命令窗口即可停止服务。页面只在本机运行，不会上传回放或修改 osu! 文件。若旧版服务仍占用默认端口，再次启动会自动切换端口并打开新版页面。无需 Conda、PyTorch 或便携 EXE。

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
