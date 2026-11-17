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
    assert pixel(image, 100, middle) != background  # the line
    assert pixel(image, 100, middle) != wave_style.color("wave_text")  # but faint
    assert pixel(image, 100, middle - 10) == background
    assert pixel(image, 100, middle + 10) == background
    detail.close()


def test_system6_has_no_faint_line(qapp):
    use("system6", "bands")
    detail = DetailView()
    detail.resize(300, detail.HEIGHT)
    detail.set_track(wave([[0.0, 0.0, 0.0]] * 600))
    detail.set_position(2.0)
    detail.show()
    image = detail.grab().toImage()
    assert pixel(image, 100, detail.height() // 2) == wave_style.color("wave_bg")
    detail.close()


# -- the 3-band look as an image (what the zoomed view draws every frame) ------------------------------------

LOW, MID, HIGH = QColor("#0000ff"), QColor("#00ff00"), QColor("#ff0000")


def bands_image(heights, rows=20, extent=8.0, smooth=False):
    return wave_style.render_bands(
        np.array(heights, dtype=np.float32),
        [LOW, MID, HIGH],
        rows=rows,
        extent=extent,
        smooth=smooth,
    )


def column(image, x):
    return [QColor(image.pixelColor(x, y)).name() for y in range(image.height())]


def test_each_band_is_a_bar_mirrored_about_the_middle_with_the_later_band_on_top():
    # one pixel column: low reaches 8 rows from the middle, mid 4, high 2
    image = bands_image([[1.0, 0.5, 0.25]])
    assert (image.width(), image.height()) == (1, 20)
    names = column(image, 0)
    assert names == names[::-1]  # (mirrored)
    assert names[10:12] == ["#ff0000"] * 2  # the two rows each side of the middle: high
    assert names[6:10] == ["#00ff00"] * 2 + ["#ff0000"] * 2  # mid beyond high
    assert (
        names[2:6] == ["#0000ff"] * 4 and names[6:8] == ["#00ff00"] * 2
    )  # low beyond mid
    assert (
        names[:2] == ["#000000"] * 2
    )  # nothing past the reach: transparent (reads as black)
    assert image.pixelColor(0, 0).alpha() == 0


def test_a_silent_column_is_empty_and_a_band_taller_than_the_view_fills_it():
    image = bands_image([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]], rows=10, extent=4.0)
    assert all(image.pixelColor(0, y).alpha() == 0 for y in range(10))
    assert all(image.pixelColor(1, y).alpha() == 255 for y in range(10))


def test_an_odd_number_of_rows_stays_mirrored_with_the_middle_row_filled():
    image = bands_image([[0.0, 0.0, 1.0]], rows=9, extent=2.0)
    names = column(image, 0)
    assert names == names[::-1]
    assert names[4] == "#ff0000"


def test_smooth_edges_blend_a_half_covered_row_instead_of_a_hard_step():
    hard = bands_image([[0.0, 0.0, 0.5]], rows=20, extent=9.0, smooth=False)
    smooth = bands_image([[0.0, 0.0, 0.5]], rows=20, extent=9.0, smooth=True)
    alphas = lambda image: [image.pixelColor(0, y).alpha() for y in range(20)]
    assert set(alphas(hard)) == {0, 255}
    assert 0 < min(a for a in alphas(smooth) if a) < 255 or set(alphas(smooth)) - {
        0,
        255,
    }
    assert alphas(smooth) == alphas(smooth)[::-1]  # (still mirrored)


def test_a_smoothed_edge_keeps_the_colour_it_only_loses_cover():
    image = bands_image([[0.0, 0.0, 0.55]], rows=20, extent=9.0, smooth=True)
    edge = [
        image.pixelColor(0, y)
        for y in range(20)
        if 0 < image.pixelColor(0, y).alpha() < 255
    ]
    assert edge, "the half-covered row is there"
    for c in (
        edge
    ):  # premultiplied, read back straight: still the band's red, not a darker red
        assert c.red() >= 250 and c.green() <= 2 and c.blue() <= 2


def test_the_zoomed_view_draws_the_bands_look_through_the_image(qapp):
    use("dark", "bands")
    view = DetailView()
    view.resize(300, view.HEIGHT)
    view.set_track(
        wave([[0.0, 0.0, 0.0]] * 300 + [[0.9, 0.5, 0.3]] * 2700)
    )  # 2 s of nothing, then sound
    view.show()
    middle = view.height() // 2
    view.set_position(
        0.5
    )  # (an 8 s window: the left of the view is before the track, the right inside it)
    image = view.grab().toImage()
    bare = wave_style.color("wave_bg")
    assert pixel(image, 5, 4) == bare
    assert pixel(image, 290, middle) != bare and pixel(image, 290, 4) == bare  # (sound, but not to the top)  # fmt: skip
    view.set_position(10.0)  # well inside the loud part: sound all the way across
    image = view.grab().toImage()
    assert pixel(image, 5, middle) != bare and pixel(image, 290, middle) != bare


def test_settings_offers_a_waveform_speed_that_starts_smooth_and_applies_at_once(qapp):
    dialog = SettingsDialog()
    assert [dialog.wave_fps_combo.itemText(i) for i in range(2)] == [
        "60 FPS",
        "30 FPS",
    ]
    assert dialog.wave_fps_combo.currentData() == 60
    seen = []
    settings.changes.wave_fps.connect(seen.append)
    dialog.wave_fps_combo.setCurrentIndex(dialog.wave_fps_combo.findData(30))
    dialog.wave_fps_combo.activated.emit(dialog.wave_fps_combo.currentIndex())
    assert settings.get_wave_fps() == 30 and seen == [30]
    again = SettingsDialog()  # (remembered)
    assert again.wave_fps_combo.currentData() == 30


# -- zoomed far out: peaks stay, and the outline does not shimmer as it scrolls -----------------------------


def spiky_track():
    """A hit every 0.5 s that is one column wide, over a quiet floor: a pixel zoomed out covers
    several columns, so which one a pixel reads decides whether the hit is drawn."""
    rows = np.full((150 * 60, 3), 0.1, dtype=np.float32)
    rows[::75] = [1.0, 1.0, 1.0]
    return Waveform(rows, 150.0)


def hits_drawn(view, position):
    """How many separate hits reach a row well above the quiet floor (runs of lit pixels)."""
    view.set_position(position)
    image = view.grab().toImage()
    bare = wave_style.color("wave_bg")
    lit = [
        x > 45 and image.pixelColor(x, 10) != bare for x in range(image.width())
    ]  # (past the zoom buttons)
    return sum(1 for x, on in enumerate(lit) if on and (x == 0 or not lit[x - 1]))


@pytest.mark.parametrize("style", ["bands", "rgb"])
def test_zoomed_out_every_hit_is_drawn_wherever_the_playhead_is(qapp, style):
    use("dark", style)
    view = DetailView()
    view.resize(300, view.HEIGHT)
    view.set_track(spiky_track())
    view.set_window_seconds(32.0)  # ~ 4800 columns across 300 pixels: 16 to a pixel
    view.show()
    counts = [hits_drawn(view, 20.0 + step * 0.013) for step in range(12)]
    assert min(counts) >= 50 and max(counts) - min(counts) <= 2, counts  # (about 64 hits in 32 s, all of them, every frame; one may cross the zoom buttons)  # fmt: skip


def test_pooled_columns_are_chosen_so_a_pixel_never_skips_one(qapp):
    use("dark", "bands")
    view = DetailView()
    view.resize(300, view.HEIGHT)
    view.set_track(spiky_track())
    for per_pixel, factor in (
        (0.5, 1),
        (1.0, 1),
        (1.3, 2),
        (2.7, 4),
        (16.0, 16),
        (17.0, 32),
    ):
        _pooled, got = view._level(per_pixel)
        assert got == factor
    assert view._level(2.7)[0].shape[0] == -(
        -9000 // 4
    )  # (and it is kept for the next frame)
    assert view._level(2.7)[0] is view._pooled[4]
