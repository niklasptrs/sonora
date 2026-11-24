"""Native, live visualizer settings page.

This component uses Sonora's renderer and settings, including its audio stream;
the previews never substitute a canned animation for captured audio.
"""
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango
import cairo


PANEL_CSS = b'''
.visualizer-panel { background: #0b0b0f; color: #ececf1; }
.visualizer-editor { padding: 26px 24px 36px; }
.visualizer-preview-rail { padding: 24px 20px; border-left: 1px solid #232329; background: #0e0e12; }
.visualizer-title { font-size: 21px; font-weight: 700; color: #eeeeF4; }
.visualizer-section { font-size: 10px; font-weight: 700; letter-spacing: 1.6px; color: #777780; margin-top: 24px; }
.visualizer-hint { font-size: 12px; color: #909098; }
button.visualizer-tile, button.visualizer-theme, .visualizer-detail { background: #141419; border: 1px solid #292930; border-radius: 13px; box-shadow: none; color: #ededf3; }
button.visualizer-tile { padding: 8px; }
button.visualizer-theme { padding: 7px; }
button.visualizer-tile:hover, button.visualizer-theme:hover { background: #1b1b22; border-color: #585164; }
button.visualizer-tile.selected, button.visualizer-theme.selected { border: 2px solid #a758f5; padding: 7px; }
.visualizer-tile-title { font-size: 12px; font-weight: 700; color: #eeeef5; }
.visualizer-theme-title { font-size: 10px; color: #a0a0aa; }
.visualizer-detail { padding: 14px; }
.visualizer-detail-title { font-size: 12px; color: #ececf3; }
.visualizer-detail-value { font-size: 11px; color: #9898a3; }
.visualizer-panel scale trough { background: #303039; border: none; min-height: 4px; border-radius: 6px; }
.visualizer-panel scale highlight { background: #ac5cff; border: none; border-radius: 6px; }
.visualizer-panel scale slider { background: #f3dcff; border: 3px solid #a758f5; min-width: 9px; min-height: 9px; box-shadow: none; }
.visualizer-panel checkbutton { color: #e9e9ef; }
.visualizer-panel checkbutton check:checked { background: #a758f5; border-color: #a758f5; color: #fff; }
.visualizer-summary { background: #141419; border: 1px solid #292930; border-radius: 13px; }
.visualizer-summary-row { padding: 10px 13px; border-bottom: 1px solid #222229; }
.visualizer-panel .visualizer-preview { border: 1px solid #35303f; border-radius: 16px; }
'''


def _label(text, css=None, wrap=False):
    label = Gtk.Label(label=text, xalign=0, wrap=wrap)
    if wrap:
        label.set_max_width_chars(46)
    if css:
        label.get_style_context().add_class(css)
    return label


class PaletteSwatch(Gtk.DrawingArea):
    def __init__(self, colours):
        super().__init__()
        self.colours = colours
        self.set_size_request(48, 25)
        self.connect('draw', self._draw)

    def _draw(self, _, cr):
        width, height = self.get_allocated_width(), self.get_allocated_height()
        # A rounded swatch built entirely from colour values.
        radius = 6
        cr.new_sub_path()
        cr.arc(width-radius, radius, radius, -1.5708, 0)
        cr.arc(width-radius, height-radius, radius, 0, 1.5708)
        cr.arc(radius, height-radius, radius, 1.5708, 3.1416)
        cr.arc(radius, radius, radius, 3.1416, 4.7124)
        cr.close_path()
        gradient = cairo.LinearGradient(0, 0, width, height)
        for i, colour in enumerate(self.colours):
            values = tuple(int(colour[j:j+2], 16)/255 for j in (1, 3, 5))
            gradient.add_color_stop_rgb(i/max(1, len(self.colours)-1), *values)
        cr.set_source(gradient)
        cr.fill()


def _desktop_preview(app, visual_factory, themes):
    class Preview(visual_factory):
        def draw(self, widget, cr):
            width, height = self.get_allocated_width(), self.get_allocated_height()
            radius = min(16, width / 2, height / 2)
            cr.save()
            cr.new_sub_path()
            cr.arc(width-radius, radius, radius, -1.5708, 0)
            cr.arc(width-radius, height-radius, radius, 0, 1.5708)
            cr.arc(radius, height-radius, radius, 1.5708, 3.1416)
            cr.arc(radius, radius, radius, 3.1416, 4.7124)
            cr.close_path()
            cr.clip()
            cr.set_source_rgb(.065, .061, .086)
            cr.paint()
            colours = themes[app.settings['theme']]
            for x, y, colour in ((width*.15, height*.12, colours[0]),
                                  (width*.9, height*.9, colours[-1])):
                values = tuple(int(colour[j:j+2], 16)/255 for j in (1, 3, 5))
                field = cairo.RadialGradient(x, y, 0, x, y, max(width, height)*.95)
                field.add_color_stop_rgba(0, *values, .23)
                field.add_color_stop_rgba(1, *values, 0)
                cr.set_source(field)
                cr.paint()
            # The widget renderer clears its own transparent surface. Render
            # it into a group so those pixels reveal this preview background
            # when shown inside the opaque dashboard window.
            cr.push_group()
            result = super().draw(widget, cr)
            cr.pop_group_to_source()
            cr.paint()
            cr.restore()
            return result
    return Preview(app, mini=True, desktop=True)


class VisualizerPanel(Gtk.Box):
    def __init__(self, app, visual_factory, styles, themes):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.app = app
        self.style_buttons = {}
        self.theme_buttons = {}
        self.summary = {}
        self._state = None
        self.get_style_context().add_class('visualizer-panel')
        provider = Gtk.CssProvider()
        provider.load_from_data(PANEL_CSS)
        Gtk.StyleContext.add_provider_for_screen(self.get_screen(), provider,
                                                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION+2)
        editor = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        editor.get_style_context().add_class('visualizer-editor')
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(editor)
        self.pack_start(scroll, True, True, 0)
        editor.pack_start(_label('WIDGETS', 'visualizer-hint'), False, False, 0)
        editor.pack_start(_label('Visualizer', 'visualizer-title'), False, False, 2)
        self._section(editor, 'STIL', 'Jede Vorschau reagiert live auf deine Musik.')
        grid = self._flow(2, 4, 10)
        for name in styles:
            tile = Gtk.Button()
            tile.get_style_context().add_class('visualizer-tile')
            contents = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            preview = visual_factory(app, mini=True, style_override=name)
            preview.set_size_request(92, 73)
            contents.pack_start(preview, True, True, 0)
            contents.pack_start(_label(name, 'visualizer-tile-title'), False, False, 0)
            tile.add(contents)
            tile.connect('clicked', lambda _, style=name: app.change('style', style))
            self.style_buttons[name] = tile
            grid.add(tile)
        editor.pack_start(grid, False, False, 0)
        self._section(editor, 'FARBE', 'Sechs Farbwelten, Albumfarben oder dein eigener Akzent.')
        swatches = self._flow(3, 7, 7)
        palette_order = [key for key in ('Nebula', 'Ember', 'Mint', 'Ice', 'Sunset', 'Mono') if key in themes]
        for name in palette_order:
            tile = self._swatch_button(name, themes[name])
            tile.connect('clicked', lambda _, theme=name: self._theme(theme))
            self.theme_buttons[name] = tile
            swatches.add(tile)
        album = self._swatch_button('Albumfarben', ('#ff7474', '#ffdd69', '#7ee6c0', '#6ba9ff', '#cc6bff'))
        album.connect('clicked', lambda *_: self._album())
        self.theme_buttons['album'] = album
        swatches.add(album)
        editor.pack_start(swatches, False, False, 0)
        custom_row = Gtk.Box(spacing=9)
        colour = Gtk.ColorButton(title='Eigene Akzentfarbe')
        rgba = Gdk.RGBA()
        rgba.parse(app.settings.get('accent') or '#a758f5')
        colour.set_rgba(rgba)
        colour.connect('color-set', self._custom)
        self.custom_colour = colour
        custom_row.pack_start(colour, False, False, 0)
        custom_row.pack_start(_label('Eigene Farbe', 'visualizer-hint'), False, False, 0)
        editor.pack_start(custom_row, False, False, 0)
        self._section(editor, 'DETAILS')
        self._scale(editor, 'Dichte', 'density', 16, 128, 1, lambda value: '%d Strahlen' % value)
        self._scale(editor, 'Hintergrundfläche', 'backdrop', 0, 1, .02, self._percent)
        self._scale(editor, 'Leuchten', 'glow', 0, 1, .02, self._percent)
        self._toggle(editor, 'Titel im Player / Vollbild', 'now_playing')
        self._toggle(editor, 'Titel im Desktop-Widget', 'widget_now_playing')
        self._toggle(editor, 'Albumcover als farbiges Leuchten', 'album_glow')
        self._toggle(editor, 'Albumcover im Kreis', 'widget_cover')
        editor.pack_start(_label('Titel- und Cover-Layout', 'visualizer-detail-title'), False, False, 3)
        editor.pack_start(app.combo(('Compact', 'Centred', 'Cover', 'Text only'),
                                    app.settings['layout'], 'layout'), False, False, 0)
        self._section(editor, 'DESKTOP-SZENE')
        self._toggle(editor, 'Desktop-Ring anzeigen', 'ring_enabled')
        self._toggle(editor, 'Lichtspuren bei Bassimpulsen', 'trails')
        self._toggle(editor, 'Farblicht am Bildschirmrand', 'edge_glow')
        self._toggle(editor, 'Titelkarte und Warteschlange', 'queue_card')
        self._toggle(editor, 'Lyrics frei auf dem Desktop platzieren', 'detached_lyrics')
        self._scale(editor, 'Lyrics-Schriftgröße', 'lyrics_font_size', 14, 64, 1, lambda n: '%d px' % n)
        self._scale(editor, 'Lyrics-Zeilenbreite', 'lyrics_width', 180, 1100, 5, lambda n: '%d px' % n)
        editor.pack_start(_label('Lyrics-Ausrichtung', 'visualizer-detail-title'), False, False, 0)
        editor.pack_start(app.combo(('Links','Mitte','Rechts'), app.settings['lyrics_align'], 'lyrics_align'), False, False, 0)
        self._section(editor, 'FENSTEREBENE', 'Der Desktop-Kreis bleibt auf Wunsch über deinen geöffneten Fenstern.')
        editor.pack_start(app.placement_control(), False, False, 0)
        self._section(editor, 'GRÖSSE')
        presets = Gtk.Box(spacing=6)
        for title, width, height in (('Klein', 240, 240), ('Mittel', 300, 300), ('Groß', 380, 380),
                                     ('Breit', 460, 220)):
            button = Gtk.Button(label=title)
            button.connect('clicked', lambda _, w=width, h=height: self._set_size(w, h))
            presets.pack_start(button, True, True, 0)
        editor.pack_start(presets, False, False, 0)
        banner = Gtk.Button(label='Banner über den Bildschirm')
        banner.connect('clicked', self._banner)
        editor.pack_start(banner, False, False, 0)
        self._scale(editor, 'Breite', 'widget_width', 150, 2560, 10, lambda value: '%d px' % value)
        self._scale(editor, 'Höhe', 'widget_height', 150, 1440, 10, lambda value: '%d px' % value)
        self._section(editor, 'FENSTER')
        self._scale(editor, 'Deckkraft', 'opacity', .2, 1, .02, self._percent)
        self._toggle(editor, 'Widget sperren', 'widget_locked')
        self._toggle(editor, 'Mausklicks durchlassen', 'widget_clickthrough')
        editor.pack_start(_label('Zum Entsperren diese Optionen im Dashboard verwenden.', 'visualizer-hint', True), False, False, 0)
        actions = Gtk.Box(spacing=8)
        for title, callback in (('Position zurücksetzen', self._center), ('Kreis anzeigen', self._show)):
            button = Gtk.Button(label=title)
            button.connect('clicked', callback)
            actions.pack_start(button, True, True, 0)
        editor.pack_start(actions, False, False, 5)
        rail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=17)
        rail.set_size_request(220, -1)
        rail.get_style_context().add_class('visualizer-preview-rail')
        large = _desktop_preview(app, visual_factory, themes)
        large.set_size_request(200, 230)
        large.get_style_context().add_class('visualizer-preview')
        rail.pack_start(large, False, False, 0)
        summary = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        summary.get_style_context().add_class('visualizer-summary')
        for title, key in (('Stil', 'style'), ('Farbe', 'theme'), ('Fensterebene', 'widget_placement'), ('Größe', 'widget_size')):
            row = Gtk.Box(spacing=9)
            row.get_style_context().add_class('visualizer-summary-row')
            row.pack_start(_label(title, 'visualizer-hint'), True, True, 0)
            value = Gtk.Label(xalign=1, ellipsize=Pango.EllipsizeMode.END, max_width_chars=18)
            value.get_style_context().add_class('visualizer-tile-title')
            row.pack_end(value, False, False, 0)
            summary.pack_start(row, False, False, 0)
            self.summary[key] = value
        rail.pack_start(summary, False, False, 0)
        show = Gtk.Button(label='Desktop-Kreis öffnen')
        show.get_style_context().add_class('suggested-action')
        show.connect('clicked', self._show)
        rail.pack_start(show, False, False, 0)
        stage = Gtk.Button(label='Album-Bühne öffnen')
        stage.set_tooltip_text('Breites Desktop-Widget mit Cover, Albumfarben und synchronisierten Lyrics')
        stage.connect('clicked', self._open_stage)
        rail.pack_start(stage, False, False, 0)
        self.pack_start(rail, False, False, 0)
        self._sonora_sync = self._refresh
        self._refresh()

    @staticmethod
    def _flow(minimum, maximum, spacing):
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_homogeneous(True)
        flow.set_min_children_per_line(minimum)
        flow.set_max_children_per_line(maximum)
        flow.set_row_spacing(spacing)
        flow.set_column_spacing(spacing)
        return flow

    @staticmethod
    def _percent(value):
        return '%d%%' % round(value*100)

    @staticmethod
    def _section(box, title, hint=None):
        box.pack_start(_label(title, 'visualizer-section'), False, False, 0)
        if hint:
            box.pack_start(_label(hint, 'visualizer-hint', True), False, False, 0)

    @staticmethod
    def _swatch_button(name, colours):
        button = Gtk.Button()
        button.get_style_context().add_class('visualizer-theme')
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        content.pack_start(PaletteSwatch(colours), False, False, 0)
        label = Gtk.Label(label=name)
        label.get_style_context().add_class('visualizer-theme-title')
        content.pack_start(label, False, False, 0)
        button.add(content)
        return button

    def _scale(self, box, title, key, low, high, step, formatter):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.get_style_context().add_class('visualizer-detail')
        header = Gtk.Box(spacing=6)
        header.pack_start(_label(title, 'visualizer-detail-title'), True, True, 0)
        initial = self.app.settings.get(key, self.app.settings['widget_size'])
        value = _label('', 'visualizer-detail-value')
        self.app.ui.message(value, formatter(initial))
        header.pack_end(value, False, False, 0)
        card.pack_start(header, False, False, 0)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, low, high, step)
        scale.set_draw_value(False)
        scale.set_value(initial)
        def changed(control):
            number = control.get_value()
            self.app.ui.message(value, formatter(number))
            if not self.app.syncing_settings:
                self.app.change(key, int(number) if key in ('widget_size', 'widget_width', 'widget_height', 'density') else number)
        scale.connect('value-changed', changed)
        self.app.setting_controls.append((key, scale))
        card.pack_start(scale, False, False, 0)
        box.pack_start(card, False, False, 0)

    def _toggle(self, box, title, key):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.get_style_context().add_class('visualizer-detail')
        self.app.setting_toggle(card, title, key)
        box.pack_start(card, False, False, 0)

    def _theme(self, theme):
        self.app.change('accent', '')
        self.app.change('album_colours', False)
        self.app.change('theme', theme)
        self._refresh()

    def _album(self):
        self.app.change('accent', '')
        self.app.change('album_colours', True)
        self._refresh()

    def _custom(self, control):
        colour = control.get_rgba()
        self.app.change('album_colours', False)
        self.app.change('accent', '#%02x%02x%02x' %
                        (round(colour.red*255), round(colour.green*255), round(colour.blue*255)))
        self._refresh()

    def _open_stage(self, *_):
        self.app.change('style', 'Album-Bühne')
        self._album()
        monitor = self.get_display().get_monitor_at_window(self.app.get_window())
        area = monitor.get_workarea()
        width = min(920, max(150, int(area.width*.8)))
        self._set_size(width, max(150, min(area.height, round(width*.39))))
        self._show()

    def _center(self, *_):
        self.app.open_widget()
        self.app.widget_window.center()

    def _set_size(self, width, height):
        self.app.select_widget_size(width, height)

    def _banner(self, *_):
        display = self.get_display()
        monitor = display.get_monitor_at_window(self.app.get_window()) if self.app.get_window() else display.get_primary_monitor()
        width = monitor.get_workarea().width if monitor else 1440
        self._set_size(min(2560, width), 220)
        self._show()

    def _show(self, *_):
        self.app.open_widget()

    def _refresh(self):
        settings = self.app.settings
        state = tuple(settings.get(key) for key in ('style', 'theme', 'accent', 'album_colours', 'widget_placement', 'widget_size', 'widget_width', 'widget_height'))
        if state == self._state:
            return True
        self._state = state
        if settings['accent']:
            colour = Gdk.RGBA()
            if colour.parse(settings['accent']):
                self.custom_colour.set_rgba(colour)
        for name, button in self.style_buttons.items():
            context = button.get_style_context()
            (context.add_class if name == settings['style'] else context.remove_class)('selected')
        selected = 'album' if settings['album_colours'] else (None if settings['accent'] else settings['theme'])
        for name, button in self.theme_buttons.items():
            context = button.get_style_context()
            (context.add_class if name == selected else context.remove_class)('selected')
        self.app.ui.message(self.summary['style'], settings['style'])
        self.app.ui.message(self.summary['theme'], 'Albumfarben' if selected == 'album' else (settings['accent'] or settings['theme']))
        placement = {'Overlay': 'Immer vorne', 'Normal': 'Normal', 'Desktop': 'Desktop'}[settings['widget_placement']]
        self.app.ui.message(self.summary['widget_placement'], placement)
        width = settings.get('widget_width', settings['widget_size'])
        height = settings.get('widget_height', settings['widget_size'])
        self.summary['widget_size'].set_text('%d × %d' % (width, height))
        return True

def build_visualizer_panel(app, visual_factory, styles, themes):
    return VisualizerPanel(app, visual_factory, styles, themes)
