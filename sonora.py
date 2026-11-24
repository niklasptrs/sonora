#!/usr/bin/python3
"""Sonora: a small native MPRIS controller and desktop audio visualizer."""
import argparse
import colorsys
import datetime
import json
import math
import os
from pathlib import Path
import sys
import threading
import time
import weakref
from soloist_setup import SoloistSetup
from interface_language import InterfaceLanguage
from soloist_backend import Soloist, SoloistError, entity_info, discover_endpoint
from desktop_settings import atomic_write, ProfileStore, ProfileError, validate_settings, autostart_enabled, set_autostart, DEFAULT_SETTINGS, CHOICES
from audio_engine import Audio, spectrum, VisualResponse
from scene_media import album_palette, load_art, mpris_upcoming
from scene_windows import SceneWindow
from desktop_widget import DesktopRing, protect_settings_from_scroll
from visualizer_panel import build_visualizer_panel
from lyrics import animated_line, load_lyrics, LyricsClient, LyricsError
from types import SimpleNamespace

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Gdk, Gio, GLib, Pango, GdkPixbuf
import numpy as np

STYLES = CHOICES['style']
THEMES = {'Nebula': ('#6366f1', '#a855f7', '#ec4899'),
          'Ember': ('#f59e0b', '#f43f5e', '#c026d3'),
          'Mint': ('#22d3ee', '#2dd4bf', '#a3e635'),
          'Ice': ('#93c5fd', '#60a5fa', '#818cf8'),
          'Sunset': ('#fbbf24', '#fb7185', '#8b5cf6'),
          'Mono': ('#f8fafc', '#cbd5e1', '#94a3b8'),
          # Keep colours stored by earlier Sonora versions selectable.
          'Ocean': ('#22d3ee', '#3b82f6', '#818cf8'),
          'Forest': ('#34d399', '#a3e635', '#06b6d4'),
          'Rose': ('#fda4af', '#e879f9', '#a78bfa')}
CONFIG = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'sonora/settings.json'
DEFAULTS = DEFAULT_SETTINGS.copy()


def load_settings():
    try:
        with CONFIG.open() as stream:
            data = stream.read(16*1024+1)
        if len(data) > 16*1024:
            return DEFAULTS.copy()
        return validate_settings(json.loads(data))
    except (OSError, ValueError, TypeError):
        return DEFAULTS.copy()



class Media:
    def __init__(self, settings):
        self.settings = settings
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            self.bus = None
        self.names = []
        self.name = ''
        self.title = 'Deine Musik. Dein Moment.'
        self.artist = 'Spotify oder einen MPRIS-Player starten'
        self.playing = False
        self.props = {}
        self.art_url = ''
        self.pending = False
        self.position_at = time.monotonic()
        self.poll()
        self.timer = GLib.timeout_add_seconds(2, self.poll)

    def poll(self):
        if self.pending or self.bus is None:
            return True
        self.pending = True
        self.bus.call('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'ListNames', None, None, Gio.DBusCallFlags.NONE, 1500, None, self.listed)
        return True

    def listed(self, bus, result):
        try:
            self.names = sorted(n for n in bus.call_finish(result).unpack()[0] if n.startswith('org.mpris.MediaPlayer2.'))
            preferred = self.settings['player']
            old_name = self.name
            self.name = preferred if preferred in self.names else next((n for n in self.names if 'spotify' in n.lower()), self.names[0] if self.names else '')
            if self.name:
                bus.call(self.name, '/org/mpris/MediaPlayer2', 'org.freedesktop.DBus.Properties', 'GetAll', GLib.Variant('(s)', ('org.mpris.MediaPlayer2.Player',)), None, Gio.DBusCallFlags.NONE, 1500, None, self.updated)
                return
            self.props = {}
            if old_name:
                self.title, self.artist, self.playing = 'Deine Musik. Dein Moment.', 'Spotify oder einen MPRIS-Player starten', False
                self.art_url = ''
        except GLib.Error:
            self.name = ''
        self.pending = False

    def updated(self, bus, result):
        try:
            self.props = bus.call_finish(result).unpack()[0]
            self.position_at = time.monotonic()
            meta = self.props.get('Metadata', {})
            self.art_url = meta.get('mpris:artUrl', '')
            self.title = meta.get('xesam:title', 'Unbekannter Titel')
            self.artist = ', '.join(meta.get('xesam:artist', [])) or 'Unbekannter Interpret'
            self.playing = self.props.get('PlaybackStatus') == 'Playing'
        except GLib.Error:
            self.name = ''
            self.props = {}
            self.playing = False
        self.pending = False

    def command(self, method):
        capability = {'Next': 'CanGoNext', 'Previous': 'CanGoPrevious', 'PlayPause': 'CanControl'}.get(method, 'CanControl')
        if self.name and self.props.get(capability, False):
            self.bus.call(self.name, '/org/mpris/MediaPlayer2', 'org.mpris.MediaPlayer2.Player', method, None, None, Gio.DBusCallFlags.NONE, 1500, None, self.command_done)

    def command_done(self, bus, result):
        try:
            bus.call_finish(result)
        except GLib.Error:
            pass
        self.poll()


def rgb(hexcolor):
    return tuple(int(hexcolor[i:i+2], 16) / 255 for i in (1, 3, 5))


def palette_for(app, theme='Dashboard'):
    if app.cover_palette and (theme == 'Album' or (theme == 'Dashboard' and app.settings['album_colours'])):
        return app.blended_palette() if hasattr(app, 'blended_palette') else app.cover_palette
    if theme == 'Album':
        return [rgb(c) for c in THEMES[app.settings['theme']]]
    if theme != 'Dashboard':
        return [rgb(c) for c in THEMES[theme]]
    accent = app.settings['accent']
    if accent:
        hue, saturation, value = colorsys.rgb_to_hsv(*rgb(accent))
        return [colorsys.hsv_to_rgb((hue+delta)%1, saturation, value) for delta in (-.12, 0, .12)]
    return [rgb(c) for c in THEMES[app.settings['theme']]]


class Visual(Gtk.DrawingArea):
    def __init__(self, app, mini=False, desktop=False, style_override=None):
        super().__init__()
        self.app = app
        if not hasattr(app, '_visuals'):
            app._visuals = weakref.WeakSet()
        app._visuals.add(self)
        self.mini = mini
        self.desktop = desktop
        self.style_override = style_override
        self.phase = 0
        self.last_bass = 0.
        self.pulse_at = -math.inf
        self.bursts = []
        self.burst_angle = 0.
        self.response = VisualResponse()
        self.last_frame = time.monotonic()
        self.last_draw = 0
        self.values = np.zeros(64)
        self.connect('draw', self.draw)
        self.set_size_request(120, 75) if mini and not desktop else self.set_size_request(200, 150)
        self.frame = GLib.timeout_add(round(1000 / app.settings['fps']), self.tick)
        self.connect('destroy', self.cleanup)

    def cleanup(self, *_):
        if self.frame:
            GLib.source_remove(self.frame)
            self.frame = None

    def tick(self):
        if self.get_mapped() and not (self.get_toplevel() is self.app and self.app.obscured):
            now = time.monotonic()
            with self.app.audio.lock:
                quiet = self.app.audio.levels.max() < 1e-7
            lyrics_in_fullscreen = (self.app.full and not self.desktop and not self.mini
                                    and self.app.settings['fullscreen_lyrics'] and self.app.lyrics_data)
            timed_stage = (self.style_override or self.app.settings['style']) == 'Album-Bühne' and self.app.lyrics_data
            if quiet and not timed_stage and not lyrics_in_fullscreen and self.values.max() < .001 and now - self.last_draw < 1:
                return True
            self.last_draw = now
            self.queue_draw()
        return True

    def text(self, cr, text, x, y, size, colour=(1, 1, 1), align=False):
        layout = self.create_pango_layout(text)
        layout.set_font_description(Pango.FontDescription('Sans ' + str(size)))
        layout.set_width(int(max(100, self.get_allocated_width() - 80) * Pango.SCALE))
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        if align:
            layout.set_alignment(Pango.Alignment.CENTER)
            x = 40
        cr.set_source_rgb(*colour)
        cr.move_to(x, y)
        from gi.repository import PangoCairo
        PangoCairo.show_layout(cr, layout)

    def _label(self, cr, text, x, y, width, size, colour, centred=False):
        """One bounded line: changing a card layout really changes its geometry."""
        from gi.repository import PangoCairo
        layout = self.create_pango_layout(str(text or ''))
        layout.set_font_description(Pango.FontDescription('Sans ' + str(size)))
        layout.set_width(int(max(1, width)*Pango.SCALE))
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_single_paragraph_mode(True)
        if centred:
            layout.set_alignment(Pango.Alignment.CENTER)
        cr.set_source_rgb(*colour)
        cr.move_to(x, y)
        PangoCairo.show_layout(cr, layout)

    def _cover(self, cr, x, y, size, circular=False):
        cover = self.app.cover
        if cover is None or size <= 0:
            return
        cr.save()
        cr.new_path()
        if circular:
            cr.arc(x+size/2, y+size/2, size/2, 0, math.tau)
        else:
            radius = min(8, size*.12)
            for cx, cy, start in ((x+size-radius, y+radius, -math.pi/2),
                                  (x+size-radius, y+size-radius, 0),
                                  (x+radius, y+size-radius, math.pi/2),
                                  (x+radius, y+radius, math.pi)):
                cr.arc(cx, cy, radius, start, start+math.pi/2)
            cr.close_path()
        cr.clip()
        factor = max(size/cover.get_width(), size/cover.get_height())
        cr.translate(x+(size-cover.get_width()*factor)/2, y+(size-cover.get_height()*factor)/2)
        cr.scale(factor, factor)
        Gdk.cairo_set_source_pixbuf(cr, cover, 0, 0)
        cr.paint()
        cr.restore()

    def _track_card(self, cr, w, h, reserve, palette):
        s = self.app.settings
        if self.desktop:
            if not s.get('widget_now_playing', False):
                return
            if (self.style_override or s['style']) in ('Ring', 'Halo'):
                # Fit the information into the lower half of the circular
                # window, leaving its centre transparent without album art.
                left, top, width = w*.23, h*.72, w*.54
                scale = min(1, w/420)
            else:
                left, top, width = w*.08, h-reserve, w*.84
                scale = min(1.15, max(.75, w/600))
        else:
            if not s.get('now_playing', True) or self.app.full:
                return
            left, top, width = w*.08, h-reserve, w*.84
            scale = min(1.15, max(.75, w/600))
        media = self.app.current_media()
        layout = s['layout']
        title_size, artist_size = 11*scale, 9*scale
        title_colour, artist_colour = (.93, .95, .99), (.62, .68, .76)
        if layout == 'Centred':
            art = 26*scale
            if self.app.cover is not None:
                self._cover(cr, left+(width-art)/2, top, art)
                top += art+5
            self._label(cr, media.title, left, top, width, title_size, title_colour, True)
            self._label(cr, media.artist, left, top+20*scale, width, artist_size, artist_colour, True)
        elif layout == 'Text only':
            self._label(cr, media.title, left, top, width, title_size, title_colour, True)
            self._label(cr, media.artist, left, top+20*scale, width, artist_size, artist_colour, True)
        else:
            art = (60 if layout == 'Cover' else 36)*scale
            if self.desktop:
                art = min(art, 38*scale)
            inset = 0
            if self.app.cover is not None:
                self._cover(cr, left, top, art)
                inset = art+12*scale
            text_top = top+max(0, (art-36*scale)/2)
            self._label(cr, media.title, left+inset, text_top, width-inset, title_size, title_colour)
            self._label(cr, media.artist, left+inset, text_top+20*scale, width-inset, artist_size, artist_colour)

    def _stage_words(self, cr, width, height, wide, palette):
        from gi.repository import PangoCairo
        media = self.app.current_media()
        position = self.app.track_position()[0] + round(self.app.settings['lyrics_offset']*1000)
        lyrics = self.app.lyrics_data
        if self.app.full and not self.desktop and not self.app.settings['fullscreen_lyrics']:
            lyrics = None
        line = lyrics.line_at(position) if lyrics and lyrics.lines else None
        frame = animated_line(line, position, self.app.settings['lyrics_motion'], media.playing) if line else None
        text = frame.text if frame else media.title
        column = width*(.41 if wide else .86)
        layout = self.create_pango_layout(text)
        font = Pango.FontDescription('Sans Bold')
        font.set_absolute_size(max(10, min(column*.125, height*.16, 76))*Pango.SCALE)
        layout.set_font_description(font)
        layout.set_width(round(column*Pango.SCALE))
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_height(-2)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_alignment(Pango.Alignment.LEFT if wide else Pango.Alignment.CENTER)
        if line and line.cues:
            attr = Pango.attr_foreground_new(*(int(c*65535) for c in palette[0]))
            attr.end_index = min(line.highlighted_bytes(position), len(text.encode()))
            attrs = Pango.AttrList()
            attrs.insert(attr)
            layout.set_attributes(attrs)
        x = width*(.055 if wide else .07)
        y = height*(.52 if wide else .8) - layout.get_pixel_size()[1]/2
        if frame:
            x += frame.dx
            y += frame.dy
            cr.push_group()
        cr.move_to(x+1, y+2)
        PangoCairo.layout_path(cr, layout)
        cr.set_source_rgba(0, 0, 0, .6)
        cr.set_line_width(2)
        cr.stroke()
        cr.move_to(x, y)
        cr.set_source_rgb(.98, .97, .94)
        PangoCairo.show_layout(cr, layout)
        if frame:
            cr.pop_group_to_source()
            cr.paint_with_alpha(frame.opacity)

    def draw(self, _, cr):
        import cairo
        w, h = self.get_allocated_width(), self.get_allocated_height()
        s = self.app.settings
        full = self.app.full and not self.desktop and not self.mini
        palette = palette_for(self.app, s['fullscreen_theme'] if full else 'Dashboard')
        background = s['fullscreen_background'] if full else 'Ambient'
        cr.save()
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0) if self.desktop else cr.set_source_rgb(.043, .055, .071)
        cr.paint()
        cr.restore()
        if background == 'Black':
            cr.set_source_rgb(.008, .009, .013)
            cr.paint()
        elif background == 'Album' and self.app.cover_background is not None:
            cover = self.app.cover_background
            cr.save()
            scale = 1.12*max(w / cover.get_width(), h / cover.get_height())
            drift = time.monotonic()*.11
            cr.translate((w - cover.get_width()*scale)/2 + math.sin(drift)*w*.034,
                         (h - cover.get_height()*scale)/2 + math.cos(drift*.7)*h*.034)
            cr.scale(scale, scale)
            Gdk.cairo_set_source_pixbuf(cr, cover, 0, 0)
            cr.paint_with_alpha(.32)
            cr.restore()
        elif not self.desktop:
            glow = cairo.RadialGradient(w*.5, h*.48, 0, w*.5, h*.48, min(w,h)*.65)
            glow.add_color_stop_rgba(0, *palette[0], .09 + s['glow']*.12)
            glow.add_color_stop_rgba(1, *palette[2], 0)
            cr.set_source(glow)
            cr.paint()
        with self.app.audio.lock:
            raw = self.app.audio.levels.copy()
            wave = self.app.audio.wave.copy()
        now = time.monotonic()
        elapsed = min(.1, max(.001, now-self.last_frame))
        self.values = self.response.update(raw, s['sensitivity'], s['smoothing'], elapsed)
        self.last_frame = now
        v = np.interp(np.linspace(0, 63, s['density']), np.arange(64), self.values)
        fullscreen_style = s['fullscreen_style'] if full else 'Dashboard'
        style = self.style_override or (s['style'] if fullscreen_style == 'Dashboard' else fullscreen_style)
        stage = style == 'Album-Bühne'
        show_card = not stage and not full and (s.get('widget_now_playing', False) if self.desktop
                                         else not self.mini and s.get('now_playing', True))
        if self.desktop and style in ('Ring', 'Halo'):
            show_card = False  # Circular card sits inside the existing circle.
        card_heights = {'Compact': 54, 'Centred': 96, 'Cover': 78, 'Text only': 46}
        reserve = card_heights[s['layout']]*min(1.15, max(.75, w/600)) if show_card else 0
        plot_height = max(80, h-130) if full else max(80, h-reserve)
        cx, cy = w/2, plot_height/2
        side = min(w, plot_height)
        if stage:
            wide = w >= h*1.35
            cx = w*.72 if wide else w*.5
            cy = plot_height*(.5 if wide else .37)
            side = min(w*.52, plot_height) if wide else min(w, plot_height*.7)
        bass = float(np.mean(self.values[:14]))
        self.phase = (self.phase+elapsed*(.035+bass*.18)) % math.tau
        radius = side*(.255 + bass*.012)
        if bass > .14 and bass-self.last_bass > .035 and now-self.pulse_at > .26:
            self.pulse_at = now
            if s['trails'] and style in ('Ring','Halo','Album-Bühne'):
                self.burst_angle = (self.burst_angle + 2.39996) % math.tau
                self.bursts.append((now,self.burst_angle,bass))
        self.last_bass = bass
        pulse_age = (now-self.pulse_at)/.7
        if (full or stage) and pulse_age < 1:
            cr.set_source_rgba(*palette[0], (1-pulse_age)**2 * .33)
            cr.set_line_width(max(1, side*.007*(1-pulse_age)))
            cr.arc(cx, cy, radius*(1+pulse_age*.8), 0, math.tau)
            cr.stroke()
        backdrop = s.get('backdrop', 0)
        if backdrop > 0:
            if style in ('Ring', 'Halo'):
                # Circular artwork keeps transparent corners at any strength.
                surface = cairo.RadialGradient(cx, cy, side*.25, cx, cy, side*.49)
                surface.add_color_stop_rgba(0, .025, .035, .055, backdrop*.88)
                surface.add_color_stop_rgba(.82, .025, .035, .055, backdrop*.5)
                surface.add_color_stop_rgba(1, .025, .035, .055, 0)
                cr.set_source(surface)
                cr.arc(cx, cy, side*.49, 0, math.tau)
                cr.fill()
            else:
                margin, corner = 10, min(20, side*.08)
                cr.new_path()
                for x, y, start in ((w-margin-corner, margin+corner, -math.pi/2),
                                    (w-margin-corner, h-margin-corner, 0),
                                    (margin+corner, h-margin-corner, math.pi/2),
                                    (margin+corner, margin+corner, math.pi)):
                    cr.arc(x, y, corner, start, start+math.pi/2)
                cr.close_path()
                cr.set_source_rgba(.025, .035, .055, backdrop*.88)
                cr.fill()
        if s.get('album_glow', False) and self.app.cover_palette:
            glow_palette = self.app.blended_palette() if hasattr(self.app, 'blended_palette') else self.app.cover_palette
            for index, colour in enumerate(glow_palette):
                angle = index*math.tau/3
                gx, gy = cx+math.cos(angle)*side*.13, cy+math.sin(angle)*side*.13
                field = cairo.RadialGradient(gx, gy, 0, gx, gy, side*.34)
                field.add_color_stop_rgba(0, *colour, .22)
                field.add_color_stop_rgba(1, *colour, 0)
                cr.set_source(field)
                cr.arc(gx, gy, side*.34, 0, math.tau)
                cr.fill()
        grad = cairo.LinearGradient(cx-radius, cy-radius, cx+radius, cy+radius)
        for i, c in enumerate(palette):
            grad.add_color_stop_rgb(i/2, *c)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        if style in ('Ring', 'Album-Bühne'):
            # Mirrored spectrum gives bass, voices and highs their own arcs.
            half = np.interp(np.linspace(0, 63, max(8, len(v)//2)), np.arange(64), self.values)
            ring = np.concatenate((half, half[::-1]))
            segments = []
            for i, val in enumerate(ring):
                angle = i/len(ring)*math.tau - math.pi/2 + self.phase
                end = radius + 1.5 + float(val)*side*.205
                segments.append((math.cos(angle), math.sin(angle), end))
            width = max(1.4, min(3.2, side/len(ring)*.68))
            core = cairo.RadialGradient(cx, cy, 0, cx, cy, radius)
            core.add_color_stop_rgba(0, *palette[0], s['glow']*min(.65, bass*1.4))
            core.add_color_stop_rgba(.65, *palette[1], s['glow']*min(.18, bass*.4))
            core.add_color_stop_rgba(1, *palette[2], 0)
            cr.set_source(core)
            cr.arc(cx, cy, radius, 0, math.tau)
            cr.fill()
            spokes = cairo.RadialGradient(cx, cy, radius, cx, cy, radius+side*.22)
            for position, colour, alpha in ((0, palette[0], 1), (.48, palette[1], .9),
                                             (1, palette[2], .18)):
                spokes.add_color_stop_rgba(position, *colour, alpha)
            for extra, alpha in ((8, s['glow']*.07), (4, s['glow']*.18), (0, 1)):
                cr.set_source(spokes)
                cr.set_line_width(width+extra)
                if extra:
                    cr.push_group()
                for x, y, end in segments:
                    cr.move_to(cx+x*(radius+3), cy+y*(radius+3))
                    cr.line_to(cx+x*end, cy+y*end)
                cr.stroke()
                if extra:
                    cr.pop_group_to_source()
                    cr.paint_with_alpha(alpha)
            cr.set_source(grad)
            cr.set_line_width(1.3)
            cr.arc(cx, cy, radius-2, 0, math.tau)
            cr.stroke()
            if not self.desktop:
                cr.set_source_rgba(*palette[0], .07)
                cr.set_line_width(1)
                cr.arc(cx, cy, side*.47, 0, math.tau)
                cr.stroke()
        elif style == 'Halo':
            # A continuous spectrum contour, distinct from the Ring's spokes.
            half = np.interp(np.linspace(0, 63, max(24, len(v)//2)), np.arange(64), self.values)
            halo = np.concatenate((half, half[::-1]))
            points = []
            for index, value in enumerate(halo):
                angle = index/len(halo)*math.tau-math.pi/2
                reach = radius+side*(.018+float(value)*.12)
                points.append((cx+math.cos(angle)*reach, cy+math.sin(angle)*reach))
            for width, alpha in ((20, s['glow']*.08), (10, s['glow']*.18), (3, .9)):
                cr.push_group()
                cr.set_source(grad)
                cr.set_line_width(width)
                cr.move_to(*points[0])
                for point in points[1:]:
                    cr.line_to(*point)
                cr.close_path()
                cr.stroke()
                cr.pop_group_to_source()
                cr.paint_with_alpha(alpha)
        elif style in ('Bars', 'Mirror', 'Blocks'):
            cr.set_source(grad)
            bw = w*.8/len(v)
            for i, val in enumerate(v):
                x, size = w*.1+i*bw, max(1, float(val)*plot_height*.62)
                if style == 'Blocks':
                    for j in range(0, int(size)+1, 8):
                        cr.rectangle(x, plot_height*.82-j, max(1, bw-2), 4)
                else:
                    baseline = cy if style == 'Mirror' else plot_height*.82
                    cr.rectangle(x, baseline-size*.7, max(1, bw-2), size*.7)
                    if style == 'Mirror':
                        cr.rectangle(x, baseline+4, max(1, bw-2), size*.45)
                cr.fill()
        elif style == 'Waveform':
            cr.set_source(grad)
            cr.set_line_width(2)
            for i, val in enumerate(wave):
                x, y = w*.08+i/255*w*.84, cy+math.tanh(float(val)*s['sensitivity']*2)*plot_height*.35
                (cr.move_to if i == 0 else cr.line_to)(x, y)
            cr.stroke()
        else:
            for band in range(5):
                # Fade the field before it reaches either canvas edge.
                field = cairo.LinearGradient(0, 0, w, 0)
                colour = palette[band%3]
                for position, alpha in ((0, 0), (.12, .15), (.35, .45), (.65, .45), (.88, .15), (1, 0)):
                    field.add_color_stop_rgba(position, *colour, alpha)
                cr.set_source(field)
                cr.set_line_width(2 + band*2)
                for i in range(len(v)):
                    x = i/(len(v)-1)*w
                    y = cy + math.sin(i*.1+band*.8)*plot_height*.08 + float(v[i])*plot_height*.3
                    (cr.move_to if i == 0 else cr.line_to)(x, y)
                cr.stroke()
        self.bursts = [burst for burst in self.bursts if now-burst[0] < .78]
        if s['trails'] and style in ('Ring','Halo','Album-Bühne'):
            for start,rotation,strength in self.bursts:
                age = (now-start)/.78
                for i in range(18):
                    angle = rotation + i*2.39996
                    speed = .64+.36*math.sin(i*3.13+rotation)**2
                    reach = radius + side*(.055+.20*speed*(1-(1-age)**2))
                    length = side*(.025+.085*strength)*math.sin(math.pi*age)
                    dx,dy = math.cos(angle),math.sin(angle)
                    x,y = cx+reach*dx,cy+reach*dy
                    tail_x,tail_y = x-length*dx,y-length*dy
                    trail = cairo.LinearGradient(tail_x,tail_y,x,y)
                    colour = palette[i%3]
                    trail.add_color_stop_rgba(0,*colour,0)
                    trail.add_color_stop_rgba(1,*colour,(1-age)*min(1,strength*2))
                    cr.set_source(trail)
                    cr.set_line_width(max(1,side*.003))
                    cr.move_to(tail_x,tail_y)
                    cr.line_to(x,y)
                    cr.stroke()
        if stage and (not self.mini or self.desktop) and not (self.desktop and s['detached_lyrics']):
            self._stage_words(cr, w, plot_height, wide, palette)
        show_cover = s.get('widget_cover', True) if self.desktop else s['layout'] != 'Text only'
        if (stage or (style in ('Ring', 'Halo') and show_cover)) and self.app.cover is not None:
            size = radius*1.82
            self._cover(cr, cx-size/2, cy-size/2, size, circular=True)
        elif style in ('Ring', 'Halo') and not self.desktop and not self.mini:
            cr.set_source_rgba(*palette[0], .12)
            cr.arc(cx, cy, radius*.8, 0, math.tau)
            cr.fill()
            self.text(cr, 'sonora', 0, cy-20, max(12, min(24, int(radius*.13))), (.88,.95,.94), True)
            self.text(cr, self.app.ui.text('D E I N   S O U N D'), 0, cy+24, 7, palette[0], True)
        if not stage and (not self.mini or self.desktop):
            self._track_card(cr, w, h, reserve, palette)
        if full:
            if s['clock']:
                self.text(cr, datetime.datetime.now().strftime('%H:%M'), 32, 24, 18, (.55,.65,.66))
            if s['now_playing']:
                media = self.app.current_media()
                self.text(cr, media.title, 40, h-110, 23, align=True)
                self.text(cr, media.artist, 40, h-68, 12, (.6,.7,.71), True)
            if s['fullscreen_lyrics'] and not stage:
                frame = self.app.lyrics_frame()
                if frame.text:
                    cr.push_group()
                    cr.save()
                    cr.translate(frame.dx, frame.dy)
                    self.text(cr, frame.text, 40, h-155, 14, palette[0], True)
                    cr.restore()
                    cr.pop_group_to_source()
                    cr.paint_with_alpha(frame.opacity)


class Clock(Gtk.DrawingArea):
    text = Visual.text

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.connect('draw', self.draw)
        self.timer = GLib.timeout_add_seconds(1, self.tick)
        self.connect('destroy', lambda *_: GLib.source_remove(self.timer))

    def tick(self):
        if self.get_mapped():
            self.queue_draw()
        return True

    def draw(self, _, cr):
        now = datetime.datetime.now()
        w, h = self.get_allocated_width(), self.get_allocated_height()
        colour = palette_for(self.app)[0]
        cr.set_source_rgb(.05, .05, .08)
        cr.paint()
        style = self.app.settings['clock_style']
        if style == 'Analog':
            x, y, r = w/2, h*.45, min(w,h)*.32
            cr.set_source_rgb(*colour)
            cr.set_line_width(2)
            cr.arc(x,y,r,0,math.tau)
            cr.stroke()
            for i in range(12):
                a = i/12*math.tau
                cr.move_to(x+math.sin(a)*r*.88,y-math.cos(a)*r*.88)
                cr.line_to(x+math.sin(a)*r*.97,y-math.cos(a)*r*.97)
                cr.stroke()
            for value, length, width in [((now.hour%12+now.minute/60)/12,.55,4), ((now.minute+now.second/60)/60,.8,2), (now.second/60,.9,1)]:
                a=value*math.tau
                cr.set_line_width(width)
                cr.move_to(x,y)
                cr.line_to(x+math.sin(a)*r*length,y-math.cos(a)*r*length)
                cr.stroke()
        elif style == 'Stacked':
            self.text(cr, now.strftime('%H'), 0, h*.08, 38, colour, True)
            self.text(cr, now.strftime('%M'), 0, h*.38, 38, colour, True)
        elif style == 'Words':
            words = ('zwölf','eins','zwei','drei','vier','fünf','sechs','sieben','acht','neun','zehn','elf')
            hour = words[now.hour%12]+' Uhr'
            minute = '%02d Minuten' % now.minute
            if self.app.ui.english:
                hours = ('twelve','one','two','three','four','five','six','seven','eight','nine','ten','eleven')
                hour = hours[now.hour%12] + " o’clock"
                minute = '%02d minutes' % now.minute
            self.text(cr, hour, 0, h*.26, 25, colour, True)
            self.text(cr, minute, 0, h*.48, 16, colour, True)
        else:
            self.text(cr, now.strftime('%H:%M' if style == 'Minimal' else '%H:%M:%S'), 0, h*.28, 32, colour, True)
        self.text(cr, now.strftime('%d.%m.%Y'), 0, h*.8, 12, (.6,.6,.7), True)


CSS = b'''
* { font-family: "DejaVu Sans", sans-serif; font-size: 12px; }
window { background: #0b0f13; color: #e5eeed; }
window.desktop-widget, window.desktop-widget > *, .desktop-widget drawingarea { background: transparent; box-shadow: none; border: none; }
.sidebar { background: #0e1419; padding: 28px 16px 20px; border-right: 1px solid #202b32; }
.brand { font-size: 26px; font-weight: bold; color: #a7f3d0; }
.eyebrow { color: #71868a; font-size: 9px; font-weight: bold; letter-spacing: 2px; }
.subtitle { color: #7f949a; font-size: 11px; }
button { background: #182229; color: #c6d6d8; border: 1px solid #2a383f; border-radius: 8px; padding: 9px 13px; box-shadow: none; text-shadow: none; }
button:hover { background: #24343b; border-color: #45605e; }
button:checked, button.suggested-action { background: #a7f3d0; color: #10251d; border-color: #a7f3d0; }
button.nav { background: transparent; border-color: transparent; padding: 11px 12px; }
button.nav:checked { background: #1b302d; color: #b3f7dc; border-color: #2c4840; }
button.transport { border-radius: 30px; padding: 12px 16px; }
button.play { background: #a7f3d0; color: #11251d; border-color: #a7f3d0; min-width: 26px; min-height: 26px; }
button.flat { background: transparent; border-color: transparent; }
combobox button { padding: 7px 10px; }
.header { padding: 25px 28px 20px; border-bottom: 1px solid #202b32; }
.title { font-size: 23px; font-weight: bold; }
.track-title { font-size: 20px; font-weight: bold; }
.inspector { background: #10181e; border-left: 1px solid #202b32; padding: 22px 20px; }
.section-title { font-size: 13px; font-weight: bold; color: #dce9e7; }
.controls { background: #10171c; border-top: 1px solid #202b32; padding: 17px 26px; }
.card { background: #111b21; border: 1px solid #26363c; border-radius: 12px; padding: 18px; }
entry, textview text { background: #10191f; color: #d5e3e4; border-radius: 7px; border-color: #2a3b43; padding: 8px; }
scale trough { background: #29383e; min-height: 4px; border: none; }
scale highlight { background: #92dfc1; border: none; }
scale slider { background: #c8f8e6; border: none; min-width: 12px; min-height: 12px; }
scale value { color: #93b5b1; font-size: 10px; }
notebook, stack { background: #0b0f13; }
notebook header { background: #10181e; border-color: #28383f; }
notebook tab { padding: 10px 18px; }
notebook tab:checked { background: #1b302d; }
scrolledwindow { border: none; }
progressbar trough { background: #25343a; border: none; min-height: 3px; }
progressbar progress { background: #a7f3d0; border: none; min-height: 3px; }
separator { background: #26363d; min-height: 1px; }
popover { background: #101b22; color: #e5eeed; border: 1px solid #38534d; border-radius: 12px; }
window { background: #0a0a0e; color: #ededf3; }
.sidebar { background: #0d0d11; border-right-color: #24242d; padding: 24px 12px 18px; }
.brand { color: #ededf3; font-size: 22px; }
.subtitle { color: #91919e; }
button { background: #15151c; color: #e3e3ec; border-color: #2b2b36; }
button:hover { background: #23212e; border-color: #775294; }
button:checked, button.suggested-action, button.play { background: #a855f7; color: #ffffff; border-color: #b366ff; }
button.nav:checked { background: #24232c; color: #ffffff; border-color: transparent; }
.inspector, .controls, .header { background: #101015; border-color: #25252f; }
.section-title { color: #ededf3; }
entry, textview text { background: #121218; color: #e3e3ec; border-color: #30303a; }
scale trough { background: #30303a; }
scale highlight, progressbar progress { background: #a855f7; }
scale slider { background: #d6b1ff; }
scale value { color: #a6a2af; }
notebook, stack { background: #0a0a0e; }
notebook header { background: #111117; border-color: #272731; }
notebook tab:checked { background: #292232; }
'''


class App(Gtk.Window):
    def __init__(self, demo=False):
        super().__init__(title='Sonora · Linux music companion')
        self.soloist = Soloist(lambda event: GLib.idle_add(self.soloist_event, event))
        self.closing = False
        self.lyrics_data = None
        self.lyrics_key = None
        self.lyrics_busy = False
        self.lyrics_generation = 0
        self.lyrics_client = None
        self.lyrics_contact = ''
        self.cover = None
        self.cover_palette = None
        self.cover_background = None
        self.cover_requested = ''
        self.previous_palette = None
        self.palette_at = 0.
        self.scene_windows = {}
        self.scene_timer = 0
        self.scene_bass = 0.
        self.upcoming = []
        self.queue_images = {}
        self.queue_key = None
        self.queue_busy = False
        self.queue_checked = -math.inf
        self.ui = InterfaceLanguage(CONFIG.with_name('language'))
        self.soloist_setup = SoloistSetup()
        self.setup_busy = False
        self.auto_soloist_path = CONFIG.with_name('soloist-auto')
        self.settings = load_settings()
        self.setting_controls = []
        self.syncing_settings = False
        self.audio = Audio(self.settings['source'])
        self.media = Media(self.settings)
        self.obscured = False
        self.full = False
        self.last_motion = time.monotonic()
        self.widget_window = None
        self.clock_window = None
        self.demo = demo
        self.demo_timer = 0
        if demo:
            self.audio.stop()
            self.audio.status = 'DEMO · synthetisches Audio, keine Spotify-Verbindung'
            self.media.title = 'Midnight, in colour'
            self.media.artist = 'Sonora · Design preview'
            self.demo_timer = GLib.timeout_add(70, self.demo_frame)
        self.set_default_size(1180, 780)
        self.set_size_request(900, 620)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.connect('destroy', self.quit)
        self.connect('key-press-event', self.key)
        self.connect('window-state-event', self.window_state)
        self.add_events(Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect('motion-notify-event', self.pointer_motion)
        css = Gtk.CssProvider()
        css.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        root = Gtk.Box()
        self.add(root)
        self.sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.sidebar.get_style_context().add_class('sidebar')
        self.sidebar_scroll = Gtk.ScrolledWindow()
        self.sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.sidebar_scroll.set_min_content_width(184)
        self.sidebar_scroll.add(self.sidebar)
        root.pack_start(self.sidebar_scroll, False, False, 0)
        brand = Gtk.Label(label='sonora', xalign=0, margin_start=12, margin_bottom=4)
        brand.get_style_context().add_class('brand')
        self.sidebar.pack_start(brand, False, False, 0)
        tagline = Gtk.Label(label='SOUND IN MOTION', xalign=0, margin_start=13, margin_bottom=35)
        tagline.get_style_context().add_class('eyebrow')
        self.sidebar.pack_start(tagline, False, False, 0)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=160)
        self.nav_buttons = {}
        self.nav_handlers = {}
        for name, label in [('visualizer', 'Visualizer'), ('player', 'Jetzt läuft'), ('lyrics', 'Lyrics'), ('soloist', 'Verbindungen'), ('settings', 'Einstellungen')]:
            button = Gtk.ToggleButton(label=label)
            button.set_halign(Gtk.Align.FILL)
            button.get_style_context().add_class('nav')
            self.nav_handlers[name] = button.connect('clicked', self.select_page, name)
            self.nav_buttons[name] = button
            self.sidebar.pack_start(button, False, False, 0)
        spacer = Gtk.Box(vexpand=True)
        self.sidebar.pack_start(spacer, True, True, 0)
        for label, action in [('Desktop-Kreis', self.open_widget), ('Desktop-Uhr', self.open_clock)]:
            button = self.button(label, action)
            button.get_style_context().add_class('flat')
            self.sidebar.pack_start(button, False, False, 0)
        self.language_button = self.button('', self.switch_language)
        self.sidebar.pack_start(self.language_button, False, False, 0)
        note = Gtk.Label(label='SPACE  Play / Pause\nF  Vollbild    V / C  Look', xalign=0, margin_start=12, margin_top=22)
        note.get_style_context().add_class('subtitle')
        self.sidebar.pack_end(note, False, False, 4)
        root.pack_start(self.stack, True, True, 0)
        player = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Gtk.Box(spacing=15)
        header.get_style_context().add_class('header')
        self.player_header = header
        heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        eyebrow = Gtk.Label(label='DEIN SOUND. DEIN MOMENT.', xalign=0)
        eyebrow.get_style_context().add_class('eyebrow')
        heading.pack_start(eyebrow, False, False, 0)
        title = Gtk.Label(label='Jetzt läuft', xalign=0)
        title.get_style_context().add_class('title')
        heading.pack_start(title, False, False, 0)
        header.pack_start(heading, True, True, 0)
        full_button = self.icon_button('view-fullscreen-symbolic', 'Vollbild (F)', self.fullscreen_toggle)
        header.pack_start(full_button, False, False, 0)
        desktop = self.button('Desktop-Kreis öffnen', self.open_widget)
        desktop.get_style_context().add_class('suggested-action')
        header.pack_start(desktop, False, False, 0)
        player.pack_start(header, False, False, 0)
        body = Gtk.Box()
        stage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.visual = Visual(self)
        stage.pack_start(self.visual, True, True, 0)
        self.status = Gtk.Label(xalign=0, margin_start=24, margin_bottom=12, ellipsize=Pango.EllipsizeMode.END)
        self.status.get_style_context().add_class('subtitle')
        stage.pack_start(self.status, False, False, 0)
        body.pack_start(stage, True, True, 0)
        self.pickers = self.quick_settings()
        body.pack_start(self.pickers, False, False, 0)
        player.pack_start(body, True, True, 0)
        self.controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.controls.get_style_context().add_class('controls')
        transport = Gtk.Box(spacing=16)
        metadata = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.track_title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=36)
        self.track_title.get_style_context().add_class('track-title')
        self.track_artist = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=45)
        self.track_artist.get_style_context().add_class('subtitle')
        self.track_title.sonora_verbatim = self.track_artist.sonora_verbatim = True
        metadata.pack_start(self.track_title, False, False, 0)
        metadata.pack_start(self.track_artist, False, False, 0)
        transport.pack_start(metadata, True, True, 0)
        transport.pack_start(self.icon_button('media-skip-backward-symbolic', 'Vorheriger Titel', lambda *_: self.control('Previous')), False, False, 0)
        self.play = self.icon_button('media-playback-start-symbolic', 'Play / Pause (Leertaste)', lambda *_: self.control('PlayPause'))
        self.play.get_style_context().add_class('play')
        transport.pack_start(self.play, False, False, 0)
        transport.pack_start(self.icon_button('media-skip-forward-symbolic', 'Nächster Titel', lambda *_: self.control('Next')), False, False, 0)
        self.controls.pack_start(transport, False, False, 0)
        progress_row = Gtk.Box(spacing=12)
        self.elapsed_label = Gtk.Label(label='0:00')
        self.elapsed_label.get_style_context().add_class('subtitle')
        self.duration_label = Gtk.Label(label='0:00')
        self.duration_label.get_style_context().add_class('subtitle')
        self.playback_progress = Gtk.ProgressBar(valign=Gtk.Align.CENTER)
        progress_row.pack_start(self.elapsed_label, False, False, 0)
        progress_row.pack_start(self.playback_progress, True, True, 0)
        progress_row.pack_start(self.duration_label, False, False, 0)
        self.controls.pack_start(progress_row, False, False, 0)
        player.pack_start(self.controls, False, False, 0)
        self.visualizer_panel = build_visualizer_panel(self, Visual, STYLES, THEMES)
        self.stack.add_named(self.visualizer_panel, 'visualizer')
        self.stack.add_named(player, 'player')
        self.stack.add_named(self.preferences(), 'settings')
        self.stack.add_named(self.scroll_page(self.soloist_page()), 'soloist')
        self.stack.add_named(self.lyrics_page(), 'lyrics')
        protect_settings_from_scroll(self.stack)
        self.stack.connect('notify::visible-child-name', self.navigation_changed)
        self.navigation_changed()
        self.apply_language()
        self.refresh_timer = GLib.timeout_add_seconds(1, self.refresh)
        self.refresh()
        self.tray = Gtk.StatusIcon.new_from_icon_name('audio-x-generic')
        self.tray.set_tooltip_text('Sonora · Musik & Visualizer')
        self.tray.connect('activate', lambda *_: self.present())
        self.tray.connect('popup-menu', self.tray_menu)

        if not demo and self.auto_soloist.get_active():
            self.run_soloist_setup()

    def apply_language(self):
        for window in (self, self.widget_window, self.clock_window):
            if window:
                self.ui.apply(window)
                window.queue_draw()
        if self.widget_window:
            self.ui.apply(self.widget_window.options)
        self.language_button.set_label('Deutsch' if self.ui.english else 'English')

    def switch_language(self, *_):
        self.ui.toggle()
        self.apply_language()
        self.refresh()

    def icon_button(self, icon, tooltip, action):
        button = self.button('', action)
        button.set_image(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON))
        button.set_always_show_image(True)
        button.set_tooltip_text(tooltip)
        button.get_style_context().add_class('transport')
        return button

    def select_page(self, button, page):
        if self.stack.get_visible_child_name() == page:
            self.navigation_changed()
        else:
            self.stack.set_visible_child_name(page)

    def navigation_changed(self, *_):
        for page, button in self.nav_buttons.items():
            with button.handler_block(self.nav_handlers[page]):
                button.set_active(self.stack.get_visible_child_name() == page)

    def quick_settings(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_size_request(222, -1)
        box.get_style_context().add_class('inspector')
        title = Gtk.Label(label='Dein Visualizer', xalign=0)
        title.get_style_context().add_class('section-title')
        box.pack_start(title, False, False, 0)
        for label, items, key in [('Stil', STYLES, 'style'), ('Farbwelt', THEMES, 'theme')]:
            caption = Gtk.Label(label=label, xalign=0, margin_top=3)
            caption.get_style_context().add_class('subtitle')
            box.pack_start(caption, False, False, 0)
            box.pack_start(self.combo(items, self.settings[key], key), False, False, 0)
        for label, key, low, high, step in [('Empfindlichkeit', 'sensitivity', .2, 5, .1), ('Nachklang', 'smoothing', 0, .95, .01), ('Leuchten', 'glow', 0, 1, .05)]:
            box.pack_start(Gtk.Label(label=label, xalign=0, margin_top=2), False, False, 0)
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, low, high, step)
            scale.set_value(self.settings[key])
            scale.set_digits(2 if key == 'smoothing' else 1)
            scale.connect('value-changed', lambda widget, k=key: self.change(k, widget.get_value()))
            self.setting_controls.append((key, scale))
            box.pack_start(scale, False, False, 0)
        box.pack_start(Gtk.Separator(margin_top=6, margin_bottom=6), False, False, 0)
        box.pack_start(Gtk.Label(label='Fensterebene', xalign=0), False, False, 0)
        box.pack_start(self.placement_control(), False, False, 0)
        self.setting_toggle(box, 'Widget sperren', 'widget_locked')
        self.setting_toggle(box, 'Mausklicks durchlassen', 'widget_clickthrough')
        note = Gtk.Label(label='Auf dem Desktop\n\nKreis ziehen zum Verschieben.\nRechtsklick öffnet die Optionen.', xalign=0, wrap=True)
        note.get_style_context().add_class('subtitle')
        box.pack_start(note, False, False, 0)
        box.pack_end(self.button('Alle Einstellungen', lambda *_: self.stack.set_visible_child_name('settings')), False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_width(222)
        scroll.add(box)
        return scroll

    @staticmethod
    def scroll_page(content):
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(content)
        return scroll

    def tray_menu(self, icon, button, timestamp):
        menu = Gtk.Menu()
        for text, fn in [('Dashboard', lambda *_: self.present()), ('Widget', self.open_widget), ('Uhr', self.open_clock), ('Beenden', lambda *_: self.destroy())]:
            item = Gtk.MenuItem(label=text)
            item.connect('activate', fn)
            menu.append(item)
        self.ui.apply(menu)
        menu.show_all()
        menu.popup(None, None, Gtk.StatusIcon.position_menu, icon, button, timestamp)

    def current_media(self):
        if self.soloist.ready and not self.demo:
            data = entity_info(self.soloist.state.get('item'))
            return SimpleNamespace(title=data['title'], artist=data['artist'] or 'Spotify Soloist', playing=self.soloist.state.get('status') == 'playing')
        if not self.demo and not self.media.name:
            return SimpleNamespace(title=self.ui.text('Deine Musik. Dein Moment.'),
                    artist=self.ui.text('Spotify oder einen MPRIS-Player starten'), playing=False)
        return self.media

    def track_position(self):
        """Return the current position and duration in milliseconds."""
        if self.soloist.ready:
            state = self.soloist.state
            total = entity_info(state.get('item'))['duration_ms']
            anchor = state.get('position') or {}
            elapsed = max(0, time.time()*1000-anchor.get('timestamp_ms', time.time()*1000)) if state.get('status') == 'playing' else 0
            position = anchor.get('position_ms', 0)+elapsed*anchor.get('speed', 0)
        elif self.media.name:
            total = self.media.props.get('Metadata', {}).get('mpris:length', 0)/1000
            position = self.media.props.get('Position', 0)/1000
            if self.media.playing:
                position += (time.monotonic()-self.media.position_at)*1000
        else:
            return 0, 0
        return max(0, min(total, position) if total > 0 else position), max(0, total)

    def lyrics_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=26)
        heading = Gtk.Label(label='Lyrics · im Takt mit deiner Musik', xalign=0)
        heading.get_style_context().add_class('title')
        box.pack_start(heading, False, False, 4)
        self.lyrics_status = Gtk.Label(label='LRC- oder Textdatei öffnen. Zeitmarken werden mit der Wiedergabe synchronisiert.', xalign=0, wrap=True)
        box.pack_start(self.lyrics_status, False, False, 0)
        actions = Gtk.Box(spacing=8)
        def open_file(*_):
            dialog = Gtk.FileChooserDialog(title=self.ui.text('Lyrics öffnen'), transient_for=self, action=Gtk.FileChooserAction.OPEN)
            dialog.add_buttons(self.ui.text('Abbrechen'), Gtk.ResponseType.CANCEL, self.ui.text('Öffnen'), Gtk.ResponseType.OK)
            file_filter = Gtk.FileFilter()
            file_filter.set_name('Lyrics (.lrc, .txt)')
            file_filter.add_pattern('*.lrc')
            file_filter.add_pattern('*.txt')
            dialog.add_filter(file_filter)
            try:
                if dialog.run() == Gtk.ResponseType.OK:
                    self.set_lyrics(load_lyrics(dialog.get_filename()))
            except (OSError, LyricsError) as exc:
                self.ui.message(self.lyrics_status, str(exc))
            finally:
                dialog.destroy()
        actions.pack_start(self.button('Datei öffnen', open_file), False, False, 0)
        actions.pack_start(self.button('Entfernen', lambda *_: self.set_lyrics(None)), False, False, 0)
        box.pack_start(actions, False, False, 0)
        contact = self.contact_entry = Gtk.Entry(input_purpose=Gtk.InputPurpose.EMAIL,
                placeholder_text='Kontakt-E-Mail oder Projekt-URL für LRCLIB')
        contact.set_max_length(480)
        self.contact_path = CONFIG.with_name('lyrics-contact.txt')
        self.contact_timer = 0
        try:
            contact.set_text(self.contact_path.read_text(encoding='utf-8').strip())
        except FileNotFoundError:
            pass
        except (OSError, UnicodeError):
            self.ui.message(self.lyrics_status, 'Gespeicherter Kontakt konnte nicht gelesen werden.')
        self.saved_contact = contact.get_text()
        contact.connect('changed', self.contact_changed)
        box.pack_start(contact, False, False, 0)
        note = Gtk.Label(label='Online-Suche sendet Titel, Interpret und den angegebenen Kontakt an LRCLIB.\nOhne Online-Suche bleiben deine Lyrics lokal.', xalign=0, wrap=True)
        note.get_style_context().add_class('subtitle')
        box.pack_start(note, False, False, 0)
        box.pack_start(self.button('Aktuellen Titel auf LRCLIB suchen', lambda *_: self.lookup_lyrics(contact.get_text().strip())), False, False, 0)
        options = Gtk.Box(spacing=10)
        options.pack_start(Gtk.Label(label='Zeilenübergang'), False, False, 0)
        options.pack_start(self.combo(CHOICES['lyrics_motion'], self.settings['lyrics_motion'], 'lyrics_motion'), False, False, 0)
        options.pack_start(Gtk.Label(label='Zeitkorrektur (s)'), False, False, 0)
        offset = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, -5, 5, .1)
        offset.set_value(self.settings['lyrics_offset'])
        offset.connect('value-changed', lambda widget: self.change('lyrics_offset', widget.get_value()))
        self.setting_controls.append(('lyrics_offset', offset))
        options.pack_start(offset, True, True, 0)
        box.pack_start(options, False, False, 0)
        self.auto_lyrics_track = None
        self.auto_lyrics = Gtk.CheckButton(label='Bei Titelwechsel auf LRCLIB suchen (für diese Sitzung)')
        def auto_toggled(button):
            self.auto_lyrics_track = None
            if button.get_active():
                try:
                    self.lyrics_client = LyricsClient('Sonora/0.7.0 (' + contact.get_text().strip() + ')')
                    self.lyrics_contact = contact.get_text().strip()
                except LyricsError as exc:
                    button.set_active(False)
                    self.ui.message(self.lyrics_status, str(exc))
            else:
                self.lyrics_generation += 1
            contact.set_sensitive(not button.get_active())
        self.auto_lyrics.connect('toggled', auto_toggled)
        box.pack_start(self.auto_lyrics, False, False, 0)
        self.lyrics_line = Gtk.Label(label='', wrap=True, justify=Gtk.Justification.CENTER, margin_bottom=12)
        self.lyrics_line.sonora_verbatim = True
        self.lyrics_line.add_tick_callback(self.update_lyrics_preview)
        self.lyrics_line.get_style_context().add_class('title')
        box.pack_start(self.lyrics_line, False, False, 12)
        self.lyrics_text = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        scroll = Gtk.ScrolledWindow()
        scroll.add(self.lyrics_text)
        box.pack_start(scroll, True, True, 0)
        return box

    def contact_changed(self, entry):
        if self.contact_timer:
            GLib.source_remove(self.contact_timer)
        self.contact_timer = GLib.timeout_add(400, self.save_contact)

    def save_contact(self):
        self.contact_timer = 0
        value = self.contact_entry.get_text().strip()
        if value != self.saved_contact:
            try:
                if value:
                    atomic_write(self.contact_path, value.encode('utf-8'))
                else:
                    self.contact_path.unlink(missing_ok=True)
                self.saved_contact = value
            except OSError as error:
                self.ui.message(self.lyrics_status, 'Kontakt konnte nicht gespeichert werden: ' + str(error))
        return False

    def lyrics_frame(self):
        position = self.track_position()[0] + round(self.settings['lyrics_offset']*1000)
        line = self.lyrics_data.line_at(position) if self.lyrics_data else None
        return animated_line(line, position, self.settings['lyrics_motion'], self.current_media().playing)

    def update_lyrics_preview(self, label, _clock):
        frame = self.lyrics_frame()
        if label.get_text() != frame.text:
            label.set_text(frame.text)
        label.set_opacity(frame.opacity)
        label.set_margin_top(20 + frame.dy)
        label.set_margin_bottom(20 - frame.dy)
        label.set_margin_start(32 + frame.dx)
        label.set_margin_end(32 - frame.dx)
        return GLib.SOURCE_CONTINUE

    def set_lyrics(self, data):
        self.lyrics_generation += 1
        self.lyrics_data = data
        media = self.current_media()
        self.lyrics_key = (media.title, media.artist) if data else None
        self.lyrics_text.get_buffer().set_text(data.plain if data else '')
        self.lyrics_line.set_text('')
        if data:
            self.ui.message(self.lyrics_status, 'Instrumental' if data.instrumental else ('Synchronisierte Lyrics geladen' if data.lines else 'Lyrics geladen · ohne Zeitmarken'))
        else:
            self.ui.message(self.lyrics_status, 'Keine Lyrics geladen')

    def lookup_lyrics(self, contact):
        if self.lyrics_busy:
            return
        try:
            if contact != self.lyrics_contact or self.lyrics_client is None:
                self.lyrics_client = LyricsClient('Sonora/0.7.0 ('+contact+')')
                self.lyrics_contact = contact
        except LyricsError as exc:
            self.ui.message(self.lyrics_status, str(exc))
            return
        media = self.current_media()
        track = (media.title, media.artist)
        _, total = self.track_position()
        duration = total/1000 if total else None
        self.lyrics_busy = True
        self.lyrics_generation += 1
        generation = self.lyrics_generation
        self.ui.message(self.lyrics_status, 'LRCLIB wird durchsucht …')
        client = self.lyrics_client
        def worker():
            try:
                data, error = client.lookup(track[0], track[1], duration=duration), None
            except LyricsError as exc:
                data, error = None, str(exc)
            GLib.idle_add(done, data, error)
        def done(data, error):
            self.lyrics_busy = False
            if self.closing or generation != self.lyrics_generation:
                return False
            now = self.current_media()
            if track != (now.title, now.artist):
                self.ui.message(self.lyrics_status, 'Titel hat gewechselt. Suche erneut starten.')
                return False
            if data:
                self.set_lyrics(data)
            else:
                self.ui.message(self.lyrics_status, error or 'Keine Lyrics für diesen Titel gefunden')
            return False
        threading.Thread(target=worker, daemon=True).start()

    def soloist_command(self, command, **fields):
        try:
            self.soloist.command(command, **fields)
        except SoloistError as exc:
            self.ui.message(self.soloist_status, str(exc))

    def soloist_event(self, event):
        if self.closing or not self.soloist.is_current_event(event):
            return False
        kind = event.get('type')
        if kind == 'connection':
            self.ui.message(self.soloist_status, 'Soloist verbunden · auf Anmeldung warten' if event.get('connected') else event.get('message', 'Soloist getrennt · Verbindung wird wiederholt'))
        elif kind == 'auth_state':
            self.ui.message(self.soloist_status, 'Soloist verbunden · '+event.get('device_name', '') if event.get('logged_in') else 'Soloist bereit · Gerät einmal in Spotify Connect auswählen')
        elif kind == 'error':
            self.ui.message(self.soloist_status, event.get('message', 'Soloist-Fehler'))
        elif kind == 'queue_changed':
            for child in self.soloist_queue.get_children():
                self.soloist_queue.remove(child)
            for entry in event.get('upcoming', [])[:80]:
                item = entry.get('item') or {}
                data = entity_info(item)
                row = Gtk.Box(spacing=10, margin=6)
                label = Gtk.Label(label=data['title']+' · '+data['artist'], xalign=0, ellipsize=Pango.EllipsizeMode.END)
                row.pack_start(label, True, True, 0)
                row.pack_start(self.button('▶', lambda _, uri=item.get('uri',''): self.soloist_command('play', uri=uri)), False, False, 0)
                self.soloist_queue.add(row)
            self.soloist_queue.show_all()
        return False

    def run_soloist_setup(self, *_):
        if self.setup_busy or self.closing:
            return
        self.setup_busy = True
        self.setup_button.set_sensitive(False)
        self.soloist_actions.set_sensitive(False)
        key = self.soloist_key.get_text().strip()
        self.soloist_key.set_text('')
        self.ui.message(self.soloist_status, 'Soloist wird eingerichtet …')

        def finish(endpoint, error):
            if self.closing:
                return False
            self.setup_busy = False
            self.setup_button.set_sensitive(True)
            self.soloist_actions.set_sensitive(True)
            if error:
                self.ui.message(self.soloist_status, error)
            else:
                self.soloist_endpoint.set_text(endpoint)
                try:
                    self.soloist.start(endpoint)
                    self.auto_soloist.set_active(True)
                except SoloistError as exc:
                    self.soloist_setup.stop_child()
                    self.ui.message(self.soloist_status, str(exc))
            return False

        def worker():
            try:
                endpoint = self.soloist_setup.start(key)
            except Exception as exc:
                GLib.idle_add(finish, None, str(exc))
            else:
                GLib.idle_add(finish, endpoint, None)
        threading.Thread(target=worker, daemon=True).start()

    def soloist_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=26)
        heading = Gtk.Label(label='Spotify Soloist · lokale Wiedergabe', xalign=0)
        heading.get_style_context().add_class('title')
        box.pack_start(heading, False, False, 4)
        description = Gtk.Label(label='Sonora installiert Soloist für deinen Benutzer und verbindet ihn mit diesem Gerät. Wähle danach Sonora in Spotify Connect.', xalign=0)
        description.set_line_wrap(True)
        box.pack_start(description, False, False, 0)
        key_link = Gtk.LinkButton.new_with_label('https://developer.spotify.com/dashboard/soloist',
                                                    'Soloist-Schlüssel bei Spotify erstellen')
        key_link.set_halign(Gtk.Align.START)
        box.pack_start(key_link, False, False, 0)
        self.soloist_key = Gtk.Entry(visibility=False, input_purpose=Gtk.InputPurpose.PASSWORD,
                placeholder_text='Soloist-Schlüssel (gespeicherten Schlüssel weiterverwenden, wenn leer)')
        self.soloist_key.set_max_length(512)
        box.pack_start(self.soloist_key, False, False, 0)
        self.setup_button = self.button('Einrichten und verbinden', self.run_soloist_setup)
        self.setup_button.get_style_context().add_class('suggested-action')
        box.pack_start(self.setup_button, False, False, 0)
        self.auto_soloist = Gtk.CheckButton(label='Bei jedem Start mit Soloist verbinden')
        self.auto_soloist.set_active(self.auto_soloist_path.exists())
        def remember_auto(button):
            if button.get_active():
                atomic_write(self.auto_soloist_path, b'1')
            else:
                self.auto_soloist_path.unlink(missing_ok=True)
        self.auto_soloist.connect('toggled', remember_auto)
        box.pack_start(self.auto_soloist, False, False, 0)
        self.soloist_status = Gtk.Label(label='Noch nicht verbunden', xalign=0)
        self.soloist_status.set_line_wrap(True)
        box.pack_start(self.soloist_status, False, False, 0)
        endpoint = self.soloist_endpoint = Gtk.Entry(text=discover_endpoint(), placeholder_text='ws://127.0.0.1:9090')
        box.pack_start(endpoint, False, False, 0)
        actions = Gtk.Box(spacing=8)
        self.soloist_actions = actions
        def connect(*_):
            try:
                self.soloist.start(endpoint.get_text().strip())
                self.ui.message(self.soloist_status, 'Verbinde …')
            except SoloistError as exc:
                self.ui.message(self.soloist_status, str(exc))
        def disconnect(*_):
            self.soloist.stop()
            self.soloist_setup.stop_child()
            self.auto_soloist.set_active(False)
            self.ui.message(self.soloist_status, 'Getrennt')
        for label, action in [('Verbinden', connect), ('Trennen', disconnect), ('Gerät aktivieren', lambda *_: self.soloist_command('activate'))]:
            actions.pack_start(self.button(label, action), False, False, 0)
        box.pack_start(actions, False, False, 0)
        uri = Gtk.Entry(placeholder_text='spotify:track:… oder spotify:playlist:…')
        box.pack_start(uri, False, False, 0)
        playrow = Gtk.Box(spacing=8)
        playrow.pack_start(self.button('URI abspielen', lambda *_: self.soloist_command('play', uri=uri.get_text().strip())), False, False, 0)
        playrow.pack_start(self.button('In Warteschlange', lambda *_: self.soloist_command('add_to_queue', uri=uri.get_text().strip())), False, False, 0)
        box.pack_start(playrow, False, False, 0)
        box.pack_start(Gtk.Label(label='Lautstärke', xalign=0), False, False, 0)
        self.soloist_volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.soloist_volume.set_value(50)
        self.soloist_volume.connect('button-release-event', lambda widget, _: self.soloist_command('set_volume', volume=int(widget.get_value())))
        self.soloist_volume.connect('key-release-event', lambda widget, _: self.soloist_command('set_volume', volume=int(widget.get_value())))
        box.pack_start(self.soloist_volume, False, False, 0)
        self.soloist_position = Gtk.Label(label='0:00 / 0:00', xalign=0)
        box.pack_start(self.soloist_position, False, False, 0)
        self.soloist_seek = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 1)
        self.soloist_seek.set_draw_value(False)
        self.soloist_seek.connect('button-release-event', lambda widget, _: self.soloist_command('seek', position_ms=int(widget.get_value())))
        self.soloist_seek.connect('key-release-event', lambda widget, _: self.soloist_command('seek', position_ms=int(widget.get_value())))
        box.pack_start(self.soloist_seek, False, False, 0)
        options = Gtk.Box(spacing=8)
        shuffle = Gtk.CheckButton(label='Shuffle')
        shuffle.connect('toggled', lambda c: self.soloist_command('set_shuffle', enabled=c.get_active()))
        options.pack_start(shuffle, False, False, 0)
        repeat = Gtk.ComboBoxText()
        for value, label in [('off','Repeat aus'), ('context','Repeat Playlist'), ('track','Repeat Titel')]:
            repeat.append(value, label)
        repeat.set_active_id('off')
        def repeat_changed(combo):
            mode = combo.get_active_id()
            if mode == 'track':
                self.soloist_command('set_repeat_context', enabled=False)
                self.soloist_command('set_repeat_track', enabled=True)
            else:
                self.soloist_command('set_repeat_track', enabled=False)
                self.soloist_command('set_repeat_context', enabled=mode == 'context')
        repeat.connect('changed', repeat_changed)
        options.pack_start(repeat, False, False, 0)
        options.pack_start(self.button('Warteschlange laden', lambda *_: self.soloist_command('get_queue', limit=80)), False, False, 0)
        box.pack_start(options, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        self.soloist_queue = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.soloist_queue.sonora_verbatim = True
        scroll.set_min_content_height(140)
        scroll.add(self.soloist_queue)
        box.pack_start(scroll, True, True, 0)
        return box

    def control(self, method):
        if self.soloist.ready:
            command = {'Previous':'skip_prev', 'Next':'skip_next', 'PlayPause':'pause' if self.soloist.state.get('status') == 'playing' else 'play'}[method]
            self.soloist_command(command)
        elif self.media.name:
            self.media.command(method)

    def demo_frame(self):
        if not self.get_visible() and not self.widget_window:
            return True
        self.media.title = 'Midnight, in colour'
        self.media.artist = 'Sonora · Design preview'
        with self.audio.lock:
            t = time.monotonic()
            beat = math.exp(-((t*1.8)%1)*9)
            self.audio.levels = np.array([(.0015 + .016*beat*math.exp(-i/18) + .006*(1+math.sin(t*4+i*.38))**2) * math.exp(-i/65) for i in range(64)])
            self.audio.wave = np.sin(np.linspace(0, 20, 256)+time.monotonic())*.2
        return True

    def button(self, label, callback):
        b = Gtk.Button(label=label)
        b.connect('clicked', callback)
        return b

    def save(self):
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        temp = CONFIG.with_suffix('.tmp')
        temp.write_text(json.dumps(self.settings, indent=2))
        temp.replace(CONFIG)

    def sync_controls(self):
        self.syncing_settings = True
        try:
            for key, widget in self.setting_controls:
                value = self.settings[key]
                if isinstance(widget, Gtk.ComboBoxText):
                    widget.set_active_id(value)
                elif isinstance(widget, Gtk.Scale):
                    widget.set_value(value)
                elif isinstance(widget, Gtk.ToggleButton):
                    widget.set_active(value)
                elif isinstance(widget, Gtk.Entry):
                    widget.set_text(value)
        finally:
            self.syncing_settings = False

        panel = getattr(self, 'visualizer_panel', None)
        if panel is not None:
            panel._sonora_sync()

    def desktop_preferences(self, box):
        self.profiles = ProfileStore()
        status = Gtk.Label(xalign=0, wrap=True)
        autostart = Gtk.CheckButton(label='Sonora bei der Anmeldung starten')
        autostart.set_active(autostart_enabled())
        def autostart_changed(widget):
            try:
                set_autostart(widget.get_active())
                self.ui.message(status, 'Autostart aktiviert' if widget.get_active() else 'Autostart deaktiviert')
            except (OSError, ValueError) as exc:
                self.ui.message(status, str(exc))
        autostart.connect('toggled', autostart_changed)
        box.pack_start(autostart, False, False, 0)
        box.pack_start(Gtk.Label(label='Profile · Darstellung und Audioeinstellungen', xalign=0), False, False, 0)
        selected = Gtk.ComboBoxText()
        selected.sonora_verbatim = True
        name = Gtk.Entry(placeholder_text='Profilname, z. B. Abend')
        box.pack_start(name, False, False, 0)
        box.pack_start(selected, False, False, 0)
        def refresh_profiles(active=None):
            selected.remove_all()
            try:
                for item in self.profiles.list():
                    selected.append(item, item)
                if active:
                    selected.set_active_id(active)
                else:
                    selected.set_active(0)
            except (OSError, ProfileError) as exc:
                self.ui.message(status, str(exc))
        def save_profile(*_):
            try:
                self.profiles.save(name.get_text(), self.settings)
                refresh_profiles(name.get_text().strip())
                self.ui.message(status, 'Profil gespeichert')
            except (OSError, ProfileError) as exc:
                self.ui.message(status, str(exc))
        def apply_profile(*_):
            chosen = selected.get_active_id()
            if not chosen:
                return
            try:
                values = self.profiles.load(chosen)
                previous_source = self.settings['source']
                self.settings.update(values)
                self.sync_controls()
                self.change('fps', values['fps'])
                self.change('opacity', values['opacity'])
                self.change('ring_enabled', values['ring_enabled'])
                if previous_source != values['source'] and not self.demo:
                    self.audio.stop()
                    self.audio = Audio(values['source'])
                self.ui.message(status, 'Profil geladen: '+chosen)
            except (OSError, ProfileError) as exc:
                self.ui.message(status, str(exc))
        def delete_profile(*_):
            chosen = selected.get_active_id()
            if chosen:
                try:
                    self.profiles.delete(chosen)
                    refresh_profiles()
                    self.ui.message(status, 'Profil gelöscht: '+chosen)
                except (OSError, ProfileError) as exc:
                    self.ui.message(status, str(exc))
        actions = Gtk.Box(spacing=8)
        for label, callback in [('Speichern', save_profile), ('Laden', apply_profile), ('Löschen', delete_profile)]:
            actions.pack_start(self.button(label, callback), False, False, 0)
        box.pack_start(actions, False, False, 0)
        box.pack_start(status, False, False, 0)
        refresh_profiles()

    def combo(self, items, active, key):
        c = Gtk.ComboBoxText()
        for item in items:
            c.append(item, item)
        c.set_active_id(active)
        def changed(widget):
            if not self.syncing_settings and widget.get_active_id() is not None:
                self.change(key, widget.get_active_id())
        c.connect('changed', changed)
        self.setting_controls.append((key, c))
        return c

    def placement_control(self):
        control = Gtk.ComboBoxText()
        for mode, label in (('Overlay', 'Immer im Vordergrund'), ('Normal', 'Normales Fenster'),
                            ('Desktop', 'Auf dem Desktop')):
            control.append(mode, label)
        control.set_active_id(self.settings['widget_placement'])
        control.connect('changed', lambda widget: self.change('widget_placement', widget.get_active_id())
                        if widget.get_active_id() is not None else None)
        self.setting_controls.append(('widget_placement', control))
        return control

    def setting_toggle(self, box, label, key):
        control = Gtk.CheckButton(label=label)
        control.set_active(self.settings[key])
        control.connect('toggled', lambda widget: self.change(key, widget.get_active()))
        self.setting_controls.append((key, control))
        box.pack_start(control, False, False, 0)
        return control

    def preferences(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20, margin=26)
        heading = Gtk.Label(label='Einstellungen', xalign=0)
        heading.get_style_context().add_class('title')
        outer.pack_start(heading, False, False, 0)
        notebook = Gtk.Notebook()
        notebook.set_scrollable(True)
        outer.pack_start(notebook, True, True, 0)
        def page(name):
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=22)
            notebook.append_page(self.scroll_page(content), Gtk.Label(label=name))
            return content
        box = page('Audio')
        fullscreen = page('Vollbild')
        for label, key in (('Visualizer-Stil', 'fullscreen_style'),
                           ('Farbpalette', 'fullscreen_theme'),
                           ('Hintergrund', 'fullscreen_background')):
            fullscreen.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
            fullscreen.pack_start(self.combo(CHOICES[key], self.settings[key], key), False, False, 0)
        self.setting_toggle(fullscreen, 'Synchronisierte Lyrics anzeigen', 'fullscreen_lyrics')
        box.pack_start(Gtk.Label(label='Player (MPRIS, automatisch bevorzugt Spotify)', xalign=0), False, False, 0)
        players = Gtk.ComboBoxText()
        players.append('', self.ui.text('Automatisch'))
        for name in self.media.names:
            players.append(name, name.removeprefix('org.mpris.MediaPlayer2.'))
        players.sonora_choices = {'': 'Automatisch'}
        players.set_active(0)
        def update_players(*_):
            selected = self.settings['player']
            self.syncing_settings = True
            players.remove_all()
            players.append('', self.ui.text('Automatisch'))
            for name in self.media.names:
                players.append(name, name.removeprefix('org.mpris.MediaPlayer2.'))
            players.set_active_id(selected if selected in self.media.names else '')
            self.syncing_settings = False
        self.stack.connect('notify::visible-child-name', update_players)
        players.connect('changed', lambda c: self.change('player', c.get_active_id() or ''))
        box.pack_start(players, False, False, 0)
        box.pack_start(Gtk.Label(label='Audioquelle (leer = Monitor des Standard-Ausgangs)', xalign=0), False, False, 0)
        source = Gtk.Entry(text=self.settings['source'], placeholder_text='z. B. alsa_output.… .monitor')
        box.pack_start(source, False, False, 0)
        def reconnect(*_):
            self.change('source', source.get_text().strip())
            self.audio.stop()
            self.audio = Audio(self.settings['source'])
        box.pack_start(self.button('Audio neu verbinden', reconnect), False, False, 0)
        self.setting_controls.append(('source', source))
        for label, key, low, high, step in [('Bildrate', 'fps', 10, 60, 1), ('Empfindlichkeit', 'sensitivity', .2, 5, .1), ('Nachklang', 'smoothing', 0, .95, .01)]:
            box.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, low, high, step)
            scale.set_value(self.settings[key])
            scale.connect('value-changed', lambda widget, k=key: self.change(k, widget.get_value()))
            self.setting_controls.append((key, scale))
            box.pack_start(scale, False, False, 0)
        hint = Gtk.Label(label='Schneller Ausschlag bei Beats. Nachklang bestimmt, wie sanft die Strahlen abfallen.', xalign=0, wrap=True)
        hint.get_style_context().add_class('subtitle')
        box.pack_start(hint, False, False, 6)
        box = page('Darstellung')
        for label, items, key in [('Visualizer-Stil', STYLES, 'style'), ('Farbpalette', THEMES, 'theme')]:
            box.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
            box.pack_start(self.combo(items, self.settings[key], key), False, False, 0)
        for label, key, low, high, step in [('Spektraldichte', 'density', 16, 128, 1), ('Leuchten', 'glow', 0, 1, .05), ('Hintergrundfläche', 'backdrop', 0, 1, .02)]:
            box.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, low, high, step)
            scale.set_value(self.settings[key])
            scale.connect('value-changed', lambda widget, k=key: self.change(k, widget.get_value()))
            self.setting_controls.append((key, scale))
            box.pack_start(scale, False, False, 0)
        clock = Gtk.CheckButton(label='Uhr anzeigen')
        clock.set_active(self.settings['clock'])
        clock.connect('toggled', lambda c: self.change('clock', c.get_active()))
        self.setting_controls.append(('clock', clock))
        box.pack_start(clock, False, False, 0)
        box.pack_start(self.combo(('Digital', 'Stacked', 'Minimal', 'Analog', 'Words'), self.settings['clock_style'], 'clock_style'), False, False, 0)
        box.pack_start(Gtk.Label(label='Titel / Cover-Layout', xalign=0), False, False, 0)
        box.pack_start(self.combo(('Compact', 'Centred', 'Cover', 'Text only'), self.settings['layout'], 'layout'), False, False, 0)
        self.setting_toggle(box, 'Titel und Interpret anzeigen', 'now_playing')
        self.setting_toggle(box, 'Albumcover als farbiges Leuchten', 'album_glow')
        album_colours = Gtk.CheckButton(label='Farben aus dem Albumcover übernehmen')
        album_colours.set_active(self.settings['album_colours'])
        album_colours.connect('toggled', lambda c: self.change('album_colours', c.get_active()))
        self.setting_controls.append(('album_colours', album_colours))
        box.pack_start(album_colours, False, False, 0)
        accent_row = Gtk.Box(spacing=10)
        accent_row.pack_start(Gtk.Label(label='Eigene Akzentfarbe'), False, False, 0)
        accent = Gtk.ColorButton()
        def set_accent(button):
            color = button.get_rgba()
            self.change('accent', '#%02x%02x%02x' % (round(color.red*255), round(color.green*255), round(color.blue*255)))
        accent.connect('color-set', set_accent)
        accent_row.pack_start(accent, False, False, 0)
        accent_row.pack_start(self.button('Palette verwenden', lambda *_: self.change('accent', '')), False, False, 0)
        box.pack_start(accent_row, False, False, 0)
        box = page('Desktop & Profile')
        self.setting_toggle(box, 'Desktop-Ring anzeigen', 'ring_enabled')
        ring_note = Gtk.Label(label='Ohne Rahmen und Titelleiste. Ziehen zum Verschieben, Rechtsklick für alle Kreis-Optionen.', xalign=0, wrap=True)
        ring_note.get_style_context().add_class('subtitle')
        box.pack_start(ring_note, False, False, 0)
        for label, key, low, high, step in [('Kreisgröße', 'widget_size', 240, 720, 10), ('Desktop-Deckkraft', 'opacity', .2, 1, .05)]:
            box.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, low, high, step)
            scale.set_value(self.settings[key])
            scale.connect('value-changed', lambda widget, k=key: self.change(k, widget.get_value()))
            self.setting_controls.append((key, scale))
            box.pack_start(scale, False, False, 0)
        box.pack_start(Gtk.Label(label='Fensterebene', xalign=0), False, False, 0)
        box.pack_start(self.placement_control(), False, False, 0)
        for label, key in [('Albumcover im Desktop-Kreis', 'widget_cover'),
                           ('Titel und Interpret im Desktop-Kreis', 'widget_now_playing'),
                           ('Widget sperren', 'widget_locked'),
                           ('Mausklicks durchlassen', 'widget_clickthrough')]:
            self.setting_toggle(box, label, key)
        click_hint = Gtk.Label(label='Gesperrt oder durchlässig: Mausklicks gehen an das Fenster dahinter.\nHier oder im Schnellregler wieder entsperren.', xalign=0, wrap=True)
        click_hint.get_style_context().add_class('subtitle')
        box.pack_start(click_hint, False, False, 0)
        box.pack_start(Gtk.Separator(margin_top=8, margin_bottom=8), False, False, 0)
        self.desktop_preferences(box)
        hint = Gtk.Label(label='Wiedergabe über Soloist oder einen laufenden Spotify-Player.\nRahmenloser Desktop-Kreis mit Rechtsklick-Optionen.\nDesktop-Hintergrundebene: X11; unter Wayland frei schwebender Kreis.\nDesktop-Effekte hängen vom verwendeten Compositor ab.', xalign=0)
        hint.set_line_wrap(True)
        hint.get_style_context().add_class('subtitle')
        box.pack_start(hint, False, False, 10)
        return outer

    def select_widget_size(self, width, height):
        s = self.settings
        previous = (int(s['widget_width']), int(s['widget_height']))
        if previous == (width, height):
            return
        ring = self.widget_window
        point = ring.get_position() if ring and ring.get_realized() and not ring._requested_size else (s['widget_x'], s['widget_y'])
        positions = dict(s['widget_positions'])
        previous_key = '%dx%d' % previous
        if tuple(point) != (-1, -1):
            positions.pop(previous_key, None)
            positions[previous_key] = list(point)
        target = positions.get('%dx%d' % (width, height))
        if target is None and tuple(point) != (-1, -1):
            target = (point[0] + (previous[0]-width)//2, point[1] + (previous[1]-height)//2)
        if len(positions) > 64:
            del positions[next(iter(positions))]
        x, y = target or (-1, -1)
        if target:
            # Keep a deliberately edge-aligned saved position when part of the
            # widget extends off-screen. Fully fit a newly inferred position.
            saved = '%dx%d' % (width, height) in positions
            monitor = self.get_display().get_monitor_at_point(x, y)
            area = monitor.get_workarea()
            x = max(area.x, min(x, area.x+area.width-min(60 if saved else width, area.width)))
            y = max(area.y, min(y, area.y+area.height-min(60 if saved else height, area.height)))
        s.update(widget_positions=positions, widget_width=width, widget_height=height,
                 widget_x=x, widget_y=y)
        if ring:
            ring.settings_changed('widget_width')
            if target:
                ring.move(x, y)
        self.sync_controls()
        self.save()

    def change(self, key, value):
        if self.syncing_settings:
            return
        self.settings[key] = int(value) if key in ('fps', 'density', 'widget_size') else value
        if key == 'ring_enabled':
            if value:
                self.open_widget()
            elif self.widget_window:
                self.widget_window.destroy()
        if key == 'widget_size':
            self.settings['widget_width'] = self.settings['widget_height'] = int(value)
        if key == 'widget_placement':
            self.settings['widget_above'] = value == 'Overlay'
        elif key == 'widget_above':
            self.settings['widget_placement'] = 'Overlay' if value else 'Normal'
        if key == 'fps':
            for visual in list(self._visuals):
                if visual and visual.frame:
                    GLib.source_remove(visual.frame)
                    visual.frame = GLib.timeout_add(round(1000 / self.settings['fps']), visual.tick)
        if key == 'opacity':
            for window in (self.widget_window, self.clock_window):
                if window:
                    window.set_opacity(value)
        if self.widget_window:
            ring = self.widget_window
            if ring.layer_capable and ring.layer_window != (self.settings['widget_placement'] != 'Normal'):
                visible = ring.get_visible()
                ring.destroy()
                self.open_widget()
                self.widget_window.set_visible(visible)
            else:
                ring.settings_changed(key)
        if key in ('ring_enabled','queue_card','detached_lyrics','edge_glow','widget_placement','widget_above','fps'):
            self.sync_scene()
        self.sync_controls()
        self.save()

    def blended_palette(self):
        t = min(1., (time.monotonic()-self.palette_at)/.8)
        if self.previous_palette is None or t >= 1:
            return self.cover_palette
        return [tuple(a+(b-a)*t for a,b in zip(old,new))
                for old,new in zip(self.previous_palette,self.cover_palette)]

    def scene_palette(self):
        return palette_for(self)

    def request_cover(self, url):
        self.cover_requested = url
        self.cover = self.cover_background = None
        if not url:
            self.cover_palette = self.previous_palette = None
            return
        def loaded(image):
            if self.closing or self.cover_requested != url:
                return False
            if image is None:
                self.cover_palette = self.previous_palette = None
                return False
            self.previous_palette = self.blended_palette() if self.cover_palette else [rgb(c) for c in THEMES[self.settings['theme']]]
            self.cover_palette = album_palette(image)
            self.palette_at = time.monotonic()
            self.cover = image
            self.cover_background = image.scale_simple(16,16,GdkPixbuf.InterpType.BILINEAR)
            return False
        load_art(url,600,loaded)

    def set_upcoming(self, rows):
        self.upcoming = rows[:3]
        urls = {url for _,_,url in self.upcoming if url}
        self.queue_images = {u:image for u,image in self.queue_images.items() if u in urls}
        for url in urls - self.queue_images.keys():
            self.queue_images[url] = None
            def loaded(image,u=url):
                if u in self.queue_images:
                    self.queue_images[u] = image
                    if 'queue' in self.scene_windows:
                        self.scene_windows['queue'].queue_draw()
                return False
            load_art(url,72,loaded)

    def refresh_queue(self, media):
        if 'queue' not in self.scene_windows or self.demo:
            return
        name = self.media.name
        title = (media.title, media.artist)
        track_id = self.media.props.get('Metadata',{}).get('mpris:trackid')
        key = ('soloist' if self.soloist.ready else name,title,track_id)
        if self.queue_key != key:
            self.queue_key = key
            self.queue_checked = -math.inf
            self.set_upcoming([])
        if self.soloist.ready:
            entries = [entity_info(row.get('item')) for row in self.soloist.state.get('upcoming',[])[:3] if isinstance(row,dict)]
            rows = [(e['title'],e['artist'],e['cover']) for e in entries]
            if self.upcoming != rows:
                self.set_upcoming(rows)
            if time.monotonic()-self.queue_checked > 15:
                self.queue_checked = time.monotonic()
                self.soloist_command('get_queue',limit=3)
            return
        if not name or not track_id or self.queue_busy or time.monotonic()-self.queue_checked < 30:
            return
        self.queue_checked = time.monotonic()
        self.queue_busy = True
        def worker():
            rows = []
            try:
                rows = mpris_upcoming(self.media.bus,name,track_id)
            except GLib.Error:
                pass
            GLib.idle_add(done,rows)
        def done(rows):
            self.queue_busy = False
            if not self.closing and self.queue_key == key:
                self.set_upcoming(rows)
            return False
        threading.Thread(target=worker,daemon=True).start()

    def sync_scene(self, *_):
        ring = self.widget_window
        visible = not self.closing and (not self.settings['ring_enabled'] or (ring and ring.get_visible()))
        wanted = set()
        if visible:
            if self.settings['queue_card']:
                wanted.add('queue')
            if self.settings['detached_lyrics']:
                wanted.add('lyrics')
            if self.settings['edge_glow']:
                wanted.update(('top','bottom','left','right'))
        for kind in set(self.scene_windows)-wanted:
            self.scene_windows.pop(kind).destroy()
        if wanted:
            display = self.get_display()
            anchor = ring or self
            if ring and ring.layer_window:
                monitor = ring._layer_monitor()
            elif anchor.get_window():
                monitor = display.get_monitor_at_window(anchor.get_window())
            else:
                monitor = display.get_monitor(0)
            for kind,win in list(self.scene_windows.items()):
                if win.monitor != monitor:
                    self.scene_windows.pop(kind).destroy()
            for kind in wanted-self.scene_windows.keys():
                self.scene_windows[kind] = SceneWindow(self,kind,monitor)
            for win in self.scene_windows.values():
                win.update_layer()
        animated = wanted - {'queue'}
        if animated and not self.scene_timer:
            self.scene_timer = GLib.timeout_add(40,self.tick_scene)
        elif not animated and self.scene_timer:
            GLib.source_remove(self.scene_timer)
            self.scene_timer = 0

    def tick_scene(self):
        with self.audio.lock:
            level = min(1.,float(np.mean(self.audio.levels[:14]))**.5*4)
        self.scene_bass += (level-self.scene_bass)*.4
        for kind,win in self.scene_windows.items():
            if kind=='queue':
                continue
            if kind=='lyrics':
                win.area.set_size_request(int(self.settings['lyrics_width']),int(self.settings['lyrics_font_size']*4.8+32))
            if kind == 'lyrics':
                position = self.track_position()[0] + round(self.settings['lyrics_offset']*1000)
                line = self.lyrics_data.line_at(position) if self.lyrics_data else None
                active = line.highlighted_bytes(position) if line else 0
                state = (self.lyrics_frame(), active, self.current_media().title, self.settings['lyrics_align'],
                         self.settings['lyrics_width'], self.settings['lyrics_font_size'], bool(self.lyrics_data))
            else:
                state = (round(self.scene_bass, 2), tuple(tuple(round(c,3) for c in rgb) for rgb in self.scene_palette()))
            if state != win.render_state:
                win.render_state = state
                win.area.queue_draw()
        return True

    def refresh(self):
        if self.closing:
            return False
        if self.full:
            idle = time.monotonic()-self.last_motion > 3
            self.controls.set_visible(not idle)
            if self.get_window():
                cursor = Gdk.Cursor.new_for_display(self.get_display(), Gdk.CursorType.BLANK_CURSOR) if idle else None
                self.get_window().set_cursor(cursor)
        media = self.current_media()
        if self.lyrics_data and self.lyrics_key != (media.title, media.artist):
            self.set_lyrics(None)
            self.ui.message(self.lyrics_status, 'Titel gewechselt · passende Lyrics öffnen oder suchen')
        self.refresh_queue(media)
        if 'queue' in self.scene_windows:
            card = self.scene_windows['queue']
            card.area.set_size_request(308,100+44*len(self.upcoming))
            card.area.queue_draw()
        track = (media.title, media.artist)
        if (self.auto_lyrics.get_active() and media.playing and not self.demo
                and not self.lyrics_busy and track != self.auto_lyrics_track):
            self.auto_lyrics_track = track
            if self.lyrics_key != track:
                self.lookup_lyrics(self.lyrics_contact)
        if self.soloist.ready:
            state = self.soloist.state
            current, total = self.track_position()
            self.soloist_position.set_text('%d:%02d / %d:%02d' % (current//60000, current//1000%60, total//60000, total//1000%60))
            if not self.soloist_seek.has_focus():
                self.soloist_seek.set_range(0, max(1, total))
                self.soloist_seek.set_value(current)
            if not self.soloist_volume.has_focus():
                self.soloist_volume.set_value(state.get('volume', 50))
        url = entity_info(self.soloist.state.get('item'))['cover'] if self.soloist.ready else (self.media.art_url if self.media.name else '')
        if url != self.cover_requested:
            self.request_cover(url)
        self.ui.message(self.status, self.audio.status)
        self.track_title.set_text(media.title)
        self.track_artist.set_text(media.artist)
        self.play.set_image(Gtk.Image.new_from_icon_name('media-playback-pause-symbolic' if media.playing else 'media-playback-start-symbolic', Gtk.IconSize.BUTTON))
        position, duration = self.track_position()
        self.playback_progress.set_fraction(min(1, position/duration) if duration else 0)
        self.elapsed_label.set_text('%d:%02d' % (position//60000, position//1000%60))
        self.duration_label.set_text('%d:%02d' % (duration//60000, duration//1000%60))
        self.play.set_sensitive(bool((self.media.name and self.media.props.get('CanControl')) or self.soloist.ready))
        return True

    def fullscreen_toggle(self, *_):
        self.full = not self.full
        self.stack.set_visible_child_name('player')
        self.sidebar_scroll.set_visible(not self.full)
        self.player_header.set_visible(not self.full)
        self.pickers.set_visible(not self.full)
        self.last_motion = time.monotonic()
        self.controls.show()
        if self.get_window():
            self.get_window().set_cursor(None)
        self.fullscreen() if self.full else self.unfullscreen()

    def pointer_motion(self, *_):
        self.last_motion = time.monotonic()
        if self.full:
            self.controls.show()
            if self.get_window():
                self.get_window().set_cursor(None)

    def do_key_press_event(self, event):
        # Deliver layout-generated AltGr characters to the entry's input method
        # before GtkWindow considers mnemonics or window accelerators.
        if (isinstance(self.get_focus(), Gtk.Editable)
                and event.state & Gdk.ModifierType.MOD5_MASK
                and Gdk.keyval_to_unicode(event.keyval)):
            return self.propagate_key_event(event)
        return Gtk.Window.do_key_press_event(self, event)

    def key(self, _, event):
        if isinstance(self.get_focus(), (Gtk.Editable, Gtk.TextView, Gtk.Scale, Gtk.ComboBox)):
            return False
        if event.state & (Gdk.ModifierType.MOD5_MASK | Gdk.ModifierType.MOD1_MASK |
                          Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SUPER_MASK):
            return False
        key = (Gdk.keyval_name(event.keyval) or '').lower()
        commands = {'space': 'PlayPause', 'left': 'Previous', 'right': 'Next'}
        if key in commands:
            self.control(commands[key])
        elif key in ('f', 'escape'):
            if key == 'f' or self.full:
                self.fullscreen_toggle()
        elif key in ('v', 'c'):
            field, items = ('style', list(STYLES)) if key == 'v' else ('theme', list(THEMES))
            setting = 'fullscreen_' + field if self.full else field
            current = self.settings[setting]
            if current == 'Dashboard':
                current = self.settings[field]
            if self.full and field == 'theme':
                items.insert(0, 'Album')
            if current not in items:
                items.append(current)
            self.change(setting, items[(items.index(current)+1) % len(items)])
        else:
            return False
        return True

    def window_state(self, _, event):
        self.obscured = bool(event.new_window_state & Gdk.WindowState.ICONIFIED)

    def open_clock(self, *_):
        if self.clock_window:
            self.clock_window.present()
            return
        window = Gtk.Window(title='Sonora · Uhr')
        window.set_default_size(360, 240)
        window.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        window.set_opacity(self.settings['opacity'])
        window.add(Clock(self))
        window.connect('destroy', lambda *_: setattr(self, 'clock_window', None))
        self.clock_window = window
        self.ui.apply(window)
        window.show_all()

    def open_widget(self, *_):
        if not self.settings['ring_enabled']:
            self.change('ring_enabled', True)
            return
        if self.widget_window:
            self.widget_window.present()
            return
        window = DesktopRing(self, Visual)
        self.widget_window = window
        self.widget_visual = window.visual
        window.connect('delete-event', self.close_widget)
        window.connect('destroy', self.widget_closed)
        window.connect('map-event', lambda *_: self.sync_scene() or False)
        window.connect('hide', self.sync_scene)
        self.ui.apply(window)
        self.ui.apply(window.options)
        window.show_all()

    def close_widget(self, *_):
        self.change('ring_enabled', False)
        return True

    def widget_closed(self, *_):
        self.widget_window = None
        self.widget_visual = None
        self.sync_scene()

    def desktop_mode(self, window):
        if isinstance(window, DesktopRing):
            self.change('widget_placement', 'Desktop')
            return
        if 'X11' in type(Gdk.Display.get_default()).__name__:
            window.set_keep_above(False)
            window.set_type_hint(Gdk.WindowTypeHint.DESKTOP)
            window.set_skip_taskbar_hint(True)
        else:
            self.audio.status = 'Wayland: Desktop-Ebene benötigt compositor-spezifische Integration.'

    def quit(self, *_):
        if self.closing:
            return
        self.closing = True
        if self.contact_timer:
            GLib.source_remove(self.contact_timer)
        self.save_contact()
        self.sync_scene()
        for timer in (self.refresh_timer, self.media.timer, self.demo_timer):
            if timer:
                GLib.source_remove(timer)
        self.lyrics_generation += 1
        self.soloist_setup.close()
        self.soloist.stop()
        self.audio.stop()
        if self.widget_window:
            self.widget_window.destroy()
        if self.clock_window:
            self.clock_window.destroy()
        self.tray.set_visible(False)
        runner = getattr(self, 'application_runner', None)
        if runner:
            runner.quit()
        else:
            Gtk.main_quit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demo', action='store_true', help='Explicit synthetic audio preview')
    parser.add_argument('--toggle-widget', action='store_true', help='Show/hide the widget in the running instance')
    parser.add_argument('--fullscreen', action='store_true', help='Toggle fullscreen in the running instance')
    parser.add_argument('--quit', action='store_true', help='Quit the running instance')
    parser.parse_args()
    try:
        gi.require_foreign('cairo')
    except ImportError:
        parser.exit(1, 'Fehlende Abhängigkeit: python3-gi-cairo installieren.\n')
    if not Gtk.init_check()[0]:
        parser.exit(1, 'Kein Linux-Display erreichbar. In einer grafischen Sitzung starten.\n')
    application = Gtk.Application(application_id='app.sonora.Client', flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
    window = None
    def command_line(runner, command):
        nonlocal window
        options = parser.parse_args(command.get_arguments()[1:])
        if options.quit:
            if window is not None:
                window.destroy()
            return 0
        starting = window is None
        if starting:
            window = App(options.demo)
            window.application_runner = runner
            runner.add_window(window)
            window.show_all()
            if not options.toggle_widget and not options.fullscreen:
                if window.settings['ring_enabled']:
                    window.open_widget()
                else:
                    window.sync_scene()
        if options.toggle_widget:
            visible = window.settings['ring_enabled'] if starting else bool(window.widget_window and window.widget_window.get_visible())
            window.change('ring_enabled', not visible)
        elif options.fullscreen:
            window.present()
            window.fullscreen_toggle()
        else:
            window.present()
        return 0
    application.connect('command-line', command_line)
    raise SystemExit(application.run(sys.argv))


if __name__ == '__main__':
    main()
