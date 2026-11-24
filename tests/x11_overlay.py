"""Optional real-window-manager acceptance checks for the X11 overlay.

Run in an isolated X11 session with a window manager (e.g. Openbox on Xvfb):
    dbus-run-session -- /usr/bin/python3 tests/x11_overlay.py

The test creates a separately running GTK application, activates it, maximizes
it and makes it fullscreen. Root-window ordering, rather than only EWMH hints,
proves the ring stays above it. The session's Show desktop flag is restored.
No user configuration or audio devices are accessed.
"""
import ctypes
import ctypes.util
import os
from pathlib import Path
import sys
import subprocess
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('GdkX11', '3.0')
from gi.repository import Gtk, Gdk, GdkX11
from desktop_settings import DEFAULT_SETTINGS
from desktop_widget import DesktopRing
from types import SimpleNamespace


def pump(seconds=.15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(.005)


class X11:
    """Only the small read/send subset of libX11 used by the acceptance check."""
    def __init__(self):
        self.lib = ctypes.CDLL(ctypes.util.find_library('X11'))
        self.lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.lib.XOpenDisplay.restype = ctypes.c_void_p
        self.lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self.lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        self.lib.XDefaultRootWindow.restype = ctypes.c_ulong
        self.lib.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                       ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
                                       ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)), ctypes.POINTER(ctypes.c_uint)]
        self.lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        self.lib.XInternAtom.restype = ctypes.c_ulong
        self.lib.XFree.argtypes = [ctypes.c_void_p]
        self.lib.XFlush.argtypes = [ctypes.c_void_p]
        self.lib.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                                       ctypes.c_long, ctypes.c_void_p]
        self.lib.XGetWindowProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.c_long, ctypes.c_long, ctypes.c_int, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte))]
        self.display = self.lib.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError('No X11 display')
        self.root = self.lib.XDefaultRootWindow(self.display)

    def close(self):
        if self.display:
            self.lib.XCloseDisplay(self.display)
            self.display = None

    def atom(self, name):
        return self.lib.XInternAtom(self.display, name.encode(), False)

    def query(self, window):
        root, parent = ctypes.c_ulong(), ctypes.c_ulong()
        children = ctypes.POINTER(ctypes.c_ulong)()
        count = ctypes.c_uint()
        self.lib.XQueryTree(self.display, window, ctypes.byref(root), ctypes.byref(parent),
                            ctypes.byref(children), ctypes.byref(count))
        try:
            return root.value, parent.value, [children[i] for i in range(count.value)]
        finally:
            if children:
                self.lib.XFree(children)

    def frame(self, window):
        for _ in range(12):
            root, parent, _ = self.query(window)
            if parent == root:
                return window
            if not parent:
                raise AssertionError('Window lost its root ancestor')
            window = parent
        raise AssertionError('Unexpected window hierarchy')

    def above(self, first, second):
        order = self.query(self.root)[2]
        return order.index(self.frame(first)) > order.index(self.frame(second))

    def property(self, window, name):
        actual, fmt, count, remaining = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_ulong(), ctypes.c_ulong()
        data = ctypes.POINTER(ctypes.c_ubyte)()
        self.lib.XGetWindowProperty(self.display, window, self.atom(name), 0, 64, False, 0,
            ctypes.byref(actual), ctypes.byref(fmt), ctypes.byref(count), ctypes.byref(remaining), ctypes.byref(data))
        try:
            if fmt.value != 32:
                return []
            values = ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))
            return [values[i] for i in range(count.value)]
        finally:
            if data:
                self.lib.XFree(data)

    def message(self, window, name, *values):
        class Data(ctypes.Union):
            _fields_ = [('b', ctypes.c_char * 20), ('s', ctypes.c_short * 10), ('l', ctypes.c_long * 5)]
        class ClientMessage(ctypes.Structure):
            _fields_ = [('type', ctypes.c_int), ('serial', ctypes.c_ulong), ('send_event', ctypes.c_int),
                        ('display', ctypes.c_void_p), ('window', ctypes.c_ulong),
                        ('message_type', ctypes.c_ulong), ('format', ctypes.c_int), ('data', Data)]
        class Event(ctypes.Union):
            _fields_ = [('client', ClientMessage), ('pad', ctypes.c_long * 24)]
        event = Event()
        event.client.type = 33  # ClientMessage
        event.client.window = window
        event.client.message_type = self.atom(name)
        event.client.format = 32
        for i, value in enumerate(values):
            event.client.data.l[i] = value
        self.lib.XSendEvent(self.display, self.root, False, (1 << 20) | (1 << 19), ctypes.byref(event))
        self.lib.XFlush(self.display)


class StubApp(Gtk.Window):
    def __init__(self):
        super().__init__()
        self.settings = DEFAULT_SETTINGS.copy()

    def change(self, key, value):
        self.settings[key] = value
        self.ring.settings_changed(key)

    def save(self):
        pass

    def control(self, _):
        pass

    def current_media(self):
        return SimpleNamespace(playing=False)


class BlankVisual(Gtk.DrawingArea):
    def __init__(self, app, **_):
        super().__init__()


class OverlayAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Gtk.init_check()[0] or 'X11' not in type(Gdk.Display.get_default()).__name__:
            raise unittest.SkipTest('A real X11 display and window manager are required')
        cls.x = X11()
        if not cls.x.property(cls.x.root, '_NET_SUPPORTING_WM_CHECK'):
            cls.x.close()
            raise unittest.SkipTest('No EWMH window manager on the X11 display')

    @classmethod
    def tearDownClass(cls):
        cls.x.close()

    def setUp(self):
        self.app = StubApp()
        self.ring = DesktopRing(self.app, BlankVisual)
        self.app.ring = self.ring
        self.ring.show_all()
        pump()
        self.wid = self.ring.get_window().get_xid()
        self.initial_desktop = self.x.property(self.x.root, '_NET_SHOWING_DESKTOP')
        competitor_code = '''
import gi, sys
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GdkX11
window = Gtk.Window(title='Sonora overlay acceptance: separate application')
window.set_default_size(900, 700)
window.add(Gtk.Label(label='Separate application: overlay must stay above this window'))
window.show_all()
print(window.get_window().get_xid(), flush=True)
Gtk.main()
'''
        self.competitor = subprocess.Popen([sys.executable, '-c', competitor_code],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=os.environ.copy())
        import select
        if not select.select([self.competitor.stdout], [], [], 5)[0]:
            self.fail('The separate GTK application failed to open')
        self.other = int(self.competitor.stdout.readline())
        pump(.4)

    def tearDown(self):
        self.x.message(self.x.root, '_NET_SHOWING_DESKTOP', *(self.initial_desktop or [0]))
        self.competitor.terminate()
        self.competitor.wait(timeout=5)
        self.competitor.stdout.close()
        self.ring.destroy()
        self.app.destroy()
        pump()

    def activate_other(self):
        self.x.message(self.other, '_NET_ACTIVE_WINDOW', 2, 0, 0)
        pump(.25)

    def assert_overlay_above(self):
        self.activate_other()
        self.assertEqual(self.x.property(self.x.root, '_NET_ACTIVE_WINDOW'), [self.other],
                         'Overlay took keyboard focus from the active application')
        self.assertTrue(self.x.above(self.wid, self.other), 'Ring is below the active application')

    def test_overlay_survives_fullscreen_show_desktop_modes_and_remap(self):
        self.assertEqual(self.app.settings['widget_placement'], 'Overlay')
        self.assert_overlay_above()
        for states in (('_NET_WM_STATE_MAXIMIZED_VERT', '_NET_WM_STATE_MAXIMIZED_HORZ'),
                       ('_NET_WM_STATE_FULLSCREEN',)):
            atoms = [self.x.atom(name) for name in states]
            self.x.message(self.other, '_NET_WM_STATE', 1, *atoms, *([0] if len(atoms) == 1 else []), 2)
            pump(.25)
            self.assertTrue(all(atom in self.x.property(self.other, '_NET_WM_STATE') for atom in atoms),
                            'Window manager did not apply competitor state: '+str(states))
            self.assert_overlay_above()
            self.x.message(self.other, '_NET_WM_STATE', 0, *atoms, *([0] if len(atoms) == 1 else []), 2)
            pump()
        self.x.message(self.x.root, '_NET_SHOWING_DESKTOP', 1)
        pump(.25)
        self.assertEqual(self.x.property(self.x.root, '_NET_SHOWING_DESKTOP'), [1])
        self.assertEqual(self.ring.get_window().is_viewable(), True, 'Show desktop hid the overlay')
        self.x.message(self.x.root, '_NET_SHOWING_DESKTOP', 0)
        pump()
        for placement in ('Normal', 'Desktop', 'Overlay'):
            self.app.change('widget_placement', placement)
            pump(.3)
            self.activate_other()
            self.assertEqual(self.x.above(self.wid, self.other), placement == 'Overlay', placement)
        self.ring.hide()
        pump()
        self.ring.show_all()
        pump()
        self.assert_overlay_above()
        self.ring.destroy()
        self.ring = DesktopRing(self.app, BlankVisual)
        self.app.ring = self.ring
        self.ring.show_all()
        pump()
        self.wid = self.ring.get_window().get_xid()
        self.assert_overlay_above()


if __name__ == '__main__':
    unittest.main(verbosity=2)
