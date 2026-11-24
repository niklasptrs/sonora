"""Audio routing, PCM flow and shutdown checks without a real sound server."""
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audio_engine import Audio, HOP_SIZE


class FakeStream:
    def __init__(self, process, pcm):
        self.process = process
        self.pcm = pcm
        self.read_count = 0
        self.blocked = threading.Event()
        self.closed = False
        self.offset = 0

    def read(self, size):
        self.read_count += 1
        if self.offset < len(self.pcm):
            data = self.pcm[self.offset:self.offset + size]
            self.offset += len(data)
            return data
        self.blocked.set()
        self.process.exited.wait(3)
        return b''

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, pcm, ignores_terminate=False):
        self.exited = threading.Event()
        self.ignores_terminate = ignores_terminate
        self.terminated = False
        self.killed = False
        self.stdout = FakeStream(self, pcm)

    def terminate(self):
        self.terminated = True
        if not self.ignores_terminate:
            self.exited.set()

    def kill(self):
        self.killed = True
        self.exited.set()

    def wait(self, timeout=None):
        if self.exited.is_set():
            return 0
        raise subprocess.TimeoutExpired('parec', timeout)


class AudioCaptureTests(unittest.TestCase):
    @staticmethod
    def tone():
        t = np.arange(4096, dtype=np.float32)/22050
        return (.4*np.sin(2*np.pi*440*t)).astype('<f4').tobytes()

    def finish(self, audio):
        audio.stop()
        audio.thread.join(1)
        self.assertFalse(audio.thread.is_alive(), 'PCM reader did not finish after shutdown')

    def test_default_output_monitor_delivers_pcm_to_fft_and_waveform(self):
        process = FakeProcess(self.tone())
        with patch('audio_engine.shutil.which', return_value='/usr/bin/parec'), patch('audio_engine.subprocess.check_output', return_value='alsa_output.test\n') as discover, patch('audio_engine.subprocess.Popen', return_value=process) as launch:
            audio = Audio()
            try:
                self.assertTrue(process.stdout.blocked.wait(1), 'PCM frame was not consumed')
                discover.assert_called_once_with(['pactl','get-default-sink'], timeout=3, text=True)
                command = launch.call_args.args[0]
                self.assertIn('--device=alsa_output.test.monitor', command)
                self.assertIn('--latency-msec=25', command)
                self.assertLessEqual(HOP_SIZE / 22050, .025)
                with audio.lock:
                    self.assertEqual(audio.levels.shape, (64,))
                    self.assertGreater(audio.levels.max(), .1)
                    self.assertEqual(audio.wave.shape, (256,))
                    self.assertGreater(np.max(np.abs(audio.wave)), .3)
                    self.assertTrue(np.isfinite(audio.levels).all())
            finally:
                self.finish(audio)
        self.assertTrue(process.terminated)
        self.assertTrue(process.stdout.closed)
        self.assertFalse(process.killed)

    def test_failed_output_discovery_never_opens_an_input(self):
        for error in (FileNotFoundError('pactl missing'), subprocess.CalledProcessError(1, 'pactl')):
            with self.subTest(error=type(error).__name__), patch('audio_engine.shutil.which', return_value='/usr/bin/parec'), patch('audio_engine.subprocess.check_output', side_effect=error), patch('audio_engine.subprocess.Popen') as launch:
                audio = Audio()
                self.finish(audio)
                launch.assert_not_called()
                np.testing.assert_array_equal(audio.levels, np.zeros(64))
                self.assertIn('Kein Audio-Ausgang', audio.status)

    def test_empty_default_sink_never_launches_a_source(self):
        with patch('audio_engine.shutil.which', return_value='/usr/bin/parec'), patch('audio_engine.subprocess.check_output', return_value='  \n'), patch('audio_engine.subprocess.Popen') as launch:
            audio = Audio()
            self.finish(audio)
            launch.assert_not_called()
            self.assertIn('Kein Standard-Audioausgang', audio.status)

    def test_explicit_source_bypasses_default_output_discovery(self):
        process = FakeProcess(self.tone())
        with patch('audio_engine.shutil.which', return_value='/usr/bin/parec'), patch('audio_engine.subprocess.check_output') as discover, patch('audio_engine.subprocess.Popen', return_value=process) as launch:
            audio = Audio('user_chosen_source')
            try:
                self.assertTrue(process.stdout.blocked.wait(1))
                discover.assert_not_called()
                self.assertIn('--device=user_chosen_source', launch.call_args.args[0])
            finally:
                self.finish(audio)

    def test_shutdown_kills_a_capture_process_that_ignores_termination(self):
        process = FakeProcess(self.tone(), ignores_terminate=True)
        with patch('audio_engine.shutil.which', return_value='/usr/bin/parec'), patch('audio_engine.subprocess.Popen', return_value=process):
            audio = Audio('test.monitor')
            try:
                self.assertTrue(process.stdout.blocked.wait(1))
            finally:
                self.finish(audio)
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)
        self.assertTrue(process.stdout.closed)

    def test_missing_capture_executable_opens_no_source(self):
        with patch('audio_engine.shutil.which', return_value=None), patch('audio_engine.subprocess.Popen') as launch:
            audio = Audio()
            self.finish(audio)
            launch.assert_not_called()
            self.assertIn('parec fehlt', audio.status)

    def test_incomplete_pcm_frame_is_not_analyzed(self):
        process = FakeProcess(b'\x00' * 100)
        with patch('audio_engine.shutil.which', return_value='/usr/bin/parec'), patch('audio_engine.subprocess.Popen', return_value=process):
            audio = Audio('test.monitor')
            try:
                audio.thread.join(1)
                self.assertEqual(process.stdout.read_count, 1)
            finally:
                self.finish(audio)
        np.testing.assert_array_equal(audio.levels, np.zeros(64))
        self.assertTrue(process.stdout.closed)
