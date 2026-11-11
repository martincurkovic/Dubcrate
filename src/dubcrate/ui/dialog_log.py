"""Record every message box the app shows, and which button was pressed.

What the user was told is half of every bug report ("it said something about a name"). Installed
once at start-up (`ui/app.py`); the tests never install it, so they can keep patching `QMessageBox`.
Taken from AKAISDS's `diagnostics_ui.py`.
"""

import logging

from PySide6.QtWidgets import QMessageBox

log = logging.getLogger(__name__)

_LEVELS = {
    "information": logging.INFO,
    "question": logging.INFO,
    "warning": logging.WARNING,
    "critical": logging.ERROR,
}
_MAX_TEXT = 500
_installed = False


def _one_line(text: object) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= _MAX_TEXT else text[:_MAX_TEXT] + "..."


def install() -> None:
    """Idempotent."""
    global _installed
    if _installed:
        return
    _installed = True
    for kind, level in _LEVELS.items():
        original = getattr(QMessageBox, kind)

        def wrapper(*args, _original=original, _kind=kind, _level=level, **kwargs):
            title = args[1] if len(args) > 1 else kwargs.get("title", "")
            text = args[2] if len(args) > 2 else kwargs.get("text", "")
            log.log(
                _level, "dialog [%s] %r: %r", _kind, _one_line(title), _one_line(text)
            )
            result = _original(*args, **kwargs)
            log.info(
                "dialog [%s] %r -> %s",
                _kind,
                _one_line(title),
                getattr(result, "name", result),
            )
            return result

        setattr(QMessageBox, kind, staticmethod(wrapper))
