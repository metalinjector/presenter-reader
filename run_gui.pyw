"""Запуск графического интерфейса двойным щелчком (Windows: без окна консоли)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from presenter_reader.gui import main  # noqa: E402

sys.exit(main())
