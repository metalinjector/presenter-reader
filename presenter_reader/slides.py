"""Сборка итогового списка слайдов из «прогонов».

1. Отбрасываем прогоны короче ``min_duration`` — это кадры перехода.
2. Отбрасываем «неподвижные» моменты внутри периодов, когда в области было
   живое видео (ведущий на весь экран): вокруг такого прогона почти всё время
   занято очень короткими прогонами, а сама картинка не похожа на экран
   (мало ровных участков).
3. Соседние одинаковые прогоны склеиваем (картинку на миг перекрыло что-то
   постороннее). Пошаговую анимацию (каждый следующий прогон содержит всё
   содержимое предыдущего) склеиваем в один слайд с итоговым кадром.
4. Для каждого слайда ищем самый похожий из ранее показанных. Повторный показ
   сохраняется ещё раз, но с пометкой «повтор N, сходство X %».
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .imageops import Comparator
from .tracker import Run


@dataclass
class Slide:
    number: int
    run: Run                                  # чей кадр сохраняем
    shows: list = field(default_factory=list)  # [(начало, конец), …]
    states: int = 1                           # сколько шагов анимации склеено
    repeat_of: int | None = None
    similarity: float | None = None

    @property
    def t_start(self) -> float:
        return self.shows[0][0]

    @property
    def t_end(self) -> float:
        return self.shows[-1][1]

    @property
    def shown_for(self) -> float:
        return sum(b - a for a, b in self.shows)


def _instability(runs: list[Run], t0: float, t1: float, short: float) -> float:
    """Доля времени в [t0, t1], занятая очень короткими прогонами."""
    if t1 <= t0:
        return 0.0
    tot = 0.0
    for r in runs:
        if r.t_end <= t0 or r.t_start >= t1:
            continue
        if r.duration < short:
            tot += min(r.t_end, t1) - max(r.t_start, t0)
    return tot / (t1 - t0)


def _is_live_video(run: Run, runs: list[Run], window: float = 4.0, short: float = 1.0,
                   still: float = 4.0) -> bool:
    """Короткая «пауза» посреди живого видео (ведущий на весь экран замер на секунду).

    Вокруг такой паузы почти всё время занято очень короткими прогонами (картинка
    меняется непрерывно). Паузы дольше ``still`` секунд оставляем: так выглядит,
    например, остановка при прокрутке документа.
    """
    if run.duration >= still:
        return False
    before = _instability(runs, run.t_start - window, run.t_start, short)
    after = _instability(runs, run.t_end, run.t_end + window, short)
    return min(before, after) >= 0.5 or (run.busy_frac >= 0.5 and max(before, after) >= 0.5)


def assemble(runs: list[Run], cmp: Comparator, min_duration: float, merge_builds: bool = True,
             similar_threshold: float = 80.0, merge_gap: float = 10.0) -> list[Slide]:
    cands = [r for r in runs if r.duration >= min_duration and r.image_path]
    cands = [r for r in cands if not _is_live_video(r, runs)]

    groups: list[Slide] = []
    for r in cands:
        g = groups[-1] if groups else None
        if g is not None and r.t_start - g.t_end <= merge_gap:
            mask = _mask(g.run, r)
            if cmp.is_same(r.ref, g.run.ref, mask):
                g.shows.append((r.t_start, r.t_end))
                if r.duration > g.run.duration:   # кадр берём из более долгого показа
                    g.run = r
                continue
            if merge_builds and cmp.contains(r.ref, g.run.ref, mask):
                g.shows.append((r.t_start, r.t_end))  # добавились элементы — шаг анимации
                g.run = r
                g.states += 1
                continue
            if merge_builds and cmp.contains(g.run.ref, r.ref, mask):
                g.shows.append((r.t_start, r.t_end))  # шаг назад в анимации
                g.states += 1
                continue
        groups.append(Slide(0, r, [(r.t_start, r.t_end)]))

    for i, s in enumerate(groups):
        s.number = i + 1
        best, best_sim = None, -1.0
        for prev in groups[:i]:
            sim = cmp.similarity(s.run.ref, prev.run.ref, _mask(prev.run, s.run))
            if sim > best_sim:
                best, best_sim = prev, sim
        if best is not None and best_sim >= similar_threshold:
            s.repeat_of, s.similarity = best.number, best_sim
    return groups


def _mask(a: Run, b: Run):
    if a.mask is None:
        return b.mask
    if b.mask is None:
        return a.mask
    return a.mask & b.mask
