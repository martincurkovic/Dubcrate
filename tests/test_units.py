"""Millimetres, centimetres or inches: the units module, the length spin box, the setting and the label window."""

import pytest

from dubcrate import labels, settings, units
from dubcrate.library import Library
from dubcrate.ui.label_dialog import LabelDialog
from dubcrate.ui.length_spin import LengthSpin
from dubcrate.ui.settings_dialog import SettingsDialog

# -- the pure half -------------------------------------------------------------------------


def test_the_units_and_their_numbers():
    assert [u.key for u in units.UNITS] == ["mm", "cm", "in"]
    assert units.from_mm(25.4, units.INCH) == pytest.approx(1.0)
    assert units.to_mm(2.5, units.CM) == pytest.approx(25.0)
    assert units.to_mm(units.from_mm(29.7, units.INCH), units.INCH) == pytest.approx(
        29.7
    )


def test_an_unknown_unit_is_millimetres():
    assert units.unit("furlongs") is units.MM and units.unit(None) is units.MM


def test_lengths_are_written_without_trailing_zeros():
    assert (
        units.number(210, units.MM) == "210" and units.number(29.7, units.MM) == "29.7"
    )
    assert units.number(210, units.CM) == "21" and units.number(297, units.CM) == "29.7"
    assert (
        units.number(210, units.INCH) == "8.27"
        and units.number(25.4, units.INCH) == "1"
    )
    assert units.length_text(100, units.CM) == "10 cm"


def test_the_paper_list_says_each_size_in_the_unit():
    assert units.page_text("A4", 210, 297, units.MM) == "A4 (210 × 297 mm)"
    assert units.page_text("A4", 210, 297, units.CM) == "A4 (21 × 29.7 cm)"
    assert units.page_text("A4", 210, 297, units.INCH) == "A4 (8.27 × 11.69 in)"


# -- the setting ---------------------------------------------------------------------------


def test_millimetres_until_chosen_and_the_choice_is_kept_and_announced(qapp):
    assert settings.get_units() == "mm"
    heard = []
    settings.changes.units.connect(lambda: heard.append(settings.get_units()))
    settings.set_units("in")
    assert settings.get_units() == "in" and heard == ["in"]
    settings.set_units("nonsense")  # (kept as millimetres, not stored as junk)
    assert settings.get_units() == "mm"
    settings.changes.units.disconnect()


# -- the length spin box -------------------------------------------------------------------


def test_a_spin_box_shows_its_millimetres_in_the_unit(qapp):
    spin = LengthSpin(29.7, 5, 100, "cm")
    assert spin.value() == pytest.approx(2.97) and spin.suffix() == " cm"
    assert (spin.minimum(), spin.maximum()) == (pytest.approx(0.5), pytest.approx(10.0))
    assert spin.decimals() == 2 and spin.singleStep() == pytest.approx(0.1)
    assert spin.mm == 29.7


def test_changing_the_unit_never_changes_the_length(qapp):
    spin = LengthSpin(29.7, 5, 300, "mm")
    changes = []
    spin.valueChanged.connect(changes.append)
    for key in ("cm", "in", "cm", "in", "mm"):
        spin.set_unit(key)
        assert spin.mm == 29.7  # exact, however often it is shown another way
    assert spin.value() == pytest.approx(29.7) and spin.suffix() == " mm"
    assert changes == []  # (switching the unit is not an edit)


def test_typing_a_length_in_the_unit_sets_the_millimetres(qapp):
    spin = LengthSpin(29.7, 5, 300, "in")
    spin.setValue(2.0)
    assert spin.mm == pytest.approx(50.8)
    spin.set_unit("cm")
    assert spin.value() == pytest.approx(5.08)


def test_a_length_is_held_inside_the_limits_and_new_limits_bring_it_in(qapp):
    spin = LengthSpin(80, 5, 100, "mm")
    spin.set_range_mm(5, 60)
    assert spin.mm == 60 and spin.value() == 60 and spin.maximum() == 60
    spin.set_mm(500)
    assert spin.mm == 60  # (not past the paper)
    spin.set_mm(1)
    assert spin.mm == 5


def test_the_box_tells_listeners_when_it_is_edited(qapp):
    spin = LengthSpin(20, 0, 100, "cm")
    heard = []
    spin.valueChanged.connect(heard.append)
    spin.setValue(3.0)
    assert heard == [3.0] and spin.mm == pytest.approx(30.0)


# -- the label window ------------------------------------------------------------------------


@pytest.fixture
def lib():
    with Library.open(":memory:") as library:
        yield library


def test_the_label_window_shows_its_sizes_in_the_chosen_unit(qapp, lib):
    settings.set_units("cm")
    dialog = LabelDialog(lib)
    assert dialog.width_spin.suffix() == " cm" and dialog.margin_spin.suffix() == " cm"
    assert (
        dialog.page_combo.itemText(dialog.page_combo.findData("A4"))
        == "A4 (21 × 29.7 cm)"
    )
    assert dialog.options().width_mm == pytest.approx(dialog.width_spin.value() * 10)


def test_the_label_window_follows_the_setting_while_it_is_open_and_keeps_its_sizes(
    qapp, lib
):
    dialog = LabelDialog(lib)
    dialog.width_spin.setValue(90)  # millimetres, the default
    before = dialog.options()
    settings.set_units("in")
    assert dialog.width_spin.suffix() == " in"
    assert dialog.width_spin.value() == pytest.approx(90 / 25.4, abs=0.01)
    assert (
        dialog.page_combo.itemText(dialog.page_combo.findData("A4"))
        == "A4 (8.27 × 11.69 in)"
    )
    settings.set_units("mm")
    assert (
        dialog.options() == before
    )  # (every size exactly as it was: held in millimetres)


def test_a_size_typed_in_inches_is_saved_in_millimetres(qapp, lib):
    settings.set_units("in")
    dialog = LabelDialog(lib)
    dialog.margin_spin.setValue(0.5)
    assert dialog.options().margin_mm == pytest.approx(12.7)
    dialog.done(0)  # (the window saves its options as it closes)
    assert settings.get_label_options().margin_mm == pytest.approx(12.7)
    assert labels.LabelOptions.__dataclass_fields__[
        "margin_mm"
    ]  # (millimetres, the stored field)


def test_a_new_paper_lowers_a_label_that_no_longer_fits_in_any_unit(qapp, lib):
    settings.set_units("cm")
    dialog = LabelDialog(lib)
    dialog.page_combo.setCurrentIndex(dialog.page_combo.findData("A6"))
    page_w, page_h = labels.PAGE_SIZES["A6"]
    assert dialog.width_spin.mm <= page_w and dialog.height_spin.mm <= page_h


# -- the Settings window -------------------------------------------------------------------


def test_the_settings_window_has_a_units_choice_that_is_the_setting(qapp):
    dialog = SettingsDialog()
    assert [dialog.units_combo.itemData(i) for i in range(3)] == ["mm", "cm", "in"]
    assert dialog.units_combo.currentData() == "mm"
    dialog.units_combo.setCurrentIndex(2)
    dialog.units_combo.activated.emit(2)
    assert settings.get_units() == "in"
    assert SettingsDialog().units_combo.currentData() == "in"  # (and it is remembered)
