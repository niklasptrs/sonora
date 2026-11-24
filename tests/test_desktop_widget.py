"""Desktop-circle geometry, option persistence and native lifecycle checks."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktop_settings import DEFAULT_SETTINGS, ProfileError, validate_settings
from desktop_widget import DesktopRing, circle_region, rounded_region, Gtk, GLib, Gdk


class RingSettingsTests(unittest.TestCase):
    def test_legacy_settings_gain_desktop_defaults(self):
        loaded = validate_settings({'style': 'Bars', 'fps': 24, 'sensitivity': 1.6})
        self.assertEqual(loaded['style'], 'Bars')
        self.assertEqual(loaded['fps'], 24)
        self.assertEqual(loaded['widget_size'], 420)
        self.assertFalse(loaded['widget_cover'])
        self.assertEqual(loaded['widget_placement'], 'Overlay')
        self.assertTrue(loaded['widget_above'])

    def test_legacy_topmost_false_upgrades_to_overlay(self):
        loaded = validate_settings({'widget_above': False, 'backdrop': 1.})
        self.assertEqual(loaded['widget_placement'], 'Overlay')
        self.assertTrue(loaded['widget_above'])
        self.assertEqual(loaded['backdrop'], 0.)

    def test_explicit_placement_survives_restart_and_has_consistent_topmost_flag(self):
        for placement in ('Overlay', 'Normal', 'Desktop'):
            saved = validate_settings({'widget_placement': placement, 'widget_above': True,
                                       'widget_locked': True, 'widget_clickthrough': True,
                                       'backdrop': .5})
            reloaded = validate_settings(saved)
            self.assertEqual(reloaded['widget_placement'], placement)
            self.assertEqual(reloaded['widget_above'], placement == 'Overlay')
            self.assertTrue(reloaded['widget_locked'])
            self.assertTrue(reloaded['widget_clickthrough'])
            self.assertEqual(reloaded['backdrop'], .5)

    def test_desktop_fields_round_trip_and_reject_invalid_sizes(self):
        fields = {'widget_size': 510, 'widget_cover': True, 'widget_above': True,
                  'widget_x': -420, 'widget_y': 120}
        saved = validate_settings(fields)
        self.assertEqual({key: saved[key] for key in fields}, fields)
        for bad in ({'widget_size': 100}, {'widget_size': 420.5}, {'widget_size': True},
                    {'widget_cover': 'yes'}, {'widget_x': 999999}, {'widget_y': float('nan')},
                    {'widget_placement': 'Floating'}, {'widget_locked': 1}, {'widget_clickthrough': 'yes'}):
            with self.subTest(bad=bad), self.assertRaises(ProfileError):
                validate_settings(bad)

    def test_old_diameter_migrates_to_dimensions_and_explicit_rectangle_persists(self):
        migrated = validate_settings({'widget_size': 510})
        self.assertEqual((migrated['widget_width'], migrated['widget_height']), (510, 510))
        rectangle = validate_settings({'widget_size': 510, 'widget_width': 460, 'widget_height': 220})
        self.assertEqual((rectangle['widget_width'], rectangle['widget_height']), (460, 220))
        self.assertEqual(validate_settings(rectangle), rectangle)
        for bad in ({'widget_width': 149}, {'widget_width': 2561}, {'widget_height': 1441},
                    {'widget_height': 220.5}, {'widget_width': True}):
            with self.subTest(bad=bad), self.assertRaises(ProfileError):
                validate_settings(bad)

    def test_shape_excludes_corners_and_includes_circle(self):
        region = circle_region(420, 420)
        self.assertTrue(region.contains_point(210, 210))
        self.assertTrue(region.contains_point(210, 20))
        for x, y in ((0, 0), (419, 0), (0, 419), (419, 419), (10, 10)):
            self.assertFalse(region.contains_point(x, y))

    def test_wide_ring_and_card_have_distinct_input_regions(self):
        ring = circle_region(460, 220)
        card = rounded_region(460, 220)
        self.assertTrue(ring.contains_point(230, 110))
        self.assertFalse(ring.contains_point(20, 110))
        self.assertTrue(card.contains_point(20, 110))
        self.assertFalse(card.contains_point(0, 0))


@unittest.skipUnless(Gtk.init_check()[0], 'A GTK display is needed for native widget checks')
class RingWindowTests(unittest.TestCase):
    def setUp(self):
        class App(Gtk.Window):
            def __init__(self):
                super().__init__()
                self.settings = DEFAULT_SETTINGS.copy()
                self.saved = 0
                self.command = ''
            def change(self, key, value):
                self.settings[key] = value
                self.saved += 1
                if hasattr(self, 'ring'):
                    self.ring.settings_changed(key)
            def save(self):
                self.saved += 1
            def control(self, command):
                self.command = command
            def current_media(self):
                from types import SimpleNamespace
                return SimpleNamespace(playing=False)
        class Visual(Gtk.DrawingArea):
            def __init__(self, app, mini=False, desktop=False):
                super().__init__()
                self.mini, self.desktop = mini, desktop
        self.app = App()
        self.ring = DesktopRing(self.app, Visual)
        self.app.ring = self.ring
        self.ring.show_all()
        self.flush()

    @staticmethod
    def flush():
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

    def tearDown(self):
        self.ring.destroy()
        self.app.destroy()
        self.flush()

    def test_window_has_no_decoration_and_uses_desktop_renderer(self):
        self.assertFalse(self.ring.get_decorated())
        self.assertTrue(self.ring.visual.desktop)
        self.assertTrue(self.ring.visual.mini)
        self.ring._apply_shape()  # Foreign Cairo-region binding must work on the real display.
        self.assertFalse(self.ring.get_accept_focus())
        self.assertFalse(self.ring.get_focus_on_map())
        self.assertTrue(self.ring.get_skip_taskbar_hint())
        self.assertTrue(self.app.settings['widget_above'])

    def test_placement_changes_reset_desktop_hint_and_topmost_flag(self):
        from desktop_widget import Gdk
        self.app.change('widget_placement', 'Desktop')
        if 'X11' in type(self.ring.get_display()).__name__:
            self.assertEqual(self.ring.get_type_hint(), Gdk.WindowTypeHint.DESKTOP)
        self.assertFalse(self.app.settings['widget_above'])
        self.app.change('widget_placement', 'Normal')
        self.assertEqual(self.ring.get_type_hint(), Gdk.WindowTypeHint.UTILITY)
        self.assertFalse(self.app.settings['widget_above'])
        self.app.change('widget_placement', 'Overlay')
        self.assertEqual(self.ring.get_type_hint(), Gdk.WindowTypeHint.UTILITY)
        self.assertTrue(self.app.settings['widget_above'])
        self.ring.hide()
        self.ring.show_all()
        self.flush()
        self.assertEqual(self.ring.get_type_hint(), Gdk.WindowTypeHint.UTILITY)
        self.assertTrue(self.app.settings['widget_above'])

    def test_legacy_topmost_control_updates_persisted_placement(self):
        self.app.change('widget_above', False)
        self.assertEqual(self.app.settings['widget_placement'], 'Normal')
        self.app.change('widget_above', True)
        self.assertEqual(self.app.settings['widget_placement'], 'Overlay')

    def test_widget_lock_prevents_drag_options_and_hover(self):
        from types import SimpleNamespace
        event = SimpleNamespace(button=1, x_root=100, y_root=100, time=1)
        self.app.change('widget_locked', True)
        self.assertFalse(self.ring._press(None, event))
        self.assertFalse(self.ring._press(None, SimpleNamespace(button=3)))
        self.ring._enter()
        self.assertFalse(self.ring.revealer.get_reveal_child())
        self.assertIsNone(self.ring._drag_origin)
        self.app.change('widget_locked', False)
        self.assertTrue(self.ring._press(None, event))
        self.assertIsNotNone(self.ring._drag_origin)
        self.app.change('widget_locked', True)
        self.assertIsNone(self.ring._drag_origin)
        self.assertFalse(self.ring.has_grab())

    def test_native_overlay_move_and_resize_persist_geometry_and_release_grab(self):
        if not self.ring._native_overlay:
            self.skipTest('Native unmanaged overlay is X11-specific')
        from types import SimpleNamespace
        before_x, before_y = self.ring.get_position()
        press = SimpleNamespace(button=1, x_root=before_x+200, y_root=before_y+200, time=1)
        self.ring._press(None, press)
        motion = SimpleNamespace(x_root=press.x_root+35, y_root=press.y_root+20,
                                 state=Gdk.ModifierType.BUTTON1_MASK, time=2)
        self.ring._motion(None, motion)
        self.flush()
        self.ring._release(None, SimpleNamespace(button=1))
        self.assertEqual(self.ring.get_position(), (before_x+35, before_y+20))
        self.assertEqual((self.app.settings['widget_x'], self.app.settings['widget_y']),
                         self.ring.get_position())
        self.assertFalse(self.ring.has_grab())
        self.ring._resize_press(None, press)
        self.ring._motion(None, motion)
        self.flush()
        self.ring._release(None, SimpleNamespace(button=1))
        self.assertEqual((self.app.settings['widget_width'], self.app.settings['widget_height']), (455, 440))
        self.assertFalse(self.ring.has_grab())

    def test_wide_ring_resize_handle_stays_inside_input_circle(self):
        self.app.change('widget_width', 960)
        self.app.change('widget_height', 220)
        self.ring._enter()
        self.flush()
        self.ring._allocate()
        width, height = self.ring.get_allocated_width(), self.ring.get_allocated_height()
        x = width-self.ring.resize_revealer.get_margin_end()-12
        y = height-self.ring.resize_revealer.get_margin_bottom()-12
        self.assertTrue(circle_region(width, height).contains_point(x, y))

    def test_clickthrough_closes_options_and_recovery_restores_input(self):
        from desktop_widget import Gdk
        self.ring.show_options()
        shapes = []
        original = Gdk.Window.input_shape_combine_region
        def apply(window, region, x, y):
            shapes.append(region.is_empty())
            return original(window, region, x, y)
        with patch.object(Gdk.Window, 'input_shape_combine_region', apply):
            self.app.change('widget_clickthrough', True)
            self.assertFalse(self.ring.options.get_visible())
            self.assertTrue(shapes[-1])
            self.app.change('widget_clickthrough', False)
            self.assertFalse(shapes[-1])
            self.app.change('widget_locked', True)
            self.assertTrue(shapes[-1])
            self.app.change('widget_locked', False)
            self.assertFalse(shapes[-1])

    def test_circle_options_update_app_and_remain_synchronised(self):
        self.ring.show_options()
        self.ring._controls['sensitivity'].set_value(3.4)
        self.assertAlmostEqual(self.app.settings['sensitivity'], 3.4)
        self.ring._controls['widget_cover'].set_active(True)
        self.assertTrue(self.app.settings['widget_cover'])
        self.app.change('theme', 'Ocean')
        self.assertEqual(self.ring._controls['theme'].get_active_id(), 'Ocean')
        self.ring.play_button.clicked()
        self.assertEqual(self.app.command, 'PlayPause')

    def test_options_are_independent_of_ring_shape_and_dismiss_outside(self):
        from types import SimpleNamespace
        self.ring.show_options()
        self.flush()
        self.assertIsInstance(self.ring.options, Gtk.Window)
        self.assertTrue(self.ring.options.get_visible())
        self.assertGreater(self.ring.options.get_allocated_height(), 500)
        self.ring.options._outside(None, SimpleNamespace(x_root=-100, y_root=-100))
        self.assertFalse(self.ring.options.get_visible())
        self.assertFalse(self.ring.options.has_grab())

    def test_non_composited_x11_has_circular_bounding_shape(self):
        from desktop_widget import Gdk
        if 'X11' not in type(self.ring.get_display()).__name__:
            self.skipTest('X11-specific shape fallback')
        with patch.object(Gdk.Screen, 'is_composited', return_value=False):
            self.ring._apply_shape()
            self.assertTrue(self.ring.get_window().is_shaped())
        self.ring._apply_shape()

    def test_destroy_cleans_up_native_widget_timers(self):
        self.ring._schedule_hide()
        self.assertTrue(self.ring._hover_timer)
        self.ring.destroy()
        self.assertEqual(self.ring._hover_timer, 0)
        self.assertEqual(self.ring._move_timer, 0)


if __name__ == '__main__':
    unittest.main()
