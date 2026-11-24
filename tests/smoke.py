"""Run on a Linux display and session bus: python3 tests/smoke.py."""
import sys
import os
import tempfile
from pathlib import Path
isolated_config = tempfile.TemporaryDirectory(prefix='sonora-smoke-')
os.environ['XDG_CONFIG_HOME'] = isolated_config.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cairo
from sonora import App, STYLES, Gtk, GLib, Gdk
app = App(demo=True)
app.show_all()
test_width = int(os.environ.get('SONORA_TEST_WIDTH', '1100'))
test_height = int(os.environ.get('SONORA_TEST_HEIGHT', '760'))
app.resize(test_width, test_height)
errors = []
def verify():
    try:
        # Some window managers maximize a large initial window automatically.
        # Respect that policy while still checking the visible screen bounds.
        maximized = bool(app.get_window().get_state() & Gdk.WindowState.MAXIMIZED)
        limit_width = app.get_screen().get_width() if maximized else test_width
        limit_height = app.get_screen().get_height() if maximized else test_height
        assert app.get_allocated_width() <= limit_width, 'Window exceeds visible display width'
        assert app.get_allocated_height() <= limit_height, 'Window exceeds visible display height'
        for style in STYLES:
            app.settings['style'] = style
            for density in (16, 64, 128):
                app.settings['density'] = density
                surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1100, 760)
                app.visual.draw(app.visual, cairo.Context(surface))
        app.settings['density'] = 64
        app.stack.set_visible_child_name('soloist')
        app.demo = False
        app.soloist.connected = True
        app.soloist.emit({'type':'auth_state','logged_in':True,'device_name':'Test device'})
        app.soloist.emit({'type':'playback_state','status':'playing','item':{'decorations':{'identity':{'name':'Protocol test'}, 'playback':{'duration_ms':120000}}}, 'position':{'position_ms':10000,'speed':0}})
        app.soloist_event({'type':'queue_changed','upcoming':[{'item':{'decorations':{'identity':{'name':'Next song'}}}}]})
        assert app.current_media().title == 'Protocol test'
        from lyrics import parse_lyrics
        app.set_lyrics(parse_lyrics('[00:00]Opening example\n[00:08]Second example'))
        app.refresh()
        app.update_lyrics_preview(app.lyrics_line, None)
        assert app.lyrics_line.get_text() == 'Second example'
        app.stack.set_visible_child_name('lyrics')
        app.set_lyrics(None)
        app.soloist.stop()
        app.demo = True
        app.stack.set_visible_child_name('settings')
        def descendants(widget):
            yield widget
            if isinstance(widget, Gtk.Container):
                for child in widget.get_children():
                    yield from descendants(child)
        controls = list(descendants(app.stack.get_child_by_name('settings')))
        name = next(w for w in controls if isinstance(w, Gtk.Entry) and w.get_placeholder_text() == 'Profilname, z. B. Abend')
        name.set_text('Smoke test')
        next(w for w in controls if isinstance(w, Gtk.Button) and w.get_label() == 'Speichern').clicked()
        assert 'Smoke test' in app.profiles.list()
        saved_theme = app.settings['theme']
        app.change('theme', 'Ocean')
        next(w for w in controls if isinstance(w, Gtk.Button) and w.get_label() == 'Laden').clicked()
        assert app.settings['theme'] == saved_theme
        from desktop_settings import autostart_enabled
        startup = next(w for w in controls if isinstance(w, Gtk.CheckButton) and w.get_label() == 'Sonora bei der Anmeldung starten')
        startup.set_active(True)
        assert autostart_enabled()
        startup.set_active(False)
        assert not autostart_enabled()
        app.stack.set_visible_child_name('player')
        app.open_clock()
        from sonora import Clock
        for face in ('Digital', 'Stacked', 'Minimal', 'Analog', 'Words'):
            app.settings['clock_style'] = face
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 400, 300)
            app.clock_window.get_child().draw(app.clock_window.get_child(), cairo.Context(surface))
        app.clock_window.destroy()
        app.open_clock()
        app.clock_window.destroy()
        app.open_widget()
        app.change('fps', 15)
        app.widget_window.destroy()
        app.open_widget()
        app.widget_window.destroy()
        app.settings['style'] = 'Ring'
        window = app.get_window()
        pixbuf = Gdk.pixbuf_get_from_window(window, 0, 0, app.get_allocated_width(), app.get_allocated_height())
        preview = os.environ.get('SONORA_TEST_PREVIEW', str(Path(__file__).resolve().parents[1] / 'preview.png'))
        pixbuf.savev(preview, 'png', [], [])
        print('PASS: renderers at three densities, Soloist, synced lyrics, profiles, autostart, clocks and widgets')
        print('Actual window: %d x %d' % (app.get_allocated_width(), app.get_allocated_height()))
        import resource
        print('Peak RSS (demo, Linux): %.1f MiB' % (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024))
    except Exception as exc:
        errors.append(exc)
    finally:
        app.destroy()
    return False
GLib.timeout_add(1500, verify)
Gtk.main()
if errors:
    raise errors[0]
