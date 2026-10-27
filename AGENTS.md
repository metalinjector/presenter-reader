# AGENTS.md — инструкция для ИИ-агента

Этот файл для ИИ-агентов (Claude Code, Codex, Cursor, Copilot и других): как скачать, установить,
проверить и запустить **Presenter Reader**. Программа находит в записи вебинара прямоугольную
область презентации, отделяет её от видео ведущего и сохраняет по одному PNG-скриншоту на каждый
показанный слайд.

- Описание алгоритма и параметров — [README.md](README.md).
- Постановка задачи — [TASK.md](TASK.md).

Выполняй шаги по порядку. После каждого шага сверяйся с блоком «Ожидается». Если результат не
совпал, смотри раздел «Если что-то пошло не так».

## Шаг 0. Что нужно на машине

- **Python 3.9–3.13.** Проверено на 3.9, 3.10, 3.11 и 3.13. Проверить версию: `python3 --version`,
  в Windows — `py -3 --version`.
- **git.** Можно обойтись без него и скачать zip (см. шаг 1).
- **Интернет** для `pip install`: около 150 МБ пакетов.
- **ffmpeg в PATH** нужен *только* для тестов и генерации демо-видео. Самой программе он не нужен:
  FFmpeg уже входит в пакет `av`.
- Видеокарта не нужна.

## Шаг 1. Скачать

Репозиторий публичный, входить в GitHub и получать ключи не нужно. Код лежит в ветке по умолчанию
`claude/pensive-euler-er1v42`.

```bash
git clone https://github.com/metalinjector/presenter-reader.git
cd presenter-reader
```

Без git:

```bash
curl -L -o presenter-reader.zip \
  https://github.com/metalinjector/presenter-reader/archive/refs/heads/claude/pensive-euler-er1v42.zip
unzip presenter-reader.zip
cd presenter-reader-claude-pensive-euler-er1v42
```

**Ожидается:** в текущей папке есть `presenter_reader/`, `tests/`, `requirements.txt`, `README.md`
и этот `AGENTS.md`. Все дальнейшие команды выполняются из этой папки.

## Шаг 2. Установить

Создай виртуальное окружение внутри проекта и дальше вызывай его Python **по полному пути**. Так
не нужно «активировать» окружение в каждой новой оболочке.

Linux / macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt pytest Pillow
```

Windows (PowerShell или cmd):

```bat
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt pytest Pillow
```

**Дальше в тексте `PY` означает `.venv/bin/python` (Linux/macOS) или
`.venv\Scripts\python.exe` (Windows).**

Что ставится:

- `requirements.txt` — numpy, opencv-python-headless, av (PyAV со встроенным FFmpeg) и
  PySide6-Essentials (графический интерфейс);
- pytest и Pillow — только для тестов и демо-видео.

Есть и вариант установки как пакета. Он дополнительно создаёт команды `presenter-reader` и
`presenter-reader-gui` в `.venv/bin` (Windows: `.venv\Scripts`):

```bash
PY -m pip install -e ".[gui,dev]"
```

Linux-серверу без рабочего стола для PySide6 нужны системные библиотеки, иначе будет
`ImportError: libEGL.so.1`:

```bash
sudo apt-get install -y libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3
```

**Ожидается:** установка занимает около минуты, а `PY -m presenter_reader --version` печатает
`presenter-reader 0.1.0`.

## Шаг 3. Самопроверка (около минуты)

Для генерации демо-видео нужен ffmpeg с кодеком libx264 (проверка:
`ffmpeg -hide_banner -encoders | grep libx264`). Если ffmpeg нет, пропусти команды 2–4 и переходи к
шагу 4 с настоящим видео. pytest в этом случае просто пропустит сквозные тесты.

```bash
PY -m pytest -q                                          # 1
PY -m tests.synth demo --scenario mini                   # 2: синтетический вебинар 33 с + эталон
PY -m presenter_reader demo/mini_g2_b0.mp4 -o demo/out   # 3
PY -m tests.evaluate demo/mini_g2_b0.mp4                 # 4: сверка с эталоном
```

**Ожидается:**

1. `9 passed`.
2. Строка `…mini_g2_b0.mp4: 33 s, screens = 5`.
3. `Найдено слайдов: 5`. Один из них повтор: `slide_004_…_repeat-of-001_sim-100pct.png`.
4. JSON-строка с `"ok": true`, а последней строкой — `ALL OK`.

## Шаг 4. Обработать запись вебинара

```bash
PY -m presenter_reader "/путь/к/вебинару.mp4"
PY -m presenter_reader "/путь/к/вебинару.mp4" -o "/путь/к/результату"
PY -m presenter_reader video1.mp4 video2.mkv -o results   # у каждого видео своя подпапка
```

- Видео должно лежать на диске. Из Google Drive и других облаков его нужно сначала скачать.
- Без `-o` результат попадает в папку `<имя видео>_slides` рядом с видео.
- Прогресс пишется в stderr, итог — в stdout. Тексты на русском, пути с кириллицей поддерживаются.
- **Коды выхода:**
  - `0` — успех;
  - `1` — хотя бы одно видео не обработано, текст ошибки в stderr;
  - `2` — запрошен GUI, а PySide6 не установлен.
- **Время:** трёхчасовая запись 1080p обрабатывается за 2–5 минут на 4 ядрах. Первые ~30 %
  прогресса — поиск области презентации, остальное — поиск смен слайдов.

Что появится в папке результата:

| Файл | Что это |
|---|---|
| `slide_NNN_ЧЧ-ММ-СС.png` | скриншот слайда: номер и время первого показа |
| `slide_NNN_…_repeat-of-MMM_sim-XXpct.png` | повторный показ: похож на слайд MMM на XX % |
| `report.json` | всё в машиночитаемом виде — **для разбора результата читай его** |
| `slides.csv` | таблица для человека, разделитель `;`, кодировка UTF-8 с BOM |
| `index.html` | галерея с тайм-кодами |
| `layout.png` | кадр с найденной областью (зелёная рамка) и «живыми» зонами (красные) |

Структура `report.json`; времена в секундах от начала записи:

```json
{
  "slides_count": 5,
  "duration": 33.0,
  "mode": "keyframes",
  "processing_seconds": 0.7,
  "region": {"x": 0, "y": 90, "w": 960, "h": 540, "method": "events"},
  "slides": [
    {"number": 4, "file": "slide_004_00-00-22_repeat-of-001_sim-100pct.png",
     "start": 22.0, "end": 27.07, "start_hms": "00:00:22", "shown_seconds": 5.1,
     "shows": [[22.0, 27.07]], "animation_steps": 1,
     "repeat_of": 1, "similarity": 100.0}
  ]
}
```

- `region.method`: `events` — область найдена по сменам слайдов; `static` — смен не было, взят
  самый большой неподвижный прямоугольник; `manual` — задана через `--roi`; `full` — весь кадр.
- У слайда, который не повтор, `repeat_of` и `similarity` равны `null`.

Проверь, что область найдена верно: открой `layout.png`. Зелёная рамка должна обводить слайд, а
окно с ведущим должно быть закрашено красным. Если рамка не там, задай область вручную.

Полезные параметры:

| Параметр | Когда нужен |
|---|---|
| `--roi X,Y,W,H` | рамка в `layout.png` не там — задать область вручную, в пикселях исходного кадра |
| `--start 10:00 --end 1:30:00` | обработать только часть записи (секунды или `чч:мм:сс`) |
| `--sensitivity 2` | пропускаются мелкие правки на слайде (одно слово, цифра); `0.5` — наоборот, грубее |
| `--min-duration 1` | сколько секунд картинка должна стоять, чтобы считаться слайдом |
| `--similar 80` | с какого процента сходства помечать повтор |
| `--no-merge-builds` | сохранять каждый шаг анимации отдельно |
| `--mode sequential` | читать все кадры подряд: медленнее, но не пропустит слайд, мелькнувший между опорными кадрами |

## Шаг 5. Вызов из Python

```python
from presenter_reader import Params, process

res = process("webinar.mp4", "webinar_slides", Params(sensitivity=1.0))
t0 = res.info["start"]                       # смещение начала потока, обычно 0
print(len(res.slides), res.layout.rect, res.out_dir)
for s, f in zip(res.slides, res.files):
    print(s.number, round(s.t_start - t0), f, s.repeat_of, s.similarity)
```

`process()` принимает два необязательных аргумента:

- `progress=callable(доля, текст)` — сообщения о ходе обработки;
- `cancel=threading.Event()` — остановка: при установленном событии `process()` выбрасывает
  `presenter_reader.Cancelled`.

## Шаг 6. Графический интерфейс

- **Для человека.** `PY -m presenter_reader` без аргументов, или `presenter-reader-gui` после
  `pip install -e .`, или (в Windows) двойной щелчок по `run_gui.pyw`. Видео перетаскиваются в окно
  мышью.
- **Агенту без экрана:**

  ```bash
  PY -m tests.gui_smoke demo/mini_g2_b0.mp4 --shot gui.png
  ```

  Скрипт открывает окно в режиме offscreen, обрабатывает видео, сохраняет снимок окна в `gui.png`
  и печатает `OK: слайдов 5, …`; код выхода `0`. Снимок можно посмотреть, чтобы оценить интерфейс.

## Если что-то пошло не так

| Симптом | Причина и решение |
|---|---|
| `No module named presenter_reader` | команда запущена не из корня репозитория — перейди в него или выполни `PY -m pip install -e .` |
| `ImportError: libEGL.so.1` (или `libxkbcommon`, `libfontconfig`) | на Linux нет системных библиотек Qt — см. шаг 2 |
| `Could not load the Qt platform plugin "xcb"` | нет дисплея: для проверки задай `QT_QPA_PLATFORM=offscreen`; на рабочем столе Linux поставь `libxcb-cursor0` |
| `No module named av` | PyAV не установился: `PY -m pip install av`. Без него программа работает через OpenCV, но заметно медленнее |
| pytest пишет `skipped` | нет ffmpeg в PATH, сквозные тесты пропущены. Это не ошибка программы |
| `No module named PIL` в `tests.synth` | `PY -m pip install Pillow` |
| `tests.synth` падает с ошибкой ffmpeg | нет ffmpeg или в нём нет libx264 |
| В `layout.png` рамка не на слайде | `--roi X,Y,W,H` |
| Мелкие правки на слайде не замечены или ложные срабатывания | `--sensitivity 2` (чувствительнее) или `--sensitivity 0.5` (грубее) |

## Правила для агента, который меняет код

- После любых правок запусти `PY -m pytest -q`.
- После правок алгоритма (`layout.py`, `tracker.py`, `slides.py`, `imageops.py`) прогони и полную
  сверку на синтетических вебинарах. Генерация займёт около 5 минут; в конце должно быть
  `ALL OK`:

  ```bash
  PY -m tests.synth vids --gop 2
  PY -m tests.synth vids --gop 10 --bframes 3 --crf 32
  PY -m tests.evaluate vids/*.mp4
  ```

- Не добавляй в git видео и результаты обработки (`*_slides/`, `demo/`, `vids/`).
- Комментарии, сообщения и тексты интерфейса пиши на русском, как в остальном коде.
- Карта модулей — в конце [README.md](README.md).
