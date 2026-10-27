"""Presenter Reader — извлечение слайдов презентации из записей вебинаров."""

__version__ = "0.1.0"

from .pipeline import Cancelled, Params, Result, process  # noqa: E402

__all__ = ["Cancelled", "Params", "Result", "process", "__version__"]
