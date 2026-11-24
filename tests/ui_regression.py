"""Native acceptance checks for the dashboard and transparent desktop renderer."""
import os
from pathlib import Path
import sys
import tempfile
import cairo
import numpy as np

config = tempfile.TemporaryDirectory(prefix='sonora-ui-check-')
os.environ['XDG_CONFIG_HOME'] = config.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sonora import App, Gtk, GLib

app = App(demo=True)
app.show_all()
failures = []

def check():
    try:
        app.change('glow', 0)  # Isolate cover transparency from the bass glow.
        app.open_widget()
        ring = app.widget_window
        for _ in range(30):
            if Gtk.events_pending(): Gtk.main_iteration_do(False)
        assert not ring.get_decorated(), 'Desktop ring must not have a titlebar'
        assert ring.get_allocated_width() == ring.get_allocated_height()
        size = ring.visual.get_allocated_width()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        ring.visual.draw(ring.visual, cairo.Context(surface))
        surface.flush()
        pixels = np.frombuffer(surface.get_data(), dtype=np.uint8).reshape(size, surface.get_stride())[:, :size*4].reshape(size, size, 4)
        assert pixels[0, 0, 3] == 0, 'Transparent corner became opaque'
        assert pixels[size//2, size//2, 3] == 0, 'Disabled cover must leave the center transparent'
        assert pixels[:, :, 3].max() > 0, 'Ring did not render'
        ring.show_options()
        for _ in range(30):
            if Gtk.events_pending(): Gtk.main_iteration_do(False)
        ring._controls['sensitivity'].set_value(3.2)
        assert app.settings['sensitivity'] == 3.2
        for key, control in app.setting_controls:
            if key == 'sensitivity':
                assert abs(control.get_value()-3.2) < .001, 'Dashboard and circle settings diverged'
        from sonora import load_settings
        app.change('queue_card', True)
        ring_toggle = next(control for key, control in app.setting_controls if key == 'ring_enabled')
        ring_toggle.set_active(False)
        assert app.widget_window is None and not load_settings()['ring_enabled']
        assert 'queue' in app.scene_windows
        ring_toggle.set_active(True)
        assert app.widget_window.get_visible() and load_settings()['ring_enabled']
        app.open_clock()
        app.contact_entry.set_text('lyrics@example.org')
        app.close()
        while not app.closing and Gtk.events_pending():
            Gtk.main_iteration_do(False)
        assert app.closing and app.widget_window is None and app.clock_window is None
        assert not app.scene_windows and app.audio.stopped.is_set()
        assert app.contact_path.read_text() == 'lyrics@example.org'
        print('PASS: transparent ring, persistent switch, independent queue, contact and full shutdown')
    except Exception as error:
        failures.append(error)
    finally:
        app.destroy()
    return False

GLib.timeout_add(800, check)
Gtk.main()
if failures:
    raise failures[0]
