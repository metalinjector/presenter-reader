"""Виджеты интерфейса без экрана (offscreen). Без PySide6 тесты пропускаются."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import Property, QEventLoop, QObject, QSettings, QTimer
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
except ImportError as e:            # нет PySide6 или системных библиотек Qt
    pytest.skip(f"интерфейс недоступен: {e}", allow_module_level=True)

from presenter_reader import gui  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_switch_click_moves_knob_not_widget(app):
    # Регрессия: свойство кружка называлось pos, как свойство QWidget.pos (положение
    # виджета), — анимация уносила переключатель в угол окна, а кружок стоял на месте.
    host = QWidget()
    lay = QVBoxLayout(host)
    lay.setContentsMargins(20, 20, 20, 20)
    sw = gui.Switch("Тест")
    lay.addWidget(sw)
    host.show()
    app.processEvents()
    where = sw.geometry().topLeft()
    assert (where.x(), where.y()) == (20, 20)
    for on in (True, False):
        sw.click()
        _wait(400)                  # анимация идёт 160 мс
        assert sw.isChecked() == on
        assert sw.geometry().topLeft() == where             # переключатель на месте
        assert sw.property("knob") == (1.0 if on else 0.0)  # кружок доехал
    host.close()


def test_switch_set_checked_is_instant(app):
    # Программная установка (окно настроек при открытии) — без анимации.
    sw = gui.Switch()
    sw.setChecked(True)
    _wait(50)
    assert sw.property("knob") == 1.0
    sw.setChecked(False)
    _wait(50)
    assert sw.property("knob") == 0.0


def test_properties_do_not_shadow_qt(app):
    """Своё свойство с именем свойства Qt-предка (pos, size, geometry…) подменяет его,
    и анимация или setProperty меняют не то."""
    clashes = []
    for cls in vars(gui).values():
        if not (isinstance(cls, type) and issubclass(cls, QObject)
                and cls.__module__ == gui.__name__):
            continue
        qt_base = next(b for b in cls.__mro__ if b.__module__.startswith("PySide6"))
        for name, value in vars(cls).items():
            if isinstance(value, Property) and qt_base.staticMetaObject.indexOfProperty(name) >= 0:
                clashes.append(f"{cls.__name__}.{name}")
    assert not clashes


def test_main_window_uses_given_settings(app, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    w = gui.MainWindow(settings)
    assert not w.sw_dups.isChecked()
    w.sw_dups.click()
    assert settings.value("skip_duplicates") == "true"
    w.close()
