"""后台渲染回放 RGB 帧并编码带音乐的 MP4，不依赖浏览器逐帧绘制。"""
import math
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path



class VideoExports:
    def __init__(self, library, root):
        self.library, self.root = library, Path(root)
        self.jobs = {}
        self.lock = threading.RLock()
        self.nvenc_support = {}

    def start(self, options):
        data = self.library.analyze(options['id'])
        start, end = float(options['start']), float(options['end'])
        fps = int(options.get('fps', 60))
        height = int(options.get('resolution', 1080))
        if height not in (540, 720, 1080):
            raise ValueError('分辨率仅支持 540p、720p 或 1080p')
        aspect = float(options.get('aspect', 2 / 3))
        if not math.isfinite(aspect) or not .1 <= aspect <= 2:
            raise ValueError('观察器宽高比无效')
        render_width = max(120, 2 * round(1080 * aspect / 2))
        output_width = max(2, 2 * round(render_width * height / 1080 / 2))
        speed, span = float(options.get('speed', 1)), float(options.get('span', 500))
        if speed not in (.5, .75, 1, 1.5) or not math.isfinite(span) or not 400 <= span <= 3200:
            raise ValueError('播放速度或一屏时长无效')
        mode = options.get('encoder', 'auto')
        if mode not in ('auto', 'gpu', 'cpu'):
            raise ValueError('编码器选项无效')
        if not all(map(math.isfinite, (start, end))) or not 0 <= start < end <= data['duration'] / 1000 + .001:
            raise ValueError('导出时间需在曲目范围内，结束时间必须大于起点')
        if fps not in (30, 60):
            raise ValueError('帧率仅支持 30 或 60')
        audio = self.library.audio_path(options['id']) if options.get('music', True) else None
        if options.get('music', True) and audio is None:
            raise ValueError('未找到谱面音乐，请取消导出配乐后重试')
        encoder = shutil.which('ffmpeg')
        if not encoder:
            try:
                import imageio_ffmpeg
                encoder = imageio_ffmpeg.get_ffmpeg_exe()
            except ImportError:
                raise ValueError('视频导出需要 FFmpeg 或 imageio-ffmpeg，请使用 torch 环境启动') from None
        gpu = False
        if mode != 'cpu':
            if encoder not in self.nvenc_support:
                try:
                    probe = subprocess.run([encoder, '-v', 'error', '-f', 'lavfi', '-i', 'color=black:s=720x1080',
                                            '-frames:v', '1', '-c:v', 'h264_nvenc', '-f', 'null', '-'],
                                           capture_output=True, timeout=10,
                                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    self.nvenc_support[encoder] = probe.returncode == 0
                except (OSError, subprocess.TimeoutExpired):
                    self.nvenc_support[encoder] = False
            gpu = self.nvenc_support[encoder]
            if mode == 'gpu' and not gpu:
                raise ValueError('GPU NVENC 无法初始化，请改用自动或 CPU 编码')
        try:
            from video_renderer import ReplayRenderer
        except ImportError:
            raise ValueError('后台视频导出需要 NumPy 和 OpenCV，请使用本机 torch 环境启动') from None
        focus = float(options.get('focusRange', 40))
        if not math.isfinite(focus) or not 40 <= focus <= 80:
            raise ValueError('偏差量程需在 ±40 至 ±80 ms 之间')
        skin = options.get('skin', 'cut')
        if skin not in ('cut', 'ball'):
            raise ValueError('音符皮肤无效')
        renderer = ReplayRenderer(data, span, fill=bool(options.get('fill', False)), focus=focus, width=render_width, pair_highlight=bool(options.get('pairHighlight', False)), skin=skin, show_tails=bool(options.get('showTails', True)))
        with self.lock:
            if any(job['state'] in ('rendering', 'encoding') for job in self.jobs.values()):
                raise ValueError('已有视频正在导出，请完成或取消后再试')
            self.root.mkdir(parents=True, exist_ok=True)
            key = uuid.uuid4().hex
            frames = math.ceil((end - start) / speed * fps)
            title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', data['replay']['title'])[:100].strip(' .') or 'mania'
            target = self.root / f'{title}_{start:.3f}-{end:.3f}s_{key[:8]}.mp4'
            partial = self.root / f'{key}.part.mp4'
            log = (self.root / f'{key}.log').open('wb')
            args = [encoder, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'rawvideo',
                    '-pixel_format', 'rgb24', '-video_size', f'{render_width}x1080', '-framerate', str(fps), '-i', 'pipe:0']
            rate = data['rate'] * speed
            if audio:
                args += ['-ss', str(start * data['rate']), '-i', str(audio), '-map', '0:v:0', '-map', '1:a:0',
                         '-af', 'volume=-6dB,apad' if rate == 1 else f"aresample=48000,asetrate=48000*{rate},aresample=48000,volume=-6dB,apad", '-c:a', 'aac', '-b:a', '192k']
            else:
                args += ['-an']
            args += ['-t', str(frames / fps)]
            if height != 1080:
                args += ['-vf', f'scale={output_width}:{height}']
            args += (['-c:v', 'h264_nvenc', '-preset', 'p4', '-rc', 'vbr', '-cq', '18', '-b:v', '0'] if gpu
                     else ['-c:v', 'libx264', '-preset', 'fast', '-crf', '18'])
            args += ['-pix_fmt', 'yuv420p', '-r', str(fps), '-movflags', '+faststart', str(partial)]
            try:
                process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                           stderr=log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except OSError:
                log.close()
                raise
            self.jobs[key] = dict(process=process, log=log, partial=partial, target=target,
                                  frames=frames, received=0, state='rendering', started=time.monotonic(),
                                  fps=fps, speed=speed, span=span, encoder='GPU NVENC' if gpu else 'CPU x264', error=None)
            threading.Thread(target=self.render_job, args=(key, renderer, start, speed, fps), daemon=True).start()
            return self.status(key)

    def job(self, key):
        if key not in self.jobs:
            raise ValueError('导出任务不存在')
        return self.jobs[key]

    def render_job(self, key, renderer, start, speed, fps):
        job = self.job(key)
        try:
            for index in range(job['frames']):
                if job['state'] == 'cancelled':
                    return
                frame = renderer.render(start * 1000 + index * 1000 * speed / fps)
                job['process'].stdin.write(frame.tobytes())
                job['received'] = index + 1
            self.finish(key)
        except Exception as error:
            if job['state'] != 'cancelled':
                self.cancel(key)
                job['error'] = str(error)
                job['state'] = 'failed'
        finally:
            job['elapsed'] = time.monotonic() - job['started']

    def status(self, key):
        job = self.job(key)
        elapsed = job.get('elapsed', time.monotonic() - job['started'])
        throughput = job['received'] / max(elapsed, .001)
        result = dict(id=key, state=job['state'], frames=job['frames'], received=job['received'],
                      progress=job['received'] / job['frames'], elapsed=elapsed, throughput=throughput,
                      remaining=(job['frames'] - job['received']) / max(throughput, .001),
                      encoder=job['encoder'], fps=job['fps'], speed=job['speed'], span=job['span'], error=job['error'])
        if job['state'] == 'complete':
            result.update(url=f'/api/video/file?id={key}', name=job['target'].name, path=str(job['target']))
        return result

    def finish(self, key):
        job = self.job(key)
        if job['state'] != 'rendering' or job['received'] != job['frames']:
            raise ValueError('视频帧未完整生成')
        job['state'] = 'encoding'
        job['process'].stdin.close()
        try:
            code = job['process'].wait(timeout=120)
        except subprocess.TimeoutExpired:
            self.cancel(key)
            raise ValueError('视频编码超时') from None
        job['log'].close()
        if code != 0:
            self.cancel(key)
            raise ValueError('视频编码失败，请检查 exports 中的编码日志')
        with self.lock:
            if job['state'] == 'cancelled':
                raise ValueError('已取消导出')
            job['partial'].replace(job['target'])
            job['state'] = 'complete'
        return dict(url=f'/api/video/file?id={key}', name=job['target'].name, path=str(job['target']))

    def cancel(self, key):
        job = self.job(key)
        if job['state'] == 'complete':
            return dict(cancelled=False)
        job['state'] = 'cancelled'
        process = job['process']
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
        job['log'].close()
        job['partial'].unlink(missing_ok=True)
        return dict(cancelled=True)

    def file(self, key):
        job = self.job(key)
        if job['state'] != 'complete':
            raise ValueError('视频尚未完成')
        return job['target']
