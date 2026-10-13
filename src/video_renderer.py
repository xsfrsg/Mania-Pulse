"""后台 RGB 渲染；时间坐标、偏差映射及曲线公式与 web/app.js 对齐。"""
import bisect
import math
import cv2
import numpy as np


def offset_position(offset, focus=40):
    value = abs(offset)
    return math.copysign(.8 * value / focus if value <= focus else .8 + .2 * (1 - math.exp(-(value - focus) / (focus / 4))), offset)


def curve_tangents(lane, focus=40):
    slopes = []
    for i, row in enumerate(lane):
        if row['offset'] is None:
            slopes.append(0)
            continue
        previous = lane[i - 1] if i else None
        following = lane[i + 1] if i + 1 < len(lane) else None
        before = previous is not None and previous['offset'] is not None and row['time'] > previous['time']
        after = following is not None and following['offset'] is not None and following['time'] > row['time']
        h0 = row['time'] - previous['time'] if before else 0
        h1 = following['time'] - row['time'] if after else 0
        value = offset_position(row['offset'], focus)
        d0 = (value - offset_position(previous['offset'], focus)) / h0 if before else 0
        d1 = (offset_position(following['offset'], focus) - value) / h1 if after else 0
        if not before:
            slope = d1
        elif not after:
            slope = d0
        elif d0 * d1 <= 0:
            slope = 0
        else:
            w0, w1 = 2 * h1 + h0, h1 + 2 * h0
            slope = (w0 + w1) / (w0 / d0 + w1 / d1)
        slopes.append(slope)
    return slopes


def hit_color(offset):
    neutral = (246, 238, 232)  # BGR
    target = (227, 213, 104) if offset < 0 else (118, 179, 244)
    amount = min(1, abs(offset) / 40)
    return tuple(round(base + (end - base) * amount) for base, end in zip(neutral, target))


class ReplayRenderer:
    width, height, top, bottom = 720, 1080, 39, 1051

    def __init__(self, data, span, fill=False, focus=40, width=720, pair_highlight=False):
        self.data, self.span = data, span
        self.fill = fill
        self.width = width
        self.pair_highlight = pair_highlight
        self.note_lookup = {(r['column'], r['time']): r for r in data['rows']}
        self.failed_pairs = {(r['column'], r['time']) for r in data.get('invalid_clicks', [])}
        self.focus = focus
        self.cell = (self.width - 24) / data['keys']
        self.hit_y = self.bottom - 8
        self.scale = (self.bottom - self.top) / span
        self.lanes = [[r for r in data['rows'] if r['column'] == i] for i in range(data['keys'])]
        self.times = [[r['time'] for r in lane] for lane in self.lanes]
        self.slopes = [curve_tangents(lane, focus) for lane in self.lanes]
        self.aux_lanes = [[r for r in data.get('auxiliary_curve', []) if r['column'] == i] for i in range(data['keys'])]
        self.aux_times = [[r['time'] for r in lane] for lane in self.aux_lanes]
        self.aux_slopes = [curve_tangents(lane, focus) for lane in self.aux_lanes]
        self.base_gaps = [min((b['time'] - a['time'] for a, b in zip(lane, lane[1:]) if b['time'] > a['time']), default=100) for lane in self.lanes]
        self.longs = [r for r in data['rows'] if r['ln']]
        self.unmatched = sorted(data.get('unmatched_clicks', []) + data.get('invalid_clicks', []), key=lambda r: r['time'])
        self.unmatched_times = [r['time'] for r in self.unmatched]
        self.pad = max(40, max((abs(r['offset']) for r in data['rows'] if r['offset'] is not None), default=40))
        self.base = np.zeros((self.height, self.width, 3), np.uint8)
        for i in range(data['keys']):
            center = 12 + (i + .5) * self.cell
            cv2.rectangle(self.base, (round(center - 2), self.top), (round(center + 1), self.bottom), (4, 4, 4), -1)
            for dx in [0, -(self.cell / 2 - 9) * .8, (self.cell / 2 - 9) * .8]:
                for yy in range(self.top, self.bottom, 7):
                    cv2.line(self.base, (round(center + dx), yy), (round(center + dx), yy + 3), (30, 36, 45), 1)
            cv2.line(self.base, (round(12 + i * self.cell), self.top), (round(12 + i * self.cell), self.bottom), (52, 65, 90))
            self.text(self.base, f'Lane {i + 1}', center, 13, centered=True)
            extent = (self.cell / 2 - 9) * .8
            for text, dx, color in [(f'-{focus:g}', -extent, (104, 213, 227)), ('0', 0, (159, 174, 197)), (f'+{focus:g}', extent, (244, 179, 118))]:
                self.text(self.base, text, center + dx, 29, color, centered=True)
            self.text(self.base, str(i + 1), center, 1071, centered=True)
        cv2.line(self.base, (self.width - 12, self.top), (self.width - 12, self.bottom), (52, 65, 90))

    @staticmethod
    def text(frame, text, x, y, color=(203, 213, 233), centered=False):
        font, size = cv2.FONT_HERSHEY_SIMPLEX, .3
        if centered:
            x -= cv2.getTextSize(text, font, size, 1)[0][0] / 2
        cv2.putText(frame, text, (round(x), round(y)), font, size, color, 1, cv2.LINE_AA)

    def y(self, time, cursor):
        return self.hit_y - (time - cursor) * self.scale

    def rect(self, frame, column, y0, y1, color, alpha=1):
        left, right = round(12 + column * self.cell + 5), round(12 + (column + 1) * self.cell - 5)
        upper, lower = max(self.top, math.floor(min(y0, y1))), min(self.bottom, math.ceil(max(y0, y1)))
        if lower > upper:
            roi = frame[upper:lower, left:right]
            if alpha == 1:
                roi[:] = color
            else:
                roi[:] = roi * (1 - alpha) + np.array(color) * alpha

    def stroke(self, frame, points, color, thickness, alpha=1):
        points = np.round(points).astype(np.int32)
        pad = math.ceil(thickness) + 3
        x0, y0 = max(12, int(points[:, 0].min()) - pad), max(self.top, int(points[:, 1].min()) - pad)
        x1, y1 = min(self.width - 12, int(points[:, 0].max()) + pad), min(self.bottom, int(points[:, 1].max()) + pad)
        if x1 <= x0 or y1 <= y0:
            return
        roi = frame[y0:y1, x0:x1]
        overlay = roi.copy()
        cv2.polylines(overlay, [points - (x0, y0)], False, color, max(1, round(thickness)), cv2.LINE_AA)
        cv2.addWeighted(overlay, alpha, roi, 1 - alpha, 0, dst=roi)

    def judgement_outline(self, frame, row, cursor):
        # 显示长度仅取 MAX 时间窗；颜色仍取实际头部判定。
        bound = self.data['windows'][0]
        left = round(12 + row['column'] * self.cell + 5)
        right = round(12 + (row['column'] + 1) * self.cell - 5)
        upper = round(self.y(row['time'] + bound, cursor))
        lower = round(self.y(row['time'] - bound, cursor))
        grade = row.get('head_judgement') or row['judgement']
        color = {'MAX': (255, 255, 255), '300': (255, 210, 55), '200': (70, 168, 21),
                 '100': (68, 143, 226), '50': (68, 143, 226)}.get(grade, (255, 98, 98))
        for yy in (upper, lower):
            if self.top <= yy < self.bottom:
                cv2.line(frame, (left, yy), (right, yy), color, 3, cv2.LINE_AA)
        side_colors = [color, color]
        y0, y1 = max(self.top, upper), min(self.bottom - 1, lower)
        if y0 <= y1:
            for xx, color in zip((left, right), side_colors):
                cv2.line(frame, (xx, y0), (xx, y1), color, 6, cv2.LINE_AA)

    def render(self, cursor):
        frame = self.base.copy()
        start = max(0, cursor - (self.bottom - self.hit_y) / self.scale)
        end = min(self.data['duration'], cursor + (self.hit_y - self.top) / self.scale)
        for row in self.longs:
            if row['time'] <= end and row['time'] + row['duration'] >= start:
                self.rect(frame, row['column'], self.y(row['time'] + row['duration'], cursor), self.y(row['time'], cursor), (145, 169, 202), .145)
        for bound, color in [(self.data['windows'][0], (184, 188, 197))]:
            if not self.fill:
                continue
            for lane, times in zip(self.lanes, self.times):
                for row in lane[bisect.bisect_left(times, start - bound):bisect.bisect_right(times, end + bound)]:
                    self.rect(frame, row['column'], self.y(row['time'] + bound, cursor), self.y(row['time'] - bound, cursor), color)
        for lane, times in zip(self.lanes, self.times):
            for row in lane[bisect.bisect_left(times, start):bisect.bisect_right(times, end)]:
                self.rect(frame, row['column'], self.y(row['time'], cursor) - 1, self.y(row['time'], cursor) + 1, (255, 255, 255), .667)
        bound = self.data['windows'][0]
        for lane, times in zip(self.lanes, self.times):
            for row in lane[bisect.bisect_left(times, start - bound):bisect.bisect_right(times, end + bound)]:
                self.judgement_outline(frame, row, cursor)
        for i, (lane, times, slopes) in enumerate(zip(self.lanes, self.times, self.slopes)):
            center, extent = 12 + (i + .5) * self.cell, self.cell / 2 - 9
            begin, finish = max(0, bisect.bisect_left(times, start - self.pad) - 1), min(len(lane), bisect.bisect_left(times, end + self.pad) + 1)
            x = lambda row: center + offset_position(row['offset'], self.focus) * extent
            for k in range(max(1, begin), finish):
                a, b = lane[k - 1], lane[k]
                if a['offset'] is None or b['offset'] is None:
                    continue
                gap = b['time'] - a['time']
                ay, by = self.y(a['time'], cursor), self.y(b['time'], cursor)
                controls = np.array([[x(a), ay], [x(a) + extent * slopes[k - 1] * gap / 3, ay + (by - ay) / 3],
                                     [x(b) - extent * slopes[k] * gap / 3, by - (by - ay) / 3], [x(b), by]])
                t = np.linspace(0, 1, min(96, max(4, math.ceil(abs(by - ay) / 12))))[:, None]
                points = (1 - t) ** 3 * controls[0] + 3 * (1 - t) ** 2 * t * controls[1] + 3 * (1 - t) * t ** 2 * controls[2] + t ** 3 * controls[3]
                width = max(.6, min(8, 8 * self.base_gaps[i] / max(gap, self.base_gaps[i])))
                def edge_color(offset):
                    mix = 1 - math.exp(-max(0, abs(offset) - self.focus) / (self.focus / 4))
                    target = (70, 160, 255) if offset < 0 else (255, 149, 65)
                    return np.array([round(v + (end - v) * mix) for v, end in zip((173, 197, 231), target)])
                ca, cb = edge_color(a['offset']), edge_color(b['offset'])
                if np.array_equal(ca, cb):
                    self.stroke(frame, points, tuple(int(v) for v in ca), width, .08 + .8 * math.exp(-max(0, gap - 100) / 220))
                    continue
                for j in range(1, len(points)):
                    fraction = (j - .5) / (len(points) - 1)
                    color = tuple(int(v) for v in np.round(ca + (cb - ca) * fraction))
                    self.stroke(frame, points[j-1:j+1], color, width, .08 + .8 * math.exp(-max(0, gap - 100) / 220))
            aux, aux_times, aux_slopes = self.aux_lanes[i], self.aux_times[i], self.aux_slopes[i]
            for k in range(max(1, bisect.bisect_left(aux_times, start) - 1), min(len(aux), bisect.bisect_right(aux_times, end) + 1)):
                a, b = aux[k - 1], aux[k]
                if not a['unmatched'] and not b['unmatched']:
                    continue
                gap = b['time'] - a['time']
                ay, by = self.y(a['time'], cursor), self.y(b['time'], cursor)
                controls = np.array([[x(a), ay], [x(a) + extent * aux_slopes[k - 1] * gap / 3, ay + (by - ay) / 3],
                                     [x(b) - extent * aux_slopes[k] * gap / 3, by - (by - ay) / 3], [x(b), by]])
                t = np.linspace(0, 1, min(512, max(8, math.ceil(abs(by - ay) / 3))))[:, None]
                points = (1-t)**3*controls[0]+3*(1-t)**2*t*controls[1]+3*(1-t)*t**2*controls[2]+t**3*controls[3]
                distance = 0
                for j in range(1, len(points)):
                    length = np.linalg.norm(points[j] - points[j-1])
                    if distance % 15 < 8:
                        fraction = (j-.5)/(len(points)-1)
                        da = a['discrepancy'] if a['discrepancy'] is not None else 40
                        db = b['discrepancy'] if b['discrepancy'] is not None else 40
                        mix = min(1, (da+(db-da)*fraction)/40)
                        color = (255, round(255-157*mix), round(255-157*mix))
                        self.stroke(frame, points[j-1:j+1], color, 3, .08+.8*math.exp(-max(0,gap-100)/220))
                    distance += length
            for row in aux[bisect.bisect_left(aux_times, start):bisect.bisect_right(aux_times, end)]:
                if row['unmatched']:
                    mix = min(1, (row['discrepancy'] if row['discrepancy'] is not None else 40)/40)
                    cv2.circle(frame, (round(x(row)), round(self.y(row['time'], cursor))), 4, (255, round(255-157*mix), round(255-157*mix)), 2, cv2.LINE_AA)
            for row in lane[begin:finish]:
                py = self.y(row['time'], cursor)
                if row['offset'] is None:
                    for sign in (-1, 1):
                        self.stroke(frame, [(center - 4, py - sign * 4), (center + 4, py + sign * 4)], (240, 138, 149), 1.7)
                    continue
                px = x(row)
                if self.top + 5 <= py < self.bottom - 5:
                    for radius, alpha in [(5, .05), (4, .08), (3, .15), (2, .65)]:
                        roi = frame[round(py) - 7:round(py) + 8, round(px) - 7:round(px) + 8]
                        overlay = roi.copy()
                        cv2.circle(overlay, (7, 7), radius, (255, 255, 255), 1, cv2.LINE_AA)
                        cv2.addWeighted(overlay, alpha, roi, 1 - alpha, 0, dst=roi)
                actual_y = self.y(row['actual'], cursor)
                marker_width, marker_height, cut = min(38, round(self.cell * .7)), 24, 5
                x0, x1 = round(center - marker_width / 2), round(center + marker_width / 2)
                y0, y1 = round(actual_y - marker_height / 2), round(actual_y + marker_height / 2)
                polygon = np.array([[x0 + cut, y0], [x1 - cut, y0], [x1, y0 + cut],
                                    [x1, y1 - cut], [x1 - cut, y1], [x0 + cut, y1],
                                    [x0, y1 - cut], [x0, y0 + cut]], dtype=np.int32)
                if self.top <= actual_y < self.bottom:
                    cv2.fillConvexPoly(frame, polygon, hit_color(row['offset']), cv2.LINE_AA)
                    cv2.polylines(frame, [polygon], True, (8, 12, 18), 2, cv2.LINE_AA)
                    cv2.line(frame, (round(center - marker_width * .27), round(actual_y)),
                             (round(center + marker_width * .27), round(actual_y)), (15, 20, 28), 2, cv2.LINE_AA)
        for click in self.unmatched[bisect.bisect_left(self.unmatched_times, start):bisect.bisect_right(self.unmatched_times, end)]:
            center = 12 + (click['column'] + .5) * self.cell
            py = self.y(click['time'], cursor)
            half = self.cell / 2 - 7
            color = (255, 66, 79) if click.get('early_miss') else (139, 146, 159)
            self.stroke(frame, [(center - half, py), (center - 8, py)], color, 2)
            self.stroke(frame, [(center + 8, py), (center + half, py)], color, 2)
            for sign in (-1, 1):
                self.stroke(frame, [(center - 4, py - sign * 4), (center + 4, py + sign * 4)], color, 2)
            if click.get('early_miss'):
                self.stroke(frame, [(center - half, py), (center - 8, py)], (255, 66, 79), 5)
                self.stroke(frame, [(center + 8, py), (center + half, py)], (255, 66, 79), 5)
                if self.top + 18 <= py < self.bottom:
                    self.text(frame, 'EARLY MISS', center, py - 12, (255, 120, 128), centered=True)
        if self.pair_highlight:
            for link in self.data.get('click_links', []):
                if link.get('estimated') or max(link['time'], link['reference_time'] + self.data['windows'][0]) < start or min(link['time'], link['reference_time'] - self.data['windows'][0]) > end:
                    continue
                self.highlight_pair(frame, link, cursor)
        cv2.line(frame, (12, round(self.hit_y)), (self.width - 12, round(self.hit_y)), (211, 220, 239), 2)
        self.text(frame, 'NOW', 15, self.hit_y - 9)
        self.text(frame, f'{cursor / 1000:.3f}s', 15, 1071, (170, 181, 199))
        return frame

    def highlight_pair(self, frame, link, cursor):
        column = link['column']
        note = self.note_lookup.get((column, link['reference_time']), {})
        failed = (column, link['time']) in self.failed_pairs
        grade = 'Miss' if failed else note.get('head_judgement') or note.get('judgement')
        color = {'MAX': (255, 255, 255), '300': (255, 210, 55), '200': (70, 168, 21),
                 '100': (68, 143, 226), '50': (68, 143, 226)}.get(grade, (255, 98, 98))
        center = 12 + (column + .5) * self.cell
        half = self.cell / 2 - 5
        upper = self.y(link['reference_time'] + self.data['windows'][0], cursor)
        lower = self.y(link['reference_time'] - self.data['windows'][0], cursor)
        actual = self.y(link['time'], cursor)
        source = upper if actual < upper else lower if actual > lower else self.y(link['reference_time'], cursor)
        marker = min(38, round(self.cell * .7))
        hit_half = self.cell / 2 - 7 if failed else marker / 2 + 3
        target = actual if failed else actual + (15 if actual < source else -15)
        y0 = max(self.top, math.floor(min(upper, lower, source, target)))
        y1 = min(self.bottom, math.ceil(max(upper, lower, source, target)) + 1)
        x0, x1 = max(12, math.floor(center-half)), min(self.width-12, math.ceil(center+half)+1)
        if y1 > y0 and x1 > x0:
            roi = frame[y0:y1, x0:x1]
            mask = np.zeros(roi.shape[:2], np.uint8)
            for polygon in [[(center-half,upper),(center+half,upper),(center+half,lower),(center-half,lower)],
                            [(center-half,source),(center+half,source),(center+hit_half,target),(center-hit_half,target)]]:
                points = np.round(np.array(polygon) - (x0,y0)).astype(np.int32)
                cv2.fillConvexPoly(mask, points, 255)
            roi[mask>0] = roi[mask>0] * .82 + np.array(color) * .18
        outline = [(center-half,upper),(center+half,upper),(center+half,lower),(center-half,lower),(center-half,upper)]
        if failed:
            hit = [(center-hit_half,actual),(center+hit_half,actual)]
        else:
            l,r,t,b,cut = center-hit_half,center+hit_half,actual-15,actual+15,6
            hit = [(l+cut,t),(r-cut,t),(r,t+cut),(r,b-cut),(r-cut,b),(l+cut,b),(l,b-cut),(l,t+cut),(l+cut,t)]
        for points in (outline, hit):
            self.stroke(frame, points, color, 9, .2)
            self.stroke(frame, points, color, 4)
