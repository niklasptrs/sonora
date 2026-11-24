"""Bounded local LRC/text support and explicitly requested LRCLIB lookups.

Online API reference: https://lrclib.net/docs.txt (GET /api/get).
The caller supplies an identifying User-Agent with its real project/contact and
runs lookup outside the UI thread. Importing this module never opens a network
connection. No lyrics are uploaded or published.
"""
from bisect import bisect_right
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from http.client import HTTPException
import json
import math
from pathlib import Path
import random
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

MAX_BYTES = 1024 * 1024
MAX_LINES = 20_000
_TIMESTAMP = re.compile(r'\[(\d{1,4}):([0-5]\d)(?:[.:](\d{1,3}))?\]')
_OFFSET = re.compile(r'^\[offset\s*:\s*([+-]?\d{1,10})\s*\]\s*$', re.I)
_METADATA = re.compile(r'^\[[a-zA-Z][a-zA-Z0-9_-]*\s*:[^\]]*\]\s*$')
_WORD_TIME = re.compile(r'<\d{1,4}:[0-5]\d(?:[.:]\d{1,3})?>')


class LyricsError(Exception):
    """Expected file/API failure; retry_after is a wait in seconds, or None."""
    def __init__(self, message, *, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class LyricLine:
    time_ms: int
    text: str
    # Relative milliseconds and UTF-8 byte offsets for Pango text attributes.
    cues: tuple = ()
    start_ms: int | None = None

    def highlighted_bytes(self, position_ms):
        if not self.cues:
            return 0
        elapsed = position_ms - (self.time_ms if self.start_ms is None else self.start_ms)
        index = bisect_right(self.cues, (elapsed, math.inf)) - 1
        return self.cues[index][1] if index >= 0 else 0


@dataclass(frozen=True, slots=True)
class Lyrics:
    plain: str
    lines: tuple[LyricLine, ...] = ()
    source: str = 'local'
    instrumental: bool = False
    _times: tuple[int, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, '_times', tuple(line.time_ms for line in self.lines))

    def index_at(self, position_ms):
        """Last started line index, or -1 before the first synchronized line."""
        try:
            value = float(position_ms)
        except (TypeError, ValueError, OverflowError):
            return -1
        if not math.isfinite(value) or value < 0:
            return -1
        return bisect_right(self._times, value) - 1

    def line_at(self, position_ms):
        index = self.index_at(position_ms)
        return self.lines[index] if index >= 0 else None


@dataclass(frozen=True, slots=True)
class LyricFrame:
    text: str = ''
    opacity: float = 1.
    dx: int = 0
    dy: int = 0


def animated_line(line, position, mode, playing):
    if not line:
        return LyricFrame()
    if not playing or mode == 'Static':
        return LyricFrame(line.text)
    elapsed = max(0, position-line.time_ms)
    if mode == 'Wortaufbau':
        if line.cues:
            end = line.highlighted_bytes(position)
            text = line.text.encode()[:end].decode(errors='ignore')
        else:
            words = line.text.split()
            step = min(110., 850./max(1, len(words)-1))
            count = 1 + int(elapsed / step)
            text = ' '.join(words[:count]) if count < len(words) else line.text
        return LyricFrame(text)
    if mode == 'Federn':
        t = min(1., elapsed / 620)
        return LyricFrame(line.text, min(1., t*5), dy=round(18*(1-t)**2*math.cos(t*math.tau*1.3)))
    t = min(1., elapsed / 260)
    return LyricFrame(line.text, t,
                      dx=round(-32*(1-t)**2) if mode == 'Seitengleiter' else 0,
                      dy=round(12*(1-t)) if mode == 'Rise' else 0)


def _check_text(text):
    if not isinstance(text, str):
        raise LyricsError('Lyrics müssen Text sein.')
    try:
        too_large = len(text) > MAX_BYTES or len(text.encode('utf-8')) > MAX_BYTES
    except UnicodeError as exc:
        raise LyricsError('Lyrics enthalten ungültige Unicode-Zeichen.') from exc
    if too_large:
        raise LyricsError('Lyrics sind zu groß (maximal 1 MiB).')
    if '\x00' in text:
        raise LyricsError('Die Datei enthält ungültige Textdaten.')
    return text.lstrip('\ufeff')


def parse_lyrics(text, source='local'):
    """Read UTF-8 text or simple/enhanced LRC with millisecond precision.

    Repeated timestamps expand repeated lines. Metadata tags are omitted. The
    last global [offset:] applies to all lines: positive means earlier, negative
    means later, as in the common LRC convention. Negative final times clamp to
    zero. Blank timed lines remain available to clear the on-screen line.
    """
    text = _check_text(text)
    raw_lines = text.splitlines()
    if len(raw_lines) > MAX_LINES:
        raise LyricsError('Zu viele Lyrics-Zeilen (maximal 20000).')
    offset = 0
    parsed = []
    plain = []
    for raw in raw_lines:
        line = raw.strip()
        offset_match = _OFFSET.fullmatch(line)
        if offset_match:
            offset = int(offset_match.group(1))
            continue
        matches = []
        cursor = 0
        # Only prefix tags are time markers; bracketed words inside lyrics stay.
        while True:
            match = _TIMESTAMP.match(line, cursor)
            if not match:
                break
            minutes, seconds, fraction = match.groups()
            millis = (int(minutes)*60 + int(seconds))*1000
            millis += int((fraction or '').ljust(3, '0') or '0')
            matches.append(millis)
            if len(matches) + len(parsed) > MAX_LINES:
                raise LyricsError('Zu viele Zeitmarken (maximal 20000).')
            cursor = match.end()
            while cursor < len(line) and line[cursor].isspace():
                cursor += 1
        if matches:
            content = line[cursor:]
            tags = list(_WORD_TIME.finditer(content))
            words = _WORD_TIME.sub('', content).strip()
            cues = []
            if tags:
                byte_end = len(content[:tags[0].start()].encode())
                for i, tag in enumerate(tags):
                    minutes, seconds = tag.group()[1:-1].split(':', 1)
                    seconds = seconds.replace(':', '.')
                    start = int(minutes)*60000 + round(float(seconds)*1000)
                    end = tags[i+1].start() if i+1 < len(tags) else len(content)
                    byte_end += len(content[tag.end():end].encode())
                    cues.append((start-matches[0], byte_end))
            cues = tuple(sorted(cues))
            plain.append(words)
            parsed.extend((millis, words, cues) for millis in matches)
            if len(parsed) > MAX_LINES:
                raise LyricsError('Zu viele Zeitmarken (maximal 20000).')
        elif not _METADATA.fullmatch(line):
            plain.append(line)
    lines = tuple(LyricLine(max(0, millis-offset), words, cues, millis-offset if cues else None)
                  for millis, words, cues in sorted(parsed, key=lambda pair: pair[0]))
    return Lyrics('\n'.join(plain).strip(), lines, source)


def load_lyrics(path):
    """Load at most 1 MiB from a local UTF-8 .lrc or text file."""
    try:
        with Path(path).open('rb') as file:
            data = file.read(MAX_BYTES+1)
    except OSError as exc:
        raise LyricsError('Lyrics-Datei konnte nicht gelesen werden.') from exc
    if len(data) > MAX_BYTES:
        raise LyricsError('Lyrics-Datei ist zu groß (maximal 1 MiB).')
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise LyricsError('Lyrics-Datei muss UTF-8-Text enthalten.') from exc
    return parse_lyrics(text, source=str(Path(path)))


def _retry_seconds(value, default=60):
    try:
        delay = float(value)
        if not math.isfinite(delay):
            raise ValueError()
    except (ValueError, TypeError, OverflowError):
        try:
            delay = parsedate_to_datetime(value).timestamp() - time.time()
        except (TypeError, ValueError, OverflowError, AttributeError):
            delay = default
    return max(0, delay)


class LyricsClient:
    """One optional LRCLIB client, with bounded responses and no auto retries.

    user_agent must include the real app name/version and a project URL or
    contact email, as required by LRCLIB. lookup returns None for a 404.
    HTTP 429/503 and calls made too soon raise LyricsError(retry_after=seconds);
    subsequent requests are blocked locally until that delay has passed.
    """
    def __init__(self, user_agent, *, timeout=10):
        if (not isinstance(user_agent, str) or not 1 <= len(user_agent) <= 512
                or any(ord(c) < 32 or ord(c) > 126 for c in user_agent)
                or not re.search(r'https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', user_agent)):
            raise LyricsError('Für LRCLIB einen User-Agent mit App/Version und echter Projekt-URL oder Kontaktadresse angeben.')
        try:
            valid_timeout = float(timeout)
        except (TypeError, ValueError, OverflowError):
            valid_timeout = float('nan')
        if not math.isfinite(valid_timeout) or not 1 <= valid_timeout <= 30:
            raise LyricsError('Lyrics-Timeout muss zwischen 1 und 30 Sekunden liegen.')
        self.user_agent = user_agent
        self.timeout = valid_timeout
        self._lock = threading.Lock()
        self._next_request = 0.0

    def lookup(self, title, artist, album='', duration=None):
        params = {}
        for key, value, required in (('track_name', title, True), ('artist_name', artist, True), ('album_name', album, False)):
            if not isinstance(value, str) or len(value) > 512:
                raise LyricsError('Ungültige Titel-, Interpret- oder Albumangabe.')
            value = value.strip()
            if required and not value:
                raise LyricsError('Titel und Interpret sind für die Lyrics-Suche erforderlich.')
            if value:
                params[key] = value
        if duration is not None:
            try:
                seconds = float(duration)
            except (TypeError, ValueError, OverflowError):
                seconds = float('nan')
            if isinstance(duration, bool) or not math.isfinite(seconds) or not 1 <= seconds <= 3600:
                raise LyricsError('Trackdauer muss zwischen 1 und 3600 Sekunden liegen.')
            params['duration'] = seconds
        request = Request('https://lrclib.net/api/get?'+urlencode(params), headers={
            'User-Agent':self.user_agent, 'Accept':'application/json'})
        with self._lock:
            remaining = self._next_request-time.monotonic()
            if remaining > 0:
                raise LyricsError('Bitte vor der nächsten Lyrics-Anfrage warten.', retry_after=remaining)
            self._next_request = time.monotonic()+.3
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    if urlparse(response.geturl()).scheme != 'https' or urlparse(response.geturl()).hostname != 'lrclib.net':
                        raise LyricsError('Unerwartete Weiterleitung bei der Lyrics-Anfrage.')
                    content_type = response.headers.get('Content-Type', '').lower().split(';', 1)[0].strip()
                    if content_type != 'application/json':
                        raise LyricsError('LRCLIB hat keine JSON-Antwort geliefert.')
                    data = response.read(MAX_BYTES+1)
            except HTTPError as exc:
                try:
                    if exc.code == 404:
                        return None
                    if exc.code in (429, 503):
                        delay = _retry_seconds(exc.headers.get('Retry-After'))
                        delay += random.uniform(0, max(.1, delay*.3))
                        self._next_request = time.monotonic()+delay
                        raise LyricsError('LRCLIB ist ausgelastet. Bitte später erneut versuchen.', retry_after=delay) from None
                    raise LyricsError('LRCLIB-Anfrage fehlgeschlagen (HTTP %s).' % exc.code) from None
                finally:
                    exc.close()
            except (URLError, OSError, TimeoutError, HTTPException) as exc:
                raise LyricsError('LRCLIB ist nicht erreichbar. Verbindung prüfen.') from exc
        if len(data) > MAX_BYTES:
            raise LyricsError('LRCLIB-Antwort ist zu groß (maximal 1 MiB).')
        try:
            result = json.loads(data)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise LyricsError('Ungültige LRCLIB-Antwort.') from exc
        if not isinstance(result, dict):
            raise LyricsError('Ungültige LRCLIB-Antwort.')
        plain, synced = result.get('plainLyrics'), result.get('syncedLyrics')
        if (plain is not None and not isinstance(plain, str)) or (synced is not None and not isinstance(synced, str)):
            raise LyricsError('Ungültige Lyrics-Daten in der LRCLIB-Antwort.')
        plain, synced = plain or '', synced or ''
        parsed = parse_lyrics(synced or plain, source='LRCLIB')
        instrumental = result.get('instrumental') is True
        return Lyrics(_check_text(plain) if plain else parsed.plain,
                      parsed.lines, 'LRCLIB', instrumental)
