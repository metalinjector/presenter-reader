"""Полный цикл обработки одного видео: обзор → поиск слайдов → сохранение."""

from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from .imageops import Comparator
from .layout import Layout, Survey
from .report import draw_layout, save_slides, write_reports
from .slides import Slide, assemble
from .tracker import Analyzer, Tracker, _combine
from .video import open_video


class Cancelled(Exception):
    """Обработка остановлена пользователем."""


@dataclass
class Params:
    min_duration: float = 1.0        # сколько секунд картинка должна стоять, чтобы считаться слайдом
    fine_step: float = 0.25          # шаг точного анализа около смен слайдов, с
    sensitivity: float = 1.0         # >1 — замечать более мелкие изменения
    merge_builds: bool = True        # пошаговую анимацию — одним кадром
    similar_threshold: float = 80.0  # с какого сходства, %, помечать повтор
    mode: str = "auto"               # auto | keyframes | sequential
    max_keyframe_gap: float = 20.0   # реже опорных кадров — читаем всё подряд
    survey_samples: int = 3000       # сколько выборок брать для поиска области
    survey_bursts: int = 600         # сколько «вспышек» (кадр через долю секунды) брать
    burst: float = 0.6               # через сколько секунд после опорного кадра «вспышка»
    analysis_width: int = 640
    roi: tuple | None = None         # область вручную: x, y, w, h
    start: float | None = None       # анализировать только часть записи, с
    end: float | None = None
    backend: str = "auto"            # auto | pyav | opencv
    keep_temp: bool = False

    def comparator(self) -> Comparator:
        s = max(0.2, float(self.sensitivity))
        return Comparator(tau=int(np.clip(round(22 / s), 8, 60)), min_px=4,
                          cursor_blocks=int(np.clip(round(10 / s), 1, 40)))


@dataclass
class Result:
    video: str
    out_dir: str
    slides: list[Slide]
    files: list[str]
    layout: Layout
    mode: str
    seconds: float
    info: dict = field(default_factory=dict)


_OWN_FILE = re.compile(r"^slide_\d{3}_.*\.png$")


def default_out_dir(video: str) -> str:
    stem = os.path.splitext(os.path.basename(video))[0]
    return os.path.join(os.path.dirname(os.path.abspath(video)), stem + "_slides")


def process(video: str, out_dir: str | None = None, params: Params | None = None,
            progress=None, cancel=None) -> Result:
    """Обработать видео. ``progress(доля 0..1, текст)``, ``cancel`` — threading.Event."""
    p = params or Params()
    out_dir = out_dir or default_out_dir(video)
    t_begin = time.perf_counter()

    def report(frac, text):
        if progress is not None:
            progress(min(1.0, max(0.0, frac)), text)
        if cancel is not None and cancel.is_set():
            raise Cancelled()

    report(0.0, "Открываю видео…")
    src = open_video(video, p.backend)
    try:
        info = src.info
        t_lo = info.start + (p.start or 0.0)
        t_hi = info.start + (p.end if p.end else info.duration)
        span = max(1e-3, t_hi - t_lo)
        ki = info.keyframe_interval
        use_keys = (p.mode != "sequential" and src.backend == "pyav" and ki is not None
                    and ki <= p.max_keyframe_gap)
        mode = "keyframes" if use_keys else "sequential"
        start_arg = t_lo if p.start else None

        # ---------------------------------------------- 1. обзор: где презентация
        full_times = [t_lo + span * (i + 0.5) / 7 for i in range(7)]
        survey = Survey(info.width, info.height, full_times=full_times)
        step = span / max(10, p.survey_samples)
        burst_every = span / max(10, p.survey_bursts)
        if use_keys:
            it = src.keyframes(min_interval=step, start=start_arg, end=t_hi,
                               burst=p.burst, burst_every=burst_every)
        else:
            it = src.sequential(max(step, 1.0), start=start_arg, end=t_hi, burst=p.burst)
        for fr in it:
            survey.add(fr.t, fr.luma(), fr.kind)
            report(0.3 * (fr.t - t_lo) / span, "Ищу область презентации…")
        layout = survey.analyze(roi=p.roi)
        if layout.rect[2] < 16 or layout.rect[3] < 16:
            layout.rect, layout.method = (0, 0, info.width, info.height), "full"

        # ---------------------------------------------- 2. поиск смен картинки
        an = Analyzer(layout, p.analysis_width)
        cmp = p.comparator()
        os.makedirs(out_dir, exist_ok=True)
        tmp = os.path.join(out_dir, ".runs_tmp")
        tracker = Tracker(an, cmp, tmp, p.min_duration)
        text = "Ищу смены слайдов…"
        if use_keys:
            prev = None
            for fr in src.keyframes(min_interval=max(p.fine_step, 0.5) * 0.99,
                                    start=start_arg, end=t_hi):
                a = an.image(fr)
                if prev is not None and fr.t - prev[0] > 1.5 * p.fine_step:
                    m = _combine(an.mask(fr.t)[0], an.mask(prev[0])[0])
                    if not cmp.is_same(a, prev[1], m):
                        # между опорными кадрами что-то поменялось — разбираем интервал подробно
                        for ff in src.frames(prev[0], fr.t, p.fine_step):
                            tracker.push(ff)
                            report(0.3 + 0.65 * (ff.t - t_lo) / span, text)
                tracker.push(fr, a)
                prev = (fr.t, a)
                report(0.3 + 0.65 * (fr.t - t_lo) / span, text)
        else:
            for fr in src.sequential(p.fine_step, start=start_arg, end=t_hi):
                tracker.push(fr)
                report(0.3 + 0.65 * (fr.t - t_lo) / span, text)
        runs = tracker.finish(t_hi)

        # ---------------------------------------------- 3. сборка и сохранение
        report(0.96, "Сохраняю слайды…")
        slides = assemble(runs, cmp, p.min_duration, p.merge_builds, p.similar_threshold)
        for f in os.listdir(out_dir):
            if _OWN_FILE.match(f):
                os.remove(os.path.join(out_dir, f))
        files = save_slides(slides, out_dir, t0=info.start)
        _save_layout_preview(src, layout, slides, out_dir, t_lo, t_hi)
        seconds = time.perf_counter() - t_begin
        meta = {
            "duration": round(span, 2),
            "start": info.start,
            "frame_size": [info.width, info.height],
            "codec": info.codec,
            "backend": src.backend,
            "mode": mode,
            "keyframe_interval": ki,
            "region": {"x": layout.rect[0], "y": layout.rect[1], "w": layout.rect[2],
                       "h": layout.rect[3], "method": layout.method},
            "processing_seconds": round(seconds, 1),
            "runs_total": len(runs),
        }
        write_reports(out_dir, video, slides, files, meta)
        if not p.keep_temp:
            shutil.rmtree(tmp, ignore_errors=True)
        report(1.0, f"Готово: {len(slides)} слайдов")
        return Result(video, out_dir, slides, files, layout, mode, seconds, meta)
    finally:
        src.close()


def _save_layout_preview(src, layout: Layout, slides: list[Slide], out_dir: str,
                         t_lo: float, t_hi: float) -> None:
    """Кадр с нарисованной найденной областью — чтобы её можно было проверить глазами."""
    if slides:
        s = max(slides, key=lambda s: s.run.duration)
        t = (s.run.t_start + s.run.t_end) / 2
    else:
        t = (t_lo + t_hi) / 2
    try:
        fr = src.frame_at(t)
    except Exception:  # noqa: BLE001 - превью необязательно
        fr = None
    if fr is None:
        return
    dyn = layout.glob
    if dyn is not None and dyn.any():
        b = layout.block
        dyn = np.kron(dyn.astype(np.uint8), np.ones((b, b), np.uint8))
    else:
        dyn = None
    img = draw_layout(fr.bgr(), layout.rect, dyn)
    cv2.imwrite(os.path.join(out_dir, "layout.png"), img)
