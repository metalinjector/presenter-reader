"""Проверка графического интерфейса без экрана (для ИИ-агентов и CI).

    python -m tests.gui_smoke VIDEO.mp4 [--shot window.png]

Открывает главное окно в режиме offscreen, добавляет видео в очередь, ждёт
окончания обработки, печатает итог и сохраняет снимок окна. Код выхода 0 —
окно открылось и видео обработано, 1 — ошибка или не уложились в время.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("video")
    ap.add_argument("--shot", default="gui_smoke.png", help="куда сохранить снимок окна")
    ap.add_argument("--timeout", type=float, default=600, help="сколько секунд ждать")
    args = ap.parse_args()

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from presenter_reader import gui

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setFont(gui._pick_font())
    w = gui.MainWindow()
    w.resize(1280, 800)
    w.show()
    t0 = time.monotonic()
    state = {"code": 1}

    def poll():
        job = w.jobs[0] if w.jobs else None
        if job is not None and job.status in ("queued", "running") and time.monotonic() - t0 < args.timeout:
            QTimer.singleShot(300, poll)
            return
        if job is None or job.status != "done":
            print(f"FAIL: статус {job.status if job else 'нет задания'} {job.error if job else ''}")
            app.quit()
            return
        w.select_job(job)
        QTimer.singleShot(1500, finish)   # дать миниатюрам загрузиться

    def finish():
        job = w.jobs[0]
        w.grab().save(args.shot)
        print(f"OK: слайдов {len(job.result.slides)}, папка {job.out_dir}, снимок окна {args.shot}")
        state["code"] = 0
        app.quit()

    w.add_files([args.video])
    w.start()   # если автозапуск в настройках выключен
    QTimer.singleShot(300, poll)
    app.exec()
    return state["code"]


if __name__ == "__main__":
    sys.exit(main())
