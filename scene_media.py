"""Album artwork, palette extraction and upcoming tracks for the desktop widgets."""
import colorsys
import math
import threading
from urllib.parse import unquote, urlparse
from urllib.request import urlopen

import gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf, Gio, GLib
import numpy as np


def load_art(url, size, callback):
    """Decode off the GTK thread and deliver one result to the main loop."""
    def worker():
        image = None
        try:
            parsed = urlparse(url)
            if parsed.scheme == 'file' and parsed.netloc in ('', 'localhost'):
                stream = open(unquote(parsed.path), 'rb')
            elif parsed.scheme == 'https':
                stream = urlopen(url, timeout=6)
                if urlparse(stream.url).scheme != 'https':
                    stream.close()
                    raise ValueError('Unsupported redirect')
            else:
                raise ValueError('Unsupported artwork URL')
            with stream:
                data = stream.read(5*1024*1024+1)
            if len(data) <= 5*1024*1024:
                loader = GdkPixbuf.PixbufLoader()
                def fit(loader, w, h):
                    factor = min(1, size/max(w,h))
                    loader.set_size(max(1, round(w*factor)), max(1, round(h*factor)))
                loader.connect('size-prepared', fit)
                loader.write(data)
                loader.close()
                image = loader.get_pixbuf()
        except (ValueError, OSError, GLib.Error):
            pass
        GLib.idle_add(callback, image)
    threading.Thread(target=worker, daemon=True).start()


def album_palette(image):
    sample = image.scale_simple(32, 32, GdkPixbuf.InterpType.BILINEAR)
    rows = np.frombuffer(sample.get_pixels(), np.uint8)
    pixels = np.vstack([rows[y*sample.get_rowstride():y*sample.get_rowstride()+32*sample.get_n_channels()]
                        for y in range(32)]).reshape(-1, sample.get_n_channels())
    if sample.get_has_alpha():
        pixels = pixels[pixels[:, 3] >= 128]
    colours = pixels[:, :3].astype(float)/255.
    if not len(colours):
        return [(0.65,)*3, (0.83,)*3, (0.96,)*3]
    bins = (colours*7.99).astype(int)
    ids = bins[:, 0]*64 + bins[:, 1]*8 + bins[:, 2]
    groups = []
    for key, count in zip(*np.unique(ids, return_counts=True)):
        rgb = colours[ids == key].mean(axis=0)
        hue, sat, value = colorsys.rgb_to_hsv(*rgb)
        score = math.sqrt(count) * (.18 + sat) * (.12 + min(value, .9))
        groups.append((score, hue, sat, value))
    _, h, s, v = max(groups)
    def contrast(group):
        _, hue, sat, value = group
        distance = min(abs(h-hue), 1-abs(h-hue))
        return group[0] * (.3 + 2*distance + abs(v-value)*.4)
    _, h2, s2, v2 = max(groups, key=contrast)
    return [colorsys.hsv_to_rgb(h, s*.90, max(.68, v)),
            colorsys.hsv_to_rgb(h2, s2*.82, max(.82, v2)),
            colorsys.hsv_to_rgb(h, s*.38, .98)]


def mpris_upcoming(bus, name, track_id):
    path = '/org/mpris/MediaPlayer2'
    interface = 'org.mpris.MediaPlayer2.TrackList'
    def call(iface, method, args):
        return bus.call_sync(name, path, iface, method, args, None,
                             Gio.DBusCallFlags.NO_AUTO_START, 1400, None).unpack()[0]
    tracks = call('org.freedesktop.DBus.Properties', 'Get', GLib.Variant('(ss)', (interface, 'Tracks')))
    if track_id not in tracks:
        return []
    start = tracks.index(track_id)+1
    upcoming = tracks[start:start+3]
    if not upcoming:
        return []
    data = call(interface, 'GetTracksMetadata', GLib.Variant('(ao)', (upcoming,)))
    return [(m.get('xesam:title', ''), ', '.join(m.get('xesam:artist', [])), m.get('mpris:artUrl', ''))
            for m in data if m.get('xesam:title')]
