"""Командная строка: python -m presenter_reader ВИДЕО [ВИДЕО …] [-o ПАПКА]."""

from __future__ import annotations

import argparse
import os
import sys
import time

from . import __version__
from .pipeline import Cancelled, Params, default_out_dir, process
from .report import fmt_time, plural


def _time(s: str) -> float:
    """«90», «1:30», «01:02:03» → секунды."""
    parts = [float(x) for x in s.split(":")]
    t = 0.0
    for x in parts:
        t = t * 60 + x
    return t


def _roi(s: str) -> tuple[int, int, int, int]:
    v = [int(x) for x in s.replace(" ", "").split(",")]
    if len(v) != 4:
        raise argparse.ArgumentTypeError("нужно четыре числа: x,y,ширина,высота")
    return tuple(v)  # type: ignore[return-value]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="presenter-reader",
        description="Находит в записи вебинара область презентации и сохраняет по "
                    "скриншоту (PNG) на каждый показанный слайд. Без аргументов "
                    "открывает графический интерфейс.")
    ap.add_argument("videos", nargs="*", help="видеофайлы вебинаров")
    ap.add_argument("-o", "--out", help="папка для результатов (по умолчанию <видео>_slides рядом с видео)")
    ap.add_argument("--min-duration", type=float, default=1.0, metavar="С",
                    help="сколько секунд картинка должна стоять неподвижно, чтобы считаться "
                         "слайдом (по умолчанию 1; меньше — риск поймать кадр перехода)")
    ap.add_argument("--sensitivity", type=float, default=1.0, metavar="X",
                    help="чувствительность к мелким изменениям: 2 — вдвое чувствительнее, "
                         "0.5 — грубее (по умолчанию 1)")
    ap.add_argument("--similar", type=float, default=80.0, metavar="%",
                    help="с какого процента сходства помечать слайд как повтор (по умолчанию 80)")
    ap.add_argument("--skip-duplicates", action="store_true",
                    help="не сохранять дубли: если ведущий вернулся к слайду и он почти не "
                         "отличается от сохранённого, скриншот не делается ещё раз, а время "
                         "повторного показа добавляется к исходному слайду в отчёте")
    ap.add_argument("--duplicate-threshold", type=float, default=95.0, metavar="%",
                    help="с какого процента сходства повтор считается дублем (по умолчанию 95)")
    ap.add_argument("--no-merge-builds", action="store_true",
                    help="не склеивать пошаговую анимацию: каждый шаг — отдельный скриншот")
    ap.add_argument("--mode", choices=["auto", "keyframes", "sequential"], default="auto",
                    help="auto — быстрый проход по опорным кадрам, если возможно; "
                         "sequential — декодировать всё подряд (медленнее)")
    ap.add_argument("--roi", type=_roi, metavar="X,Y,W,H",
                    help="задать область презентации вручную (пиксели кадра)")
    ap.add_argument("--start", type=_time, metavar="ВРЕМЯ", help="начать с момента (сек или чч:мм:сс)")
    ap.add_argument("--end", type=_time, metavar="ВРЕМЯ", help="закончить на моменте")
    ap.add_argument("--backend", choices=["auto", "pyav", "opencv"], default="auto",
                    help=argparse.SUPPRESS)
    ap.add_argument("--keep-temp", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--gui", action="store_true", help="открыть графический интерфейс")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return ap


class _Bar:
    def __init__(self, name: str):
        self.name = name
        self.t0 = time.perf_counter()
        self.last = 0.0
        self.tty = sys.stderr.isatty()

    def __call__(self, frac: float, text: str) -> None:
        now = time.perf_counter()
        if frac < 1.0 and now - self.last < 0.2:
            return
        self.last = now
        n = int(frac * 30)
        line = f"[{'#' * n}{'.' * (30 - n)}] {frac * 100:5.1f}%  {text}  ({now - self.t0:.0f} с)"
        if self.tty:
            sys.stderr.write("\r" + line[:120].ljust(120))
            if frac >= 1.0:
                sys.stderr.write("\n")
        elif frac >= 1.0 or int(frac * 10) != int(getattr(self, "_dec", -1)):
            self._dec = int(frac * 10)
            sys.stderr.write(line + "\n")
        sys.stderr.flush()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.gui or not args.videos:
        try:
            from .gui import run_gui
        except ImportError as e:
            if args.videos or args.gui:
                print(f"Графический интерфейс недоступен ({e}). Установите: pip install PySide6",
                      file=sys.stderr)
                return 2
            build_parser().print_help()
            return 0
        return run_gui(args.videos)

    params = Params(min_duration=args.min_duration, sensitivity=args.sensitivity,
                    similar_threshold=args.similar, merge_builds=not args.no_merge_builds,
                    skip_duplicates=args.skip_duplicates, duplicate_threshold=args.duplicate_threshold,
                    mode=args.mode, roi=args.roi, start=args.start, end=args.end,
                    backend=args.backend, keep_temp=args.keep_temp)
    code = 0
    for video in args.videos:
        if args.out and len(args.videos) > 1:
            stem = os.path.splitext(os.path.basename(video))[0]
            out = os.path.join(args.out, stem)
        else:
            out = args.out or default_out_dir(video)
        print(f"\n▶ {video}", file=sys.stderr)
        try:
            res = process(video, out, params, progress=_Bar(video))
        except Cancelled:
            return 130
        except Exception as e:  # noqa: BLE001
            print(f"\nОшибка: {e}", file=sys.stderr)
            code = 1
            continue
        x, y, w, h = res.layout.rect
        print(f"Область презентации: x={x} y={y} {w}×{h} ({res.layout.method}); "
              f"режим: {res.mode}; время обработки: {res.seconds:.1f} с")
        n_dup = res.info.get("duplicates_skipped", 0)
        dups = f" (дублей не сохранено: {n_dup})" if n_dup else ""
        print(f"Найдено слайдов: {len(res.slides)}{dups}  →  {res.out_dir}")
        for s, f in zip(res.slides, res.files):
            extra = f"  (повтор №{s.repeat_of}, сходство {s.similarity:.0f}%)" if s.repeat_of else ""
            if s.appearances > 1:
                extra += f"  (показан {s.appearances} {plural(s.appearances, 'раз', 'раза', 'раз')})"
            print(f"  {s.number:3d}. {fmt_time(s.t_start - res.info.get('start', 0))}  {f}{extra}")
    return code


if __name__ == "__main__":
    sys.exit(main())
