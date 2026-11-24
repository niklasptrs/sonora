"""Small Linux desktop helpers for Sonora, without GUI or account dependencies.

Profiles deliberately contain only presentation/audio settings. OAuth tokens,
Soloist API keys, and other arbitrary application data never enter a profile.
"""
from collections.abc import Mapping
import configparser
import json
import math
import os
from pathlib import Path
import re
import tempfile
import unicodedata

MAX_PROFILE_BYTES = 16 * 1024
MAX_PROFILES = 32
MAX_NAME_LENGTH = 64
PROFILE_VERSION = 1

DEFAULT_SETTINGS = dict(
    style='Ring', theme='Ice', fps=60, sensitivity=2.2, smoothing=.45,
    clock=True, player='', source='', layout='Centred', clock_style='Digital',
    density=128, backdrop=0., glow=.3, opacity=1., accent='', album_colours=False,
    now_playing=True, widget_now_playing=False, album_glow=False,
    widget_size=420, widget_cover=False, widget_above=True, widget_x=-1, widget_y=-1,
    ring_enabled=True, widget_placement='Overlay', widget_locked=False, widget_clickthrough=False,
    widget_width=420, widget_height=420, widget_positions={},
    fullscreen_style='Dashboard', fullscreen_theme='Dashboard',
    fullscreen_background='Ambient', fullscreen_lyrics=True,
    trails=True, edge_glow=False, queue_card=False, detached_lyrics=False,
    lyrics_x=-1, lyrics_y=-1, queue_x=-1, queue_y=-1,
    lyrics_width=460, lyrics_font_size=32, lyrics_align='Links',
    lyrics_motion='Fade', lyrics_offset=0.,
)
CHOICES = {
    'style': ('Bars', 'Mirror', 'Blocks', 'Ring', 'Halo', 'Waveform', 'Aurora', 'Album-Bühne'),
    'theme': ('Ice', 'Nebula', 'Ocean', 'Ember', 'Forest', 'Rose', 'Mono', 'Mint', 'Sunset'),
    'fullscreen_style': ('Dashboard', 'Bars', 'Mirror', 'Blocks', 'Ring', 'Halo', 'Waveform', 'Aurora', 'Album-Bühne'),
    'fullscreen_theme': ('Dashboard', 'Album', 'Ice', 'Nebula', 'Ocean', 'Ember', 'Forest', 'Rose', 'Mono', 'Mint', 'Sunset'),
    'fullscreen_background': ('Ambient', 'Black', 'Album'),
    'lyrics_align': ('Links', 'Mitte', 'Rechts'),
    'lyrics_motion': ('Fade', 'Rise', 'Wortaufbau', 'Seitengleiter', 'Federn', 'Static'),
    'layout': ('Compact', 'Centred', 'Cover', 'Text only'),
    'clock_style': ('Digital', 'Stacked', 'Minimal', 'Analog', 'Words'),
    'widget_placement': ('Overlay', 'Normal', 'Desktop'),
}
NUMBER_BOUNDS = {
    'lyrics_width': (180, 1100), 'lyrics_font_size': (14, 64),
    'lyrics_x': (-32768,32767), 'lyrics_y': (-32768,32767),
    'queue_x': (-32768,32767), 'queue_y': (-32768,32767),
    'lyrics_offset': (-5., 5.),
    'fps': (10, 60), 'sensitivity': (.2, 5.), 'smoothing': (0., .95),
    'density': (16, 128), 'backdrop': (0., 1.), 'glow': (0., 1.),
    'opacity': (.2, 1.), 'widget_size': (240, 720),
    'widget_width': (150, 2560), 'widget_height': (150, 1440),
    'widget_x': (-32768, 32767), 'widget_y': (-32768, 32767),
}


class ProfileError(ValueError):
    """A profile name, document or setting does not match the bounded schema."""


def config_root():
    """Respect the XDG absolute-path requirement; ignore relative overrides."""
    configured = os.environ.get('XDG_CONFIG_HOME', '')
    return Path(configured) if configured and Path(configured).is_absolute() else Path.home() / '.config'


def profile_name(name):
    if not isinstance(name, str):
        raise ProfileError('Profilname muss Text sein.')
    name = unicodedata.normalize('NFC', name.strip())
    if (not 1 <= len(name) <= MAX_NAME_LENGTH
            or not any(c.isalnum() for c in name)
            or any(not (c.isalnum() or c in ' _-') for c in name)):
        raise ProfileError('Profilname: 1–64 Buchstaben/Ziffern, Leerzeichen, _ oder - verwenden.')
    return name


def validate_settings(settings):
    """Return complete validated settings; discard every unknown key.

    Invalid known fields raise ProfileError instead of silently changing a
    saved profile. Unknown fields are intentionally excluded, including keys
    named access_token, refresh_token, api_key, client_id or password.
    """
    if not isinstance(settings, Mapping):
        raise ProfileError('Profileinstellungen müssen ein Objekt sein.')
    result = DEFAULT_SETTINGS.copy()
    for key, default in DEFAULT_SETTINGS.items():
        if key not in settings:
            continue
        value = settings[key]
        if key in CHOICES:
            if not isinstance(value, str) or value not in CHOICES[key]:
                raise ProfileError('Ungültige Einstellung: ' + key)
        elif key == 'widget_positions':
            if not isinstance(value, dict) or len(value) > 64:
                raise ProfileError('Ungültige Liste gespeicherter Widget-Positionen.')
            for size, point in value.items():
                if (not isinstance(size, str) or not re.fullmatch(r'[0-9]{1,4}x[0-9]{1,4}', size)
                        or not isinstance(point, list) or len(point) != 2
                        or any(type(n) is not int or not -32768 <= n <= 32767 for n in point)):
                    raise ProfileError('Ungültige Widget-Position.')
            value = {size: point.copy() for size, point in value.items()}
        elif key in NUMBER_BOUNDS:
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or (isinstance(value, float) and not math.isfinite(value))):
                raise ProfileError('Ungültige Zahl: ' + key)
            low, high = NUMBER_BOUNDS[key]
            if not low <= value <= high or (key in ('fps', 'density', 'widget_size', 'widget_width', 'widget_height', 'widget_x', 'widget_y') and value != int(value)):
                raise ProfileError('Einstellung außerhalb des erlaubten Bereichs: ' + key)
            value = int(value) if key in ('fps', 'density', 'widget_size', 'widget_width', 'widget_height', 'widget_x', 'widget_y') else float(value)
        elif isinstance(default, bool):
            if not isinstance(value, bool):
                raise ProfileError('Ungültiger Schalter: ' + key)
        elif key == 'accent':
            if not isinstance(value, str) or (value and not re.fullmatch(r'#[0-9a-fA-F]{6}', value)):
                raise ProfileError('Akzentfarbe muss leer oder #RRGGBB sein.')
        elif key in ('player', 'source'):
            if (not isinstance(value, str) or len(value) > 512
                    or any(unicodedata.category(c).startswith('C') for c in value)):
                raise ProfileError('Ungültiger Quellen-/Playername: ' + key)
        result[key] = value
    # 0.3 saved widget_above=False even when the user never chose a placement.
    # Start those installations in the requested overlay mode; new explicit
    # Normal/Desktop choices are preserved. Keep the old flag as a derived
    # compatibility field for callers that still display a topmost checkbox.
    result['widget_above'] = result['widget_placement'] == 'Overlay'
    for field in ('widget_width', 'widget_height'):
        if field not in settings:
            result[field] = result['widget_size']
    if 'widget_placement' not in settings and result['backdrop'] == 1.:
        # The old backdrop value was stored but never drawn by the circle.
        # Keep that appearance during upgrade rather than add an opaque disc.
        result['backdrop'] = 0.
    return result


def atomic_write(path, data):
    """Replace only the final file after a complete write, using private mode."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


class ProfileStore:
    """At most 32 named profile JSON documents in the user's XDG config dir."""
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else config_root() / 'sonora/profiles'

    def _path(self, name):
        return self.directory / (profile_name(name) + '.json')

    def list(self):
        if not self.directory.exists():
            return []
        names = []
        with os.scandir(self.directory) as entries:
            for entry in entries:
                if not entry.name.endswith('.json') or not entry.is_file(follow_symlinks=False):
                    continue
                try:
                    name = profile_name(entry.name[:-5])
                except ProfileError:
                    continue
                if name != entry.name[:-5]:
                    continue
                names.append(name)
                if len(names) > MAX_PROFILES:
                    raise ProfileError('Zu viele Profile im Profilverzeichnis (maximal 32).')
        return sorted(names, key=str.casefold)

    def save(self, name, settings):
        name = profile_name(name)
        path = self._path(name)
        clean = validate_settings(settings)
        data = (json.dumps({'version': PROFILE_VERSION, 'name': name, 'settings': clean},
                           ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode('utf-8')
        if len(data) > MAX_PROFILE_BYTES:
            raise ProfileError('Profil ist zu groß (maximal 16 KiB).')
        existing = self.list()
        if name not in existing and len(existing) >= MAX_PROFILES:
            raise ProfileError('Maximal 32 Profile können gespeichert werden.')
        atomic_write(path, data)
        return path

    def load(self, name):
        name = profile_name(name)
        path = self._path(name)
        if path.is_symlink():
            raise ProfileError('Verknüpfte Profildateien werden nicht geladen.')
        with path.open('rb') as stream:
            data = stream.read(MAX_PROFILE_BYTES + 1)
        if len(data) > MAX_PROFILE_BYTES:
            raise ProfileError('Profil ist zu groß (maximal 16 KiB).')
        try:
            document = json.loads(data)
        except (UnicodeError, ValueError):
            raise ProfileError('Ungültige JSON-Profildatei.') from None
        if (not isinstance(document, dict) or type(document.get('version')) is not int or document.get('version') != PROFILE_VERSION
                or document.get('name') != name or 'settings' not in document):
            raise ProfileError('Unbekanntes oder beschädigtes Profilformat.')
        return validate_settings(document['settings'])

    def delete(self, name):
        self._path(name).unlink(missing_ok=True)


def desktop_exec(executable):
    """Quote one executable according to Desktop Entry string/Exec escaping.

    No shell is invoked. Percent signs must be doubled to avoid field-code
    expansion; backslashes have both the key-string and Exec quoting layers.
    """
    path = os.fspath(executable)
    if not path or any(unicodedata.category(c).startswith('C') for c in path):
        raise ValueError('Ausführungspfad enthält ungültige Zeichen.')
    escaped = []
    for char in path:
        if char == '\\':
            escaped.append('\\' * 4)
        elif char in ('"', '$', '`'):
            escaped.append('\\' * 2 + char)
        elif char == '%':
            escaped.append('%%')
        else:
            escaped.append(char)
    return '"' + ''.join(escaped) + '"'


def autostart_path():
    return config_root() / 'autostart/sonora.desktop'


def autostart_enabled():
    path = autostart_path()
    try:
        if path.is_symlink() or path.stat().st_size > 4096:
            return False
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(path.read_text(encoding='utf-8'))
        entry = parser['Desktop Entry']
        return (entry.get('Type') == 'Application' and bool(entry.get('Exec'))
                and not entry.getboolean('Hidden', fallback=False)
                and entry.getboolean('X-GNOME-Autostart-enabled', fallback=True))
    except (OSError, ValueError, KeyError, configparser.Error):
        return False


def set_autostart(enabled, executable=None):
    """Enable/disable this user's Sonora startup entry; return its Path.

    Default executable is run.sh beside this module, so both a source checkout
    and a user installation launch their corresponding copy. Callers may pass
    an absolute run.sh path explicitly. Existing unrelated startup entries are
    never edited; only sonora.desktop is managed.
    """
    if not isinstance(enabled, bool):
        raise TypeError('enabled must be a bool')
    path = autostart_path()
    if not enabled:
        path.unlink(missing_ok=True)
        return path
    executable = Path(executable) if executable is not None else Path(__file__).with_name('run.sh')
    executable = executable.absolute()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError('Startskript existiert nicht oder ist nicht ausführbar.')
    content = ('[Desktop Entry]\nType=Application\nName=Sonora\n'
               'Comment=Music visualizer and Spotify controller\n'
               'Exec=' + desktop_exec(executable) + '\n'
               'Icon=audio-x-generic\nTerminal=false\n'
               'X-GNOME-Autostart-enabled=true\n')
    atomic_write(path, content.encode('utf-8'))
    return path
