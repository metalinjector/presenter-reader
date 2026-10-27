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
5. В режиме «без дублей» (``drop_duplicates``) повтор, почти неотличимый от уже
   сохранённого слайда, не сохраняется: его показы добавляются к исходному слайду.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .imageops import Comparator, block_sum, structure_map
from .tracker import Run


@dataclass
class Slide:
    number: int
    run: Run                                  # чей кадр сохраняем
    shows: list = field(default_factory=list)  # [(начало, конец), …]
    states: int = 1                           # сколько шагов анимации склеено
    repeat_of: int | None = None
    similarity: float | None = None
    before: Run | None = None                 # состояние до последнего «шага анимации»
    appearances: int = 1                      # сколько раз показан (с учётом влитых дублей)
    best_ref: Slide | None = field(default=None, repr=False)   # самый похожий из прежних
    best_sim: float | None = None

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


def _is_blank(run: Run, cmp: Comparator) -> bool:
    """Пустая картинка (чёрная заставка, белый экран) — не слайд."""
    s = structure_map(run.ref)
    if run.mask is not None:
        s &= run.mask
    return int((block_sum(s, cmp.block) >= cmp.min_px).sum()) < 3


def assemble(runs: list[Run], cmp: Comparator, min_duration: float, merge_builds: bool = True,
             similar_threshold: float = 80.0, merge_gap: float = 10.0) -> list[Slide]:
    cands = [r for r in runs if r.duration >= min_duration and r.image_path]
    cands = [r for r in cands if not _is_live_video(r, runs) and not _is_blank(r, cmp)]

    groups: list[Slide] = []
    for r in cands:
        g = groups[-1] if groups else None
        if g is not None and r.t_start - g.t_end <= merge_gap:
            mask = _mask(g.run, r)
            d = cmp.diff(r.ref, g.run.ref, mask)
            # мелкое отличие (остановившийся курсор, след перехода) — тот же слайд
            if cmp.same(d) or cmp.minor(d):
                g.shows.append((r.t_start, r.t_end))
                if r.duration > g.run.duration:   # кадр берём из более долгого показа
                    g.run = r
                continue
            if merge_builds and cmp.contains(r.ref, g.run.ref, mask):
                g.shows.append((r.t_start, r.t_end))  # добавились элементы — шаг анимации
                g.before, g.run = g.run, r
                g.states += 1
                continue
            if merge_builds and cmp.contains(g.run.ref, r.ref, mask):
                g.shows.append((r.t_start, r.t_end))  # шаг назад
                g.states += 1
                # Вернулись ровно к прежнему состоянию — значит, «добавка» была временной
                # (всплывающее уведомление, подсказка), а не шагом анимации: для
                # скриншота берём чистое состояние.
                b = g.before
                if b is not None and cmp.is_same(r.ref, b.ref, _mask(b, r)):
                    g.run = r if r.duration > b.duration else b
                    g.before = None
                    g.states -= 2
                continue
        groups.append(Slide(0, r, [(r.t_start, r.t_end)]))

    # Повторы: сравниваем с каждым ранее показанным слайдом. Чтобы на сотнях
    # слайдов не делать N² полных сравнений, сначала отбираем кандидатов по
    # крошечным миниатюрам 64×36, а точный процент сходства считаем для трёх
    # самых близких.
    thumbs = np.stack([cv2.resize(g.run.ref, (64, 36), interpolation=cv2.INTER_AREA)
                       for g in groups]).astype(np.int16) if groups else None
    for i, s in enumerate(groups):
        s.number = i + 1
        if i == 0:
            continue
        dist = np.abs(thumbs[:i] - thumbs[i]).mean(axis=(1, 2))
        best, best_sim = None, -1.0
        for j in np.argsort(dist)[:3]:
            if dist[j] > 40:
                break
            prev = groups[int(j)]
            sim = cmp.similarity(s.run.ref, prev.run.ref, _mask(prev.run, s.run))
            if sim > best_sim:
                best, best_sim = prev, sim
        if best is not None:
            s.best_ref, s.best_sim = best, best_sim
            if best_sim >= similar_threshold:
                s.repeat_of, s.similarity = best.number, best_sim
    return groups


def drop_duplicates(slides: list[Slide], threshold: float = 95.0) -> tuple[list[Slide], int]:
    """Режим «без дублей»: не сохранять повторно показанный слайд, если он почти
    не отличается от уже сохранённого (сходство ≥ ``threshold`` %).

    Время такого повторного показа добавляется к исходному слайду (``shows``,
    ``appearances``). Повторы с заметными отличиями остаются с пометкой, номера
    слайдов пересчитываются подряд. Возвращает (оставшиеся слайды, сколько пропущено).
    """
    merged_into: dict[int, Slide] = {}

    def canon(x: Slide | None) -> Slide | None:
        while x is not None and id(x) in merged_into:
            x = merged_into[id(x)]
        return x

    kept: list[Slide] = []
    for s in slides:
        orig = canon(s.best_ref)   # исходный мог и сам оказаться влитым дублем
        if orig is not None and s.best_sim is not None and s.best_sim >= threshold:
            orig.shows = sorted(orig.shows + s.shows)
            orig.appearances += s.appearances
            merged_into[id(s)] = orig
            continue
        s.best_ref = orig
        kept.append(s)
    for i, s in enumerate(kept, 1):
        s.number = i
    for s in kept:
        if s.repeat_of is not None and s.best_ref is not None:
            s.repeat_of = s.best_ref.number
    return kept, len(slides) - len(kept)


def _mask(a: Run, b: Run):
    if a.mask is None:
        return b.mask
    if b.mask is None:
        return a.mask
    return a.mask & b.mask
