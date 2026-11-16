"""A spin box for a length that is kept in millimetres and shown in the unit chosen in Settings.

`mm` is the value in millimetres. It is set by the program (`set_mm`) or by the user typing or clicking in the unit
shown, and **changing the unit (`set_unit`) never changes it**: the shown number is only converted again, so a length
survives any number of unit changes exactly (a box showing 2.97 cm still holds 29.7 mm). `valueChanged` is the
box's own, in the shown unit, so a listener that only wants to know it changed need not care which.
"""

from PySide6.QtWidgets import QDoubleSpinBox

from dubcrate import units


class LengthSpin(QDoubleSpinBox):
    def __init__(
        self,
        mm: float,
        low_mm: float,
        high_mm: float,
        unit_key: str = "mm",
        parent=None,
    ):
        super().__init__(parent)
        self._unit = units.unit(unit_key)
        self._low, self._high = low_mm, high_mm
        self._mm = max(low_mm, min(high_mm, mm))
        self._show()
        self.valueChanged.connect(self._typed)

    @property
    def mm(self) -> float:
        return self._mm

    def set_mm(self, mm: float) -> None:
        self._mm = max(self._low, min(self._high, mm))
        self._show()

    def set_unit(self, unit_key: str) -> None:
        """Show the same length in another unit."""
        self._unit = units.unit(unit_key)
        self._show()

    def set_range_mm(self, low_mm: float, high_mm: float) -> None:
        """New limits in millimetres; a length outside them is brought inside."""
        self._low, self._high = low_mm, high_mm
        self._mm = max(low_mm, min(high_mm, self._mm))
        self._show()

    def _show(self) -> None:
        """Set the box up for the unit and the limits, showing `mm` in it, without telling any listener."""
        u = self._unit
        blocked = self.blockSignals(True)
        try:
            self.setDecimals(u.decimals)
            self.setSingleStep(u.step)
            self.setSuffix(f" {u.suffix}")
            self.setRange(units.from_mm(self._low, u), units.from_mm(self._high, u))
            self.setValue(units.from_mm(self._mm, u))
        finally:
            self.blockSignals(blocked)

    def _typed(self, shown: float) -> None:
        """The user (or a test) changed the number: that is the new length."""
        self._mm = max(self._low, min(self._high, units.to_mm(shown, self._unit)))
