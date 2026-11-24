#!/usr/bin/python3
"""Optional capture test using a private PulseAudio server and null sink.

Run with system pulseaudio/pactl/parec/pacat on PATH. Extracted binaries can be
selected with PULSE_TEST_BIN_DIR, PULSE_TEST_LIBRARY_PATH and
PULSE_TEST_MODULE_DIR. No existing audio server, microphone or hardware is used.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np


def run():
    env = os.environ.copy()
    if env.get('PULSE_TEST_BIN_DIR'):
        env['PATH'] = env['PULSE_TEST_BIN_DIR'] + os.pathsep + env.get('PATH', '')
    if env.get('PULSE_TEST_LIBRARY_PATH'):
        env['LD_LIBRARY_PATH'] = env['PULSE_TEST_LIBRARY_PATH'] + (
            os.pathsep + env['LD_LIBRARY_PATH'] if env.get('LD_LIBRARY_PATH') else '')
    missing = [name for name in ('pulseaudio', 'pactl', 'parec', 'pacat')
               if not shutil.which(name, path=env.get('PATH'))]
    if missing:
        print('SKIP: missing optional audio test executables: ' + ', '.join(missing))
        return 77
    with tempfile.TemporaryDirectory(prefix='sonora-audio-') as temporary:
        base = Path(temporary)
        for child in ('runtime', 'state', 'config'):
            (base / child).mkdir(mode=0o700)
        env.update(PULSE_RUNTIME_PATH=str(base / 'runtime'),
                   PULSE_STATE_PATH=str(base / 'state'),
                   PULSE_CONFIG_PATH=str(base / 'config'),
                   PULSE_SERVER='unix:' + str(base / 'native'))
        arguments = ['pulseaudio', '-n', '--daemonize=no', '--use-pid-file=no',
                     '--exit-idle-time=-1', '--realtime=no', '--high-priority=no',
                     '--disable-shm=yes', '--log-target=stderr']
        if env.get('PULSE_TEST_MODULE_DIR'):
            arguments += ['-p', env['PULSE_TEST_MODULE_DIR']]
        arguments += ['-L', 'module-native-protocol-unix socket=' + str(base / 'native') + ' auth-anonymous=1',
                      '-L', 'module-null-sink sink_name=sonora_test rate=22050 channels=1']
        audio = playback = daemon = writer = None
        previous_env = os.environ.copy()
        cleanup = {}
        try:
            with (base / 'daemon.log').open('w') as log:
                daemon = subprocess.Popen(arguments, env=env, stdout=log, stderr=log)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                result = subprocess.run(['pactl', 'get-default-sink'], env=env,
                                        capture_output=True, text=True, timeout=2)
                if result.returncode == 0:
                    break
                if daemon.poll() is not None:
                    raise RuntimeError('Private PulseAudio daemon exited.')
                time.sleep(.1)
            else:
                raise RuntimeError('Private PulseAudio socket did not become ready.')
            if result.stdout.strip() != 'sonora_test':
                raise AssertionError('Capture would use an unexpected sink: ' + result.stdout.strip())

            # Audio uses inherited subprocess environment. This alters only this
            # standalone test process, and is restored after capture stops.
            os.environ.update(env)
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from audio_engine import Audio, spectrum
            audio = Audio()
            playback = subprocess.Popen(
                ['pacat', '--playback', '--raw', '--format=float32le',
                 '--rate=22050', '--channels=1', '--device=sonora_test'],
                env=env, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
            t = np.arange(22050 * 4, dtype=np.float32) / 22050
            pcm = (.4 * np.sin(2 * np.pi * 1000 * t)).astype('<f4').tobytes()
            playback_errors = []

            def play():
                try:
                    _, stderr = playback.communicate(pcm, timeout=10)
                    if playback.returncode:
                        playback_errors.append(stderr.decode(errors='replace'))
                except Exception as error:
                    playback_errors.append(str(error))

            writer = threading.Thread(target=play, daemon=True)
            writer.start()
            peak = wave_peak = 0.
            dominant = None
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                with audio.lock:
                    levels = audio.levels.copy()
                    waveform = audio.wave.copy()
                if float(levels.max()) > peak:
                    peak, dominant = float(levels.max()), int(levels.argmax())
                wave_peak = max(wave_peak, float(np.abs(waveform).max()))
                if peak > .1 and wave_peak > .2:
                    break
                time.sleep(.05)
            writer.join(10)
            if writer.is_alive() or playback_errors:
                raise AssertionError('Tone playback failed: ' + '; '.join(playback_errors))
            reference_band = int(spectrum(.4 * np.sin(2 * np.pi * 1000 * np.arange(2048) / 22050)).argmax())
            if not peak > .1 or not wave_peak > .2 or abs(dominant - reference_band) > 1:
                raise AssertionError('Captured tone did not reach FFT/waveform: ' +
                                     repr((audio.status, peak, wave_peak, dominant)))
            print(json.dumps({'result': 'PASS', 'backend': 'private PulseAudio null sink',
                              'source': 'sonora_test.monitor', 'fft_peak': peak,
                              'fft_dominant_band': dominant, 'waveform_peak': wave_peak,
                              'status': audio.status}, indent=2))
        except Exception:
            log_path = base / 'daemon.log'
            if log_path.exists():
                print('Private daemon log:\n' + log_path.read_text(errors='replace')[-4000:], file=sys.stderr)
            raise
        finally:
            if audio:
                audio.stop()
                audio.thread.join(2)
                cleanup['capture_thread_stopped'] = not audio.thread.is_alive()
            if playback and playback.poll() is None:
                playback.kill()
                playback.wait(timeout=3)
            if writer:
                writer.join(2)
                cleanup['playback_writer_stopped'] = not writer.is_alive()
            if daemon:
                daemon.terminate()
                try:
                    daemon.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    daemon.kill()
                    daemon.wait(timeout=3)
                cleanup['private_daemon_stopped'] = daemon.poll() is not None
            os.environ.clear()
            os.environ.update(previous_env)
            print(json.dumps(cleanup, sort_keys=True))
        if not all(cleanup.values()):
            raise AssertionError('Audio test did not shut down cleanly.')
    return 0


if __name__ == '__main__':
    raise SystemExit(run())
