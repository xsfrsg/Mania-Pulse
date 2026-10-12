"""osu!mania 逐列时序分析。

判定规则参考 Mania-Replay-Master (Apache-2.0)，源码和许可证见上游 GitHub 仓库及 NOTICE。
保留原项目的匹配/LN 规则，不将重建结果冒充游戏官方逐音符记录。
只使用 Python 标准库，读取 osu! 目录，不修改游戏文件。
"""
from __future__ import annotations

import argparse
import bisect
import csv
import errno
import hashlib
import json
import lzma
import mimetypes
import os
import struct
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
LABELS = ['MAX', '300', '200', '100', '50', 'Miss']
API_VERSION = 2


class Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def take(self, size):
        if size < 0 or self.pos + size > len(self.data):
            raise ValueError('回放文件不完整')
        value = self.data[self.pos:self.pos + size]
        self.pos += size
        return value

    def number(self, fmt):
        return struct.unpack('<' + fmt, self.take(struct.calcsize('<' + fmt)))[0]

    def string(self):
        marker = self.number('B')
        if marker == 0:
            return ''
        if marker != 11:
            raise ValueError('回放字符串格式错误')
        size, shift = 0, 0
        while True:
            byte = self.number('B')
            size |= (byte & 127) << shift
            if not byte & 128:
                break
            shift += 7
            if shift > 35:
                raise ValueError('回放字符串长度错误')
        return self.take(size).decode('utf-8', errors='replace')


def replay_header(path):
    reader = Reader(path.read_bytes())
    mode, version = reader.number('B'), reader.number('i')
    md5, player, replay_hash = reader.string(), reader.string(), reader.string()
    counts = [reader.number('H') for _ in range(6)]
    score, combo = reader.number('i'), reader.number('H')
    reader.number('B')
    mods = reader.number('i')
    reader.string()  # life bar
    ticks = reader.number('q')
    try:
        date = (datetime(1, 1, 1) + timedelta(microseconds=ticks // 10)).isoformat(timespec='seconds')
    except OverflowError:
        date = ''
    length = reader.number('i')
    # stable .osr 顺序: 300/100/50/geki/katu/miss；mania 显示顺序不同。
    return dict(mode=mode, version=version, md5=md5.lower(), date=date,
                counts=[counts[3], counts[0], counts[4], counts[1], counts[2], counts[5]],
                mods=mods, score=score, combo=combo, compressed=reader.take(length))


@dataclass(eq=False)
class Note:
    time: int
    column: int
    duration: int = 0
    start: int = -1
    end: int = -1
    related: 'Note | None' = None
    index: int = 0
    offset: int = 0
    offset_end: int = 0
    head_action: 'Note | None' = None

    @property
    def finish(self):
        return self.time + self.duration


def map_metadata(raw):
    values, section = {}, ''
    for line in raw.decode('utf-8-sig', errors='replace').splitlines():
        line = line.strip()
        if line.startswith('['):
            section = line
        elif section in ('[Metadata]', '[Difficulty]', '[General]') and ':' in line:
            key, value = line.split(':', 1)
            values[key.strip()] = value.strip()
    return values


def parse_map(raw, rate=1, mirror=False):
    meta = map_metadata(raw)
    if int(meta.get('Mode', 0)) != 3:
        raise ValueError('第一版仅支持原生 osu!mania 谱面')
    keys = int(float(meta.get('CircleSize', 4)))
    if not 1 <= keys <= 18:
        raise ValueError('不支持的键数')
    notes, section = [], ''
    for line in raw.decode('utf-8-sig', errors='replace').splitlines():
        line = line.strip()
        if line.startswith('['):
            section = line
        elif section == '[HitObjects]' and line and not line.startswith('//'):
            fields = line.split(',')
            # 部分变速工具输出小数毫秒；兼容读取后按现有整数毫秒基准截断。
            stamp, kind = int(float(fields[2])), int(fields[3])
            duration = max(0, int(float(fields[5].split(':')[0])) - stamp) if kind & 128 else 0
            column = min(keys - 1, max(0, int(fields[0]) * keys // 512))
            if mirror:
                column = keys - 1 - column
            notes.append(Note(int(stamp / rate), column, int(duration / rate)))
    notes.sort(key=lambda n: n.time)
    for index, note in enumerate(notes):
        note.index = index
    return meta, keys, notes


def parse_actions(compressed, keys, rate=1):
    text = lzma.decompress(compressed).decode('ascii')
    current, held, actions = 0, [None] * keys, []
    for frame in text.split(','):
        if not frame:
            continue
        fields = frame.split('|')
        delta = int(fields[0])
        if delta == -12345:  # RNG seed，不是输入事件
            continue
        current += delta
        mask = int(float(fields[1]))
        for column in range(keys):
            if mask & (1 << column):
                if held[column] is None:
                    held[column] = current
            elif held[column] is not None:
                begin = held[column]
                actions.append(Note(int(begin / rate), column, int((current - begin) / rate)))
                held[column] = None
    # 原项目仅记录已松开的按键；尾部未松开事件记录为诊断，不推断松开时刻。
    unclosed = sum(value is not None for value in held)
    actions.sort(key=lambda n: n.time)
    for index, action in enumerate(actions):
        action.index = index
    return actions, unclosed


def windows(od, mods):
    if mods & (1 << 29):
        od = min(10, od * 1.4) if mods & 16 else max(0, od / 2) if mods & 2 else od
        ranges = [(22.4, 19.4, 13.9), (64, 49, 34), (97, 82, 67),
                  (127, 112, 97), (151, 136, 121), (188, 173, 158)]
        return [int(mid + (hi - mid) * (od - 5) / 5 if od > 5 else
                    mid - (mid - lo) * (5 - od) / 5) for lo, mid, hi in ranges]
    factor = 1 / 1.4 if mods & 16 else 1.4 if mods & 2 else 1
    return [int((value + (3 * (10 - od) if i else 0)) * factor)
            for i, value in enumerate((16, 34, 67, 97, 121, 158))]


def judge(notes, actions, window, score_v2=False):
    """逐列队列等价于上游同列过滤；保留 LN 重判和 omScoreV2Adjust 默认行为。"""
    lanes = {}
    for note in notes:
        lanes.setdefault(note.column, []).append(note)
    positions = {col: 0 for col in lanes}
    cannot, ln_judged = set(), set()

    def grade(diff):
        return next((i for i, bound in enumerate(window[:-1]) if diff <= bound), -1)

    def can_hit(action, note, lane, position):
        diff = action.time - note.time
        if -diff > window[5]:
            return 'early'
        if not note.duration:
            return 'late' if diff >= window[3] else 'hit'
        late = action.time - note.finish > window[3] * (1.5 if score_v2 else 1)
        if position + 1 < len(lane):
            nxt = lane[position + 1]
            gap = nxt.time - note.time
            limit = nxt.time - 1 if gap < window[4] else nxt.time - window[4] if gap > window[4] * 2 else note.time + window[4]
            late = action.time > limit
        if late:
            return 'late'
        for prev in reversed(actions[:action.index]):
            if prev.column != action.column:
                continue
            if prev.finish < note.finish - window[4]:
                break
            held_long = prev.finish > (prev.related.finish if prev.related else 0) + window[3] * (1.5 if score_v2 else 1)
            if not held_long and prev.related is not None:
                continue
            if note.time + window[4] < prev.finish < note.finish + window[3] * (1.5 if score_v2 else 1):
                return 'late'
        return 'again'

    for action in actions:
        lane = lanes.get(action.column, [])
        pos = positions.get(action.column, 0)
        target = None
        while pos < len(lane):
            note = lane[pos]
            state = can_hit(action, note, lane, pos)
            if state == 'early':
                break
            if state == 'late' or note in cannot:
                pos += 1
                continue
            target = note
            if state != 'again':
                pos += 1
            break
        positions[action.column] = pos
        if target is None:
            continue
        action.offset, action.offset_end = action.time - target.time, action.finish - target.finish
        result = grade(abs(action.offset))
        action.start = result
        if target.start == -1:
            target.start = result
        # 只保留第一次命中头部的有效按下，LN 后续补按不当作新音符。
        if target.head_action is None and result >= 0:
            target.head_action = action
        action.related, target.related = target, action
        if not target.duration:
            continue
        end_diff = abs(action.offset_end)
        action.end = grade(end_diff / 1.5) if action.offset_end <= window[3] * 1.5 else -1
        if target in ln_judged or target.start in (-1, 5):
            if 0 <= action.end <= 3:
                action.end = 4
        target.end = action.end
        start_diff = abs(action.offset)
        if target.time - window[4] > action.time:
            start_diff = abs(target.time - (target.finish - 1))
        total_diff = start_diff + end_diff
        again = action.offset_end < -window[4]
        total_grade = -1 if again else next((i for i, factor in enumerate((1.2, 1.1, 1, 1))
                                            if start_diff <= window[i] * factor and total_diff <= window[i] * factor * 2), 4)
        if target in ln_judged and total_grade in (0, 1):
            total_grade = 2
        ln_judged.add(target)
        if not score_v2:
            target.start = total_grade
        if not again:
            cannot.add(target)
    return notes


def mod_names(mods):
    names = [(1, 'NF'), (2, 'EZ'), (8, 'HD'), (16, 'HR'), (32, 'SD'),
             (64, 'DT'), (256, 'HT'), (512, 'NC'), (16384, 'PF'),
             (1 << 29, 'V2'), (1 << 30, 'Mirror')]
    return ' '.join(name for bit, name in names if mods & bit) or 'NoMod'


def normalize_osu_dir(path):
    candidate = Path(path).expanduser().resolve()
    for root in (candidate, *candidate.parents):
        if (root / 'Songs').is_dir() and (root / 'Replays').is_dir():
            return root
    return None


class Library:
    def __init__(self, osu_dir):
        self.osu_dir = Path(osu_dir).resolve() if osu_dir else None
        self.lock = threading.RLock()
        self.maps, self.replays, self.cached = {}, {}, {}
        self.refresh()

    def configure(self, osu_dir):
        root = normalize_osu_dir(osu_dir)
        if root is None:
            raise ValueError('所选目录及其上级目录中未找到同时包含 Songs 和 Replays 的 osu! 文件夹')
        with self.lock:
            self.osu_dir = root
        return self.refresh()

    def refresh(self):
        with self.lock:
            start = time.perf_counter()
            maps, entries = {}, []
            if self.osu_dir is None:
                self.maps, self.replays, self.cached, self.entries = {}, {}, {}, []
                self.scan_seconds = 0
                return self.info()
            for path in (self.osu_dir / 'Songs').rglob('*.osu'):
                try:
                    raw = path.read_bytes()
                    md5 = hashlib.md5(raw).hexdigest()
                    maps.setdefault(md5, []).append((path, map_metadata(raw)))
                except OSError:
                    continue
            self.maps, self.replays, self.cached = maps, {}, {}
            for path in sorted((self.osu_dir / 'Replays').glob('*.osr'), key=lambda p: p.stat().st_mtime, reverse=True):
                key = hashlib.sha256(path.name.encode()).hexdigest()[:20]
                try:
                    header = replay_header(path)
                    matches = maps.get(header['md5'], [])
                    meta = matches[0][1] if matches else {}
                    entry = dict(id=key, filename=path.name, mode=header['mode'], date=header['date'],
                                 mods=mod_names(header['mods']), md5=header['md5'], matched=bool(matches),
                                 title=meta.get('TitleUnicode') or meta.get('Title') or path.stem,
                                 artist=meta.get('ArtistUnicode') or meta.get('Artist', ''),
                                 difficulty=meta.get('Version', ''), keys=meta.get('CircleSize', ''),
                                 supported=header['mode'] == 3 and meta.get('Mode') == '3')
                    self.replays[key] = (path, header, entry)
                    entries.append(entry)
                except (ValueError, OSError, struct.error) as error:
                    entries.append(dict(id=key, filename=path.name, title=path.stem, error=str(error), matched=False))
            self.entries = entries
            self.scan_seconds = round(time.perf_counter() - start, 3)
            return self.info()

    def info(self):
        return dict(replays=self.entries, map_count=sum(map(len, self.maps.values())),
                    scan_seconds=self.scan_seconds, osu_dir=str(self.osu_dir or ''), configured=self.osu_dir is not None)

    def analyze(self, key):
        with self.lock:
            if key in self.cached:
                return self.cached[key]
            if key not in self.replays:
                raise ValueError('回放不存在，请刷新列表')
            path, header, entry = self.replays[key]
            matches = self.maps.get(header['md5'], [])
            if not matches:
                raise ValueError('找不到 MD5 完全一致的谱面，不进行分析')
            raw = matches[0][0].read_bytes()
            if hashlib.md5(raw).hexdigest() != header['md5']:
                raise ValueError('谱面已修改，请刷新列表重新匹配')
            current = replay_header(path)
            if current['md5'] != header['md5'] or current['compressed'] != header['compressed']:
                raise ValueError('回放已修改，请刷新列表')
            mods = header['mods']
            # 随机/Co-op/键数转换没有可靠列映射，宁可拒绝而不是生成错图。
            unsupported = (1 << 21) | (1 << 25) | sum(1 << i for i in (15, 16, 17, 18, 19, 24, 26, 27, 28))
            if mods & unsupported:
                raise ValueError('第一版暂不支持 Random / Co-op / 键数转换 mods')
            rate = 1.5 if mods & (64 | 512) else 0.75 if mods & 256 else 1
            meta, keys, notes = parse_map(raw, rate, bool(mods & (1 << 30)))
            actions, unclosed = parse_actions(header['compressed'], keys, rate)
            window = windows(float(meta.get('OverallDifficulty', 5)), mods)
            judge(notes, actions, window, bool(mods & (1 << 29)))
            reconstructed = Counter(n.start for n in notes)
            if mods & (1 << 29):
                reconstructed.update(n.end for n in notes if n.duration)
            counts = [reconstructed[i] for i in range(5)] + [reconstructed[-1] + reconstructed[5]]
            # 每一行都是谱面音符，漏音保留 null，使前端自然断线。
            rows = []
            for n in notes:
                a = n.head_action
                hit = a is not None and 0 <= n.start <= 4
                rows.append(dict(time=n.time, column=n.column, offset=a.offset if hit else None,
                                 actual=a.time if hit else None, judgement=LABELS[n.start] if n.start >= 0 else 'Miss',
                                 head_judgement=LABELS[a.start] if hit else None, ln=bool(n.duration), duration=n.duration, index=n.index))
            warnings = []
            if counts != header['counts']:
                warnings.append('重建判定总数与回放记录不一致；曲线可用于观察，逐音符有效性尚不能视为官方判定。')
            if unclosed:
                warnings.append(f'{unclosed} 个尾部按键没有松开记录，未推断为有效命中。')
            unmatched_clicks = [dict(time=a.time, column=a.column) for a in actions if a.related is None]
            invalid_clicks = [dict(time=a.time, column=a.column, reference_time=a.related.time, early_miss=a.offset < 0) for a in actions if a.related is not None and a.start < 0]
            click_links = [dict(time=a.time, column=a.column, reference_time=a.related.time, estimated=False, early_miss=a.start < 0 and a.offset < 0) for a in actions if a.related is not None]
            auxiliary_curve = []
            for column in range(keys):
                lane = [r for r in rows if r['column'] == column]
                times = [r['time'] for r in lane]
                valid = [r for r in lane if r['offset'] is not None]
                valid_times = [r['time'] for r in valid]
                auxiliary_curve.extend(dict(time=r['time'], column=column, offset=r['offset'], discrepancy=0, unmatched=False) for r in valid)
                # 无判定归属的点击只显示灰色标记，不猜测音符，也不接入曲线。
                for click in (a for a in invalid_clicks if a['column'] == column):
                    if not lane:
                        continue
                    position = bisect.bisect_left(times, click['reference_time'])
                    reference = lane[position]
                    offset = click['time'] - reference['time']
                    baseline = None
                    if valid:
                        index = bisect.bisect_left(valid_times, reference['time'])
                        a, b = valid[max(0, index - 1)], valid[min(index, len(valid) - 1)]
                        fraction = (reference['time'] - a['time']) / (b['time'] - a['time']) if b['time'] > a['time'] else 0
                        baseline = a['offset'] + (b['offset'] - a['offset']) * fraction
                    auxiliary_curve.append(dict(time=click['time'], column=column, offset=offset, reference_time=reference['time'],
                                                discrepancy=abs(offset - baseline) if baseline is not None else None, unmatched=True))
            auxiliary_curve.sort(key=lambda r: r['time'])
            result = dict(replay=entry, keys=keys, rows=rows, duration=max(max((n.finish for n in notes), default=1), max((a.time for a in actions), default=1)),
                          rate=rate, windows=window, official_counts=header['counts'], reconstructed_counts=counts,
                          counts_match=counts == header['counts'], warnings=warnings,
                          unmatched_actions=len(unmatched_clicks), unmatched_clicks=unmatched_clicks, invalid_clicks=invalid_clicks, click_links=click_links, auxiliary_curve=auxiliary_curve, action_count=len(actions),
                          map_path=str(matches[0][0]), source='Mania-Replay-Master 重建判定', time_unit='实际游玩毫秒')
            audio_path = self.audio_path(key)
            result['audio'] = dict(available=audio_path is not None, name=audio_path.name if audio_path else '',
                                   url=f'/api/audio?id={key}' if audio_path else '')
            cover_path = self.cover_path(key)
            result['cover'] = dict(available=cover_path is not None, url=f'/api/cover?id={key}' if cover_path else '')
            self.cached[key] = result
            return result

    def cover_path(self, key):
        """读取精确匹配谱面的背景图，仅允许 Songs 内的本地图片。"""
        if key not in self.replays:
            return None
        _, header, _ = self.replays[key]
        matches = self.maps.get(header['md5'], [])
        if not matches:
            return None
        map_path, _ = matches[0]
        section = ''
        for line in map_path.read_text(encoding='utf-8-sig', errors='replace').splitlines():
            line = line.strip()
            if line.startswith('['):
                section = line
            if section != '[Events]' or not line or line.startswith('//'):
                continue
            try:
                fields = next(csv.reader([line], skipinitialspace=True))
            except csv.Error:
                continue
            if len(fields) >= 3 and fields[0].lower() in ('0', 'background'):
                path = (map_path.parent / fields[2].replace('\\', '/')).resolve()
                if self.osu_dir is not None and path.is_relative_to((self.osu_dir / 'Songs').resolve()) and path.is_file() and path.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp', '.bmp'):
                    return path
        return None

    def audio_path(self, key):
        """只提供精确匹配谱面的 AudioFilename，不允许请求任意本地路径。"""
        if key not in self.replays:
            return None
        _, header, _ = self.replays[key]
        matches = self.maps.get(header['md5'], [])
        if not matches:
            return None
        map_path, meta = matches[0]
        filename = meta.get('AudioFilename', '').strip().strip('"')
        if not filename:
            return None
        path = (map_path.parent / filename).resolve()
        if self.osu_dir is None or not path.is_relative_to((self.osu_dir / 'Songs').resolve()) or not path.is_file():
            return None
        return path


def make_handler(library):
    from video_export import VideoExports
    exports = VideoExports(library, ROOT / 'exports')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            print(fmt % args, flush=True)

        def respond(self, status, value, content_type='application/json; charset=utf-8'):
            body = json.dumps(value, ensure_ascii=False).encode() if isinstance(value, (dict, list)) else value
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            try:
                if parsed.path == '/api/version':
                    return self.respond(200, {'version': API_VERSION})
                if parsed.path == '/api/replays':
                    with library.lock:
                        return self.respond(200, library.info())
                if parsed.path == '/api/analyze':
                    key = parse_qs(parsed.query).get('id', [''])[0]
                    return self.respond(200, library.analyze(key))
                if parsed.path == '/api/audio':
                    key = parse_qs(parsed.query).get('id', [''])[0]
                    path = library.audio_path(key)
                    if path is None:
                        return self.respond(404, {'error': '谱面音乐文件未找到'})
                    return self.serve_audio(path)
                if parsed.path == '/api/video/status':
                    key = parse_qs(parsed.query).get('id', [''])[0]
                    return self.respond(200, exports.status(key))
                if parsed.path == '/api/cover':
                    key = parse_qs(parsed.query).get('id', [''])[0]
                    path = library.cover_path(key)
                    if path is None:
                        return self.respond(404, {'error': '谱面封面未找到'})
                    return self.respond(200, path.read_bytes(), mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
                if parsed.path == '/api/video/file':
                    key = parse_qs(parsed.query).get('id', [''])[0]
                    return self.serve_audio(exports.file(key))
                assets = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}
                if parsed.path in assets:
                    name, mime = assets[parsed.path]
                    return self.respond(200, (ROOT / 'web' / name).read_bytes(), mime + '; charset=utf-8')
                return self.respond(404, {'error': '页面不存在'})
            except (ValueError, OSError, struct.error, lzma.LZMAError) as error:
                return self.respond(400, {'error': str(error)})

        def serve_audio(self, path):
            # 支持 HTTP Range，HTML audio 才能直接跳转到整曲中间位置。
            size = path.stat().st_size
            start, end, status = 0, size - 1, 200
            byte_range = self.headers.get('Range')
            if byte_range:
                try:
                    unit, bounds = byte_range.split('=', 1)
                    if unit != 'bytes' or ',' in bounds:
                        raise ValueError('Unsupported range')
                    first, last = bounds.split('-', 1)
                    if first:
                        start = int(first)
                        end = min(int(last), size - 1) if last else size - 1
                    else:
                        suffix = int(last)
                        if suffix <= 0:
                            raise ValueError('Invalid suffix')
                        start = max(0, size - suffix)
                    if start < 0 or start > end or start >= size:
                        raise ValueError('Invalid range')
                    status = 206
                except ValueError:
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{size}')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
            self.send_response(status)
            self.send_header('Content-Type', mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Length', str(end - start + 1))
            if status == 206:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            self.end_headers()
            try:
                with path.open('rb') as stream:
                    stream.seek(start)
                    remaining = end - start + 1
                    while remaining:
                        chunk = stream.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # 用户拖动或切换回放时浏览器会取消旧请求。

        def do_POST(self):
            # 刷新只读本地文件；拒绝跨站请求。
            origin = self.headers.get('Origin')
            if origin and origin != 'http://' + self.headers.get('Host', ''):
                return self.respond(403, {'error': '来源不允许'})
            parsed = urlparse(self.path)
            if parsed.path.startswith('/api/video/'):
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 4 * 1024 * 1024:
                        raise ValueError('导出请求大小无效')
                    body = self.rfile.read(length)
                    key = parse_qs(parsed.query).get('id', [''])[0]
                    if parsed.path == '/api/video/start':
                        return self.respond(200, exports.start(json.loads(body)))
                    if parsed.path == '/api/video/cancel':
                        return self.respond(200, exports.cancel(key))
                except (ValueError, OSError, KeyError, TypeError) as error:
                    return self.respond(400, {'error': str(error)})
            if self.path == '/api/refresh':
                return self.respond(200, library.refresh())
            if parsed.path == '/api/select-osu':
                try:
                    selected = pick_osu_directory(library.osu_dir)
                    if not selected:
                        return self.respond(200, {'cancelled': True})
                    info = library.configure(selected)
                    config_path = ROOT / '.runtime' / 'osu-dir.json'
                    config_path.parent.mkdir(parents=True, exist_ok=True)
                    config_path.write_text(json.dumps({'osu_dir': str(library.osu_dir)}, ensure_ascii=False), encoding='utf-8')
                    return self.respond(200, info)
                except (OSError, ValueError, RuntimeError) as error:
                    return self.respond(400, {'error': str(error)})
            self.respond(404, {'error': '接口不存在'})
    return Handler


def pick_osu_directory(current):
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes('-topmost', True)
        selected = filedialog.askdirectory(title='选择 osu! 文件夹（也可以选 Songs 或 Replays）',
                                           initialdir=str(current or Path.home()), mustexist=True)
    finally:
        root.destroy()
    return selected


def main():
    parser = argparse.ArgumentParser(description='osu!mania 逐列偏差分析')
    candidates = [Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'osu!',
                  Path.home() / 'AppData' / 'Local' / 'osu!']
    default_dir = next((normalize_osu_dir(p) for p in candidates if normalize_osu_dir(p)), None)
    config_path = ROOT / '.runtime' / 'osu-dir.json'
    saved_dir = None
    try:
        saved_dir = json.loads(config_path.read_text(encoding='utf-8')).get('osu_dir')
    except (OSError, ValueError, AttributeError):
        pass
    parser.add_argument('--osu-dir', default=None)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    osu_dir = normalize_osu_dir(args.osu_dir or saved_dir or default_dir) if (args.osu_dir or saved_dir or default_dir) else None
    library = Library(osu_dir)
    handler = make_handler(library)
    server = None
    if not 0 <= args.port <= 65535:
        parser.error('端口必须在 0 到 65535 之间')
    ports = [0] if args.port == 0 else range(args.port, min(65535, args.port + 20) + 1)
    for port in ports:
        try:
            server = ThreadingHTTPServer(('127.0.0.1', port), handler)
            break
        except OSError as error:
            if error.errno != errno.EADDRINUSE and getattr(error, 'winerror', None) != 10048:
                raise
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/version', timeout=.5) as response:
                    current = json.load(response).get('version')
                if current == API_VERSION:
                    url = f'http://127.0.0.1:{port}'
                    print(f'已复用运行中的新版服务：{url}', flush=True)
                    if not args.no_browser:
                        webbrowser.open(url)
                    return
            except (OSError, ValueError, urllib.error.URLError):
                pass
    if server is None:
        raise RuntimeError(f'端口 {args.port} 到 {min(65535, args.port + 20)} 均被占用；请关闭旧版服务后重试')
    url = f'http://127.0.0.1:{server.server_port}'
    print(f'{url} | {len(library.entries)} replays | {library.info()["map_count"]} maps', flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
