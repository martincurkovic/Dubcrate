"""The waveform looks (3-band, RGB, blue) and the light/dark waveform background."""

import numpy as np
import pytest
from PySide6.QtGui import QColor

from dubcrate import settings
from dubcrate.library import Library
from dubcrate.ui import theme, wave_style
from dubcrate.ui.main_window import MainWindow
from dubcrate.ui.settings_dialog import SettingsDialog
from dubcrate.ui.track_table import render_preview
from dubcrate.ui.waveform_view import DetailView, OverviewView
from dubcrate.waveform import Waveform


def wave(rows, per_second=150.0) -> Waveform:
    return Waveform(np.array(rows, dtype=np.float32), per_second)


def pixel(pixmap_or_image, x, y) -> QColor:
    image = (
        pixmap_or_image.toImage()
        if hasattr(pixmap_or_image, "toImage")
        else pixmap_or_image
    )
    return QColor(image.pixelColor(x, y))


def use(preference="dark", style="bands", background="theme"):
    theme.set_theme_preference(preference)
    settings.set_wave_style(style)
    settings.set_wave_background(background)


# -- settings -----------------------------------------------------------------


def test_the_defaults_are_the_3_band_look_following_the_theme(qapp):
    assert settings.get_wave_style() == "bands"
    assert settings.get_wave_background() == "theme"


def test_nonsense_saved_values_fall_back_to_the_defaults(qapp):
    settings._qs().setValue("waveform/style", "plaid")
    settings._qs().setValue("waveform/background", "neon")
    assert (settings.get_wave_style(), settings.get_wave_background()) == (
        "bands",
        "theme",
    )


# -- the background follows the theme -----------------------------------------


def test_the_light_theme_has_a_light_waveform_and_the_dark_theme_a_dark_one(qapp):
    use("light")
    assert wave_style.is_light() and wave_style.color("wave_bg").lightness() > 200
    use("dark")
    assert not wave_style.is_light() and wave_style.color("wave_bg").lightness() < 50


def test_the_highs_are_not_white_on_a_white_waveform(qapp):
    use("light")
    assert wave_style.color("wave_high").lightness() < 128  # white highs would vanish
    use("dark")
    assert wave_style.color("wave_high").lightness() > 200


def test_always_dark_keeps_rekordboxs_dark_waveform_in_the_light_theme(qapp):
    use("light", background="dark")
    assert not wave_style.is_light()
    assert wave_style.color("wave_bg") == QColor(theme.DARK_PALETTE["wave_bg"])


def test_system_6_keeps_its_own_black_and_white_and_always_draws_3_band(qapp):
    use("system6", style="rgb", background="dark")
    assert wave_style.is_mono()
    assert wave_style.current_style() == "bands"
    assert wave_style.color("wave_bg") == QColor("#ffffff")


def test_every_palette_has_every_waveform_key(qapp):
    keys = [k for k in theme.DARK_PALETTE if k.startswith("wave_")]
    assert {"wave_blue", "wave_blue_core"} <= set(keys)
    for palette in (theme.LIGHT_PALETTE, theme.SYSTEM6_PALETTE):
        assert set(keys) <= set(palette)


def test_changing_the_look_or_background_tells_the_waveforms(qapp):
    seen = []
    wave_style.notifier.changed.connect(lambda: seen.append(1))
    wave_style.set_style("rgb")
    wave_style.set_style("rgb")  # no change: no signal
    wave_style.set_background("dark")
    assert len(seen) == 2
    assert (settings.get_wave_style(), settings.get_wave_background()) == (
        "rgb",
        "dark",
    )


# -- what each look actually draws --------------------------------------------

BASS = [[1.0, 0.0, 0.0]] * 40  # all low: red in RGB
TREBLE = [[0.0, 0.0, 1.0]] * 40  # all high: blue in RGB


def test_rgb_draws_lows_red_and_highs_blue_in_the_preview_strip(qapp):
    use("dark", "rgb")
    red = pixel(render_preview(wave(BASS), 40, 20), 20, 18)
    blue = pixel(render_preview(wave(TREBLE), 40, 20), 20, 18)
    assert red.red() > 200 and red.green() < 60 and red.blue() < 60
    assert blue.blue() > 200 and blue.red() < 60


def test_blue_draws_blue_with_a_whiter_core_for_brighter_sounds(qapp):
    use("dark", "blue")
    bassy = pixel(render_preview(wave(BASS), 40, 20), 20, 19)
    bright = pixel(render_preview(wave([[0.05, 0.4, 0.8]] * 40), 40, 20), 20, 19)
    assert bassy.blue() > bassy.red()  # blue
    assert bright.red() > bassy.red() + 100  # whitened towards the core colour


def test_the_looks_are_different_pictures_of_the_same_track(qapp):
    track = wave([[0.8, 0.3, 0.1]] * 30 + [[0.1, 0.5, 0.9]] * 30)
    images = {}
    for style in ("bands", "rgb", "blue"):
        use("dark", style)
        images[style] = render_preview(track, 60, 20).toImage()
    assert images["bands"] != images["rgb"] != images["blue"] != images["bands"]


def test_the_preview_background_follows_the_theme(qapp):
    track = wave([[0.0, 0.0, 0.0]] * 40)  # silent: nothing but the background
    use("light")
    assert pixel(render_preview(track, 40, 20), 2, 0).lightness() > 200
    use("light", background="dark")
    assert pixel(render_preview(track, 40, 20), 2, 0).lightness() < 50


@pytest.mark.parametrize("style", ["bands", "rgb", "blue"])
@pytest.mark.parametrize("preference", ["dark", "light", "system6"])
def test_every_view_draws_in_every_look_and_theme(qapp, style, preference):
    use(preference, style)
    track = wave([[0.7, 0.4, 0.2]] * 600)
    overview = OverviewView()
    overview.resize(300, overview.HEIGHT)
    overview.set_track(4.0, track)
    detail = DetailView()
    detail.resize(300, detail.HEIGHT)
    detail.set_track(track)
    detail.set_position(2.0)
    for view, row in (
        (overview, 12 + 40 - 4),
        (detail, 56),
    ):  # just above the floor / the middle
        view.show()
        image = view.grab().toImage()
        assert image.width() == 300
        background = wave_style.color("wave_bg")
        drawn = [
            QColor(image.pixelColor(x, row)) != background for x in range(0, 300, 7)
        ]
        assert any(drawn), (
            type(view).__name__,
            style,
            preference,
        )  # a waveform, not a blank
        view.close()


def test_the_detail_view_scrolls_the_colour_looks_too(qapp):
    use("dark", "rgb")
    track = wave(
        [[1.0, 0.0, 0.0]] * 200 + [[0.0, 0.0, 1.0]] * 200
    )  # 1.3 s red, then 1.3 s blue
    detail = DetailView()
    detail.resize(400, detail.HEIGHT)
    detail.set_track(track)
    detail.show()
    detail.set_position(0.5)
    left = pixel(detail.grab(), 205, 56)
    detail.set_position(2.2)
    right = pixel(detail.grab(), 205, 56)
    assert left.red() > left.blue() and right.blue() > right.red()
    detail.close()


def test_the_overview_redraws_when_the_look_changes(qapp):
    use("dark", "bands")
    overview = OverviewView()
    overview.resize(200, overview.HEIGHT)
    overview.set_track(2.0, wave(BASS * 5))
    overview.show()
    before = overview.grab().toImage()
    wave_style.set_style("rgb")
    assert overview.grab().toImage() != before
    overview.close()


# -- the controls -------------------------------------------------------------


def test_settings_offers_the_looks_and_applies_them_at_once(qapp):
    dialog = SettingsDialog()
    assert dialog.wave_style_combo.currentData() == "bands"
    assert dialog.wave_background_combo.currentData() == "theme"
    dialog.wave_style_combo.setCurrentIndex(dialog.wave_style_combo.findData("blue"))
    dialog.wave_style_combo.activated.emit(dialog.wave_style_combo.currentIndex())
    dialog.wave_background_combo.setCurrentIndex(
        dialog.wave_background_combo.findData("dark")
    )
    dialog.wave_background_combo.activated.emit(
        dialog.wave_background_combo.currentIndex()
    )
    assert (settings.get_wave_style(), settings.get_wave_background()) == (
        "blue",
        "dark",
    )
    again = SettingsDialog()
    assert (
        again.wave_style_combo.currentData(),
        again.wave_background_combo.currentData(),
    ) == ("blue", "dark")


def test_the_view_menu_switches_the_look_and_follows_settings(qapp):
    lib = Library.open(":memory:")
    win = MainWindow(lib)
    try:
        assert win._wave_actions["bands"].isChecked()
        win._wave_actions["rgb"].trigger()
        assert settings.get_wave_style() == "rgb"
        wave_style.set_style("blue")  # changed elsewhere (Settings)
        assert (
            win._wave_actions["blue"].isChecked()
            and not win._wave_actions["rgb"].isChecked()
        )
    finally:
        win.close()
        win.deleteLater()
        lib.close()


@pytest.mark.parametrize("style", ["bands", "rgb", "blue"])
@pytest.mark.parametrize("preference", ["dark", "light"])
def test_the_detail_view_has_a_faint_line_through_the_middle_even_in_silence(
    qapp, style, preference
):
    use(preference, style)
    detail = DetailView()
    detail.resize(300, detail.HEIGHT)
    detail.set_track(wave([[0.0, 0.0, 0.0]] * 600))
    detail.set_position(2.0)
    detail.show()
    image = detail.grab().toImage()
    background = wave_style.color("wave_bg")
    middle = detail.height() // 2
    assert pixel(image, 20, middle) != background  # the line
    assert pixel(image, 20, middle) != wave_style.color("wave_text")  # but faint
    assert pixel(image, 20, middle - 10) == background
    assert pixel(image, 20, middle + 10) == background
    detail.close()


def test_system6_has_no_faint_line(qapp):
    use("system6", "bands")
    detail = DetailView()
    detail.resize(300, detail.HEIGHT)
    detail.set_track(wave([[0.0, 0.0, 0.0]] * 600))
    detail.set_position(2.0)
    detail.show()
    image = detail.grab().toImage()
    assert pixel(image, 20, detail.height() // 2) == wave_style.color("wave_bg")
    detail.close()
