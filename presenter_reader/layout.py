"""Поиск области презентации: «обзорный» проход по видео.

Идея. Каждая точка кадра ведёт себя по-своему:

* **камера ведущего** меняется почти между любыми двумя соседними выборками;
* **статичный интерфейс** (рамки, логотипы, список участников) не меняется никогда;
* **слайды** подолгу неподвижны, но иногда меняются — причём **все точки
  слайда меняются одновременно** (в момент смены слайда);
* **чат** тоже меняется редко, но в свои моменты, не совпадающие со слайдами.

Поэтому:

1. По выборкам (опорным кадрам) строим для каждой пары соседних выборок карту
   изменённых блоков 12×12 пикселей (в масштабе 480 точек по ширине).
2. Блок, который меняется «постоянно» (в ≥3 из 5 соседних пар), — динамический
   (камера, видео). Динамические зоны дополняем до прямоугольников: окно камеры
   прямоугольное, даже если человек двигается только в его части.
3. Оставшиеся изменения — «события». Соседние блоки, меняющиеся в одни и те же
   моменты, объединяем в кластеры, а кластеры с совпадающей историей событий —
   в группы. Слайды дают одну крупную группу, чат — отдельную.
4. Берём группу с наибольшей площадью охватывающего прямоугольника и подгоняем
   его края к видимой границе показа (резкий перепад яркости, за которым — ровный
   фон) по медианному кадру.

Если смен слайдов не было вовсе, берём самый большой неподвижный прямоугольник
с содержимым (максимальная сумма по карте «текстуры»).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from .imageops import block_mean, block_sum, downscale, max_sum_rectangle, structure_map

_K3 = np.ones((3, 3), np.uint8)


@dataclass
class Layout:
    frame_w: int
    frame_h: int
    rect: tuple[int, int, int, int]           # x, y, w, h в пикселях кадра
    method: str                               # events | static | manual | full
    sw: int = 0                               # размер обзорного кадра
    sh: int = 0
    block: int = 12
    times: np.ndarray | None = None           # моменты выборок (K+1)
    dyn: np.ndarray | None = None             # K×bh×bw: динамика по парам
    glob: np.ndarray | None = None            # bh×bw: динамично почти всегда
    debug: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    @property
    def survey_rect(self) -> tuple[int, int, int, int]:
        """Прямоугольник в координатах обзорного кадра (x0, y0, x1, y1)."""
        x, y, w, h = self.rect
        kx, ky = self.sw / self.frame_w, self.sh / self.frame_h
        return (int(math.floor(x * kx)), int(math.floor(y * ky)),
                int(math.ceil((x + w) * kx)), int(math.ceil((y + h) * ky)))

    def _rect_blocks(self) -> np.ndarray:
        bh, bw = self.sh // self.block, self.sw // self.block
        m = np.zeros((bh, bw), bool)
        x0, y0, x1, y1 = self.survey_rect
        b = self.block
        m[y0 // b: -(-y1 // b), x0 // b: -(-x1 // b)] = True
        return m

    def _pair_index(self, t: float) -> int | None:
        if self.times is None or self.dyn is None or len(self.dyn) == 0:
            return None
        k = int(np.searchsorted(self.times, t, side="right")) - 1
        return min(max(k, 0), len(self.dyn) - 1)

    def dynamic_blocks_at(self, t: float) -> tuple[np.ndarray | None, bool]:
        """Какие блоки исключить из сравнения в момент ``t`` и «живая» ли сейчас область.

        Всегда исключаем постоянно динамичные зоны (камера поверх слайда).
        Временные динамичные зоны (GIF, видео в углу слайда) исключаем, только
        если они небольшие; если «живёт» большая часть области (ведущий на весь
        экран, быстрое листание), маску не трогаем — это решают дальше.
        """
        if self.glob is None:
            return None, False
        k = self._pair_index(t)
        if k is None:
            return self.glob, False
        rb = self._rect_blocks()
        temp = self.dyn[k] & rb & ~self.glob
        area = max(1, int((rb & ~self.glob).sum()))
        busy = temp.sum() / area > 0.25
        return (self.glob if busy else (self.glob | self.dyn[k])), bool(busy)


class Survey:
    """Накопитель статистики по выборкам видео (обычно — опорным кадрам).

    Основные выборки (``kind="key"``) дают «долгие» пары — между соседними
    выборками: по ним видно смены слайдов. Выборки ``kind="burst"`` (кадр через
    ~0,6 с после основного) дают «короткие» пары: за полсекунды слайд почти
    никогда не меняется, а камера — почти всегда. Так «живые» зоны отделяются
    от слайдов, даже если слайды листают часто.
    """

    def __init__(self, frame_w: int, frame_h: int, width: int = 480, block: int = 12,
                 tau: int = 20, ev_frac: float = 0.05, keep: int = 48,
                 full_times: list[float] | None = None):
        self.frame_w, self.frame_h = frame_w, frame_h
        self.block = block
        sw = max(block * 8, min(width, frame_w) // block * block)
        sh = max(block * 4, int(math.floor(sw * frame_h / frame_w / block + 0.5)) * block)
        self.sw, self.sh = sw, sh
        self.bw, self.bh = sw // block, sh // block
        self.tau = tau
        self.ev_px = max(1, int(round(ev_frac * block * block)))
        self.keep = keep
        self.times: list[float] = []
        self.events: list[np.ndarray] = []
        self.burst_times: list[float] = []
        self.bursts: list[np.ndarray] = []
        self.act = np.zeros((sh, sw), np.uint32)        # «долгие» изменения пикселя
        self.act_short = np.zeros((sh, sw), np.uint32)  # «короткие» изменения пикселя
        self.samples: list[np.ndarray] = []
        self.stride = 1
        self.n = 0
        self.prev: np.ndarray | None = None
        self.prev_t: float | None = None
        self.full_times = sorted(full_times or [])
        self.full: list[np.ndarray] = []      # несколько кадров в полном размере

    def _small(self, luma: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        raw = downscale(luma, (self.sw, self.sh))
        return raw, cv2.GaussianBlur(raw, (3, 3), 0)

    def _blocks(self, ch: np.ndarray) -> np.ndarray:
        return block_sum(ch, self.block) >= self.ev_px

    def add(self, t: float, luma: np.ndarray, kind: str = "key") -> None:
        raw, s = self._small(luma)
        if kind == "burst":
            if self.prev is not None and t - self.prev_t <= 2.5:
                ch = cv2.absdiff(s, self.prev) > self.tau
                self.act_short += ch
                self.burst_times.append(self.prev_t)
                self.bursts.append(self._blocks(ch))
            return
        if self.prev is not None:
            ch = cv2.absdiff(s, self.prev) > self.tau
            self.act += ch
            self.events.append(self._blocks(ch))
            if t - self.prev_t <= 1.5:   # опорные кадры часто — пара годится и как «короткая»
                self.act_short += ch
                self.burst_times.append(self.prev_t)
                self.bursts.append(self.events[-1])
        self.times.append(t)
        if self.full_times and t >= self.full_times[0]:
            self.full.append(luma.copy())
            while self.full_times and t >= self.full_times[0]:
                self.full_times.pop(0)
        if self.n % self.stride == 0:
            self.samples.append(raw)
            if len(self.samples) >= 2 * self.keep:
                self.samples = self.samples[::2]
                self.stride *= 2
        self.n += 1
        self.prev, self.prev_t = s, t

    # ------------------------------------------------------------------
    def analyze(self, roi: tuple[int, int, int, int] | None = None) -> Layout:
        lay = Layout(self.frame_w, self.frame_h, (0, 0, self.frame_w, self.frame_h), "full",
                     self.sw, self.sh, self.block)
        if self.n == 0:
            return lay
        stack = np.stack(self.samples)
        bg = np.median(stack, axis=0).astype(np.uint8)
        # медианное отклонение: устойчиво к периодам «ведущий на весь экран»
        mad = np.median(np.abs(stack.astype(np.int16) - bg.astype(np.int16)), axis=0).astype(np.float32)
        lay.debug.update(bg=bg, mad=mad)
        lay.times = np.asarray(self.times)

        K = len(self.events)
        E = np.stack(self.events) if K else np.zeros((0, self.bh, self.bw), bool)
        D, G = _dynamic_maps(E, lay.times, self.burst_times, self.bursts)
        lay.dyn, lay.glob = D, G
        lay.debug.update(events=E, glob=G, n_bursts=len(self.bursts))

        # Точная (попиксельная) карта постоянно «живых» окон — для подгонки краёв.
        # Блочная карта G надёжно находит окна камер, но грубая (блок 12 px и запас
        # в блок). Уточняем: внутри G берём пиксели, которые хоть иногда менялись,
        # и каждое пятно дополняем до прямоугольника.
        if len(self.bursts) >= 3:
            act = self.act_short.astype(np.float32) / len(self.bursts)
        else:
            act = self.act.astype(np.float32) / max(1, K)
        b = self.block
        g_px = np.kron(G.astype(np.uint8), np.ones((b, b), np.uint8)) > 0 if G is not None \
            else np.zeros(act.shape, bool)
        live = (g_px & (act >= 0.1)).astype(np.uint8)
        live = cv2.morphologyEx(live, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
        dyn_px = _rectangularize(live, min_fill=0.0) if live.any() else live > 0
        lay.debug["dyn_px"] = dyn_px

        if roi is not None:
            lay.rect, lay.method = roi, "manual"
            return lay

        rect_b = _event_region(E, D, G, lay.debug) if K >= 2 else None
        if rect_b is not None:
            method = "events"
        else:
            rect_b = _static_region(bg, G, self.block)
            method = "static" if rect_b is not None else "full"
        if rect_b is None:
            return lay
        b = self.block
        x0, y0, x1, y1 = rect_b[0] * b, rect_b[1] * b, rect_b[2] * b, rect_b[3] * b
        changed = self.act > 0
        lay.debug["changed"] = changed
        x0, y0, x1, y1 = _refine_edges(bg.astype(np.float32), mad, dyn_px, changed,
                                       (x0, y0, x1, y1), b)
        kx, ky = self.frame_w / self.sw, self.frame_h / self.sh
        X0, Y0 = int(round(x0 * kx)), int(round(y0 * ky))
        X1, Y1 = int(round(x1 * kx)), int(round(y1 * ky))
        if self.full:
            full_bg = np.median(np.stack(self.full), axis=0).astype(np.float32)
            X0, Y0, X1, Y1 = _snap_full(full_bg, (X0, Y0, X1, Y1), int(math.ceil(max(kx, ky))) + 1)
        X0, Y0 = max(0, X0), max(0, Y0)
        X1, Y1 = min(self.frame_w, X1), min(self.frame_h, Y1)
        lay.rect = (X0, Y0, X1 - X0, Y1 - Y0)
        lay.method = method
        lay.debug["rect_blocks"] = rect_b
        return lay


# ---------------------------------------------------------------- динамика

def _window_count(E: np.ndarray, r: int) -> np.ndarray:
    """Для каждой пары k: сколько раз блок менялся в парах k−r..k+r."""
    c = np.cumsum(np.concatenate([np.zeros((1,) + E.shape[1:], np.int32),
                                  E.astype(np.int32)]), axis=0)
    K = len(E)
    hi = np.minimum(np.arange(K) + r + 1, K)
    lo = np.maximum(np.arange(K) - r, 0)
    return c[hi] - c[lo]


def _rectangularize(m: np.ndarray, min_fill: float = 0.25) -> np.ndarray:
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), connectivity=8)
    out = np.zeros(m.shape, bool)
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if a >= min_fill * w * h:
            out[y:y + h, x:x + w] = True
        else:
            out |= lab == i
    return out


def _clean(m: np.ndarray) -> np.ndarray:
    if not m.any():
        return m
    return cv2.dilate(_rectangularize(m).astype(np.uint8), _K3) > 0


def _dynamic_maps(E: np.ndarray, times: np.ndarray, burst_times: list[float],
                  bursts: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Динамические блоки по парам (K×bh×bw) и «почти всегда динамичные» (bh×bw)."""
    K = len(E)
    if K == 0:
        return E.copy(), np.zeros(E.shape[1:], bool)
    if len(bursts) >= 3:
        S = np.stack(bursts)
        bt = np.asarray(burst_times)
        # «живое» = менялось в короткой паре и ещё хотя бы в одной из соседних
        nb = np.zeros_like(S)
        nb[1:] |= S[:-1]
        nb[:-1] |= S[1:]
        B = S & nb
        Bc = np.stack([_clean(b) for b in B])
        spacing = float(np.median(np.diff(bt))) if len(bt) > 1 else 1.0
        w = max(1.0, 0.6 * spacing)    # «вспышка» описывает состояние около своего момента
        D = np.zeros_like(E)
        lo = np.searchsorted(bt, times[:-1] - w, side="left")
        hi = np.searchsorted(bt, times[1:] + w, side="right")
        for k in range(K):
            if hi[k] > lo[k]:
                D[k] = Bc[lo[k]:hi[k]].any(axis=0)
            else:
                j = int(np.argmin(np.abs(bt - times[k])))
                D[k] = Bc[j]
    else:
        persistent = E & (_window_count(E, 2) >= 3)
        D = _window_count(persistent, 2) > 0
        for k in range(K):
            D[k] = _clean(D[k])
    # «живёт почти всегда» — окно камеры рядом со слайдом или поверх него.
    # Порог выше половины: ведущий на весь экран бывает долго, но не большую часть вебинара
    G = D.mean(axis=0) >= 0.65
    if G.any():
        G = _clean(G)
    return D, G


# ---------------------------------------------------------------- события

class _DSU:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.p[b] = a


def _event_region(E, D, G, debug) -> tuple[int, int, int, int] | None:
    """Прямоугольник (в блоках) самой большой согласованно меняющейся области."""
    K, bh, bw = E.shape
    # Вокруг постоянно «живых» окон (камеры) тоже не доверяем событиям: туда долетает
    # шум и движения, а узкие промежутки между соседними окнами камер так закрываются.
    Gx = cv2.dilate(G.astype(np.uint8), _K3) > 0 if G.any() else G
    Em = E & ~D & ~Gx[None]
    # Периоды, когда «живёт» заметная часть кадра (ведущий на весь экран, галерея
    # участников), выбрасываем целиком вместе с соседними парами: там меняется всё
    # подряд, и такие изменения склеили бы слайд с интерфейсом вокруг.
    extra = (D & ~G[None]).reshape(K, -1).mean(axis=1)
    takeover = extra >= 0.1
    takeover[1:] |= takeover[:-1].copy()
    takeover[:-1] |= (extra >= 0.1)[1:]
    Em[takeover] = False
    debug["takeover"] = takeover
    free = (~D & ~G[None]).reshape(K, -1).sum(1)
    cnt = Em.reshape(K, -1).sum(1)
    # «глобальные» события (меняется почти весь кадр) — обычно смена раскладки.
    # Если они в меньшинстве среди крупных событий, не даём им склеить слайд с
    # неподвижным интерфейсом вокруг.
    sig = cnt >= np.maximum(2, 0.05 * free)
    glob_ev = sig & (cnt >= 0.9 * np.maximum(free, 1))
    if glob_ev.sum() and glob_ev.sum() < 0.5 * sig.sum():
        Em[glob_ev] = False
    n = Em.sum(0)
    debug["event_counts"] = n
    if n.max() < 1:
        return None

    # 1) соседние блоки с общей историей событий
    co_h = (Em[:, :, :-1] & Em[:, :, 1:]).sum(0)
    co_v = (Em[:, :-1, :] & Em[:, 1:, :]).sum(0)
    aff_h = co_h / np.maximum(np.maximum(n[:, :-1], n[:, 1:]), 1)
    aff_v = co_v / np.maximum(np.maximum(n[:-1, :], n[1:, :]), 1)
    ok_h = (aff_h >= 0.4) & (np.minimum(n[:, :-1], n[:, 1:]) >= 1)
    ok_v = (aff_v >= 0.4) & (np.minimum(n[:-1, :], n[1:, :]) >= 1)
    dsu = _DSU(bh * bw)
    for y, x in zip(*np.nonzero(ok_h)):
        dsu.union(y * bw + x, y * bw + x + 1)
    for y, x in zip(*np.nonzero(ok_v)):
        dsu.union(y * bw + x, (y + 1) * bw + x)
    comps: dict[int, list[int]] = {}
    for i in np.flatnonzero(n.ravel() >= 1):
        comps.setdefault(dsu.find(int(i)), []).append(int(i))
    comps_l = list(comps.values())
    if not comps_l:
        return None

    # 2) кластеры с совпадающей историей событий и недалеко друг от друга → группы
    flat = Em.reshape(K, -1)
    series, boxes = [], []
    for c in comps_l:
        need = max(1, int(math.ceil(0.15 * len(c))))
        series.append(flat[:, c].sum(1) >= need)
        ys, xs = np.divmod(np.asarray(c), bw)
        boxes.append((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
    gap_max = max(3, int(0.2 * max(bw, bh)))
    g = _DSU(len(comps_l))
    ev_n = [int(s.sum()) for s in series]
    for i in range(len(comps_l)):
        if ev_n[i] < 1:
            continue
        for j in range(i + 1, len(comps_l)):
            if ev_n[j] < 1:
                continue
            inter = int((series[i] & series[j]).sum())
            gap = _box_gap(boxes[i], boxes[j])
            if gap > gap_max:
                continue
            similar = inter / max(ev_n[i], ev_n[j]) >= 0.4
            # Редко меняющаяся часть слайда (низ, куда текст доходит не на каждом
            # слайде) меняется только вместе с частой — её история почти целиком
            # входит в историю частой. Склеиваем, если части соприкасаются.
            nested = gap <= 1 and min(ev_n[i], ev_n[j]) >= 2 and \
                inter / min(ev_n[i], ev_n[j]) >= 0.75
            if similar or nested:
                g.union(i, j)
    groups: dict[int, list[int]] = {}
    for i in range(len(comps_l)):
        groups.setdefault(g.find(i), []).append(i)

    best, best_score = None, 0
    cands = []
    for members in groups.values():
        blocks = [b for m in members for b in comps_l[m]]
        if len(blocks) < 2:
            continue
        x0 = min(boxes[m][0] for m in members)
        y0 = min(boxes[m][1] for m in members)
        x1 = max(boxes[m][2] for m in members)
        y1 = max(boxes[m][3] for m in members)
        events = int(np.any(np.stack([series[m] for m in members]), axis=0).sum())
        area = (x1 - x0) * (y1 - y0)
        cands.append(((x0, y0, x1, y1), len(blocks), events))
        if events >= 1 and area > best_score:
            best, best_score = (x0, y0, x1, y1), area
    debug["groups"] = cands
    if best is None or best_score < 4:
        return None
    return best


def _box_gap(a, b) -> int:
    dx = max(0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0, max(a[1], b[1]) - min(a[3], b[3]))
    return max(dx, dy)


def _static_region(bg: np.ndarray, G: np.ndarray, block: int):
    """Запасной вариант: самый большой неподвижный прямоугольник с содержимым."""
    tex = block_mean(structure_map(bg, grow=1).astype(np.float32), block)
    score = np.where(tex > 0.03, 1.0, -0.35)
    if G is not None:
        score = np.where(G, -4.0, score)
    y0, y1, x0, x1, v = max_sum_rectangle(score)
    if v <= 2:
        return None
    return x0, y0, x1, y1


# ---------------------------------------------------------------- края

_SIDES = ("left", "top", "right", "bottom")


def _view(a: np.ndarray, side: str) -> np.ndarray:
    """Повернуть массив так, чтобы нужная сторона стала «левой»."""
    if side == "left":
        return a
    if side == "right":
        return a[:, ::-1]
    if side == "top":
        return a.T
    return a[::-1].T


def _side_pos(rect, side, W, H):
    """Положение стороны и диапазон строк вдоль неё в повёрнутых координатах."""
    x0, y0, x1, y1 = rect
    return {"left": (x0, y0, y1, W), "right": (W - x1, y0, y1, W),
            "top": (y0, x0, x1, H), "bottom": (H - y1, x0, x1, H)}[side]


def _set_side(rect, side, pos, W, H):
    x0, y0, x1, y1 = rect
    if side == "left":
        x0 = pos
    elif side == "right":
        x1 = W - pos
    elif side == "top":
        y0 = pos
    else:
        y1 = H - pos
    return x0, y0, x1, y1


def _edge_candidates(bg, mad, dyn, changed, pos, r0, r1, block, size):
    """Кандидаты границы для «левой» стороны: [(x, цвет снаружи или None для края кадра)].

    Граница — резкий перепад яркости вдоль большей части стороны, за которым
    ровный неподвижный фон одного цвета; между ней и текущей стороной нет
    «живых» зон. Перебор снаружи внутрь (сначала самые внешние).
    """
    W = bg.shape[1]
    m = max(1, (r1 - r0) // 10)
    rows = slice(r0 + m, max(r0 + m + 1, r1 - m))
    lo = max(2, pos - max(2 * block, int(0.3 * size)))
    hi = min(W - 1, pos + 3 * block)
    out = []
    for x in range(lo, hi + 1):
        # внутрь — «чисто», только если в срезаемой полосе ничего не менялось;
        # иначе кандидат «грязный» и примется, лишь если его подтвердят другие стороны
        dirty = x > pos and changed[rows, pos:x].mean() > 0.02
        if np.median(np.abs(bg[rows, x] - bg[rows, x - 2])) < 9:
            continue
        a = max(0, x - 4)
        strip = bg[rows, a:x - 1]
        if strip.shape[1] == 0:
            continue
        if np.median(strip.std(axis=1)) > 6:
            continue
        means = strip.mean(axis=1)
        q1, q3 = np.percentile(means, [25, 75])
        if q3 - q1 > 10 or np.median(mad[rows, a:x - 1]) > 4:
            continue
        if x < pos and dyn[rows, x:pos].mean() > 0.02:
            continue
        out.append((x, float(np.median(means)), dirty))
    if not any(not d for _, _, d in out) and 0 < pos <= max(2 * block, int(0.15 * size)) \
            and dyn[rows, :pos].mean() <= 0.35:
        out.append((0, None, False))   # сторона почти упирается в край кадра
    return out


def _refine_edges(bg, mad, dyn, changed, rect, block):
    """Подгонка сторон прямоугольника к видимой границе области показа.

    Для каждой стороны ищем кандидатов; цвет «подложки» снаружи у настоящей
    границы слайда обычно одинаков со всех сторон (чёрные поля Zoom, фон
    страницы вебинара), а у ложных границ (шапка сайта, полоса шаблона) — нет.
    Поэтому выбираем общий цвет подложки и на каждой стороне берём самого
    внешнего кандидата этого цвета.
    """
    H, W = bg.shape
    for _ in range(2):
        cands = {}
        for side in _SIDES:
            pos, r0, r1, size = _side_pos(rect, side, W, H)
            cands[side] = _edge_candidates(_view(bg, side), _view(mad, side), _view(dyn, side),
                                           _view(changed, side), pos, r0, r1, block, size)
        clean = {side: [(x, c) for x, c, d in cands[side] if not d] for side in _SIDES}
        colors = [c for side in _SIDES for _, c in clean[side] if c is not None]
        surround, support = None, set()
        if colors:
            def sides_with(c):
                return {side for side in _SIDES
                        if any(cc is not None and abs(cc - c) <= 12 for _, cc in clean[side])}
            surround = max(colors, key=lambda c: len(sides_with(c)))
            support = sides_with(surround)
        for side in _SIDES:
            # 1) чистый кандидат цвета подложки; 2) «грязный» того же цвета, если этот
            # цвет подтверждён другой стороной; 3) край кадра; 4) оставить как есть
            pick = None
            for x, c in clean[side]:
                if c is not None and (surround is None or abs(c - surround) <= 12):
                    pick = x
                    break
            if pick is None and surround is not None and support - {side}:
                dirty = [x for x, c, d in cands[side]
                         if d and c is not None and abs(c - surround) <= 12]
                if dirty:
                    pick = min(dirty)
            if pick is None:
                pick = next((x for x, c, _ in cands[side] if c is None), None)
            if pick is not None:
                rect = _set_side(rect, side, pick, W, H)
    return rect


def _snap_full(bg: np.ndarray, rect, radius: int):
    """Последний штрих: уточнить стороны по кадру полного размера (±radius пикселей)."""
    H, W = bg.shape
    for side in _SIDES:
        pos, r0, r1, size = _side_pos(rect, side, W, H)
        if pos <= 0:
            continue   # сторона по краю кадра — уточнять нечего
        v = _view(bg, side)
        m = max(1, (r1 - r0) // 10)
        rows = slice(r0 + m, max(r0 + m + 1, r1 - m))
        best, best_g = None, 8.0
        for x in range(max(1, pos - radius), min(v.shape[1] - 1, pos + radius) + 1):
            g = float(np.median(np.abs(v[rows, x] - v[rows, x - 1])))
            if g > best_g:
                best, best_g = x, g
        if best is not None:
            rect = _set_side(rect, side, best, W, H)
    return rect
