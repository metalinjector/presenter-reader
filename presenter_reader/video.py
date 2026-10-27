"""Чтение видео.

Основной бэкенд — PyAV (библиотеки FFmpeg). Он умеет то, что даёт главный
выигрыш в скорости:

* декодировать **только опорные кадры** (keyframes, I-кадры) — остальные
  пакеты даже не передаются в декодер;
* быстро перематывать к нужному опорному кадру и декодировать короткий
  интервал целиком — только там, где на слайде что-то поменялось.

Запасной бэкенд — OpenCV (cv2.VideoCapture): работает всегда, но умеет только
последовательное чтение, поэтому медленнее.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Iterator

import cv2
import numpy as np

try:  # PyAV необязателен, но очень желателен
    import av
except ImportError:  # pragma: no cover - проверяется на машине пользователя
    av = None

# форматы, у которых плоскость 0 — это готовая яркость Y (8 бит)
_LUMA_PLANE_FORMATS = {
    "yuv420p", "yuvj420p", "yuv422p", "yuvj422p", "yuv444p", "yuvj444p",
    "yuv440p", "yuvj440p", "yuv410p", "yuv411p", "nv12", "nv21", "gray",
}


class Frame:
    """Декодированный кадр. Яркость и цвет вычисляются лениво.

    ``kind``: "key" — опорный кадр (или обычная выборка), "burst" — кадр через
    долю секунды после опорного: по паре «опорный + burst» видно, что
    в кадре двигается постоянно (камера), а что стоит.
    """

    __slots__ = ("t", "_av", "_bgr", "_luma", "kind")

    def __init__(self, t: float, av_frame=None, bgr: np.ndarray | None = None, kind: str = "key"):
        self.t = t
        self._av = av_frame
        self._bgr = bgr
        self._luma = None
        self.kind = kind

    def luma(self) -> np.ndarray:
        """Яркость кадра (H×W, uint8). Для YUV — без всякого пересчёта."""
        if self._luma is None:
            f = self._av
            if f is not None:
                if f.format.name in _LUMA_PLANE_FORMATS:
                    p = f.planes[0]
                    buf = np.frombuffer(p, np.uint8)
                    self._luma = buf.reshape(-1, p.line_size)[: p.height, : p.width]
                else:
                    self._luma = f.to_ndarray(format="gray")
            else:
                self._luma = cv2.cvtColor(self._bgr, cv2.COLOR_BGR2GRAY)
        return self._luma

    def bgr(self) -> np.ndarray:
        """Полноцветный кадр (H×W×3, BGR) — нужен только для скриншотов."""
        if self._bgr is None:
            self._bgr = self._av.to_ndarray(format="bgr24")
        return self._bgr


@dataclass
class VideoInfo:
    path: str
    width: int
    height: int
    duration: float            # секунды
    fps: float
    start: float               # время первого кадра (для отсчёта тайм-кодов с нуля)
    codec: str
    keyframe_interval: float | None  # медианный интервал между опорными кадрами, с
    backend: str


def _frame_time(f, tb: float) -> float | None:
    if f.pts is not None:
        return float(f.pts * tb)
    t = getattr(f, "time", None)
    return float(t) if t is not None else None


class AVSource:
    """Источник на PyAV: проход по опорным кадрам и точечное декодирование."""

    backend = "pyav"

    def __init__(self, path: str, threads: int = 0):
        if av is None:
            raise RuntimeError("PyAV не установлен")
        self.path = path
        self.threads = threads
        self._seek_container = None
        self.info = self._probe()

    # ------------------------------------------------------------ служебное
    def _open(self):
        c = av.open(self.path)
        s = c.streams.video[0]
        s.thread_type = "AUTO"
        if self.threads:
            s.codec_context.thread_count = self.threads
        return c, s

    def _probe(self) -> VideoInfo:
        c, s = self._open()
        try:
            tb = float(s.time_base)
            cc = s.codec_context
            width, height = cc.width, cc.height
            fps = float(s.average_rate) if s.average_rate else 25.0
            start = float(s.start_time * tb) if s.start_time is not None else 0.0
            if s.duration:
                duration = float(s.duration * tb)
            elif c.duration:
                duration = c.duration / 1_000_000
            else:
                duration = 0.0
            # интервал между опорными кадрами: читаем только пакеты (без
            # декодирования) первых ~90 секунд
            keys = []
            last_t = None
            for pkt in c.demux(s):
                if pkt.size == 0:
                    continue
                pt = pkt.pts if pkt.pts is not None else pkt.dts
                if pt is None:
                    continue
                t = float(pt * tb)
                last_t = t if last_t is None else max(last_t, t)
                if pkt.is_keyframe:
                    keys.append(t)
                if t - start > 90 and len(keys) >= 3:
                    break
            if not duration and last_t is not None:
                duration = last_t - start
            ki = None
            if len(keys) >= 2:
                gaps = np.diff(sorted(keys))
                gaps = gaps[gaps > 0]
                if len(gaps):
                    ki = float(np.median(gaps))
            elif len(keys) == 1 and last_t is not None and last_t - keys[0] > 30:
                ki = math.inf  # за полторы минуты лишь один опорный кадр
            if width == 0 or height == 0:  # редкие контейнеры: смотрим первый кадр
                c.seek(0)
                for f in c.decode(s):
                    width, height = f.width, f.height
                    break
            return VideoInfo(self.path, width, height, duration, fps, start,
                             cc.name, ki, self.backend)
        finally:
            c.close()

    # ------------------------------------------------------------ чтение
    def keyframes(self, min_interval: float = 0.0, start: float | None = None,
                  end: float | None = None, burst: float = 0.0,
                  burst_every: float = 0.0) -> Iterator[Frame]:
        """Только опорные кадры, не чаще чем раз в ``min_interval`` секунд.

        При ``burst > 0`` после опорного кадра (не чаще раза в ``burst_every`` с)
        дополнительно декодируются пакеты на ``burst`` секунд вперёд и выдаётся
        один кадр ``kind="burst"``. Остальные пакеты в декодер не попадают.
        """
        c, s = self._open()
        try:
            tb = float(s.time_base)
            cc = s.codec_context
            if burst <= 0:
                cc.skip_frame = "NONKEY"
            if start:
                c.seek(int(start / tb), stream=s, backward=True, any_frame=False)
            last = -math.inf
            last_burst = -math.inf
            feed_until = None          # до какого момента подаём пакеты «вспышки»
            burst_keys: set[int] = set()  # pts опорных кадров, для которых нужна «вспышка»
            want_burst = False
            key_t = None

            def emit(frames):
                nonlocal key_t, want_burst
                for f in frames:
                    ft = _frame_time(f, tb)
                    if ft is None or (start is not None and ft < start - 1e-6):
                        continue
                    if end is not None and ft > end + burst + 1:
                        continue
                    if f.key_frame:
                        key_t = ft
                        want_burst = f.pts in burst_keys
                        burst_keys.discard(f.pts)
                        yield Frame(ft, f)
                    elif want_burst and key_t is not None and ft >= key_t + burst - 1e-6:
                        want_burst = False
                        yield Frame(ft, f, kind="burst")

            for pkt in c.demux(s):
                if pkt.size == 0:
                    continue
                pt = pkt.pts if pkt.pts is not None else pkt.dts
                t = float(pt * tb) if pt is not None else None
                if pkt.is_keyframe:
                    if t is not None:
                        if end is not None and t > end:
                            break
                        if t - last < min_interval:
                            feed_until = None
                            continue
                        last = t
                    feed_until = None
                    if burst > 0 and t is not None and t - last_burst >= burst_every:
                        feed_until = t + burst + 0.3
                        last_burst = t
                        burst_keys.add(pkt.pts)
                elif feed_until is not None:
                    dt = pkt.dts if pkt.dts is not None else pkt.pts
                    if dt is None or dt * tb > feed_until:
                        feed_until = None
                        continue
                else:
                    continue
                yield from emit(_safe_decode(cc, pkt))
            yield from emit(_safe_decode(cc, None))
        finally:
            c.close()

    def frames(self, t0: float, t1: float, step: float) -> Iterator[Frame]:
        """Кадры строго внутри (t0, t1) с шагом не меньше ``step``.

        Перематывает к опорному кадру не позже t0 и декодирует подряд — так
        устроено сжатие: промежуточный кадр нельзя получить без предыдущих.
        """
        if self._seek_container is None:
            self._seek_container = self._open()
        c, s = self._seek_container
        tb = float(s.time_base)
        c.seek(max(0, int(round(t0 / tb))), stream=s, backward=True, any_frame=False)
        nxt = t0 + step
        for f in c.decode(s):
            t = _frame_time(f, tb)
            if t is None or t <= t0 + 1e-6:
                continue
            if t >= t1 - 1e-6:
                break
            if t >= nxt - 1e-6:
                yield Frame(t, f)
                nxt = t + step

    def sequential(self, step: float, start: float | None = None,
                   end: float | None = None, burst: float = 0.0) -> Iterator[Frame]:
        """Все кадры подряд (декодируются все), отдаются с шагом ``step``.

        При ``burst > 0`` после каждой выборки отдаётся ещё кадр ``kind="burst"``
        примерно через ``burst`` секунд.
        """
        c, s = self._open()
        try:
            tb = float(s.time_base)
            if start:
                c.seek(int(start / tb), stream=s, backward=True, any_frame=False)
            yield from _stepper(
                (Frame(t, f) for pkt in c.demux(s)
                 for f in _safe_decode(s.codec_context, pkt if pkt.size else None)
                 if (t := _frame_time(f, tb)) is not None),
                step, start, end, burst)
        finally:
            c.close()

    def frame_at(self, t: float) -> Frame | None:
        """Ближайший кадр не раньше ``t`` (для скриншота в нужный момент)."""
        if self._seek_container is None:
            self._seek_container = self._open()
        c, s = self._seek_container
        tb = float(s.time_base)
        c.seek(max(0, int(round(t / tb))), stream=s, backward=True, any_frame=False)
        for f in c.decode(s):
            ft = _frame_time(f, tb)
            if ft is not None and ft >= t - 1e-6:
                return Frame(ft, f)
        return None

    def close(self):
        if self._seek_container is not None:
            self._seek_container[0].close()
            self._seek_container = None


def _stepper(frames, step: float, start, end, burst: float) -> Iterator[Frame]:
    """Прореживание подряд идущих кадров: выборка раз в ``step`` + «вспышка»."""
    nxt = -math.inf if start is None else start
    burst_at = None
    for fr in frames:
        if end is not None and fr.t > end:
            return
        if burst_at is not None and fr.t >= burst_at - 1e-6:
            burst_at = None
            fr.kind = "burst"
            yield fr
            continue
        if fr.t >= nxt - 1e-6:
            yield fr
            nxt = fr.t + step
            if burst > 0 and step > 2 * burst:
                burst_at = fr.t + burst


def _safe_decode(cc, pkt):
    """Декодирование, устойчивое к битым пакетам."""
    try:
        return cc.decode(pkt)
    except av.error.InvalidDataError:
        return []
    except av.error.EOFError:
        return []


class CVSource:
    """Запасной источник на OpenCV: только последовательное чтение."""

    backend = "opencv"

    def __init__(self, path: str, threads: int = 0):
        self.path = path
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            raise RuntimeError(f"не удалось открыть видео: {path}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        self.info = VideoInfo(path, w, h, n / fps if n else 0.0, fps, 0.0, "?", None, self.backend)

    def keyframes(self, *a, **k):
        raise NotImplementedError("OpenCV не умеет читать только опорные кадры")

    def frames(self, t0: float, t1: float, step: float) -> Iterator[Frame]:
        cap = cv2.VideoCapture(self.path)
        try:
            cap.set(cv2.CAP_PROP_POS_MSEC, t0 * 1000)
            nxt = t0 + step
            while True:
                if not cap.grab():
                    break
                t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
                if t <= t0 + 1e-6:
                    continue
                if t >= t1 - 1e-6:
                    break
                if t >= nxt - 1e-6:
                    ok, img = cap.retrieve()
                    if ok:
                        yield Frame(t, bgr=img)
                    nxt = t + step
        finally:
            cap.release()

    def sequential(self, step: float, start: float | None = None,
                   end: float | None = None, burst: float = 0.0) -> Iterator[Frame]:
        cap = cv2.VideoCapture(self.path)
        try:
            if start:
                cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
            nxt = -math.inf if start is None else start
            burst_at = None
            while cap.grab():
                t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
                if end is not None and t > end:
                    break
                kind = None
                if burst_at is not None and t >= burst_at - 1e-6:
                    kind, burst_at = "burst", None
                elif t >= nxt - 1e-6:
                    kind, nxt = "key", t + step
                    if burst > 0 and step > 2 * burst:
                        burst_at = t + burst
                if kind:
                    ok, img = cap.retrieve()
                    if ok:
                        yield Frame(t, bgr=img, kind=kind)
        finally:
            cap.release()

    def frame_at(self, t: float) -> Frame | None:
        cap = cv2.VideoCapture(self.path)
        try:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, img = cap.read()
            return Frame(cap.get(cv2.CAP_PROP_POS_MSEC) / 1000, bgr=img) if ok else None
        finally:
            cap.release()

    def close(self):
        pass


def open_video(path: str, backend: str = "auto", threads: int = 0):
    if not os.path.exists(path) and "://" not in path:
        raise FileNotFoundError(path)
    if backend in ("auto", "pyav") and av is not None:
        return AVSource(path, threads)
    if backend == "pyav":
        raise RuntimeError("PyAV не установлен: pip install av")
    return CVSource(path, threads)
