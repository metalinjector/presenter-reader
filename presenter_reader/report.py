"""Сохранение результатов: PNG-скриншоты, отчёты, кадр с найденной областью."""

from __future__ import annotations

import csv
import html
import json
import os
import shutil
from urllib.parse import quote

import cv2
import numpy as np

from .slides import Slide


def fmt_time(t: float, sep: str = ":") -> str:
    t = max(0, int(round(t)))
    return f"{t // 3600:02d}{sep}{t % 3600 // 60:02d}{sep}{t % 60:02d}"


def plural(n: int, one: str, few: str, many: str) -> str:
    """Склонение по числу: 1 слайд, 2 слайда, 5 слайдов."""
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return one
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return few
    return many


def slide_filename(s: Slide, t0: float) -> str:
    name = f"slide_{s.number:03d}_{fmt_time(s.t_start - t0, '-')}"
    if s.repeat_of is not None:
        name += f"_repeat-of-{s.repeat_of:03d}_sim-{int(s.similarity)}pct"
    return name + ".png"


def save_slides(slides: list[Slide], out_dir: str, t0: float) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    files = []
    for s in slides:
        name = slide_filename(s, t0)
        dst = os.path.join(out_dir, name)
        shutil.copyfile(s.run.image_path, dst)
        files.append(name)
    return files


def draw_layout(frame_bgr: np.ndarray, rect, dyn_mask: np.ndarray | None = None) -> np.ndarray:
    """Кадр с найденной областью (зелёная рамка) и «живыми» зонами (красные)."""
    img = frame_bgr.copy()
    h, w = img.shape[:2]
    if dyn_mask is not None and dyn_mask.any():
        m = cv2.resize(dyn_mask.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST) > 0
        red = np.zeros_like(img)
        red[:] = (40, 40, 230)
        img[m] = cv2.addWeighted(img, 0.55, red, 0.45, 0)[m]
    x, y, rw, rh = rect
    t = max(2, w // 400)
    cv2.rectangle(img, (x + t // 2, y + t // 2), (x + rw - 1 - t // 2, y + rh - 1 - t // 2), (60, 220, 60), t)
    return img


def write_reports(out_dir: str, video: str, slides: list[Slide], files: list[str], info: dict) -> None:
    t0 = info.get("start", 0.0)
    rows = []
    for s, f in zip(slides, files):
        rows.append({
            "number": s.number,
            "file": f,
            "start": round(s.t_start - t0, 2),
            "end": round(s.t_end - t0, 2),
            "start_hms": fmt_time(s.t_start - t0),
            "shown_seconds": round(s.shown_for, 1),
            "shows": [[round(a - t0, 2), round(b - t0, 2)] for a, b in s.shows],
            "animation_steps": s.states,
            "appearances": s.appearances,
            "repeat_of": s.repeat_of,
            "similarity": None if s.similarity is None else round(s.similarity, 1),
        })
    data = dict(info)
    data.update(video=os.path.abspath(video), slides_count=len(slides), slides=rows)
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "slides.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        wr = csv.writer(fh, delimiter=";")
        wr.writerow(["№", "файл", "начало", "показан, с", "показов", "повтор слайда", "сходство, %"])
        for r in rows:
            wr.writerow([r["number"], r["file"], r["start_hms"], r["shown_seconds"], r["appearances"],
                         r["repeat_of"] or "", r["similarity"] if r["similarity"] is not None else ""])
    _write_html(out_dir, video, rows, info)


def _write_html(out_dir: str, video: str, rows: list[dict], info: dict) -> None:
    cards = []
    for r in rows:
        badge = ""
        if r["appearances"] > 1:
            n = r["appearances"]
            badge += f'<span class="badge acc">показан {n} {plural(n, "раз", "раза", "раз")}</span>'
        if r["repeat_of"]:
            badge += (f'<span class="badge">повтор №{r["repeat_of"]} · '
                      f'сходство {r["similarity"]:.0f}%</span>')
        cards.append(
            f'<figure><a href="{html.escape(r["file"])}"><img loading="lazy" '
            f'src="{html.escape(r["file"])}" alt="слайд {r["number"]}"></a>'
            f'<figcaption><b>№{r["number"]}</b> · {r["start_hms"]} · {r["shown_seconds"]:.0f} с {badge}'
            f'</figcaption></figure>')
    n_dup = info.get("duplicates_skipped", 0)
    dups = f" (дублей не сохранено: {n_dup})" if n_dup else ""
    pdf = info.get("pdf")
    pdf_link = f' · <a href="{html.escape(quote(pdf))}">все слайды в PDF</a>' if pdf else ""
    layout = '<img class="layout" src="layout.png" alt="найденная область">' \
        if os.path.exists(os.path.join(out_dir, "layout.png")) else ""
    page = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Слайды: {html.escape(os.path.basename(video))}</title>
<style>
:root {{ color-scheme: light dark; --bg:#f6f7fb; --fg:#1d2330; --card:#fff; --muted:#667; --acc:#3b82f6; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#11141b; --fg:#e8ebf2; --card:#1b2030; --muted:#9aa3b5; }} }}
body {{ margin:0; padding:24px; background:var(--bg); color:var(--fg); font:15px/1.45 system-ui, sans-serif; }}
h1 {{ font-size:22px; margin:0 0 4px; }} .muted {{ color:var(--muted); }}
.grid {{ display:grid; gap:16px; grid-template-columns:repeat(auto-fill, minmax(300px, 1fr)); margin-top:20px; }}
figure {{ margin:0; background:var(--card); border-radius:12px; overflow:hidden; box-shadow:0 1px 4px #0002; }}
figure img {{ width:100%; display:block; }} figcaption {{ padding:10px 12px; }}
.badge {{ display:inline-block; margin-left:6px; padding:1px 8px; border-radius:99px; background:#f59e0b33; color:#b45309; font-size:13px; }}
.badge.acc {{ background:#6a5af933; color:#5b4bd8; }}
.layout {{ max-width:min(640px, 100%); border-radius:12px; margin-top:12px; }}
</style></head><body>
<h1>{html.escape(os.path.basename(video))}</h1>
<div class="muted">Слайдов: {len(rows)}{dups} · длительность {fmt_time(info.get("duration", 0))} ·
обработано за {info.get("processing_seconds", 0):.0f} с{pdf_link}</div>
{layout}
<div class="grid">{''.join(cards)}</div>
</body></html>"""
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page)
