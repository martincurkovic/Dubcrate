"""How keys are written on screen: Camelot (8A) or musical (A min), chosen in Settings.

Whatever the source wrote is converted for display only (`dubcrate.keys`); the library keeps
it as it was. `notifier.changed` fires when the choice changes, so every table and the player
strip redraw.
"""

from PySide6.QtCore import QObject, Signal

from dubcrate import keys, settings


class _Notifier(QObject):
    changed = Signal()  # the key notation was changed in Settings


notifier = _Notifier()


def display(text: str) -> str:
    return keys.display(text, settings.get_key_notation())


def set_notation(notation: str) -> None:
    if notation != settings.get_key_notation():
        settings.set_key_notation(notation)
        notifier.changed.emit()
