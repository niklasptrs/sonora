"""Render acceptance checks for the options exposed by the dashboard."""
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cairo
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sonora import DEFAULTS, Gtk, GdkPixbuf, Visual


@unittest.skipUnless(Gtk.init_check()[0], 'A GTK display is needed for rendered option checks')
class VisualOptionsTests(unittest.TestCase):
    def setUp(self):
        self.app = SimpleNamespace(
            settings=DEFAULTS.copy(), full=False, obscured=False, ui=SimpleNamespace(text=str),
            cover=None, cover_palette=None,
            audio=SimpleNamespace(lock=threading.Lock(), levels=np.zeros(64), wave=np.zeros(256)),
            current_media=lambda: SimpleNamespace(title='Example title', artist='Example artist'),
        )
        self.window = Gtk.Window()
        self.visual = Visual(self.app, desktop=True)
        self.window.add(self.visual)
        self.window.resize(420, 420)
        self.window.show_all()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

    def tearDown(self):
        self.window.destroy()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

    def render(self):
        width, height = self.visual.get_allocated_width(), self.visual.get_allocated_height()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        self.visual.phase = 0
        self.visual.last_frame = 1
        with patch('sonora.time.monotonic', return_value=1):
            self.visual.draw(self.visual, cairo.Context(surface))
        surface.flush()
        return np.frombuffer(surface.get_data(), dtype=np.uint8).reshape(height, surface.get_stride())[:, :width*4].reshape(height, width, 4).copy()

    def test_backdrop_changes_surface_without_filling_transparent_corners(self):
        clear = self.render()
        self.app.settings['backdrop'] = 1
        backdrop = self.render()
        middle = backdrop.shape[0]//2
        self.assertEqual(clear[middle, middle, 3], 0)
        self.assertGreater(backdrop[middle, middle, 3], 0)
        self.assertEqual(backdrop[0, 0, 3], 0)

    def test_desktop_style_and_halo_have_distinct_artwork(self):
        ring = self.render()
        self.app.settings['style'] = 'Bars'
        bars = self.render()
        self.app.settings['style'] = 'Halo'
        halo = self.render()
        self.assertGreater(np.count_nonzero(ring != bars), 100)
        self.assertGreater(np.count_nonzero(ring != halo), 100)

    def test_all_four_track_layouts_render_different_card_geometries(self):
        self.visual.desktop = False
        self.app.cover = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 32, 32)
        self.app.cover.fill(0x30c0e0ff)
        images = []
        for layout in ('Compact', 'Centred', 'Cover', 'Text only'):
            self.app.settings['layout'] = layout
            images.append(self.render())
        for first in range(len(images)):
            for second in range(first+1, len(images)):
                self.assertGreater(np.count_nonzero(images[first] != images[second]), 100)
        self.app.settings['now_playing'] = False
        self.assertGreater(np.count_nonzero(images[-1] != self.render()), 100)

    def test_album_light_and_desktop_metadata_are_independent_switches(self):
        clear = self.render()
        self.app.cover_palette = [(1, .2, .3), (.2, .6, 1), (.5, .8, .4)]
        self.app.settings['album_glow'] = True
        self.assertGreater(np.count_nonzero(clear != self.render()), 100)
        self.app.settings['album_glow'] = False
        self.app.settings['widget_now_playing'] = True
        metadata = self.render()
        self.assertGreater(np.count_nonzero(clear != metadata), 100)
        middle = metadata.shape[0]//2
        self.assertEqual(metadata[middle, middle, 3], 0)

    def test_tile_override_renders_its_style_without_changing_selected_style(self):
        self.visual.mini = True
        self.visual.desktop = False
        self.visual.style_override = 'Bars'
        tile = self.render()
        self.assertEqual(self.app.settings['style'], 'Ring')
        self.visual.style_override = None
        self.app.settings['style'] = 'Bars'
        self.assertTrue(np.array_equal(tile, self.render()))


if __name__ == '__main__':
    unittest.main()
