"""Графический интерфейс Presenter Reader (PySide6 / Qt 6).

Перетащите записи вебинаров в окно — программа найдёт область презентации,
сохранит по скриншоту на каждый слайд и покажет их галереей.

Запуск: ``presenter-reader-gui`` или ``python -m presenter_reader`` без аргументов.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass, field

from PySide6.QtCore import (QEasingCurve, QObject, QPropertyAnimation, QRect, QRectF, QSettings,
                            QSize, Qt, QUrl, Signal, Property)
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QFontDatabase, QIcon, QImage,
                           QImageReader, QLinearGradient, QPainter, QPainterPath, QPalette, QPen,
                           QPixmap, QStandardItem, QStandardItemModel)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
                               QFileDialog, QFrame, QGraphicsDropShadowEffect,
                               QHBoxLayout, QLabel, QLayout, QLineEdit, QListView, QMainWindow, QMenu,
                               QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
                               QSlider, QStackedWidget, QStyle, QStyledItemDelegate,
                               QStyleOptionViewItem, QToolButton, QVBoxLayout, QWidget)

from . import __version__
from .pipeline import Cancelled, Params, Result, default_out_dir, process
from .report import fmt_time, plural

VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".flv", ".ts", ".mts", ".m2ts",
             ".wmv", ".mpg", ".mpeg", ".3gp", ".ogv"}

# ---------------------------------------------------------------- оформление

THEMES = {
    "dark": dict(bg="#0d0f15", surface="#151821", surface2="#1c2030", hover="#222739",
                 border="#262b3b", text="#e8eaf1", muted="#8b92a7", faint="#5d6478",
                 accent="#7c6cff", accent2="#4f8bff", ok="#34d399", warn="#fbbf24", bad="#f87171",
                 shadow="#000000"),
    "light": dict(bg="#f3f5fa", surface="#ffffff", surface2="#eef1f8", hover="#e6eaf4",
                  border="#dde2ee", text="#151a26", muted="#5f6880", faint="#9aa1b5",
                  accent="#6a5af9", accent2="#3b7cf6", ok="#10b981", warn="#d97706", bad="#ef4444",
                  shadow="#8a93b0"),
}


def stylesheet(t: dict) -> str:
    return f"""
    QWidget {{ color: {t['text']}; font-size: 13.5px; }}
    QMainWindow, #Root {{ background: {t['bg']}; }}
    QToolTip {{ background: {t['surface2']}; color: {t['text']}; border: 1px solid {t['border']};
                padding: 6px 8px; border-radius: 8px; }}
    #Header {{ background: transparent; }}
    #AppTitle {{ font-size: 18px; font-weight: 650; }}
    #AppSub {{ color: {t['muted']}; font-size: 12.5px; }}
    #SectionTitle {{ font-size: 13px; font-weight: 650; color: {t['muted']};
                     letter-spacing: 0.4px; text-transform: uppercase; }}
    #Card, #JobCard, #Panel {{ background: {t['surface']}; border: 1px solid {t['border']};
                               border-radius: 16px; }}
    #JobCard[selected="true"] {{ border: 1.5px solid {t['accent']}; background: {t['surface2']}; }}
    #JobCard:hover {{ background: {t['surface2']}; }}
    #JobName {{ font-weight: 600; font-size: 13.5px; }}
    #JobInfo, #Muted {{ color: {t['muted']}; font-size: 12.5px; }}
    #Chip {{ border-radius: 10px; padding: 2px 9px; font-size: 11.5px; font-weight: 600; }}
    #BigTitle {{ font-size: 20px; font-weight: 700; }}
    #Stat {{ background: {t['surface2']}; border-radius: 10px; padding: 6px 12px; }}
    #StatValue {{ font-size: 16px; font-weight: 700; }}
    #StatLabel {{ color: {t['muted']}; font-size: 11.5px; }}
    QPushButton {{ background: {t['surface2']}; border: 1px solid {t['border']}; border-radius: 10px;
                   padding: 8px 14px; font-weight: 600; }}
    QPushButton:hover {{ background: {t['hover']}; }}
    QPushButton:pressed {{ background: {t['border']}; }}
    QPushButton:disabled {{ color: {t['faint']}; }}
    QPushButton#Primary {{ border: none; color: white; padding: 11px 18px; font-size: 14px;
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t['accent']}, stop:1 {t['accent2']}); }}
    QPushButton#Primary:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {QColor(t['accent']).lighter(112).name()}, stop:1 {QColor(t['accent2']).lighter(112).name()}); }}
    QPushButton#Primary:disabled {{ background: {t['surface2']}; color: {t['faint']}; }}
    QPushButton#Danger {{ color: {t['bad']}; }}
    QToolButton {{ background: transparent; border: none; border-radius: 9px; padding: 6px; }}
    QToolButton:hover {{ background: {t['hover']}; }}
    QProgressBar {{ background: {t['surface2']}; border: none; border-radius: 3px; height: 6px; }}
    QProgressBar::chunk {{ border-radius: 3px;
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t['accent']}, stop:1 {t['accent2']}); }}
    QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
    QScrollBar::handle:vertical {{ background: {t['border']}; border-radius: 3px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {t['faint']}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
    QListView {{ background: transparent; border: none; outline: 0; }}
    QDialog {{ background: {t['bg']}; }}
    QLineEdit, QAbstractSpinBox, QComboBox {{ background: {t['surface2']}; border: 1px solid {t['border']};
        border-radius: 9px; padding: 6px 10px; min-height: 22px; selection-background-color: {t['accent']}; }}
    QAbstractSpinBox QLineEdit {{ background: transparent; border: none; padding: 0; min-height: 0; }}
    QLineEdit:focus, QAbstractSpinBox:focus, QComboBox:focus {{ border: 1px solid {t['accent']}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{ background: {t['surface']}; border: 1px solid {t['border']};
        selection-background-color: {t['hover']}; outline: 0; padding: 4px; }}
    QSlider::groove:horizontal {{ height: 6px; background: {t['surface2']}; border-radius: 3px; }}
    QSlider::sub-page:horizontal {{ border-radius: 3px;
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t['accent']}, stop:1 {t['accent2']}); }}
    QSlider::handle:horizontal {{ background: white; border: 2px solid {t['accent']}; width: 14px;
        height: 14px; margin: -6px 0; border-radius: 9px; }}
    QMenu {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 10px; padding: 6px; }}
    QMenu::item {{ padding: 7px 18px; border-radius: 7px; }}
    QMenu::item:selected {{ background: {t['hover']}; }}
    """


# Иконки — простые линейные SVG (24×24), цвет подставляется при отрисовке.
_SVG = {
    "upload": '<path d="M12 15V4M7 9l5-5 5 5"/><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
    "film": '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    "moon": '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "play": '<path d="M7 4.5v15l12-7.5z"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    "x": '<path d="M18 6 6 18M6 6l12 12"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "alert": '<circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16.5v.5"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="9" cy="10" r="2"/><path d="m21 16-5-5-8 9"/>',
    "report": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>',
    "layers": '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "refresh": '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>',
}


def svg_icon(name: str, color: str, size: int = 20, stroke: float = 1.8) -> QIcon:
    body = _SVG[name]
    fill = color if name in ("play", "stop") else "none"
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{fill}" stroke="{color}" '
           f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
    renderer = QSvgRenderer(svg.encode())
    icon = QIcon()
    for scale in (1, 2):
        pm = QPixmap(size * scale, size * scale)
        pm.fill(Qt.transparent)
        pm.setDevicePixelRatio(scale)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        renderer.render(p, QRectF(0, 0, size, size))
        p.end()
        icon.addPixmap(pm)
    return icon


def app_icon() -> QIcon:
    """Значок приложения: градиентный квадрат со «слайдом» и рамкой выделения."""
    icon = QIcon()
    for s in (16, 32, 64, 128, 256):
        pm = QPixmap(s, s)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        g = QLinearGradient(0, 0, s, s)
        g.setColorAt(0, QColor("#7c6cff"))
        g.setColorAt(1, QColor("#4f8bff"))
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, s, s), s * 0.24, s * 0.24)
        p.fillPath(path, g)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 235))
        p.drawRoundedRect(QRectF(s * 0.2, s * 0.27, s * 0.6, s * 0.4), s * 0.05, s * 0.05)
        p.setBrush(QColor("#6a5af9"))
        p.drawRoundedRect(QRectF(s * 0.26, s * 0.33, s * 0.3, s * 0.06), s * 0.02, s * 0.02)
        p.drawRoundedRect(QRectF(s * 0.26, s * 0.45, s * 0.42, s * 0.05), s * 0.02, s * 0.02)
        p.drawRoundedRect(QRectF(s * 0.26, s * 0.54, s * 0.36, s * 0.05), s * 0.02, s * 0.02)
        p.setBrush(QColor(255, 255, 255, 200))
        p.drawRoundedRect(QRectF(s * 0.38, s * 0.72, s * 0.24, s * 0.05), s * 0.02, s * 0.02)
        p.end()
        icon.addPixmap(pm)
    return icon


def shadow(widget: QWidget, theme: dict, blur: int = 28, alpha: int = 60) -> None:
    eff = QGraphicsDropShadowEffect(widget)
    c = QColor(theme["shadow"])
    c.setAlpha(alpha)
    eff.setColor(c)
    eff.setBlurRadius(blur)
    eff.setOffset(0, 6)
    widget.setGraphicsEffect(eff)


def human_size(n: int) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return f"{n:.0f} {unit}" if unit in ("Б", "КБ") else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def human_duration(sec: float) -> str:
    sec = int(round(sec))
    if sec < 60:
        return f"{sec} с"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m} мин {s:02d} с"
    h, m = divmod(m, 60)
    return f"{h} ч {m:02d} мин"


def collect_videos(paths) -> list[str]:
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, _, files in os.walk(p):
                for f in sorted(files):
                    if os.path.splitext(f)[1].lower() in VIDEO_EXT:
                        out.append(os.path.join(root, f))
        elif os.path.splitext(p)[1].lower() in VIDEO_EXT:
            out.append(p)
    return out


# ---------------------------------------------------------------- модель очереди

@dataclass
class Job:
    path: str
    out_dir: str = ""
    status: str = "queued"          # queued | running | done | error | cancelled
    progress: float = 0.0
    stage: str = ""
    started: float = 0.0
    result: Result | None = None
    error: str = ""
    card: QWidget | None = field(default=None, repr=False)

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


class Bridge(QObject):
    """Сигналы из рабочего потока в интерфейс (Qt сам доставит их в главный поток)."""
    progress = Signal(object, float, str)
    done = Signal(object, object)
    failed = Signal(object, str)
    thumb = Signal(int, int, QImage)


# ---------------------------------------------------------------- виджеты

class Switch(QCheckBox):
    """Переключатель-«таблетка» в современном стиле."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self._pos = 0.0
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)
        self.theme = THEMES["dark"]

    def _animate(self, on):
        self._anim.stop()
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def get_pos(self):
        return self._pos

    def set_pos(self, v):
        self._pos = v
        self.update()

    pos = Property(float, get_pos, set_pos)

    def sizeHint(self):
        fm = self.fontMetrics()
        return QSize(46 + (fm.horizontalAdvance(self.text()) + 12 if self.text() else 0), 26)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def setChecked(self, on):
        super().setChecked(on)
        self._pos = 1.0 if on else 0.0

    def paintEvent(self, _):
        t = self.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = QRectF(0, 3, 42, 22)
        off, on = QColor(t["border"]), QColor(t["accent"])
        c = QColor(int(off.red() + (on.red() - off.red()) * self._pos),
                   int(off.green() + (on.green() - off.green()) * self._pos),
                   int(off.blue() + (on.blue() - off.blue()) * self._pos))
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(track, 11, 11)
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(3 + self._pos * 20, 6, 16, 16))
        if self.text():
            p.setPen(QColor(t["text"]))
            p.drawText(QRect(54, 0, self.width() - 54, self.height()), Qt.AlignVCenter, self.text())
        p.end()


class DropZone(QFrame):
    """Большая зона «перетащите файлы сюда» с подсветкой при наведении файла."""
    clicked = Signal()

    def __init__(self, theme: dict, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(190)
        self._glow = 0.0
        self._anim = QPropertyAnimation(self, b"glow", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 26, 24, 24)
        lay.setSpacing(8)
        lay.addStretch()
        self.icon = QLabel(alignment=Qt.AlignCenter)
        lay.addWidget(self.icon)
        self.title = QLabel("Перетащите сюда записи вебинаров", alignment=Qt.AlignCenter)
        self.title.setStyleSheet("font-size: 15.5px; font-weight: 650;")
        lay.addWidget(self.title)
        self.sub = QLabel("или нажмите, чтобы выбрать файлы · можно и целую папку",
                          alignment=Qt.AlignCenter)
        self.sub.setObjectName("Muted")
        self.sub.setWordWrap(True)
        lay.addWidget(self.sub)
        lay.addStretch()
        self.apply_theme(theme)

    def apply_theme(self, theme):
        self.theme = theme
        self.icon.setPixmap(svg_icon("upload", theme["accent"], 40, 1.6).pixmap(40, 40))
        self.update()

    def get_glow(self):
        return self._glow

    def set_glow(self, v):
        self._glow = v
        self.update()

    glow = Property(float, get_glow, set_glow)

    def highlight(self, on: bool):
        self._anim.stop()
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def enterEvent(self, e):
        self.highlight(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.highlight(False)
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()

    def paintEvent(self, _):
        t = self.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        base = QColor(t["surface"])
        acc = QColor(t["accent"])
        fill = QColor(int(base.red() + (acc.red() - base.red()) * 0.10 * self._glow),
                      int(base.green() + (acc.green() - base.green()) * 0.10 * self._glow),
                      int(base.blue() + (acc.blue() - base.blue()) * 0.10 * self._glow))
        p.setBrush(fill)
        border = QColor(t["border"]) if self._glow < 0.5 else acc
        pen = QPen(border, 1.6 + self._glow)
        pen.setStyle(Qt.DashLine)
        pen.setDashPattern([5, 4])
        p.setPen(pen)
        p.drawRoundedRect(r, 18, 18)
        p.end()


class Chip(QLabel):
    STYLES = {"queued": ("В очереди", "muted"), "running": ("Анализ", "accent"),
              "done": ("Готово", "ok"), "error": ("Ошибка", "bad"), "cancelled": ("Отменено", "warn")}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Chip")

    def set_status(self, status: str, theme: dict, text: str | None = None):
        label, key = self.STYLES[status]
        c = QColor(theme[key])
        bg = QColor(c)
        bg.setAlpha(38)
        self.setText(text or label)
        self.setStyleSheet(f"color: {c.name()}; background: rgba({bg.red()},{bg.green()},{bg.blue()},{bg.alpha()});")


class JobCard(QFrame):
    selected = Signal(object)
    remove = Signal(object)
    open_dir = Signal(object)
    rerun = Signal(object)

    def __init__(self, job: Job, theme: dict, parent=None):
        super().__init__(parent)
        self.job = job
        self.theme = theme
        self.setObjectName("JobCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("selected", False)
        self._info_text = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 12, 12)
        lay.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(12)
        self.ico = QLabel()
        self.ico.setFixedSize(38, 38)
        self.ico.setAlignment(Qt.AlignCenter)
        top.addWidget(self.ico)
        texts = QVBoxLayout()
        texts.setSpacing(3)
        self.name = QLabel(job.name)
        self.name.setObjectName("JobName")
        self.name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.name.setToolTip(job.path)
        texts.addWidget(self.name)
        line2 = QHBoxLayout()
        line2.setSpacing(8)
        self.chip = Chip()
        line2.addWidget(self.chip)
        self.info = QLabel(self._file_info())
        self.info.setObjectName("JobInfo")
        self.info.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        line2.addWidget(self.info, 1)
        texts.addLayout(line2)
        top.addLayout(texts, 1)
        self.btn_again = QToolButton()
        self.btn_again.setToolTip("Обработать заново с текущими настройками")
        self.btn_again.clicked.connect(lambda: self.rerun.emit(self.job))
        top.addWidget(self.btn_again, 0, Qt.AlignTop)
        self.btn_dir = QToolButton()
        self.btn_dir.setToolTip("Открыть папку со слайдами")
        self.btn_dir.clicked.connect(lambda: self.open_dir.emit(self.job))
        top.addWidget(self.btn_dir, 0, Qt.AlignTop)
        self.btn_rm = QToolButton()
        self.btn_rm.setToolTip("Убрать из очереди")
        self.btn_rm.clicked.connect(lambda: self.remove.emit(self.job))
        top.addWidget(self.btn_rm, 0, Qt.AlignTop)
        lay.addLayout(top)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        lay.addWidget(self.bar)
        self.apply_theme(theme)
        self.refresh()

    def _file_info(self) -> str:
        try:
            return human_size(os.path.getsize(self.job.path))
        except OSError:
            return ""

    def apply_theme(self, theme):
        self.theme = theme
        acc = QColor(theme["accent"])
        self.ico.setStyleSheet(f"background: rgba({acc.red()},{acc.green()},{acc.blue()},36); border-radius: 11px;")
        self.ico.setPixmap(svg_icon("film", theme["accent"], 20).pixmap(20, 20))
        self.btn_again.setIcon(svg_icon("refresh", theme["muted"], 18))
        self.btn_dir.setIcon(svg_icon("folder", theme["muted"], 18))
        self.btn_rm.setIcon(svg_icon("x", theme["muted"], 18))
        self.refresh()

    def set_selected(self, on: bool):
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.selected.emit(self.job)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def _elide(self):
        fm = self.name.fontMetrics()
        self.name.setText(fm.elidedText(self.job.name, Qt.ElideMiddle, max(40, self.name.width())))
        fm = self.info.fontMetrics()
        self.info.setText(fm.elidedText(self._info_text, Qt.ElideRight, max(40, self.info.width())))

    def _set_info(self, text: str):
        self._info_text = text
        self._elide()

    def refresh(self):
        j, t = self.job, self.theme
        self.btn_again.setVisible(j.status in ("done", "error", "cancelled"))
        self.btn_dir.setVisible(j.status == "done")
        self.btn_rm.setVisible(j.status != "running")
        self.bar.setVisible(j.status in ("running", "queued") and j.progress > 0 or j.status == "running")
        self.bar.setValue(int(j.progress * 1000))
        if j.status == "running":
            pct = f"{j.progress * 100:.0f}%"
            self.chip.set_status("running", t, pct)
            eta = ""
            if j.progress > 0.04:
                el = time.monotonic() - j.started
                rest = el * (1 - j.progress) / j.progress
                eta = f" · осталось ~{human_duration(rest)}"
            self._set_info(f"{j.stage}{eta}")
        elif j.status == "done" and j.result is not None:
            n = len(j.result.slides)
            self.chip.set_status("done", t, f"{n} {plural(n, 'слайд', 'слайда', 'слайдов')}")
            dur = j.result.info.get("duration", 0)
            self._set_info(f"запись {human_duration(dur)} · обработано за {human_duration(j.result.seconds)}")
        elif j.status == "error":
            self.chip.set_status("error", t)
            self._set_info(j.error[:300])
            self.info.setToolTip(j.error)
        else:
            self.chip.set_status(j.status, t)
            self._set_info(self._file_info())


class SlideDelegate(QStyledItemDelegate):
    """Карточка слайда в галерее: миниатюра со скруглением, номер, время, пометка повтора."""
    W, H = 268, 196

    def __init__(self, theme: dict, parent=None):
        super().__init__(parent)
        self.theme = theme

    def sizeHint(self, option, index):
        return QSize(self.W, self.H)

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index):
        t = self.theme
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(option.rect).adjusted(6, 6, -6, -6)
        hover = option.state & QStyle.State_MouseOver
        sel = option.state & QStyle.State_Selected
        card = QPainterPath()
        card.addRoundedRect(r, 14, 14)
        p.fillPath(card, QColor(t["surface2"] if hover or sel else t["surface"]))
        p.setPen(QPen(QColor(t["accent"] if sel else t["border"]), 1.4 if sel else 1))
        p.drawPath(card)
        img_r = QRectF(r.left() + 8, r.top() + 8, r.width() - 16, (r.width() - 16) * 9 / 16)
        clip = QPainterPath()
        clip.addRoundedRect(img_r, 9, 9)
        pm = index.data(Qt.DecorationRole)
        p.setClipPath(clip)
        if isinstance(pm, QPixmap) and not pm.isNull():
            s = pm.size().scaled(img_r.size().toSize(), Qt.KeepAspectRatio)
            x = img_r.left() + (img_r.width() - s.width()) / 2
            y = img_r.top() + (img_r.height() - s.height()) / 2
            p.fillRect(img_r, QColor(t["bg"]))
            p.drawPixmap(QRectF(x, y, s.width(), s.height()), pm, QRectF(pm.rect()))
        else:
            p.fillRect(img_r, QColor(t["bg"]))
        p.setClipping(False)
        data = index.data(Qt.UserRole) or {}
        y = img_r.bottom() + 10
        f = QFont(option.font)
        f.setPixelSize(13)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor(t["text"]))
        num = f"№ {data.get('number', '')}"
        p.drawText(QRectF(r.left() + 12, y, 80, 20), Qt.AlignLeft | Qt.AlignVCenter, num)
        f.setWeight(QFont.Normal)
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(QColor(t["muted"]))
        caption = f"{data.get('time', '')} · {data.get('shown', '')}"
        p.drawText(QRectF(r.left() + 56, y, r.width() - 70, 20), Qt.AlignLeft | Qt.AlignVCenter, caption)
        n = data.get("appearances", 1)
        if n > 1:   # дубли не сохранялись — сколько раз слайд показывали
            x = r.left() + 56 + p.fontMetrics().horizontalAdvance(caption + " · ")
            p.setPen(QColor(t["accent"]))
            p.drawText(QRectF(x, y, r.right() - x - 8, 20), Qt.AlignLeft | Qt.AlignVCenter,
                       f"{n} {plural(n, 'показ', 'показа', 'показов')}")
        rep = data.get("repeat")
        if rep:
            # пометка повтора — внизу миниатюры, чтобы не закрывать заголовок слайда
            text = f"повтор №{rep[0]} · {rep[1]:.0f}%"
            w = p.fontMetrics().horizontalAdvance(text) + 16
            badge = QRectF(img_r.right() - w - 6, img_r.bottom() - 26, w, 20)
            path = QPainterPath()
            path.addRoundedRect(badge, 10, 10)
            p.fillPath(path, QColor(0, 0, 0, 160))
            p.setPen(QColor(t["warn"]))
            p.drawText(badge, Qt.AlignCenter, text)
        p.restore()


class ClickLabel(QLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()


class StatBox(QFrame):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.setObjectName("Stat")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(0)
        self.value = QLabel("—")
        self.value.setObjectName("StatValue")
        lay.addWidget(self.value)
        self.label = QLabel(label)
        self.label.setObjectName("StatLabel")
        lay.addWidget(self.label)


class ResultsView(QWidget):
    """Правая часть окна: сводка по выбранному видео и галерея слайдов."""

    def __init__(self, theme: dict, bridge: Bridge, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.bridge = bridge
        self.job: Job | None = None
        self._gen = 0
        self.stack = QStackedWidget(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.stack)

        # пустое состояние
        empty = QWidget()
        el = QVBoxLayout(empty)
        el.addStretch()
        self.empty_icon = QLabel(alignment=Qt.AlignCenter)
        el.addWidget(self.empty_icon)
        self.empty_title = QLabel("Здесь появятся слайды", alignment=Qt.AlignCenter)
        self.empty_title.setStyleSheet("font-size: 17px; font-weight: 650;")
        el.addWidget(self.empty_title)
        self.empty_sub = QLabel("Добавьте запись вебинара слева — программа сама найдёт область "
                                "презентации и сохранит по скриншоту на каждый слайд.",
                                alignment=Qt.AlignCenter)
        self.empty_sub.setObjectName("Muted")
        self.empty_sub.setWordWrap(True)
        self.empty_sub.setFixedWidth(440)
        el.addWidget(self.empty_sub, 0, Qt.AlignHCenter)
        el.addStretch()
        self.stack.addWidget(empty)

        # результаты
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(4, 0, 0, 0)
        pl.setSpacing(14)
        head = QHBoxLayout()
        head.setSpacing(16)
        titles = QVBoxLayout()
        titles.setSpacing(4)
        self.title = QLabel()
        self.title.setObjectName("BigTitle")
        self.title.setMinimumWidth(100)
        titles.addWidget(self.title)
        self.subtitle = QLabel()
        self.subtitle.setObjectName("Muted")
        titles.addWidget(self.subtitle)
        head.addLayout(titles, 1)
        self.layout_thumb = ClickLabel()
        self.layout_thumb.setFixedSize(192, 108)
        self.layout_thumb.setCursor(Qt.PointingHandCursor)
        self.layout_thumb.setToolTip("Найденная область презентации (зелёная рамка).\nНажмите, чтобы открыть.")
        self.layout_thumb.clicked.connect(lambda: self._open(self._layout_path()))
        head.addWidget(self.layout_thumb)
        pl.addLayout(head)
        stats = QHBoxLayout()
        stats.setSpacing(10)
        self.st_slides = StatBox("слайдов")
        self.st_repeats = StatBox("повторов")
        self.st_region = StatBox("область")
        self.st_time = StatBox("обработка")
        for w in (self.st_slides, self.st_repeats, self.st_region, self.st_time):
            stats.addWidget(w)
        stats.addStretch()
        self.btn_dir = QPushButton(" Открыть папку")
        self.btn_dir.clicked.connect(lambda: self._open(self.job.out_dir if self.job else None))
        stats.addWidget(self.btn_dir)
        self.btn_report = QPushButton(" Отчёт")
        self.btn_report.setToolTip("HTML-отчёт с тайм-кодами")
        self.btn_report.clicked.connect(lambda: self._open(
            os.path.join(self.job.out_dir, "index.html") if self.job else None))
        stats.addWidget(self.btn_report)
        pl.addLayout(stats)
        self.view = QListView()
        self.view.setViewMode(QListView.IconMode)
        self.view.setResizeMode(QListView.Adjust)
        self.view.setMovement(QListView.Static)
        self.view.setUniformItemSizes(True)
        self.view.setSpacing(2)
        self.view.setMouseTracking(True)
        self.view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._menu)
        self.view.doubleClicked.connect(lambda i: self._open(i.data(Qt.UserRole + 1)))
        self.model = QStandardItemModel(self)
        self.view.setModel(self.model)
        self.delegate = SlideDelegate(theme, self.view)
        self.view.setItemDelegate(self.delegate)
        pl.addWidget(self.view, 1)
        self.stack.addWidget(page)

        # прогресс выбранного видео
        prog = QWidget()
        gl = QVBoxLayout(prog)
        gl.addStretch()
        self.prog_title = QLabel(alignment=Qt.AlignCenter)
        self.prog_title.setStyleSheet("font-size: 17px; font-weight: 650;")
        gl.addWidget(self.prog_title)
        self.prog_stage = QLabel(alignment=Qt.AlignCenter)
        self.prog_stage.setObjectName("Muted")
        gl.addWidget(self.prog_stage)
        self.prog_bar = QProgressBar()
        self.prog_bar.setRange(0, 1000)
        self.prog_bar.setTextVisible(False)
        self.prog_bar.setFixedHeight(8)
        self.prog_bar.setMaximumWidth(420)
        gl.addSpacing(8)
        gl.addWidget(self.prog_bar, 0, Qt.AlignHCenter)
        gl.addStretch()
        self.stack.addWidget(prog)

        bridge.thumb.connect(self._on_thumb)
        self.apply_theme(theme)

    def apply_theme(self, theme):
        self.theme = theme
        self.delegate.theme = theme
        self.empty_icon.setPixmap(svg_icon("layers", theme["faint"], 56, 1.4).pixmap(56, 56))
        self.btn_dir.setIcon(svg_icon("folder", theme["text"], 18))
        self.btn_report.setIcon(svg_icon("report", theme["text"], 18))
        self.view.viewport().update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit_grid()

    def _fit_grid(self):
        avail = self.view.viewport().width() - 4
        cols = max(1, avail // 250)
        w = max(220, avail // cols)
        h = int((w - 28) * 9 / 16) + 58
        self.delegate.W, self.delegate.H = w, h
        self.view.setGridSize(QSize(w, h))

    def _layout_path(self):
        return os.path.join(self.job.out_dir, "layout.png") if self.job else None

    def _open(self, path):
        if path and os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _menu(self, pos):
        idx = self.view.indexAt(pos)
        if not idx.isValid():
            return
        path = idx.data(Qt.UserRole + 1)
        m = QMenu(self)
        m.addAction("Открыть слайд", lambda: self._open(path))
        m.addAction("Показать в папке", lambda: self._open(os.path.dirname(path)))
        m.exec(self.view.viewport().mapToGlobal(pos))

    def show_job(self, job: Job | None):
        self.job = job
        if job is None:
            self.stack.setCurrentIndex(0)
            return
        if job.status != "done" or job.result is None:
            self.stack.setCurrentIndex(2)
            self.update_progress(job)
            return
        self.stack.setCurrentIndex(1)
        self._fit_grid()
        res = job.result
        self.title.setText(job.name)
        dur = res.info.get("duration", 0)
        self.subtitle.setText(f"Запись {human_duration(dur)} · папка «{os.path.basename(res.out_dir)}»")
        self.subtitle.setToolTip(res.out_dir)
        n = len(res.slides)
        reps = sum(1 for s in res.slides if s.repeat_of)
        self.st_slides.value.setText(str(n))
        self.st_slides.label.setText(plural(n, "слайд", "слайда", "слайдов"))
        if res.info.get("skip_duplicates"):
            dups = res.info.get("duplicates_skipped", 0)
            self.st_repeats.value.setText(str(dups))
            self.st_repeats.label.setText(plural(dups, "дубль пропущен", "дубля пропущено",
                                                 "дублей пропущено"))
        else:
            self.st_repeats.value.setText(str(reps))
            self.st_repeats.label.setText(plural(reps, "повтор", "повтора", "повторов"))
        x, y, w, h = res.layout.rect
        self.st_region.value.setText(f"{w}×{h}")
        self.st_region.setToolTip(f"x={x}, y={y}, {w}×{h} — способ: {res.layout.method}")
        self.st_time.value.setText(human_duration(res.seconds))
        lp = self._layout_path()
        if lp and os.path.exists(lp):
            pm = QPixmap(lp).scaled(192, 108, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.layout_thumb.setPixmap(_rounded(pm, 10))
        self.model.clear()
        t0 = res.info.get("start", 0)
        self._gen += 1
        gen = self._gen
        paths = []
        for s, f in zip(res.slides, res.files):
            it = QStandardItem()
            path = os.path.join(res.out_dir, f)
            it.setData({"number": s.number, "time": fmt_time(s.t_start - t0),
                        "shown": human_duration(s.shown_for), "appearances": s.appearances,
                        "repeat": (s.repeat_of, s.similarity) if s.repeat_of else None}, Qt.UserRole)
            it.setData(path, Qt.UserRole + 1)
            it.setToolTip(f"{f}\nПоказан с {fmt_time(s.t_start - t0)}, всего {human_duration(s.shown_for)}")
            it.setEditable(False)
            self.model.appendRow(it)
            paths.append(path)
        threading.Thread(target=self._load_thumbs, args=(gen, paths), daemon=True).start()

    def _load_thumbs(self, gen, paths):
        for i, path in enumerate(paths):
            if gen != self._gen:
                return
            r = QImageReader(path)
            sz = r.size()
            if sz.isValid():
                r.setScaledSize(sz.scaled(QSize(512, 288), Qt.KeepAspectRatio))
            img = r.read()
            if not img.isNull():
                self.bridge.thumb.emit(gen, i, img)

    def _on_thumb(self, gen, row, img):
        if gen != self._gen or row >= self.model.rowCount():
            return
        self.model.item(row).setData(QPixmap.fromImage(img), Qt.DecorationRole)

    def update_progress(self, job: Job):
        if job is not self.job or self.stack.currentIndex() != 2:
            return
        self.prog_title.setText(job.name)
        if job.status == "running":
            self.prog_stage.setText(f"{job.stage}  {job.progress * 100:.0f}%")
        elif job.status == "queued":
            self.prog_stage.setText("Ждёт своей очереди")
        elif job.status == "error":
            self.prog_stage.setText(f"Ошибка: {job.error}")
        elif job.status == "cancelled":
            self.prog_stage.setText("Обработка остановлена")
        self.prog_bar.setValue(int(job.progress * 1000))


def _rounded(pm: QPixmap, radius: float) -> QPixmap:
    out = QPixmap(pm.size())
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(out.rect()), radius, radius)
    p.setClipPath(path)
    p.drawPixmap(0, 0, pm)
    p.end()
    return out


class SettingsDialog(QDialog):
    def __init__(self, settings: QSettings, theme: dict, parent=None):
        super().__init__(parent)
        self.s = settings
        self.setWindowTitle("Настройки")
        lay = QVBoxLayout(self)
        lay.setSizeConstraint(QLayout.SetFixedSize)   # окно по размеру содержимого
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(16)
        title = QLabel("Настройки")
        title.setObjectName("BigTitle")
        lay.addWidget(title)

        def row(label, widget, hint=None):
            box = QVBoxLayout()
            box.setSpacing(5)
            l = QLabel(label)
            l.setStyleSheet("font-weight: 600;")
            box.addWidget(l)
            box.addWidget(widget)
            if hint:
                h = QLabel(hint)
                h.setObjectName("Muted")
                h.setWordWrap(True)
                h.setFixedWidth(500)
                box.addWidget(h)
            lay.addLayout(box)

        out = QWidget()
        ol = QHBoxLayout(out)
        ol.setContentsMargins(0, 0, 0, 0)
        self.out_mode = QComboBox()
        self.out_mode.addItems(["Рядом с видео (папка «<имя>_slides»)", "В выбранную папку"])
        self.out_mode.setCurrentIndex(int(settings.value("out_mode", 0)))
        ol.addWidget(self.out_mode, 1)
        lay_dir = QWidget()
        dl = QHBoxLayout(lay_dir)
        dl.setContentsMargins(0, 0, 0, 0)
        self.out_dir = QLineEdit(settings.value("out_dir", ""))
        self.out_dir.setPlaceholderText("Папка для результатов")
        dl.addWidget(self.out_dir, 1)
        b = QPushButton("Выбрать…")
        b.clicked.connect(self._pick)
        dl.addWidget(b)
        row("Куда сохранять слайды", out)
        lay.addWidget(lay_dir)
        self.out_mode.currentIndexChanged.connect(lambda i: lay_dir.setEnabled(i == 1))
        lay_dir.setEnabled(self.out_mode.currentIndex() == 1)

        def slider(lo, hi, value, fmt, left="", right=""):
            box = QWidget()
            bl = QHBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            if left:
                a = QLabel(left)
                a.setObjectName("Muted")
                bl.addWidget(a)
            sl = QSlider(Qt.Horizontal)
            sl.setRange(lo, hi)
            sl.setValue(value)
            bl.addWidget(sl, 1)
            if right:
                c = QLabel(right)
                c.setObjectName("Muted")
                bl.addWidget(c)
            val = QLabel()
            val.setFixedWidth(52)
            val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            val.setStyleSheet("font-weight: 650;")
            bl.addWidget(val)
            sl.valueChanged.connect(lambda v: val.setText(fmt(v)))
            val.setText(fmt(value))
            return box, sl

        box, self.sens = slider(5, 30, int(round(float(settings.value("sensitivity", 1.0)) * 10)),
                                lambda v: f"×{v / 10:.1f}", "грубее", "чувствительнее")
        row("Чувствительность к изменениям", box,
            "Выше — замечаются мелкие правки на слайде (одно слово, цифра); ниже — меньше ложных срабатываний.")
        box, self.min_dur = slider(1, 20, int(round(float(settings.value("min_duration", 1.0)) * 2)),
                                   lambda v: f"{v / 2:.1f} с".replace(".", ","))
        row("Минимальное время неподвижной картинки", box,
            "Картинка, простоявшая меньше, считается кадром перехода между слайдами.")
        box, self.similar = slider(50, 100, int(float(settings.value("similar", 80))), lambda v: f"{v} %")
        row("Помечать повтор при сходстве от", box,
            "Повторно показанный слайд сохраняется ещё раз, а в имя файла добавляется номер "
            "похожего слайда и процент сходства.")

        self.builds = Switch("Пошаговую анимацию слайда сохранять одним кадром")
        self.builds.theme = theme
        self.builds.setChecked(settings.value("merge_builds", "true") in (True, "true"))
        lay.addWidget(self.builds)
        self.thorough = Switch("Тщательный режим: читать все кадры (медленнее)")
        self.thorough.theme = theme
        self.thorough.setChecked(settings.value("thorough", "false") in (True, "true"))
        lay.addWidget(self.thorough)
        self.autostart = Switch("Начинать обработку сразу после добавления файлов")
        self.autostart.theme = theme
        self.autostart.setChecked(settings.value("autostart", "true") in (True, "true"))
        lay.addWidget(self.autostart)

        lay.addStretch()
        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        ok = QPushButton("Сохранить")
        ok.setObjectName("Primary")
        ok.clicked.connect(self._save)
        btns.addWidget(ok)
        lay.addLayout(btns)

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, "Папка для результатов", self.out_dir.text())
        if d:
            self.out_dir.setText(d)

    def _save(self):
        s = self.s
        s.setValue("out_mode", self.out_mode.currentIndex())
        s.setValue("out_dir", self.out_dir.text())
        s.setValue("sensitivity", self.sens.value() / 10)
        s.setValue("min_duration", self.min_dur.value() / 2)
        s.setValue("similar", self.similar.value())
        s.setValue("merge_builds", "true" if self.builds.isChecked() else "false")
        s.setValue("thorough", "true" if self.thorough.isChecked() else "false")
        s.setValue("autostart", "true" if self.autostart.isChecked() else "false")
        self.accept()


# ---------------------------------------------------------------- главное окно

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("PresenterReader", "PresenterReader")
        self.theme_name = self.settings.value("theme", "") or _system_theme()
        self.theme = THEMES[self.theme_name]
        self.jobs: list[Job] = []
        self.current: Job | None = None
        self.selected: Job | None = None
        self.cancel_event = threading.Event()
        self.stopping = False
        self.worker: threading.Thread | None = None
        self.bridge = Bridge()
        self.bridge.progress.connect(self._on_progress)
        self.bridge.done.connect(self._on_done)
        self.bridge.failed.connect(self._on_failed)
        self._last_ui = 0.0

        self.setWindowTitle("Presenter Reader")
        self.setWindowIcon(app_icon())
        self.resize(1240, 780)
        self.setMinimumSize(980, 620)
        self.setAcceptDrops(True)

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(22, 16, 22, 20)
        outer.setSpacing(16)

        # шапка
        header = QHBoxLayout()
        header.setSpacing(12)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(36, 36))
        header.addWidget(logo)
        tl = QVBoxLayout()
        tl.setSpacing(0)
        t = QLabel("Presenter Reader")
        t.setObjectName("AppTitle")
        tl.addWidget(t)
        st = QLabel("Слайды презентации из записи вебинара — автоматически")
        st.setObjectName("AppSub")
        tl.addWidget(st)
        header.addLayout(tl)
        header.addStretch()
        self.btn_theme = QToolButton()
        self.btn_theme.setToolTip("Светлая / тёмная тема")
        self.btn_theme.setIconSize(QSize(20, 20))
        self.btn_theme.clicked.connect(self.toggle_theme)
        header.addWidget(self.btn_theme)
        self.btn_settings = QToolButton()
        self.btn_settings.setToolTip("Настройки")
        self.btn_settings.setIconSize(QSize(20, 20))
        self.btn_settings.clicked.connect(self.open_settings)
        header.addWidget(self.btn_settings)
        outer.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(18)
        outer.addLayout(body, 1)

        # левая колонка
        left = QVBoxLayout()
        left.setSpacing(12)
        self.drop = DropZone(self.theme)
        self.drop.clicked.connect(self.pick_files)
        left.addWidget(self.drop)
        qh = QHBoxLayout()
        ql = QLabel("Очередь")
        ql.setObjectName("SectionTitle")
        qh.addWidget(ql)
        qh.addStretch()
        self.queue_info = QLabel("")
        self.queue_info.setObjectName("Muted")
        qh.addWidget(self.queue_info)
        left.addLayout(qh)
        self.queue_area = QScrollArea()
        self.queue_area.setWidgetResizable(True)
        self.queue_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.queue_box = QVBoxLayout(holder)
        self.queue_box.setContentsMargins(0, 0, 4, 0)
        self.queue_box.setSpacing(10)
        self.queue_empty = QLabel("Пока пусто. Добавьте одно или несколько видео — они обработаются по очереди.")
        self.queue_empty.setObjectName("Muted")
        self.queue_empty.setWordWrap(True)
        self.queue_box.addWidget(self.queue_empty)
        self.queue_box.addStretch()
        self.queue_area.setWidget(holder)
        left.addWidget(self.queue_area, 1)
        self.sw_dups = Switch("Не сохранять дубли")
        self.sw_dups.setToolTip(
            "Если ведущий вернулся к слайду и он почти не отличается от уже сохранённого\n"
            "(сходство от 95 %), скриншот не делается ещё раз — время повторного показа\n"
            "добавляется к исходному слайду. Повторы с заметными отличиями сохраняются\n"
            "с пометкой, как обычно.\n\n"
            "Действует для следующих обработок; готовое видео можно обработать заново\n"
            "кнопкой ↻ на его карточке.")
        self.sw_dups.setChecked(self.settings.value("skip_duplicates", "false") in (True, "true"))
        self.sw_dups.toggled.connect(
            lambda on: self.settings.setValue("skip_duplicates", "true" if on else "false"))
        left.addWidget(self.sw_dups)
        self.btn_go = QPushButton()
        self.btn_go.setObjectName("Primary")
        self.btn_go.setCursor(Qt.PointingHandCursor)
        self.btn_go.setIconSize(QSize(16, 16))
        self.btn_go.clicked.connect(self.start_or_stop)
        left.addWidget(self.btn_go)
        lw = QWidget()
        lw.setLayout(left)
        lw.setFixedWidth(400)
        body.addWidget(lw)

        # правая колонка
        panel = QFrame()
        panel.setObjectName("Panel")
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(20, 18, 14, 14)
        self.results = ResultsView(self.theme, self.bridge)
        pl.addWidget(self.results)
        body.addWidget(panel, 1)
        self.panel = panel

        self.apply_theme()
        self.update_buttons()

    # ---------------------------------------------------------------- тема
    def apply_theme(self):
        t = self.theme
        app = QApplication.instance()
        app.setStyleSheet(stylesheet(t))
        pal = app.palette()
        pal.setColor(QPalette.Window, QColor(t["bg"]))
        pal.setColor(QPalette.Base, QColor(t["surface"]))
        pal.setColor(QPalette.Text, QColor(t["text"]))
        pal.setColor(QPalette.WindowText, QColor(t["text"]))
        pal.setColor(QPalette.Highlight, QColor(t["accent"]))
        app.setPalette(pal)
        self.btn_theme.setIcon(svg_icon("sun" if self.theme_name == "dark" else "moon", t["muted"], 20))
        self.btn_settings.setIcon(svg_icon("settings", t["muted"], 20))
        self.drop.apply_theme(t)
        self.sw_dups.theme = t
        self.sw_dups.update()
        self.results.apply_theme(t)
        for j in self.jobs:
            if j.card:
                j.card.apply_theme(t)
        shadow(self.panel, t, 36, 70 if self.theme_name == "dark" else 35)
        self.update_buttons()

    def toggle_theme(self):
        self.theme_name = "light" if self.theme_name == "dark" else "dark"
        self.theme = THEMES[self.theme_name]
        self.settings.setValue("theme", self.theme_name)
        self.apply_theme()

    # ---------------------------------------------------------------- файлы
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and collect_videos(u.toLocalFile() for u in e.mimeData().urls()):
            e.acceptProposedAction()
            self.drop.highlight(True)
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self.drop.highlight(False)

    def dropEvent(self, e):
        self.drop.highlight(False)
        files = collect_videos(u.toLocalFile() for u in e.mimeData().urls())
        e.acceptProposedAction()
        self.add_files(files)

    def pick_files(self):
        exts = " ".join(f"*{x}" for x in sorted(VIDEO_EXT))
        files, _ = QFileDialog.getOpenFileNames(self, "Записи вебинаров", self.settings.value("last_dir", ""),
                                                f"Видео ({exts});;Все файлы (*)")
        if files:
            self.settings.setValue("last_dir", os.path.dirname(files[0]))
            self.add_files(files)

    def add_files(self, files):
        known = {os.path.abspath(j.path) for j in self.jobs if j.status in ("queued", "running")}
        added = []
        for f in files:
            if os.path.abspath(f) in known:
                continue
            job = Job(f)
            card = JobCard(job, self.theme)
            card.selected.connect(self.select_job)
            card.remove.connect(self.remove_job)
            card.open_dir.connect(lambda j: QDesktopServices.openUrl(QUrl.fromLocalFile(j.out_dir)))
            card.rerun.connect(self.rerun_job)
            job.card = card
            self.jobs.append(job)
            self.queue_box.insertWidget(self.queue_box.count() - 1, card)
            added.append(job)
        self.queue_empty.setVisible(not self.jobs)
        if added:
            if self.selected is None or self.selected.status != "done":
                self.select_job(added[0])
            if self.settings.value("autostart", "true") in (True, "true") and self.current is None:
                self.start()
        self.update_buttons()

    def remove_job(self, job: Job):
        if job.status == "running":
            return
        self.jobs.remove(job)
        job.card.setParent(None)
        job.card.deleteLater()
        if self.selected is job:
            self.selected = None
            self.results.show_job(None)
        self.queue_empty.setVisible(not self.jobs)
        self.update_buttons()

    def rerun_job(self, job: Job):
        """Поставить готовое (или прерванное) видео в очередь заново с текущими настройками."""
        if job.status not in ("done", "error", "cancelled"):
            return
        job.status, job.progress, job.stage, job.result, job.error = "queued", 0.0, "", None, ""
        job.card.refresh()
        if self.selected is job:
            self.results.show_job(job)
        if self.current is None:
            self.start()
        self.update_buttons()

    def select_job(self, job: Job):
        self.selected = job
        for j in self.jobs:
            j.card.set_selected(j is job)
        self.results.show_job(job)

    # ---------------------------------------------------------------- обработка
    def params(self) -> Params:
        s = self.settings
        return Params(sensitivity=float(s.value("sensitivity", 1.0)),
                      min_duration=float(s.value("min_duration", 1.0)),
                      similar_threshold=float(s.value("similar", 80)),
                      merge_builds=s.value("merge_builds", "true") in (True, "true"),
                      skip_duplicates=s.value("skip_duplicates", "false") in (True, "true"),
                      mode="sequential" if s.value("thorough", "false") in (True, "true") else "auto")

    def out_dir_for(self, video: str) -> str:
        s = self.settings
        if int(s.value("out_mode", 0)) == 1 and s.value("out_dir", ""):
            stem = os.path.splitext(os.path.basename(video))[0]
            return os.path.join(s.value("out_dir"), stem + "_slides")
        return default_out_dir(video)

    def start_or_stop(self):
        if self.current is not None:
            self.cancel_event.set()
            self.stopping = True
            self.update_buttons()
        else:
            self.start()

    def start(self):
        if self.current is not None:
            return
        if not any(j.status == "queued" for j in self.jobs):
            for j in self.jobs:            # «Начать» после остановки — продолжить отменённые
                if j.status == "cancelled":
                    j.status, j.progress = "queued", 0.0
                    j.card.refresh()
        nxt = next((j for j in self.jobs if j.status == "queued"), None)
        if nxt is None:
            self.update_buttons()
            return
        self.cancel_event.clear()
        self.current = nxt
        nxt.status, nxt.progress, nxt.stage = "running", 0.0, "Открываю видео…"
        nxt.started = time.monotonic()
        nxt.out_dir = self.out_dir_for(nxt.path)
        nxt.card.refresh()
        if self.selected is nxt:
            self.results.show_job(nxt)
        params = self.params()
        self.worker = threading.Thread(target=self._work, args=(nxt, params), daemon=True)
        self.worker.start()
        self.update_buttons()

    def _work(self, job: Job, params: Params):
        try:
            res = process(job.path, job.out_dir, params,
                          progress=lambda f, text: self.bridge.progress.emit(job, f, text),
                          cancel=self.cancel_event)
        except Cancelled:
            self.bridge.failed.emit(job, "")
        except Exception as e:  # noqa: BLE001 - показать пользователю
            self.bridge.failed.emit(job, f"{type(e).__name__}: {e}")
        else:
            self.bridge.done.emit(job, res)

    def _on_progress(self, job: Job, frac: float, text: str):
        job.progress, job.stage = frac, text
        now = time.monotonic()
        if now - self._last_ui < 0.08 and frac < 1:
            return
        self._last_ui = now
        job.card.refresh()
        self.results.update_progress(job)
        self.update_buttons()

    def _on_done(self, job: Job, res: Result):
        job.status, job.result, job.progress = "done", res, 1.0
        self._finish(job)

    def _on_failed(self, job: Job, err: str):
        job.status = "error" if err else "cancelled"
        job.error = err
        if not err:
            self.jobs_cancel_rest()
        self._finish(job)

    def jobs_cancel_rest(self):
        pass  # остальные остаются в очереди — их можно запустить кнопкой

    def _finish(self, job: Job):
        job.card.refresh()
        self.current = None
        if self.selected is job or self.selected is None:
            self.select_job(job)
        cancelled = job.status == "cancelled"
        self.stopping = False
        if not cancelled:
            self.start()
        self.update_buttons()

    def update_buttons(self):
        t = self.theme
        queued = sum(1 for j in self.jobs if j.status == "queued") or \
            sum(1 for j in self.jobs if j.status == "cancelled")
        done = sum(1 for j in self.jobs if j.status == "done")
        if self.current is not None:
            self.btn_go.setEnabled(not self.stopping)
            self.btn_go.setText("  Останавливаю…" if self.stopping else "  Остановить")
            self.btn_go.setIcon(svg_icon("stop", "#ffffff" if not self.stopping else t["faint"], 16))
        else:
            self.btn_go.setEnabled(queued > 0)
            self.btn_go.setText(f"  Начать обработку ({queued})" if queued else "  Начать обработку")
            self.btn_go.setIcon(svg_icon("play", "#ffffff" if queued else t["faint"], 16))
        parts = []
        if self.jobs:
            parts.append(f"{done} из {len(self.jobs)} готово")
        self.queue_info.setText(" · ".join(parts))

    def open_settings(self):
        d = SettingsDialog(self.settings, self.theme, self)
        d.exec()

    def closeEvent(self, e):
        if self.current is not None:
            r = QMessageBox.question(self, "Идёт обработка", "Остановить обработку и выйти?")
            if r != QMessageBox.Yes:
                e.ignore()
                return
            self.cancel_event.set()
            self.worker.join(timeout=10)   # дать обработке остановиться и убрать временные файлы
        e.accept()


def _system_theme() -> str:
    try:
        scheme = QApplication.styleHints().colorScheme()
        return "light" if scheme == Qt.ColorScheme.Light else "dark"
    except Exception:  # noqa: BLE001
        return "dark"


def _pick_font() -> QFont:
    fams = set(QFontDatabase.families())
    for name in ("Inter", "Segoe UI Variable Text", "Segoe UI", "SF Pro Text", "Helvetica Neue",
                 "Ubuntu", "Noto Sans", "DejaVu Sans"):
        if name in fams:
            f = QFont(name)
            f.setPixelSize(14)
            return f
    return QFont()


def run_gui(paths=None) -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Presenter Reader")
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    app.setFont(_pick_font())
    w = MainWindow()
    w.show()
    if paths:
        w.add_files(collect_videos(paths))
    return app.exec()


def main() -> int:
    return run_gui(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
