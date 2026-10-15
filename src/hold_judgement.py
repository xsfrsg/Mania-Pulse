"""osu!stable 长条的输入状态与结算；原始输入与结算时刻分开保存。"""
from dataclasses import dataclass, field


def hit_grade(error, windows, multiplier=1):
    """只在比较判定时取整，不分别截断起点和持续时间。"""
    error = round(abs(error))
    return next((i for i, bound in enumerate(windows[:-1]) if error <= int(bound * multiplier)), -1)


@dataclass
class HoldJudgement:
    note: object
    windows: list
    score_v2: bool = False
    phase: str = 'waiting'
    head_grade: int | None = None
    head_error: float | None = None
    regrab_error: float | None = None
    attempts: int = 0
    broken: bool = False
    settled_at: float | None = None
    release_time: float | None = None
    effective_tail_error: float | None = None
    final_grade: int = -1
    tail_grade: int = -1
    pending: bool = False
    transitions: list = field(default_factory=list)

    def record(self, time, phase, event):
        self.phase = phase
        self.transitions.append(dict(time=time, state=phase, event=event))

    def accept(self, action):
        """消费一段真实按住区间；未松键时 finish 仅为最后观测时刻。"""
        if self.settled_at is not None:
            return
        w, note = self.windows, self.note
        deadline = note.finish + w[3] * (1.5 if self.score_v2 else 1)
        if action.time > deadline:
            return
        self.attempts += 1
        error = abs(action.time - note.time)
        if self.head_grade is None:
            self.head_grade = hit_grade(error, w)
            self.head_error = error
        self.regrab_error = round(error)
        self.record(action.time, 'holding', 'press' if self.attempts == 1 else 'repress')
        known = action.release_known
        self.release_time = action.finish if known else None
        horizon = action.finish
        if not known and horizon < deadline:
            self.pending = True
            self.record(horizon, 'holding', 'observation_end')
            return
        self.pending = False
        # ScoreV2 的尾部窗口为 1.5 倍；ScoreV1 不把该倍数套到合并公式。
        early_window = w[4] * (1.5 if self.score_v2 else 1)
        if known and action.finish < note.finish - early_window:
            if action.finish >= note.time:
                self.broken = True
            self.record(action.finish, 'broken' if self.broken else 'waiting', 'release_before_tail')
            return
        auto = horizon > deadline or not known
        self.settled_at = deadline if auto else action.finish
        # 实测 stable 的持续持有结算：不把任意久以后的真实松键当成尾判误差。
        # 此误差限制是回放验证的重建规则，并非宣称已取得官方逐音符结果。
        tail_error = w[4] if auto else round(abs(action.finish - note.finish))
        self.effective_tail_error = tail_error
        if self.score_v2:
            tail_error = int(w[3] * 1.5) if auto else round(abs(action.finish - note.finish))
            self.effective_tail_error = tail_error
            self.tail_grade = hit_grade(tail_error, w, 1.5)
            if self.broken or self.attempts > 1 or self.head_grade < 0:
                if self.tail_grade >= 0:
                    self.tail_grade = max(4, self.tail_grade)
            self.final_grade = self.head_grade
        else:
            # 显示的首次首误差保持锁定；stable 重按恢复仍使用当前段误差。
            start_error = self.regrab_error
            if action.time < note.time - w[4]:
                start_error = max(0, note.duration - 1)
            combined = start_error + tail_error
            self.final_grade = next((i for i, factor in enumerate((1.2, 1.1, 1, 1))
                                     if start_error <= w[i] * factor and combined <= w[i] * factor * 2), 4)
            if self.attempts > 1 and self.final_grade in (0, 1):
                self.final_grade = 2
        self.record(self.settled_at, 'settled', 'held_timeout' if auto else 'release')

    def finish(self):
        if self.score_v2 and self.head_grade is not None:
            self.final_grade = self.head_grade
        if self.settled_at is None and not self.pending:
            self.record(self.note.finish + self.windows[3] * (1.5 if self.score_v2 else 1),
                        'missed', 'tail_not_held')
        return self.final_grade
