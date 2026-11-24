"""Install Spotify's upstream Linux release per user and own only the child we start."""
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
from urllib.request import urlopen

from soloist_backend import SoloistError, local_endpoint

ARCHIVES = {'x86_64': 'x86_64', 'aarch64': 'arm64', 'armv7l': 'arm32'}
MAX_BYTES = 160 * 1024 * 1024
BUILD_LIFETIME = 90 * 86400


class SoloistSetup:
    def __init__(self):
        data = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
        self.binary = data / 'sonora/bin/soloist'
        self.data = Path(os.environ.get('STATE_DIRECTORY', data / 'soloist'))
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.child = None

    def check_cancelled(self):
        if self.cancelled.is_set():
            raise SoloistError('Einrichtung abgebrochen.')

    @staticmethod
    def usable(binary):
        if not binary or not os.access(binary, os.X_OK):
            return False
        try:
            version = subprocess.run([binary, '--version'], stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, timeout=5, check=True)
            build = re.search(r'\bbuild (\d{10})\b', version.stdout)
            return bool(build and time.time() < int(build[1]) + BUILD_LIFETIME)
        except (OSError, subprocess.SubprocessError):
            return False

    def install(self):
        for path in (self.binary, shutil.which('soloist')):
            if self.usable(path):
                return Path(path)
        arch = ARCHIVES.get(platform.machine())
        if not arch:
            raise SoloistError('Für diese Prozessorarchitektur gibt es kein Soloist-Paket.')
        url = f'https://soloist-builds.spotifycdn.com/soloist_release_{arch}.tar.gz'
        self.binary.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.binary.parent) as directory:
            archive = Path(directory, 'release.tar.gz')
            with urlopen(url, timeout=20) as response, archive.open('wb') as out:
                while chunk := response.read(256 * 1024):
                    self.check_cancelled()
                    if out.tell() + len(chunk) > MAX_BYTES:
                        raise SoloistError('Das Soloist-Paket ist zu groß.')
                    out.write(chunk)
            candidate = Path(directory, 'soloist')
            with tarfile.open(archive, 'r:gz') as package:
                for member in package:
                    if member.name in ('soloist', './soloist') and member.isfile() and 0 < member.size <= MAX_BYTES:
                        with package.extractfile(member) as source, candidate.open('xb') as out:
                            shutil.copyfileobj(source, out)
                        break
            if not candidate.exists():
                raise SoloistError('Das Archiv enthält kein Soloist-Programm.')
            candidate.chmod(0o700)
            if not self.usable(candidate):
                raise SoloistError('Dieser Soloist-Build ist abgelaufen oder auf diesem System nicht ausführbar.')
            self.check_cancelled()
            candidate.replace(self.binary)
        return self.binary

    def endpoint(self, binary):
        # ctl status also succeeds when a daemon is offline. Check its reported listener.
        result = subprocess.run([binary, 'ctl', 'status', '--data-dir', self.data],
                                capture_output=True, text=True, timeout=3)
        listener = re.search(r'^\s*ws: listening on (\S+)\s*$', result.stdout, re.MULTILINE)
        if result.returncode == 0 and listener:
            return local_endpoint('ws://' + listener[1])
        return None

    def start(self, key):
        binary = self.install()
        self.check_cancelled()
        endpoint = self.endpoint(binary)
        if endpoint and not key:
            return endpoint
        if key:
            if '\n' in key or '\r' in key:
                raise SoloistError('Bitte einen gültigen Soloist-Schlüssel eingeben.')
            result = subprocess.run([binary, 'setup-api-key', '--data-dir', self.data],
                                    input=key+'\n', text=True, capture_output=True, timeout=10)
            if result.returncode:
                raise SoloistError('Soloist konnte den Schlüssel nicht speichern.')
            if endpoint:
                # An externally owned process must pick up a rotated key on its next start.
                if self.child is None:
                    raise SoloistError('Schlüssel gespeichert. Extern gestarteten Soloist neu starten und verbinden.')
                self.stop_child()
        elif not (self.data/'credentials/api_key').is_file():
            raise SoloistError('Soloist ist installiert. Persönlichen Soloist-Schlüssel eingeben und Einrichtung erneut starten.')
        with self.lock:
            self.check_cancelled()
            self.child = subprocess.Popen([binary, '--device-name', 'Sonora', '--data-dir', self.data,
                    '--ws', '127.0.0.1:0', '--cache-size', '100'], stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            child = self.child
        try:
            for _ in range(50):
                self.check_cancelled()
                endpoint = self.endpoint(binary)
                if endpoint:
                    return endpoint
                if child.poll() is not None:
                    break
                self.cancelled.wait(.1)
        except Exception:
            self.stop_child()
            raise
        self.stop_child()
        raise SoloistError('Soloist konnte die lokale Wiedergabe nicht starten. Audioausgabe und Schlüssel prüfen.')

    def stop_child(self):
        with self.lock:
            child, self.child = self.child, None
            if child and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()

    def close(self):
        self.cancelled.set()
        self.stop_child()
