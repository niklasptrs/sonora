"""A transparent, undecorated desktop ring with controls at the circle."""
import math

import cairo
import gi
gi.require_version('Gtk', '3.0')
gi.require_foreign('cairo')
from gi.repository import Gtk, Gdk, GLib
from desktop_settings import CHOICES
try:
    gi.require_version('GtkLayerShell', '0.1')
    from gi.repository import GtkLayerShell
except (ImportError, ValueError):
    GtkLayerShell = None

PALETTES = ('Ice', 'Nebula', 'Ocean', 'Ember', 'Forest', 'Rose', 'Mono', 'Mint', 'Sunset')


def protect_settings_from_scroll(widget):
    """Let the surrounding scroll view handle wheel events over value controls."""
    if isinstance(widget, (Gtk.Scale, Gtk.ComboBox, Gtk.SpinButton)):
        widget.connect('scroll-event', _scroll_page)
    elif isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            protect_settings_from_scroll(child)


def _scroll_page(control, event):
    scroll = control.get_ancestor(Gtk.ScrolledWindow)
    if scroll:
        scroll.event(event)
    return True


def circle_region(width, height, fraction=.49):
    """A scanline region: transparent corners never eat desktop clicks."""
    radius = min(width, height) * fraction
    cx, cy = width / 2, height / 2
    region = cairo.Region()
    for y in range(height):
        distance = abs(y + .5 - cy)
        if distance < radius:
            half = math.sqrt(radius * radius - distance * distance)
            left, right = math.ceil(cx-half), math.floor(cx+half)
            if right > left:
                region.union(cairo.RectangleInt(left, y, right-left, 1))
    return region


def rounded_region(width, height, radius=18):
    """A card-shaped input/bounding region for the noncircular visualizers."""
    radius = min(radius, width / 2, height / 2)
    region = cairo.Region()
    for y in range(height):
        dy = max(radius - y - .5, y + .5 - (height - radius), 0)
        inset = math.ceil(radius - math.sqrt(max(0, radius * radius - dy * dy))) if dy else 0
        region.union(cairo.RectangleInt(inset, y, max(0, width - 2 * inset), 1))
    return region


class CircleOptions(Gtk.Window):
    """A separate popup: GTK popovers would be clipped by the shaped ring."""
    def __init__(self, ring):
        # A Wayland layer surface cannot parent an ordinary GTK popup.
        wayland = 'Wayland' in type(ring.get_display()).__name__
        super().__init__(type=Gtk.WindowType.TOPLEVEL if wayland else Gtk.WindowType.POPUP)
        self.wayland = wayland
        self.ring = ring
        self._seat = None
        self.set_decorated(False)
        self.set_resizable(False)
        rgba = self.get_screen().get_rgba_visual()
        if rgba is not None:
            self.set_visual(rgba)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG if wayland else Gdk.WindowTypeHint.POPUP_MENU)
        self.set_skip_taskbar_hint(True)
        self.set_transient_for(ring.app if wayland else ring)
        self.get_style_context().add_class('ring-options')
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.KEY_PRESS_MASK)
        self.connect('button-press-event', self._outside)
        self.connect('key-press-event', self._key)
        self.connect('focus-out-event', lambda *_: self.popdown())
        self.connect('destroy', lambda *_: self._ungrab())

    def popup(self):
        ring = self.ring
        x, y = ring.get_position()
        size = ring.get_allocated_width()
        monitor = self.get_display().get_monitor_at_window(ring.get_window())
        area = monitor.get_workarea()
        width, height = 340, min(710, area.height-24)
        # Attach the panel to the circle's side, then keep every control on screen.
        right = x+int(size*.66)
        if right+width > area.x+area.width-12:
            right = x+int(size*.34)-width
        self.set_size_request(width, height)
        self.resize(width, height)
        self.move(max(area.x+12, min(right, area.x+area.width-width-12)),
                  max(area.y+12, min(y+size//2-height//2, area.y+area.height-height-12)))
        self.show_all()
        self.present()
        if self.wayland:
            return
        self.grab_add()
        seat = self.get_display().get_default_seat()
        status = seat.grab(self.get_window(), Gdk.SeatCapabilities.POINTER | Gdk.SeatCapabilities.KEYBOARD,
                           True, None, None, None)
        if status == Gdk.GrabStatus.SUCCESS:
            self._seat = seat

    def _ungrab(self):
        if self._seat:
            self._seat.ungrab()
            self._seat = None
        if self.has_grab():
            self.grab_remove()

    def popdown(self):
        self._ungrab()
        self.hide()
        self.ring._schedule_hide()
        return False

    def _outside(self, _, event):
        x, y = self.get_position()
        if not (x <= event.x_root < x+self.get_allocated_width() and
                y <= event.y_root < y+self.get_allocated_height()):
            self.popdown()
            return True
        return False

    def _key(self, _, event):
        if Gdk.keyval_name(event.keyval) == 'Escape':
            self.popdown()
            return True
        return False


class DesktopRing(Gtk.Window):
    def __init__(self, app, visual_factory):
        super().__init__(title='Sonora · Desktop-Kreis')
        self.app = app
        self._syncing = False
        self._move_timer = 0
        self._restoring = True
        self._controls = {}
        self._hover_timer = 0
        self._disposed = False
        self.layer_capable = bool(GtkLayerShell and 'Wayland' in type(self.get_display()).__name__
                                  and GtkLayerShell.is_supported())
        self.layer_window = self.layer_capable and app.settings['widget_placement'] != 'Normal'
        if self.layer_window:
            GtkLayerShell.init_for_window(self)
            GtkLayerShell.set_namespace(self, 'sonora')
            GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        self._click_timer = 0
        self._moved = False
        self._native_overlay = False
        self._drag_origin = None
        self._drag_kind = None
        self._drag_size = None
        self._size_idle = 0
        self._requested_size = None
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.set_app_paintable(True)
        rgba = self.get_screen().get_rgba_visual()
        if rgba is not None:
            self.set_visual(rgba)
        self.get_style_context().add_class('desktop-ring')
        provider = Gtk.CssProvider()
        provider.load_from_data(b'''
            window.desktop-ring, window.desktop-ring > overlay { background: transparent; box-shadow: none; }
            .ring-control { background: rgba(17,19,30,.90); color: #eef0ff; border: 1px solid rgba(200,210,255,.20); border-radius: 50%; padding: 0; min-width: 36px; min-height: 36px; box-shadow: none; }
            .ring-control:hover { background: #30233e; border-color: #a855f7; }
            .ring-options { background: #111117; color: #ededf3; border: 1px solid #2b2b36; border-radius: 16px; }
            .ring-options label { color: #ededf3; }
            .ring-options button { background: #19191f; color: #ededf3; border: 1px solid #32323d; border-radius: 8px; padding: 7px 9px; box-shadow: none; }
            .ring-options button:hover { background: #30233e; color: #ffffff; }
            .ring-options viewport, .ring-options scrolledwindow { background: transparent; }
            .ring-options check { background: #24242d; border: 1px solid #50505c; }
            .ring-options check:checked { background: #a855f7; color: #ffffff; }
            .ring-options scale trough { background: #30303a; min-height: 4px; border-radius: 5px; }
            .ring-options scale highlight { background: #a855f7; }
            .ring-options scale slider { background: #d6b1ff; border: none; min-width: 13px; min-height: 13px; }
        ''')
        Gtk.StyleContext.add_provider_for_screen(self.get_screen(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION+1)
        self.set_default_size(app.settings['widget_width'], app.settings['widget_height'])
        overlay = Gtk.Overlay()
        self.add(overlay)
        self.visual = visual_factory(app, mini=True, desktop=True)
        overlay.add(self.visual)
        self.revealer = Gtk.Revealer(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
        self.revealer.set_transition_duration(160)
        actions = Gtk.Box(spacing=3)
        self.previous_button = self._button('media-skip-backward-symbolic','Vorheriger Titel',lambda *_: app.control('Previous'))
        actions.pack_start(self.previous_button,False,False,0)
        self.play_button = self._button('media-playback-start-symbolic', 'Wiedergabe / Pause', lambda *_: app.control('PlayPause'))
        actions.pack_start(self.play_button, False, False, 0)
        self.next_button = self._button('media-skip-forward-symbolic','Nächster Titel',lambda *_: app.control('Next'))
        actions.pack_start(self.next_button,False,False,0)
        self.options_button = self._button('preferences-system-symbolic', 'Kreis-Optionen · auch per Rechtsklick', lambda *_: self.show_options())
        actions.pack_start(self.options_button, False, False, 0)
        self.revealer.add(actions)
        overlay.add_overlay(self.revealer)
        self.resize_revealer = Gtk.Revealer(halign=Gtk.Align.END, valign=Gtk.Align.END)
        self.resize_revealer.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
        self.resize_revealer.set_transition_duration(160)
        self.resize_handle = Gtk.EventBox()
        self.resize_handle.set_tooltip_text('Zum Ändern der Größe ziehen')
        self.resize_handle.add(Gtk.Image.new_from_icon_name('view-fullscreen-symbolic', Gtk.IconSize.BUTTON))
        self.resize_handle.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.resize_handle.connect('button-press-event', self._resize_press)
        self.resize_revealer.add(self.resize_handle)
        overlay.add_overlay(self.resize_revealer)
        self._make_options()
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK |
                        Gdk.EventMask.LEAVE_NOTIFY_MASK | Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect('button-press-event', self._press)
        self.connect('enter-notify-event', self._enter)
        self.connect('motion-notify-event', self._motion)
        self.connect('button-release-event', self._release)
        self.connect('leave-notify-event', self._leave)
        self.connect('configure-event', self._configure)
        self.connect('size-allocate', self._allocate)
        self.connect('realize', self._realize)
        self.connect('map-event', self._mapped)
        self.connect('destroy', self._dispose)
        self.connect('key-press-event', self._key)
        self.settings_changed()

    @staticmethod
    def _button(icon, tooltip, callback):
        button = Gtk.Button()
        button.set_image(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON))
        button.set_tooltip_text(tooltip)
        button.get_style_context().add_class('ring-control')
        button.connect('clicked', callback)
        return button

    def _make_options(self):
        self.options = CircleOptions(self)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=16)
        box.set_size_request(275, -1)
        heading = Gtk.Label(xalign=0)
        heading.set_markup('<b>Dein Desktop-Kreis</b>')
        box.pack_start(heading, False, False, 0)
        hint = Gtk.Label(label='Klick auf den Ring: Titelkarte · Doppelklick: nächster Stil · Ziehen: verschieben', xalign=0, wrap=True)
        hint.set_max_width_chars(34)
        box.pack_start(hint, False, False, 0)
        for title, key, low, high, step in (
            ('Breite', 'widget_width', 150, 2560, 10),
            ('Höhe', 'widget_height', 150, 1440, 10),
            ('Empfindlichkeit', 'sensitivity', .2, 5, .1),
            ('Nachklang', 'smoothing', 0, .95, .01),
            ('Leuchten', 'glow', 0, 1, .05),
            ('Deckkraft', 'opacity', .2, 1, .05),
        ):
            row = Gtk.Box(spacing=8)
            row.pack_start(Gtk.Label(label=title, xalign=0), True, True, 0)
            value = Gtk.Label(xalign=1)
            row.pack_end(value, False, False, 0)
            box.pack_start(row, False, False, 0)
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, low, high, step)
            scale.set_draw_value(False)
            def changed(widget, field=key, output=value):
                number = widget.get_value()
                output.set_text(('%d px' % number) if field in ('widget_width', 'widget_height') else ('%.2f' % number))
                if not self._syncing:
                    self.app.change(field, int(number) if field in ('widget_width', 'widget_height') else number)
            scale.connect('value-changed', changed)
            self._controls[key] = scale
            box.pack_start(scale, False, False, 0)
        palette = Gtk.ComboBoxText()
        for name in PALETTES:
            palette.append(name, name)
        palette.connect('changed', lambda widget: self._change('theme', widget.get_active_id()))
        self._controls['theme'] = palette
        box.pack_start(Gtk.Label(label='Farbpalette', xalign=0), False, False, 0)
        box.pack_start(palette, False, False, 0)
        placement = Gtk.ComboBoxText()
        for name, label in (('Overlay', 'Immer im Vordergrund'),
                            ('Normal', 'Normales Fenster'),
                            ('Desktop', 'Auf dem Desktop (X11)')):
            placement.append(name, label)
        placement.connect('changed', lambda widget: self._change('widget_placement', widget.get_active_id()))
        self._controls['widget_placement'] = placement
        box.pack_start(Gtk.Label(label='Platzierung', xalign=0), False, False, 0)
        box.pack_start(placement, False, False, 0)
        for text, key in (('Albumcover im Kreis', 'widget_cover'),
                          ('Widget sperren', 'widget_locked'),
                          ('Mausklicks durchlassen', 'widget_clickthrough')):
            toggle = Gtk.CheckButton(label=text)
            toggle.connect('toggled', lambda widget, field=key: self._change(field, widget.get_active()))
            self._controls[key] = toggle
            box.pack_start(toggle, False, False, 0)
        click_hint = Gtk.Label(label='Gesperrt oder durchlässig: Mausklicks gehen an das Fenster dahinter. Im Dashboard wieder entsperren.',
                               xalign=0, wrap=True)
        click_hint.set_max_width_chars(34)
        box.pack_start(click_hint, False, False, 0)
        row = Gtk.Box(spacing=8)
        for text, callback in (('Zentrieren', self.center), ('Dashboard', self._dashboard), ('Ausblenden', lambda *_: self.app.change('ring_enabled', False))):
            button = Gtk.Button(label=text)
            button.connect('clicked', callback)
            row.pack_start(button, True, True, 0)
        box.pack_start(row, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(box)
        self.options.add(scroll)
        protect_settings_from_scroll(box)

    def _change(self, key, value):
        if not self._syncing and value is not None:
            self.app.change(key, value)
            # A named palette must be visible even after a custom accent was chosen.
            if key == 'theme':
                self.app.change('accent', '')
                self.app.change('album_colours', False)

    def settings_changed(self, key=None):
        if self._disposed:
            return
        settings = self.app.settings
        if key == 'widget_size':
            settings['widget_width'] = settings['widget_height'] = int(settings['widget_size'])
        if key is None or key in ('widget_size', 'widget_width', 'widget_height'):
            self._request_size(int(settings['widget_width']), int(settings['widget_height']))
        if key is None or key == 'opacity':
            self.set_opacity(settings['opacity'])
        if key == 'widget_above':
            settings['widget_placement'] = 'Overlay' if settings['widget_above'] else 'Normal'
        if key is None or key in ('widget_above', 'widget_placement'):
            settings['widget_above'] = settings['widget_placement'] == 'Overlay'
            self._apply_placement()
        if key is None or key in ('widget_locked', 'widget_clickthrough', 'style'):
            if self.get_realized():
                self._apply_shape()
            if settings['widget_locked'] or settings['widget_clickthrough']:
                self.options.popdown()
                self.revealer.set_reveal_child(False)
                self.resize_revealer.set_reveal_child(False)
                self._end_drag()
        self._syncing = True
        try:
            for name, control in self._controls.items():
                value = settings[name]
                if isinstance(control, Gtk.Scale):
                    control.set_value(value)
                elif isinstance(control, Gtk.ToggleButton):
                    control.set_active(value)
                else:
                    control.set_active_id(value)
        finally:
            self._syncing = False
        self.visual.queue_draw()

    def show_options(self):
        self.settings_changed()
        self.revealer.set_reveal_child(True)
        self.options.popup()

    def _desktop_layer(self, *_):
        self.app.change('widget_placement', 'Desktop')
        self.options.popdown()

    def _apply_placement(self):
        """Apply the complete mode, including resetting the old desktop hint.

        On X11 the overlay bypasses managed-window stacking, so fullscreen and
        Show Desktop cannot push it behind another application. Dragging that
        window is handled locally. Wayland needs compositor-specific rules for
        a guaranteed top layer; GTK's keep-above request is its fallback.
        """
        mode = self.app.settings['widget_placement']
        if self.layer_window:
            layer = GtkLayerShell.Layer.BOTTOM if mode == 'Desktop' else GtkLayerShell.Layer.OVERLAY
            GtkLayerShell.set_layer(self, layer)
            return
        x11 = 'X11' in type(self.get_display()).__name__
        desktop = mode == 'Desktop' and x11
        native_overlay = mode == 'Overlay' and x11
        window = self.get_window()
        remap = False
        if window is not None and native_overlay != self._native_overlay:
            remap = self.get_mapped()
            if remap:
                self.hide()
            window.set_override_redirect(native_overlay)
            self._native_overlay = native_overlay
        self.set_type_hint(Gdk.WindowTypeHint.DESKTOP if desktop else Gdk.WindowTypeHint.UTILITY)
        self.set_keep_above(mode == 'Overlay')
        self.set_keep_below(desktop)
        if mode == 'Overlay' or desktop:
            self.stick()
        else:
            self.unstick()
        if remap:
            self.show_all()

    def _mapped(self, *_):
        if not self._disposed:
            self._apply_placement()
            self._apply_shape()
            if self._native_overlay:
                self.get_window().raise_()
        return False

    def _dashboard(self, *_):
        self.options.popdown()
        self.app.show()
        self.app.present()

    def move(self, x, y):
        if not self.layer_window:
            return super().move(x, y)
        area = self._layer_monitor().get_geometry()
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.LEFT, max(0, min(x-area.x, area.width-60)))
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, max(0, min(y-area.y, area.height-60)))

    def _layer_monitor(self):
        monitor = GtkLayerShell.get_monitor(self)
        if monitor is None:
            monitor = self.get_display().get_monitor_at_window(self.app.get_window()) if self.app.get_window() else None
            monitor = monitor or self.get_display().get_monitor(0)
            GtkLayerShell.set_monitor(self, monitor)
        return monitor

    def get_position(self):
        if not self.layer_window:
            return super().get_position()
        area = self._layer_monitor().get_geometry()
        return (area.x + GtkLayerShell.get_margin(self, GtkLayerShell.Edge.LEFT),
                area.y + GtkLayerShell.get_margin(self, GtkLayerShell.Edge.TOP))

    def center(self, *_):
        monitor = self.get_display().get_monitor_at_window(self.get_window()) if self.get_window() else self.get_display().get_primary_monitor()
        if self.layer_window:
            monitor = self._layer_monitor()
        if monitor is not None:
            area = monitor.get_workarea()
            width, height = int(self.app.settings['widget_width']), int(self.app.settings['widget_height'])
            self.move(area.x+(area.width-width)//2, area.y+(area.height-height)//2)

    def _realize(self, *_):
        x, y = self.app.settings['widget_x'], self.app.settings['widget_y']
        if (x, y) != (-1, -1):
            # Keep an old position reachable when displays have changed.
            display = self.get_display()
            monitors = [display.get_monitor(i).get_workarea() for i in range(display.get_n_monitors())]
            if any(rect.x <= x < rect.x+rect.width-60 and rect.y <= y < rect.y+rect.height-60 for rect in monitors):
                self.move(x, y)
            else:
                self.center()
        else:
            self.center()
        self._restoring = False
        self._apply_placement()
        self._apply_shape()

    def _allocate(self, *_):
        if self.get_realized():
            self._apply_shape()
        circular = self.app.settings['style'] in ('Ring', 'Halo')
        width, height = self.get_allocated_width(), self.get_allocated_height()
        if self._requested_size == (width, height):
            self._requested_size = None
        side = min(width, height)
        # The ring stays centred when a wide/banner size is selected. Keep its
        # grip inside that circle rather than at the rectangular window corner,
        # where the native input shape would make it impossible to drag.
        self.resize_revealer.set_margin_end(max(8, int(width / 2 - side * .32 - 12)) if circular else 12)
        self.resize_revealer.set_margin_bottom(max(8, int(height / 2 - side * .32 - 12)) if circular else 12)

    def _request_size(self, width, height):
        """Coalesce both dimension controls before asking GTK to resize.

        GTK can still await the first configure event when several settings
        change in one turn. Submitting only the latest pair avoids losing the
        second dimension; the pending pair also protects it from stale frames.
        """
        self._requested_size = (width, height)
        if not self._size_idle:
            # Live previews can keep the normal idle queue busy. A default
            # priority one-shot still coalesces this turn without starvation.
            self._size_idle = GLib.idle_add(self._apply_requested_size, priority=GLib.PRIORITY_DEFAULT)

    def _apply_requested_size(self):
        self._size_idle = 0
        if not self._disposed and self._requested_size is not None:
            dimensions = self._requested_size
            self.set_default_size(*dimensions)
            self.resize(*dimensions)
            if self._native_overlay and self.get_window() is not None:
                # An override-redirect window has no manager to acknowledge
                # GTK's configure handshake. Resize its native surface too.
                self.get_window().resize(*dimensions)
        return False

    def _apply_shape(self):
        window = self.get_window()
        if window is None:
            return
        width, height = self.get_allocated_width(), self.get_allocated_height()
        region = (circle_region(width, height) if self.app.settings['style'] in ('Ring', 'Halo')
                  else rounded_region(width, height))
        passthrough = self.app.settings['widget_locked'] or self.app.settings['widget_clickthrough']
        window.input_shape_combine_region(cairo.Region() if passthrough else region, 0, 0)
        # Without compositing alpha is unavailable. At least remove every corner
        # through the native X11 bounding shape, rather than show a square.
        if 'X11' in type(self.get_display()).__name__ and not self.get_screen().is_composited():
            window.shape_combine_region(region, 0, 0)
        else:
            window.shape_combine_region(None, 0, 0)

    def _configure(self, *_):
        if self._restoring or self._disposed:
            return False
        if self._move_timer:
            GLib.source_remove(self._move_timer)
        self._move_timer = GLib.timeout_add(400, self._save_position)
        return False

    def _save_position(self, dimensions=None):
        self._move_timer = 0
        if not self._disposed:
            x, y = self.get_position()
            width, height = dimensions or self._requested_size or (self.get_allocated_width(), self.get_allocated_height())
            previous = tuple(self.app.settings[field] for field in ('widget_x', 'widget_y', 'widget_width', 'widget_height'))
            if (x, y, width, height) != previous:
                # One atomic settings write for position and both dimensions.
                self.app.settings.update(widget_x=x, widget_y=y, widget_width=width, widget_height=height)
                if hasattr(self.app, 'sync_controls'):
                    self.app.sync_controls()
                self.app.save()
        return False

    def _press(self, _, event):
        if self.app.settings['widget_locked'] or self.app.settings['widget_clickthrough']:
            return False
        if event.button == 3:
            self.show_options()
            return True
        if event.button == 1 and not self.app.settings['widget_locked']:
            if getattr(event, 'type', None) == Gdk.EventType._2BUTTON_PRESS:
                if self._click_timer:
                    GLib.source_remove(self._click_timer)
                    self._click_timer = 0
                self._end_drag()
                styles = CHOICES['style']
                current = self.app.settings['style']
                self.app.change('style', styles[(styles.index(current) + 1) % len(styles)])
                return True
            self._begin_drag('move', event)
            return True
        return False

    def _resize_press(self, _, event):
        if event.button == 1 and not (self.app.settings['widget_locked'] or self.app.settings['widget_clickthrough']):
            self._begin_drag('resize', event)
            return True
        return False

    def _begin_drag(self, kind, event):
        x, y = self.get_position()
        self._drag_kind = kind
        self._moved = False
        self._drag_size = None
        self._drag_origin = (event.x_root, event.y_root, x, y,
                             self.get_allocated_width(), self.get_allocated_height())

    def _motion(self, _, event):
        self._enter()
        if not self._drag_origin or not (event.state & Gdk.ModifierType.BUTTON1_MASK):
            return False
        root_x, root_y, x, y, width, height = self._drag_origin
        dx, dy = int(event.x_root - root_x), int(event.y_root - root_y)
        if not self.has_grab():
            if not self.drag_check_threshold(int(root_x), int(root_y), int(event.x_root), int(event.y_root)):
                return True
            self._moved = True
            if self._drag_kind == 'move' and not (self._native_overlay or self.layer_window):
                self.begin_move_drag(1, int(root_x), int(root_y), event.time)
                self._end_drag()
                return True
            self.grab_add()
        if self._drag_kind == 'resize':
            self._drag_size = (max(150, min(2560, width + dx)), max(150, min(1440, height + dy)))
            self._request_size(*self._drag_size)
        else:
            self.move(x + dx, y + dy)
        return True

    def _end_drag(self):
        self._drag_origin = self._drag_kind = None
        self._drag_size = None
        if self.has_grab():
            self.grab_remove()

    def _release(self, _, event):
        if event.button == 1 and self._drag_origin:
            # GTK allocates a requested size on the next frame. A quick release
            # can arrive first, so persist the final requested dimensions rather
            # than overwrite them with the previous frame's allocation.
            dimensions = self._drag_size
            if self._drag_kind == 'move' and not self._moved:
                if self._click_timer:
                    GLib.source_remove(self._click_timer)
                delay = Gtk.Settings.get_default().get_property('gtk-double-click-time')
                self._click_timer = GLib.timeout_add(delay,self._toggle_card)
            self._end_drag()
            if self._move_timer:
                GLib.source_remove(self._move_timer)
                self._move_timer = 0
            self._save_position(dimensions)
            self.settings_changed()
            return True
        return False

    def _toggle_card(self):
        self._click_timer = 0
        if not self._disposed and not self.app.settings['widget_locked'] and not self.app.settings['widget_clickthrough']:
            self.app.change('queue_card', not self.app.settings['queue_card'])
        return False

    def _enter(self, *_):
        if self.app.settings['widget_locked'] or self.app.settings['widget_clickthrough']:
            return False
        if self._hover_timer:
            GLib.source_remove(self._hover_timer)
            self._hover_timer = 0
        media = self.app.current_media()
        self.play_button.set_image(Gtk.Image.new_from_icon_name(
            'media-playback-pause-symbolic' if media.playing else 'media-playback-start-symbolic', Gtk.IconSize.BUTTON))
        self.revealer.set_reveal_child(True)
        self.resize_revealer.set_reveal_child(True)
        return False

    def _leave(self, *_):
        self._schedule_hide()
        return False

    def _schedule_hide(self):
        if self._disposed:
            return
        if self._hover_timer:
            GLib.source_remove(self._hover_timer)
        def hide():
            self._hover_timer = 0
            if not self.options.get_visible():
                self.revealer.set_reveal_child(False)
                self.resize_revealer.set_reveal_child(False)
            return False
        self._hover_timer = GLib.timeout_add(600, hide)

    def _key(self, _, event):
        if Gdk.keyval_name(event.keyval) == 'Escape':
            self.options.popdown()
            return True
        return False

    def _dispose(self, *_):
        self._disposed = True
        self._end_drag()
        for timer in (self._move_timer, self._hover_timer, self._size_idle, self._click_timer):
            if timer:
                GLib.source_remove(timer)
        self._move_timer = self._hover_timer = self._size_idle = 0
        self.options.destroy()
