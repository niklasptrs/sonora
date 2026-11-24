"""Native acceptance checks for the live visualizer settings page."""
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sonora
from sonora import App, Gtk, Gdk, STYLES


def pump(seconds=.4):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for _ in range(50):
            if time.monotonic() >= deadline or not Gtk.events_pending():
                break
            Gtk.main_iteration_do(False)
        time.sleep(.002)


@unittest.skipUnless(Gtk.init_check()[0], 'A GTK display is needed for dashboard checks')
class VisualizerPanelTests(unittest.TestCase):
    def setUp(self):
        self.config = tempfile.TemporaryDirectory(prefix='sonora-panel-test-')
        self.environment = patch.dict(os.environ, {'XDG_CONFIG_HOME': self.config.name})
        self.environment.start()
        self.config_path = patch.object(sonora, 'CONFIG', Path(self.config.name) / 'settings.json')
        self.config_path.start()
        self.app = App(demo=True)
        self.app.application_runner = SimpleNamespace(quit=lambda: None)
        self.app.show_all()
        self.panel = self.app.visualizer_panel
        pump()

    def tearDown(self):
        self.app.destroy()
        pump(.05)
        self.config_path.stop()
        self.environment.stop()
        self.config.cleanup()

    def test_style_and_palette_actions_update_all_settings_surfaces(self):
        self.assertEqual(self.app.stack.get_visible_child_name(), 'visualizer')
        self.assertEqual(set(self.panel.style_buttons), set(STYLES))
        self.panel.style_buttons['Halo'].clicked()
        self.assertEqual(self.app.settings['style'], 'Halo')
        self.assertEqual(self.panel.summary['style'].get_text(), 'Halo')
        self.assertTrue(self.panel.style_buttons['Halo'].get_style_context().has_class('selected'))
        for key, control in self.app.setting_controls:
            if key == 'style' and isinstance(control, Gtk.ComboBoxText):
                self.assertEqual(control.get_active_id(), 'Halo')
        self.app.change('accent', '#123456')
        self.app.change('album_colours', True)
        self.panel.theme_buttons['Mint'].clicked()
        self.assertEqual(self.app.settings['theme'], 'Mint')
        self.assertEqual(self.app.settings['accent'], '')
        self.assertFalse(self.app.settings['album_colours'])
        self.panel.theme_buttons['album'].clicked()
        self.assertTrue(self.app.settings['album_colours'])
        self.assertEqual(self.panel.summary['theme'].get_text(), 'Albumfarben')

    def test_size_controls_resize_real_widget_and_summary(self):
        self.app.open_widget()
        self.panel._set_size(460, 220)
        pump()
        ring = self.app.widget_window
        self.assertEqual((ring.get_allocated_width(), ring.get_allocated_height()), (460, 220))
        self.assertEqual(self.panel.summary['widget_size'].get_text(), '460 × 220')
        controls = dict((key, control) for key, control in self.app.setting_controls
                        if key in ('widget_width', 'widget_height'))
        controls['widget_width'].set_value(510)
        pump()
        self.assertEqual(self.app.settings['widget_width'], 510)
        self.assertEqual(ring.get_allocated_width(), 510)
        self.app.change('widget_locked', True)
        self.assertTrue(all(control.get_active() for key, control in self.app.setting_controls
                            if key == 'widget_locked'))

    def test_preset_positions_survive_a_round_trip_and_reload(self):
        self.app.open_widget()
        ring = self.app.widget_window
        if not ring.layer_window and 'X11' not in type(ring.get_display()).__name__:
            self.skipTest('This compositor controls window placement')
        self.panel._set_size(300, 300)
        pump()
        ring.move(150, 160)
        pump()
        self.panel._set_size(460, 220)
        pump()
        self.assertEqual(tuple(ring.get_position()), (70, 200))
        ring.move(300, 140)
        pump()
        self.panel._set_size(300, 300)
        pump()
        self.assertEqual(tuple(ring.get_position()), (150, 160))
        self.assertEqual(sonora.load_settings()['widget_positions']['460x220'], [300, 140])

    def test_frame_rate_changes_reach_every_live_preview(self):
        visuals = list(self.app._visuals)
        self.assertGreaterEqual(len(visuals), len(STYLES) + 2)
        counts = {visual: 0 for visual in visuals}
        for visual in visuals:
            original = visual.tick
            def counted_tick(item=visual, callback=original):
                counts[item] += 1
                return callback()
            visual.tick = counted_tick
        self.app.change('fps', 20)
        pump(.45)
        self.assertTrue(all(1 <= count <= 14 for count in counts.values()), counts)

    def test_small_dashboard_scrolls_vertically_without_horizontal_overflow(self):
        self.app.resize(960, 640)
        pump()
        self.assertEqual((self.app.get_allocated_width(), self.app.get_allocated_height()), (960, 640))
        scroll = self.panel.get_children()[0]
        horizontal = scroll.get_hadjustment()
        vertical = scroll.get_vadjustment()
        self.assertLessEqual(horizontal.get_upper(), horizontal.get_page_size() + 1)
        self.assertGreater(vertical.get_upper(), vertical.get_page_size())
        vertical.set_value(vertical.get_upper() - vertical.get_page_size())
        pump()
        self.assertAlmostEqual(vertical.get_value(), vertical.get_upper() - vertical.get_page_size(), delta=1)


if __name__ == '__main__':
    unittest.main()
