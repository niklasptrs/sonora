"""Low-latency monitor capture and audio-driven visual envelopes.

Levels remain linear FFT amplitudes so consumers can inspect real input. The
display mapping is separate: it can lift quiet music without normalising silence
or feeding synthetic motion into the visualizer.
"""
from functools import lru_cache
import math
import shutil
import subprocess
import threading

import numpy as np


SAMPLE_RATE = 22050
FFT_SIZE = 2048
HOP_SIZE = 512


@lru_cache(maxsize=8)
def fft_plan(size, count):
    # Fixed frequency boundaries keep a note in the same display band when the
    # window size changes. Duplicate low-frequency bins are interpolated below.
    return np.hanning(size), np.geomspace(30, SAMPLE_RATE / 2, count + 1)


def spectrum(samples, count=64):
    """Log-frequency spectrum of mono PCM using a Hann window."""
    samples = np.asarray(samples, dtype=np.float32)
    if samples.size < 128:
        return np.zeros(count, dtype=np.float32)
    samples = np.nan_to_num(samples, nan=0., posinf=0., neginf=0.)
    window, edges = fft_plan(samples.size, count)
    fft = np.abs(np.fft.rfft((samples - samples.mean()) * window)) / (samples.size / 2)
    frequencies = np.fft.rfftfreq(samples.size, 1 / SAMPLE_RATE)
    bands = []
    for low, high in zip(edges, edges[1:]):
        a, b = np.searchsorted(frequencies, (low, high))
        if b > a:
            bands.append(float(fft[a:b].max()))
        else:
            bands.append(float(np.interp(math.sqrt(low * high), frequencies, fft)))
    return np.asarray(bands, dtype=np.float32)


class VisualResponse:
    """Fast attack, bounded quiet-input lift and time-based decay for drawing.

    Call ``update(raw, sensitivity, smoothing, dt)`` once per rendered frame.
    ``smoothing`` controls release, not the initial response to a beat. The
    returned array and ``bass`` are always driven solely by captured samples.
    """
    def __init__(self, count=64):
        self.values = np.zeros(count, dtype=np.float32)
        self.peak = .025
        self.bass = 0.
        self.tilt = np.geomspace(1., 2.4, count).astype(np.float32)

    def update(self, raw, sensitivity=1.6, smoothing=.72, dt=1 / 60):
        raw = np.asarray(raw, dtype=np.float32)
        raw = np.maximum(np.nan_to_num(raw, nan=0., posinf=0., neginf=0.), 0)
        dt = min(.25, max(0., float(dt)))
        weighted = raw * self.tilt
        peak = float(weighted.max())
        peak_tau = .012 if peak > self.peak else 1.1
        self.peak += (peak - self.peak) * (-math.expm1(-dt / peak_tau))
        # Even very quiet noise cannot cause unlimited automatic amplification.
        gain = min(8., max(.5, .10 / max(.0001, self.peak)))
        target = np.clip(np.sqrt(np.maximum(weighted - .00008, 0) * gain)
                         * max(0., sensitivity) * 1.8, 0., 1.)
        release = .045 + min(1., max(0., smoothing)) * .20
        tau = np.where(target > self.values, .008, release)
        self.values += (target - self.values) * (-np.expm1(-dt / tau))
        self.values[self.values < .0005] = 0.
        self.bass = float(np.sqrt(np.mean(self.values[:max(1, len(raw) // 4)] ** 2)))
        return self.values


class Audio:
    def __init__(self, source=''):
        self.lock = threading.Lock()
        self.levels = np.zeros(64, dtype=np.float32)
        self.wave = np.zeros(256, dtype=np.float32)
        self.status = 'Audio wird vorbereitet …'
        self.process = None
        self.stop_lock = threading.Lock()
        self.stopped = threading.Event()
        self.source = source
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        if not shutil.which('parec'):
            self.status = 'Audio: pulseaudio-utils installieren (parec fehlt)'
            return
        try:
            with self.stop_lock:
                if self.stopped.is_set():
                    return
                source = self.source
                if not source:
                    try:
                        sink = subprocess.check_output(['pactl', 'get-default-sink'], timeout=3, text=True).strip()
                        if not sink:
                            self.status = 'Kein Standard-Audioausgang verfügbar.'
                            return
                        source = sink + '.monitor'
                    except (OSError, subprocess.SubprocessError):
                        self.status = 'Kein Audio-Ausgang gefunden. Quelle in Einstellungen wählen.'
                        return
                if self.stopped.is_set():
                    return
                self.process = subprocess.Popen([
                    'parec', '--raw', '--format=float32le', '--rate=' + str(SAMPLE_RATE),
                    '--channels=1', '--latency-msec=25', '--process-time-msec=10',
                    '--device=' + source,
                ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self.status = 'Live · ' + source
            history = np.zeros(FFT_SIZE, dtype=np.float32)
            while not self.stopped.is_set():
                data = self.process.stdout.read(HOP_SIZE * 4)
                if len(data) != HOP_SIZE * 4:
                    break
                samples = np.nan_to_num(np.frombuffer(data, dtype='<f4'), nan=0., posinf=0., neginf=0.)
                history[:-HOP_SIZE] = history[HOP_SIZE:]
                history[-HOP_SIZE:] = samples
                with self.lock:
                    self.levels = spectrum(history)
                    self.wave = samples[::2].copy()
            if not self.stopped.is_set():
                self.status = 'Audioquelle nicht verfügbar. Quelle prüfen und neu verbinden.'
        except OSError as exc:
            self.status = 'Audio: ' + str(exc)
        finally:
            self.stop()
            if self.process and self.process.stdout:
                self.process.stdout.close()

    def stop(self):
        self.stopped.set()
        with self.stop_lock:
            process = self.process
            if process is not None:
                try:
                    process.terminate()
                    process.wait(timeout=1)
                except ProcessLookupError:
                    pass
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        with self.lock:
            self.levels.fill(0)
            self.wave.fill(0)
