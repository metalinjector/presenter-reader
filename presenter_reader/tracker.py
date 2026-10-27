"""Отслеживание смен картинки внутри найденной области.

Поток выборок (моменты времени + кадры) режется на «прогоны» — отрезки, на
которых картинка в области презентации не меняется (с допуском на курсор и
шум сжатия). Каждый прогон сравнивается со своим **первым** кадром, а не с
предыдущим: так медленное накопление изменений (ведущий рисует пометки по
штриху) тоже рано или поздно даст новый прогон.

Для скриншота прогона сохраняется несколько полноцветных кадров, взятых в
разные моменты (номера выборок 0, 1, 2, 4, 8, 16, …, хранятся три последних).
Опорные кадры (I-кадры) в приоритете: в промежуточных кадрах после плавного
перехода сжатие ещё несколько секунд держит бледный след прошлого слайда.
Итоговое изображение — поэлементная медиана: курсор, который двигался,
пропадает, а шум сжатия усредняется.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import cv2
import numpy as np

from .imageops import Comparator, downscale, median_image
from .layout import Layout
from .video import Frame


@dataclass
class Run:
    idx: int
    t_start: float
    t_last: float
    ref: np.ndarray
    mask: np.ndarray | None
    n: int = 1
    n_busy: int = 0
    t_end: float = 0.0
    caps: list = field(default_factory=list)       # кадры для скриншота (любые)
    key_caps: list = field(default_factory=list)   # кадры для скриншота (опорные)
    n_key: int = 0
    first: list = field(default_factory=list)   # первые выборки — для эталона без курсора
    image_path: str | None = None
    flat: float = 1.0

    @property
    def duration(self) -> float:
        return self.t_end - self.t_start

    @property
    def busy_frac(self) -> float:
        return self.n_busy / max(1, self.n)


class Analyzer:
    """Готовит «аналитическую» картинку области и маску исключаемых зон."""

    def __init__(self, layout: Layout, width: int = 640, block: int = 8):
        self.layout = layout
        x, y, w, h = layout.rect
        self.rect = (x, y, w, h)
        aw = max(block * 8, min(width, w) // block * block)
        ah = max(block * 4, int(round(aw * h / w / block)) * block)
        self.size = (aw, ah)
        self._mask_cache: dict[bytes, np.ndarray | None] = {}

    def image(self, frame: Frame) -> np.ndarray:
        x, y, w, h = self.rect
        crop = frame.luma()[y:y + h, x:x + w]
        small = downscale(crop, self.size)
        # лёгкое размытие гасит «звон» сжатия на краях букв (разница I- и P-кадров)
        return cv2.GaussianBlur(small, (3, 3), 0)

    def mask(self, t: float) -> tuple[np.ndarray | None, bool]:
        blocks, busy = self.layout.dynamic_blocks_at(t)
        if blocks is None or not blocks.any():
            return None, busy
        key = blocks.tobytes()
        if key not in self._mask_cache:
            self._mask_cache[key] = self._to_mask(blocks)
        return self._mask_cache[key], busy

    def _to_mask(self, blocks: np.ndarray) -> np.ndarray | None:
        lay = self.layout
        b = lay.block
        px = np.kron(blocks.astype(np.uint8), np.ones((b, b), np.uint8))
        x0, y0, x1, y1 = lay.survey_rect
        crop = px[y0:y1, x0:x1]
        if crop.size == 0 or not crop.any():
            return None
        m = cv2.resize(crop, self.size, interpolation=cv2.INTER_NEAREST) == 0
        return m

    def crop_color(self, frame: Frame) -> np.ndarray:
        x, y, w, h = self.rect
        return frame.bgr()[y:y + h, x:x + w].copy()


def _combine(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return a & b


def flatness(img: np.ndarray) -> float:
    """Доля «ровных» пикселей. У слайдов и экранов высокая, у камеры низкая."""
    g = cv2.morphologyEx(img, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
    return float((g <= 3).mean())


class Tracker:
    def __init__(self, analyzer: Analyzer, comparator: Comparator, tmp_dir: str,
                 min_duration: float, keep_caps: int = 3):
        self.an = analyzer
        self.cmp = comparator
        self.tmp_dir = tmp_dir
        self.min_duration = min_duration
        self.keep_caps = keep_caps
        self.runs: list[Run] = []
        self.cur: Run | None = None
        self.last_t: float | None = None
        # небольшое изменение «под подозрением»: курсор или правка? (t, a, mask, busy, frame, diff)
        self.pending = None
        os.makedirs(tmp_dir, exist_ok=True)
        self._writer = ThreadPoolExecutor(max_workers=2)   # PNG пишем в фоне
        self._writes = []

    def push(self, frame: Frame, a: np.ndarray | None = None) -> None:
        t = frame.t
        if self.last_t is not None and t <= self.last_t + 1e-6:
            return  # повтор или выборка из прошлого
        self.last_t = t
        if a is None:
            a = self.an.image(frame)
        mask, busy = self.an.mask(t)
        cur = self.cur
        if cur is None:
            self._open(t, a, mask, busy, frame)
            return
        d = self.cmp.diff(a, cur.ref, _combine(mask, cur.mask))
        if self.cmp.same(d):
            self.pending = None
            self._extend(cur, t, a, busy, frame)
            return
        if self.cmp.minor(d):
            # Небольшое отличие. Движущийся курсор каждый раз в новом месте, а
            # настоящая правка (новое слово, пометка) остаётся. Решаем по
            # следующей выборке: изменение подтвердилось — начинаем новый прогон
            # с момента его появления.
            p = self.pending
            if p is not None and self._persists(p, a, mask):
                self.pending = None
                self._close(p[0])
                self._open(*p)
                self._extend(self.cur, t, a, busy, frame)
                return
            self.pending = (t, a, mask, busy, frame, d)
            cur.n += 1
            cur.n_busy += busy
            cur.t_last = t
            return
        self.pending = None
        self._close(t)
        self._open(t, a, mask, busy, frame)

    def _persists(self, p, a, mask) -> bool:
        """Изменение из выборки ``p`` осталось на месте в выборке ``a``."""
        region = cv2.dilate(p[5].changed.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
        d = self.cmp.diff(a, p[1], _combine(mask, p[2]))
        n_region = int(region.sum())
        return n_region > 0 and int((d.changed & region).sum()) <= 0.3 * n_region

    def _open(self, t, a, mask, busy, frame, *_):
        run = Run(len(self.runs), t, t, a, mask, n_busy=int(busy), first=[a])
        self._capture(run, frame, 0)
        self.cur = run

    def _extend(self, cur: Run, t, a, busy, frame) -> None:
        cur.n += 1
        cur.n_busy += busy
        cur.t_last = t
        if cur.first:
            cur.first.append(a)
            if len(cur.first) == 3:
                # эталон — медиана первых трёх выборок: убирает курсор и
                # «хвост» плавного перехода, если они попали в первую выборку
                cur.ref = median_image(cur.first)
                cur.first = []
        self._capture(cur, frame, cur.n - 1)

    def _capture(self, run: Run, frame: Frame, k: int) -> None:
        """Кадры для скриншота: выборки № 0, 1, 2, 4, 8, … и так же опорные кадры."""
        if frame.is_key:
            j = run.n_key
            run.n_key += 1
            if j & (j - 1) == 0:
                run.key_caps.append(self.an.crop_color(frame))
                if len(run.key_caps) > self.keep_caps:
                    run.key_caps.pop(0)
            return
        if run.key_caps:
            return  # опорные кадры уже есть — промежуточные не нужны
        if k & (k - 1) == 0:
            run.caps.append(self.an.crop_color(frame))
            if len(run.caps) > self.keep_caps:
                run.caps.pop(0)

    def _close(self, t_end: float) -> None:
        run = self.cur
        if run is None:
            return
        run.t_end = t_end
        run.flat = flatness(run.ref)
        caps = run.key_caps or run.caps
        if run.duration >= self.min_duration and caps:
            caps = caps if len(caps) >= 3 else caps[-1:]
            img = median_image(caps)
            path = os.path.join(self.tmp_dir, f"run_{run.idx:06d}.png")
            self._writes.append(self._writer.submit(
                cv2.imwrite, path, img, [cv2.IMWRITE_PNG_COMPRESSION, 3]))
            run.image_path = path
        run.caps = []
        run.key_caps = []
        run.first = []
        self.runs.append(run)
        self.cur = None

    def abort(self) -> None:
        """Остановка без результата: дождаться фоновой записи и закрыть пул."""
        for f in self._writes:
            f.exception()
        self._writes = []
        self._writer.shutdown()

    def finish(self, t_end: float) -> list[Run]:
        if self.cur is not None:
            self._close(max(t_end, self.cur.t_last))
        for f in self._writes:
            f.result()
        self._writes = []
        self._writer.shutdown()
        return self.runs
