"""Примитивы сравнения изображений.

Все сравнения идут по яркости в уменьшенном масштабе («аналитическое»
изображение) и по блокам 8×8 пикселей:

1. попиксельная разница |A − B| > порога → «изменённые» пиксели;
2. в блоке ≥ ``min_px`` изменённых пикселей → блок изменён (одиночные
   пиксели шума сжатия отсеиваются);
3. изменённые блоки склеиваются в связные пятна; маленькие пятна — это
   курсор, указка или шум, крупные — реальная смена содержимого.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

_K3 = np.ones((3, 3), np.uint8)


def block_sum(binary: np.ndarray, block: int) -> np.ndarray:
    """Число единиц в каждом блоке block×block (хвосты обрезаются)."""
    h, w = binary.shape
    hb, wb = h // block, w // block
    b = binary[: hb * block, : wb * block].astype(np.float32)
    m = cv2.resize(b, (wb, hb), interpolation=cv2.INTER_AREA)   # среднее по блоку
    return np.rint(m * (block * block)).astype(np.int32)


def downscale(img: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Уменьшение с усреднением (как INTER_AREA), но каскадом через половинки — в разы быстрее."""
    w, h = size
    while img.shape[1] >= 2 * w and img.shape[0] >= 2 * h:
        img = cv2.resize(img, (img.shape[1] // 2, img.shape[0] // 2), interpolation=cv2.INTER_AREA)
    return cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)


def block_mean(img: np.ndarray, block: int) -> np.ndarray:
    h, w = img.shape
    hb, wb = h // block, w // block
    b = img[: hb * block, : wb * block].astype(np.float32)
    return b.reshape(hb, block, wb, block).mean(axis=(1, 3))


def largest_component(mask: np.ndarray) -> int:
    if not mask.any():
        return 0
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    return int(stats[1:, cv2.CC_STAT_AREA].max()) if n > 1 else 0


def structure_map(img: np.ndarray, thr: int = 40, grow: int = 2) -> np.ndarray:
    """Где на картинке есть «содержимое» (контрастные края: текст, линии)."""
    g = cv2.morphologyEx(img, cv2.MORPH_GRADIENT, _K3)
    s = (g > thr).astype(np.uint8)
    if grow:
        s = cv2.dilate(s, np.ones((2 * grow + 1, 2 * grow + 1), np.uint8))
    return s > 0


@dataclass
class Diff:
    changed: np.ndarray      # bool-карта изменённых блоков
    n_changed: int
    largest: int             # самое крупное связное пятно, в блоках
    n_valid: int             # сколько блоков вообще участвует в сравнении


class Comparator:
    """Решает, «та же самая» картинка или нет, с допуском на курсор и шум."""

    def __init__(self, block: int = 8, tau: int = 22, min_px: int = 4,
                 cursor_blocks: int = 10):
        self.block = block
        self.tau = tau
        self.min_px = min_px
        self.cursor_blocks = cursor_blocks

    def diff(self, a: np.ndarray, b: np.ndarray, mask: np.ndarray | None) -> Diff:
        ch = cv2.absdiff(a, b) > self.tau
        if mask is not None:
            ch &= mask
        cnt = block_sum(ch, self.block)
        changed = cnt >= self.min_px
        n = int(changed.sum())
        if mask is not None:
            n_valid = int((block_sum(mask, self.block) >= self.block * self.block // 2).sum())
        else:
            n_valid = changed.size
        return Diff(changed, n, largest_component(changed) if n else 0, n_valid)

    def same(self, d: Diff) -> bool:
        """Отличия не больше двух «курсоров» — значит, картинка та же."""
        return d.largest <= self.cursor_blocks and d.n_changed <= 2 * self.cursor_blocks

    def minor(self, d: Diff) -> bool:
        """Отличие небольшое: это может быть и курсор, и мелкая правка."""
        return d.largest <= 4 * self.cursor_blocks and d.n_changed <= 6 * self.cursor_blocks

    def is_same(self, a, b, mask) -> bool:
        return self.same(self.diff(a, b, mask))

    def contains(self, big: np.ndarray, small: np.ndarray, mask: np.ndarray | None) -> bool:
        """Всё содержимое ``small`` осталось на месте в ``big``.

        Так выглядит пошаговая анимация: к слайду добавляются пункты, а уже
        показанное не меняется. Проверяем, что в местах, где у ``small`` есть
        содержимое, картинки совпадают.
        """
        s = structure_map(small)
        if mask is not None:
            s &= mask
        if block_sum(s, self.block).sum() == 0:
            return True  # пустая картинка содержится в любой
        return self.same(self.diff(big, small, s))

    def similarity(self, a: np.ndarray, b: np.ndarray, mask: np.ndarray | None) -> float:
        """Процент сходства двух картинок (по блокам с содержимым)."""
        d = self.diff(a, b, mask)
        content = structure_map(a) | structure_map(b)
        if mask is not None:
            content &= mask
        cblocks = block_sum(content, self.block) >= self.min_px
        relevant = cblocks | d.changed
        n_rel = int(relevant.sum())
        if n_rel == 0:
            return 100.0
        return 100.0 * (1.0 - int((d.changed & relevant).sum()) / n_rel)


def max_sum_rectangle(score: np.ndarray) -> tuple[int, int, int, int, float]:
    """Прямоугольник с максимальной суммой (y0, y1, x0, x1), границы полуоткрытые.

    Двумерная задача о максимальном подмассиве: перебор пар строк + Кадане
    по столбцам, векторизовано по numpy. Для сетки 40×30 — доли миллисекунды.
    """
    h, w = score.shape
    best = (-np.inf, 0, 1, 0, 1)
    for top in range(h):
        col = np.cumsum(score[top:], axis=0)            # суммы столбцов для всех bottom
        pref = np.concatenate([np.zeros((col.shape[0], 1)), np.cumsum(col, axis=1)], axis=1)
        mins = np.minimum.accumulate(pref[:, :-1], axis=1)
        argmins = _argmin_accumulate(pref[:, :-1])
        gains = pref[:, 1:] - mins
        idx = np.unravel_index(np.argmax(gains), gains.shape)
        val = gains[idx]
        if val > best[0]:
            r, c = idx
            best = (float(val), top, top + r + 1, int(argmins[r, c]), c + 1)
    v, y0, y1, x0, x1 = best
    return y0, y1, x0, x1, v


def _argmin_accumulate(a: np.ndarray) -> np.ndarray:
    out = np.zeros(a.shape, np.int64)
    cur = a[:, 0].copy()
    pos = np.zeros(a.shape[0], np.int64)
    for j in range(a.shape[1]):
        better = a[:, j] < cur
        cur = np.where(better, a[:, j], cur)
        pos = np.where(better, j, pos)
        out[:, j] = pos
    return out


def write_png(path: str, img: np.ndarray, compression: int = 3) -> bool:
    """Сохранить PNG по любому пути.

    cv2.imwrite в Windows не умеет пути с кириллицей (молча возвращает False),
    поэтому кодируем в память и пишем файл средствами numpy.
    """
    ok, buf = cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, compression])
    if ok:
        buf.tofile(path)
    return bool(ok)


def median_image(images: list[np.ndarray]) -> np.ndarray:
    """Поэлементная медиана — убирает курсор, если он двигался."""
    if len(images) == 1:
        return images[0]
    if len(images) == 3:
        a, b, c = images
        return np.maximum(np.minimum(a, b), np.minimum(np.maximum(a, b), c))
    return np.median(np.stack(images), axis=0).astype(images[0].dtype)
