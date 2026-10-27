"""Генератор синтетических «вебинаров» с известным правильным ответом.

Каждый сценарий — это видео с презентацией, «живой» камерой ведущего,
курсором мыши, иногда чатом, анимацией пунктов, плавными переходами и
возвратами к уже показанным слайдам. Вместе с видео сохраняется JSON
с эталоном: прямоугольник области слайдов и список уникальных слайдов.

Запуск:  python -m tests.synth OUT_DIR [--scenario zoom] [--gop 2] ...
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import subprocess
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/Library/Fonts/Arial.ttf",
]

TITLES = [
    "Ресурсные платежи", "Налог на добычу полезных ископаемых", "Водный налог",
    "Сборы за пользование объектами животного мира", "Порядок исчисления",
    "Налоговая база", "Ставки налога", "Сроки уплаты", "Льготы", "Примеры расчёта",
    "Итоги", "Вопросы и ответы",
]
WORDS = ("налог база ставка период платёж объект субъект льгота расчёт декларация "
         "срок уплата добыча вода лицензия тонна кубометр коэффициент сумма").split()


def _font(size: int):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


@dataclass
class Slide:
    sid: int
    title: str
    bullets: list[str]
    theme: str = "light"          # light | dark | blue
    extra: str = "none"           # none | table | chart


@dataclass
class Shown:
    """Один показ слайда: id, сколько пунктов видно, длительность, переход."""
    sid: int
    visible: int
    duration: float
    fade: float = 0.0
    toast: tuple | None = None     # (сдвиг от начала показа, длительность) всплывающего уведомления


@dataclass
class Scenario:
    name: str
    width: int
    height: int
    slide_rect: tuple[int, int, int, int]          # x, y, w, h
    cam_rect: tuple[int, int, int, int] | None
    chat_rect: tuple[int, int, int, int] | None
    slides: dict[int, Slide]
    timeline: list = field(default_factory=list)   # Shown | ("camera", dur)
    cursor_periods: list = field(default_factory=list)
    background: tuple[int, int, int] = (30, 30, 30)
    extra_cams: list = field(default_factory=list)    # ещё окна камер (галерея)


# ---------------------------------------------------------------- отрисовка

def render_slide(slide: Slide, visible: int, w: int, h: int) -> np.ndarray:
    themes = {
        "light": ((255, 255, 255), (20, 20, 20), (0, 90, 170)),
        "dark": ((28, 32, 44), (235, 235, 235), (255, 170, 40)),
        "blue": ((232, 240, 250), (15, 30, 60), (200, 40, 40)),
    }
    bg, fg, accent = themes[slide.theme]
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    s = w / 960.0
    d.rectangle([0, 0, w, int(70 * s)], fill=accent)
    d.text((int(30 * s), int(14 * s)), slide.title, font=_font(int(34 * s)), fill=(255, 255, 255))
    d.text((int(w - 120 * s), int(h - 30 * s)), f"{slide.sid + 1}", font=_font(int(16 * s)), fill=fg)
    y = int(100 * s)
    for i, line in enumerate(slide.bullets[:visible]):
        d.ellipse([int(36 * s), y + int(10 * s), int(46 * s), y + int(20 * s)], fill=accent)
        d.text((int(60 * s), y), line, font=_font(int(24 * s)), fill=fg)
        y += int(44 * s)
    if slide.extra == "table" and visible >= len(slide.bullets):
        x0, y0, cw, ch = int(60 * s), y + int(10 * s), int(160 * s), int(34 * s)
        rnd = random.Random(slide.sid)
        for r in range(4):
            for c in range(5):
                d.rectangle([x0 + c * cw, y0 + r * ch, x0 + (c + 1) * cw, y0 + (r + 1) * ch], outline=fg)
                d.text((x0 + c * cw + 8, y0 + r * ch + 6), f"{rnd.randint(10, 9999)}",
                       font=_font(int(18 * s)), fill=fg)
    if slide.extra == "chart" and visible >= len(slide.bullets):
        rnd = random.Random(slide.sid * 7)
        base = h - int(60 * s)
        for i in range(8):
            bh = rnd.randint(int(40 * s), int(200 * s))
            x = int(520 * s) + i * int(48 * s)
            d.rectangle([x, base - bh, x + int(34 * s), base], fill=accent)
    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)


class Webcam:
    """Синтетический ведущий: качается, моргает, «говорит», шумит матрица."""

    def __init__(self, w: int, h: int, seed: int = 1):
        self.w, self.h = w, h
        self.rng = np.random.default_rng(seed)
        bg = np.zeros((h, w, 3), np.uint8)
        for yy in range(h):
            bg[yy, :] = (90 + 60 * yy // h, 110 + 40 * yy // h, 130 + 30 * yy // h)
        r = random.Random(seed)
        for _ in range(12):  # «книжная полка» на фоне
            x = r.randint(0, w - 20)
            y = r.randint(0, h // 2)
            cv2.rectangle(bg, (x, y), (x + r.randint(8, 25), y + r.randint(25, 60)),
                          (r.randint(30, 200), r.randint(30, 200), r.randint(30, 200)), -1)
        self.bg = bg
        self.walk = np.zeros(2)

    def frame(self, t: float) -> np.ndarray:
        img = self.bg.copy()
        w, h = self.w, self.h
        self.walk = 0.97 * self.walk + self.rng.normal(0, 1.2, 2)
        cx = int(w / 2 + 0.12 * w * math.sin(t * 0.9) + self.walk[0] * w / 100)
        cy = int(h * 0.45 + 0.05 * h * math.sin(t * 1.7) + self.walk[1] * h / 120)
        hw, hh = int(w * 0.13), int(h * 0.22)
        cv2.ellipse(img, (cx, cy + int(h * 0.62)), (int(w * 0.3), int(h * 0.38)), 0, 0, 360, (60, 60, 160), -1)
        cv2.ellipse(img, (cx, cy), (hw, hh), 0, 0, 360, (150, 180, 225), -1)
        cv2.ellipse(img, (cx, cy - int(hh * 0.8)), (int(hw * 1.05), int(hh * 0.45)), 0, 180, 360, (30, 40, 60), -1)
        blink = (t % 4.0) < 0.15
        for ex in (-1, 1):
            p = (cx + ex * hw // 3, cy - hh // 5)
            if blink:
                cv2.line(img, (p[0] - 6, p[1]), (p[0] + 6, p[1]), (40, 40, 40), 2)
            else:
                cv2.circle(img, p, max(3, hw // 9), (40, 40, 40), -1)
        mouth = int(abs(math.sin(t * 9.0 + math.sin(t * 2.3) * 3)) * hh * 0.18) + 2
        cv2.ellipse(img, (cx, cy + hh // 2), (hw // 3, mouth), 0, 0, 360, (50, 50, 120), -1)
        gain = 1.0 + 0.03 * math.sin(t * 0.37)
        noise = self.rng.normal(0, 4.0, img.shape)
        return np.clip(img * gain + noise, 0, 255).astype(np.uint8)


class Chat:
    """Чат: новые сообщения появляются случайно, старые уезжают вверх."""

    def __init__(self, w: int, h: int, total: float, seed: int = 3):
        self.w, self.h = w, h
        r = random.Random(seed)
        names = ["Анна", "Игорь", "Мария", "Олег", "Светлана", "Дмитрий", "Ольга"]
        self.msgs = []
        t = 3.0
        while t < total:
            text = " ".join(r.choice(WORDS) for _ in range(r.randint(2, 5)))
            self.msgs.append((t, f"{r.choice(names)}: {text}"))
            t += r.uniform(4.0, 16.0)
        self.cache: dict[int, np.ndarray] = {}

    def frame(self, t: float) -> np.ndarray:
        n = sum(1 for mt, _ in self.msgs if mt <= t)
        if n not in self.cache:
            img = Image.new("RGB", (self.w, self.h), (245, 245, 245))
            d = ImageDraw.Draw(img)
            d.rectangle([0, 0, self.w, 30], fill=(210, 210, 215))
            d.text((10, 6), "Чат", font=_font(16), fill=(0, 0, 0))
            lines = [m for _, m in self.msgs[:n]][-((self.h - 40) // 24):]
            for i, m in enumerate(lines):
                d.text((10, 40 + i * 24), m[:40], font=_font(14), fill=(30, 30, 30))
            self.cache[n] = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        return self.cache[n]


def draw_cursor(img: np.ndarray, x: int, y: int) -> None:
    pts = np.array([[0, 0], [0, 22], [6, 17], [10, 26], [14, 24], [10, 16], [17, 16]]) + [x, y]
    cv2.fillPoly(img, [pts.astype(np.int32)], (255, 255, 255))
    cv2.polylines(img, [pts.astype(np.int32)], True, (0, 0, 0), 1)


# ---------------------------------------------------------------- сценарии

def _deck(n: int, seed: int, themes=("light",)) -> dict[int, Slide]:
    r = random.Random(seed)
    slides = {}
    for i in range(n):
        bullets = [" ".join(r.choice(WORDS) for _ in range(r.randint(3, 6))).capitalize()
                   for _ in range(r.randint(2, 5))]
        extra = r.choice(["none", "none", "table", "chart"])
        slides[i] = Slide(i, TITLES[i % len(TITLES)], bullets, themes[i % len(themes)], extra)
    return slides


def make_scenario(name: str, seed: int = 0) -> Scenario:
    W, H = 1280, 720
    if name == "zoom":
        slides = _deck(8, seed + 10)
        sc = Scenario(name, W, H, (0, 90, 960, 540), (970, 10, 300, 169), None, slides)
        full = lambda i: len(slides[i].bullets)  # noqa: E731
        sc.timeline = [
            Shown(0, full(0), 20), Shown(1, full(1), 25, fade=0.7), Shown(2, 1, 8),
            Shown(2, 2, 7), Shown(2, full(2), 18), Shown(3, full(3), 22, fade=0.5),
            Shown(4, full(4), 16), Shown(5, full(5), 20, fade=0.7), Shown(3, full(3), 12),
            Shown(6, full(6), 25), Shown(7, full(7), 18, fade=0.5),
        ]
        sc.cursor_periods = [(30, 45), (95, 120), (170, 185)]
    elif name == "pip":
        slides = _deck(6, seed + 20, themes=("dark", "light"))
        sc = Scenario(name, W, H, (0, 0, W, H), (1004, 556, 256, 144), None, slides)
        sc.timeline = [Shown(i, len(slides[i].bullets), d, fade=f) for i, d, f in
                       [(0, 18, 0), (1, 22, 0.5), (2, 15, 0), (3, 25, 0.8), (4, 14, 0), (5, 20, 0.5)]]
        sc.cursor_periods = [(10, 30), (60, 80)]
    elif name == "web":
        slides = _deck(7, seed + 30, themes=("blue",))
        sc = Scenario(name, W, H, (20, 60, 860, 484), (900, 60, 360, 202), (900, 280, 360, 420),
                      slides, background=(250, 250, 252))
        full = lambda i: len(slides[i].bullets)  # noqa: E731
        sc.timeline = [Shown(0, full(0), 15), Shown(1, 1, 6), Shown(1, 2, 6), Shown(1, full(1), 14),
                       Shown(2, full(2), 20, fade=0.5), Shown(3, full(3), 18), Shown(4, full(4), 16),
                       Shown(1, full(1), 10), Shown(5, full(5), 18, fade=0.5), Shown(6, full(6), 15)]
        sc.cursor_periods = [(20, 40), (100, 130)]
    elif name == "toast":
        # чёрная заставка, всплывающее уведомление поверх слайда, анимация пунктов
        slides = _deck(4, seed + 80)
        sc = Scenario(name, W, H, (0, 90, 960, 540), (970, 10, 300, 169), None, slides)
        full = lambda i: len(slides[i].bullets)  # noqa: E731
        sc.timeline = [("blank", 5), Shown(0, full(0), 14, toast=(5, 4)), Shown(1, 1, 6),
                       Shown(1, full(1), 12), Shown(2, full(2), 12, fade=0.5, toast=(3, 4)),
                       Shown(3, full(3), 10)]
        sc.cursor_periods = [(30, 38)]
    elif name == "mini":
        # короткий ролик для автотестов: анимация, повтор, курсор, камера сбоку
        slides = _deck(4, seed + 70)
        slides[1].bullets = slides[1].bullets[:3] + ["Итог раздела"] * (3 - len(slides[1].bullets[:3]))
        sc = Scenario(name, W, H, (0, 90, 960, 540), (970, 10, 300, 169), None, slides)
        full = lambda i: len(slides[i].bullets)  # noqa: E731
        sc.timeline = [Shown(0, full(0), 6), Shown(1, 1, 4), Shown(1, full(1), 6, fade=0.4),
                       Shown(2, full(2), 6), Shown(0, full(0), 5), Shown(3, full(3), 6, fade=0.4)]
        sc.cursor_periods = [(7, 12)]
    elif name == "dark43":
        # слайды 4:3 в тёмной теме по центру, чёрные поля слева/справа, камера
        # слева сверху, в середине — быстрое листание по 2–3 секунды
        slides = _deck(9, seed + 50, themes=("dark",))
        sc = Scenario(name, W, H, (280, 0, 960, 720), (8, 8, 256, 144), None, slides,
                      background=(12, 12, 12))
        full = lambda i: len(slides[i].bullets)  # noqa: E731
        sc.timeline = [Shown(0, full(0), 15), Shown(1, full(1), 14, fade=0.4),
                       Shown(2, full(2), 3), Shown(3, full(3), 2.5), Shown(4, full(4), 3),
                       Shown(5, full(5), 2), Shown(6, full(6), 16), Shown(2, full(2), 12),
                       Shown(7, full(7), 14, fade=0.4), Shown(8, full(8), 12)]
        sc.cursor_periods = [(5, 12), (60, 75)]
    elif name == "teams":
        # низкое разрешение, слайд сверху слева, внизу полоса из трёх камер
        slides = _deck(6, seed + 60, themes=("light", "blue"))
        sc = Scenario(name, W, H, (160, 20, 960, 540), (160, 575, 300, 140), None, slides,
                      background=(40, 36, 32))
        sc.extra_cams = [(490, 575, 300, 140), (820, 575, 300, 140)]
        sc.timeline = [Shown(i, len(slides[i].bullets), d, fade=f) for i, d, f in
                       [(0, 16, 0), (1, 18, 0.5), (2, 12, 0), (3, 20, 0.6), (4, 15, 0), (5, 14, 0.4)]]
        sc.cursor_periods = [(20, 35)]
    elif name == "mixed":
        slides = _deck(6, seed + 40)
        sc = Scenario(name, W, H, (0, 90, 960, 540), (970, 10, 300, 169), None, slides)
        full = lambda i: len(slides[i].bullets)  # noqa: E731
        sc.timeline = [("camera", 20)] + [Shown(i, full(i), d, fade=0.5) for i, d in
                                          [(0, 18), (1, 20), (2, 16), (3, 22)]] + \
                      [("camera", 25)] + [Shown(4, full(4), 18), Shown(5, full(5), 15)]
        sc.cursor_periods = [(40, 60)]
    else:
        raise ValueError(name)
    return sc


# ---------------------------------------------------------------- рендер видео

def expected(sc: Scenario) -> dict:
    """Эталон: уникальные слайды (анимации и повторы склеены) и их показы."""
    t = 0.0
    shows = []
    for item in sc.timeline:
        if isinstance(item, Shown):
            shows.append({"sid": item.sid, "visible": item.visible, "start": t, "end": t + item.duration})
            t += item.duration
        else:
            t += item[1]   # камера на весь экран или пустой экран — слайдов нет
    unique: list[int] = []
    for s in shows:
        if s["sid"] not in unique:
            unique.append(s["sid"])
    first = {sid: min(s["start"] for s in shows if s["sid"] == sid) for sid in unique}
    # ожидаемые скриншоты: шаги анимации подряд — один кадр, повторный показ — новый кадр
    screens: list[dict] = []
    for s in shows:
        last = screens[-1] if screens else None
        if last is not None and last["sid"] == s["sid"] and abs(last["end"] - s["start"]) < 1e-6:
            last["end"] = s["end"]
            continue
        rep = next((i + 1 for i, e in enumerate(screens) if e["sid"] == s["sid"]), None)
        screens.append({"sid": s["sid"], "start": s["start"], "end": s["end"], "repeat_of": rep})
    return {
        "scenario": sc.name, "width": sc.width, "height": sc.height,
        "slide_rect": list(sc.slide_rect), "cam_rect": list(sc.cam_rect) if sc.cam_rect else None,
        "duration": t, "unique_slides": len(unique),
        "slides": [{"sid": sid, "first_shown": first[sid]} for sid in unique],
        "screens": screens,
        "shows": shows,
    }


def render(sc: Scenario, out_path: str, fps: float = 15, gop_seconds: float = 2.0,
           bframes: int = 0, crf: int = 28, seed: int = 0) -> dict:
    gt = expected(sc)
    total = gt["duration"]
    W, H = sc.width, sc.height
    sx, sy, sw, sh = sc.slide_rect
    rng = random.Random(seed)
    cam = Webcam(sc.cam_rect[2], sc.cam_rect[3], seed) if sc.cam_rect else None
    extra = [(r, Webcam(r[2], r[3], seed + 11 + i)) for i, r in enumerate(sc.extra_cams)]
    bigcam = Webcam(W, H, seed + 5)
    chat = Chat(sc.chat_rect[2], sc.chat_rect[3], total, seed) if sc.chat_rect else None

    base = np.zeros((H, W, 3), np.uint8)
    base[:] = sc.background
    if sc.name == "zoom" or sc.name == "mixed":
        cv2.putText(base, "Participants (3)", (975, 215), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)
        for i, n in enumerate(["Lecturer", "Anna", "Oleg"]):
            cv2.putText(base, n, (985, 250 + 28 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (170, 170, 170), 1)
    if sc.name == "web":
        cv2.rectangle(base, (0, 0), (W, 44), (60, 70, 90), -1)
        cv2.putText(base, "WEBINAR  |  Lesson 2", (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)

    slide_cache: dict = {}

    def slide_img(sid, vis):
        key = (sid, vis)
        if key not in slide_cache:
            slide_cache[key] = render_slide(sc.slides[sid], vis, sw, sh)
        return slide_cache[key]

    # раскладываем таймлайн в отрезки
    segs = []
    t = 0.0
    prev = None
    for item in sc.timeline:
        if isinstance(item, Shown):
            segs.append((t, t + item.duration, item, prev))
            prev = item
            t += item.duration
        else:
            segs.append((t, t + item[1], item[0], None))
            prev = None
            t += item[1]

    # траектория курсора
    def cursor_pos(tt):
        for a, b in sc.cursor_periods:
            if a <= tt < b:
                k = (tt - a)
                x = sx + sw * (0.5 + 0.35 * math.sin(k * 0.8 + a)) + 20 * math.sin(k * 3.1)
                y = sy + sh * (0.5 + 0.3 * math.sin(k * 0.55 + a * 2))
                if int(k) % 5 in (3, 4):  # курсор иногда замирает
                    k0 = int(k) - int(k) % 5 + 3
                    x = sx + sw * (0.5 + 0.35 * math.sin(k0 * 0.8 + a)) + 20 * math.sin(k0 * 3.1)
                    y = sy + sh * (0.5 + 0.3 * math.sin(k0 * 0.55 + a * 2))
                return int(x), int(y)
        return None

    gop = max(1, int(round(gop_seconds * fps)))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{W}x{H}", "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", str(crf), "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0",
           "-bf", str(bframes), "-pix_fmt", "yuv420p", out_path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    n_frames = int(total * fps)
    for fi in range(n_frames):
        tt = fi / fps
        seg = next(s for s in segs if s[0] <= tt < s[1]) if tt < segs[-1][1] else segs[-1]
        a, b, item, prev = seg
        if item == "camera":
            frame = bigcam.frame(tt)
        elif item == "blank":
            frame = base.copy()
            frame[sy:sy + sh, sx:sx + sw] = 0
            if cam is not None:
                cx, cy, cw, ch = sc.cam_rect
                frame[cy:cy + ch, cx:cx + cw] = cam.frame(tt)
        else:
            frame = base.copy()
            cur = slide_img(item.sid, item.visible)
            if item.fade > 0 and prev is not None and tt - a < item.fade:
                alpha = (tt - a) / item.fade
                cur = cv2.addWeighted(slide_img(prev.sid, prev.visible), 1 - alpha, cur, alpha, 0)
            frame[sy:sy + sh, sx:sx + sw] = cur
            if item.toast and item.toast[0] <= tt - a < item.toast[0] + item.toast[1]:
                tx, ty = sx + sw - 330, sy + sh - 90
                cv2.rectangle(frame, (tx, ty), (tx + 310, ty + 70), (60, 60, 60), -1)
                cv2.rectangle(frame, (tx, ty), (tx + 310, ty + 70), (120, 120, 120), 1)
                cv2.putText(frame, "Ivan joined the meeting", (tx + 16, ty + 42),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
            if cam is not None:
                cx, cy, cw, ch = sc.cam_rect
                frame[cy:cy + ch, cx:cx + cw] = cam.frame(tt)
            for (ex, ey, ew, eh), ecam in extra:
                frame[ey:ey + eh, ex:ex + ew] = ecam.frame(tt + 3.7)
            if chat is not None:
                qx, qy, qw, qh = sc.chat_rect
                frame[qy:qy + qh, qx:qx + qw] = chat.frame(tt)
            p = cursor_pos(tt)
            if p is not None:
                draw_cursor(frame, *p)
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    proc.wait()
    gt.update({"fps": fps, "gop_seconds": gop_seconds, "bframes": bframes, "crf": crf, "file": out_path})
    with open(os.path.splitext(out_path)[0] + ".json", "w", encoding="utf-8") as f:
        json.dump(gt, f, ensure_ascii=False, indent=1)
    del rng
    return gt


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out_dir")
    ap.add_argument("--scenario", default="all")
    ap.add_argument("--fps", type=float, default=15)
    ap.add_argument("--gop", type=float, default=2.0)
    ap.add_argument("--bframes", type=int, default=0)
    ap.add_argument("--crf", type=int, default=28)
    ap.add_argument("--size", default=None, help="итоговый размер кадра, например 854x480")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    names = ["zoom", "pip", "web", "mixed", "dark43", "teams", "toast"] if args.scenario == "all" \
        else args.scenario.split(",")
    for n in names:
        path = os.path.join(args.out_dir, f"{n}_g{args.gop:g}_b{args.bframes}.mp4")
        gt = render(make_scenario(n), path, args.fps, args.gop, args.bframes, args.crf)
        if args.size:
            gt = rescale(path, gt, args.size, args.gop, args.bframes, args.crf, args.fps)
        print(f"{path}: {gt['duration']:.0f} s, screens = {len(gt['screens'])}")


def rescale(path: str, gt: dict, size: str, gop: float, bframes: int, crf: int, fps: float) -> dict:
    """Перекодировать видео в другой размер кадра и пересчитать эталонные прямоугольники."""
    w, h = (int(v) for v in size.lower().split("x"))
    kx, ky = w / gt["width"], h / gt["height"]
    tmp = path + ".tmp.mp4"
    g = max(1, int(round(gop * fps)))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-vf", f"scale={w}:{h}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-g", str(g),
                    "-keyint_min", str(g), "-sc_threshold", "0", "-bf", str(bframes),
                    "-pix_fmt", "yuv420p", tmp], check=True)
    os.replace(tmp, path)

    def sc_rect(r):
        x, y, rw, rh = r
        return [round(x * kx), round(y * ky), round((x + rw) * kx) - round(x * kx),
                round((y + rh) * ky) - round(y * ky)]
    gt["slide_rect"] = sc_rect(gt["slide_rect"])
    if gt.get("cam_rect"):
        gt["cam_rect"] = sc_rect(gt["cam_rect"])
    gt["width"], gt["height"] = w, h
    with open(os.path.splitext(path)[0] + ".json", "w", encoding="utf-8") as f:
        json.dump(gt, f, ensure_ascii=False, indent=1)
    return gt


if __name__ == "__main__":
    main()
