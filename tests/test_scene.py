import sys
from pathlib import Path
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scene_media import album_palette, mpris_upcoming
from gi.repository import GdkPixbuf


class SceneTests(unittest.TestCase):
    def test_album_red_survives_and_neutral_cover_stays_neutral(self):
        image = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 32, 32)
        image.fill(0xd12612ff)
        main, _, accent = album_palette(image)
        self.assertGreater(main[0], main[1]*2)
        self.assertGreater(accent[1], main[1])
        image.fill(0x333333ff)
        for colour in album_palette(image):
            self.assertAlmostEqual(max(colour), min(colour))

    def test_queue_follows_current_track(self):
        class Bus:
            def call_sync(self, name, path, interface, method, args, *unused):
                if method == 'Get':
                    data = ['/first', '/current', '/next']
                else:
                    assert args.unpack() == (['/next'],)
                    data = [{'xesam:title': 'Song', 'xesam:artist': ['Artist']}]
                return SimpleNamespace(unpack=lambda: (data,))
        self.assertEqual(mpris_upcoming(Bus(),'player','/current'), [('Song','Artist','')])
        self.assertEqual(mpris_upcoming(Bus(),'player','/other'), [])
