import sys
from pathlib import Path
import unittest
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audio_engine import spectrum, VisualResponse

class SpectrumTest(unittest.TestCase):
    def test_silence(self):
        np.testing.assert_array_equal(spectrum(np.zeros(4096)), np.zeros(64))

    def test_tone_moves_up_spectrum(self):
        t = np.arange(4096) / 22050
        low = spectrum(np.sin(2*np.pi*100*t))
        high = spectrum(np.sin(2*np.pi*4000*t))
        self.assertGreater(high.argmax(), low.argmax())
        self.assertTrue(np.isfinite(high).all())
        self.assertGreater(high.max(), .45)

    def test_short_input(self):
        self.assertEqual(spectrum([0]).shape, (64,))

    def test_bands_stay_at_same_frequency_for_different_window_sizes(self):
        peaks = []
        for size in (1024, 2048, 4096):
            tone = np.sin(2 * np.pi * 1000 * np.arange(size) / 22050)
            peaks.append(int(spectrum(tone).argmax()))
        self.assertLessEqual(max(peaks) - min(peaks), 1)


class VisualResponseTests(unittest.TestCase):
    def test_silence_never_animates_even_after_gain_adapts(self):
        response = VisualResponse()
        for _ in range(500):
            response.update(np.zeros(64), dt=1 / 60)
        np.testing.assert_array_equal(response.values, np.zeros(64))
        self.assertEqual(response.bass, 0)

    def test_transient_is_visible_in_one_frame_and_then_decays(self):
        response = VisualResponse()
        transient = np.zeros(64)
        transient[4] = .06
        result = response.update(transient, dt=1 / 60)
        self.assertGreater(result[4], .65)
        initial = result[4]
        response.update(np.zeros(64), dt=1 / 60)
        self.assertLess(response.values[4], initial)
        for _ in range(120):
            response.update(np.zeros(64), dt=1 / 60)
        self.assertEqual(response.values.max(), 0)

    def test_quiet_music_is_visible_without_lifting_noise(self):
        music = np.zeros(64)
        music[15] = .002
        response = VisualResponse()
        for _ in range(120):
            response.update(music)
        self.assertGreater(response.values[15], .3)
        noise = VisualResponse()
        for _ in range(120):
            noise.update(np.full(64, .00001))
        self.assertEqual(noise.values.max(), 0)

    def test_attack_and_decay_do_not_depend_on_frame_rate(self):
        results = []
        for fps in (24, 30, 60):
            response = VisualResponse()
            for _ in range(fps):
                response.update(np.full(64, .02), dt=1 / fps)
            for _ in range(fps // 2):
                response.update(np.zeros(64), dt=1 / fps)
            results.append(response.values.copy())
        np.testing.assert_allclose(results[0], results[1], atol=.005)
        np.testing.assert_allclose(results[0], results[2], atol=.005)

    def test_sensitivity_remains_effective_and_nonfinite_input_is_safe(self):
        quiet = np.full(64, .002)
        low = VisualResponse().update(quiet, sensitivity=.5).copy()
        high = VisualResponse().update(quiet, sensitivity=2.).copy()
        self.assertGreater(high.max(), low.max() * 2)
        invalid = VisualResponse().update(np.full(64, np.inf))
        self.assertTrue(np.isfinite(invalid).all())
