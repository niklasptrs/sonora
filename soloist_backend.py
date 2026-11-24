"""Event-driven local integration with Spotify Soloist's documented JSON API.

The separately installed Spotify binary and its API key remain outside Sonora.
Callbacks run on the transport thread. GUI adapters must schedule them on their
main loop, then call ``is_current_event`` before applying a queued notification.
"""
import json
import math
import os
from pathlib import Path
import queue
import threading
from urllib.parse import urlparse

try:
    import websocket
except ImportError:
    websocket = None


class SoloistError(Exception):
    pass


def local_endpoint(value):
    try:
        parsed = urlparse(value)
        port = parsed.port
        host = parsed.hostname
    except (ValueError, TypeError, AttributeError):
        raise SoloistError('Ungültige Soloist-Adresse oder ungültiger Port.') from None
    if (parsed.scheme != 'ws' or host not in ('127.0.0.1', '::1', 'localhost')
            or not port or parsed.username or parsed.password or parsed.path not in ('', '/')
            or parsed.query or parsed.fragment):
        raise SoloistError('Soloist-Adresse muss lokal sein: ws://127.0.0.1:9090')
    return value


def discover_endpoint():
    directory = Path(os.environ.get('STATE_DIRECTORY') or (Path(os.environ.get('XDG_DATA_HOME', Path.home()/'.local/share'))/'soloist'))
    try:
        host = (directory/'ws.addr').read_text().strip()
        port = (directory/'ws.port').read_text().strip()
        if ':' in host:
            host = '['+host+']'
        return local_endpoint('ws://'+host+':'+port)
    except (OSError, SoloistError, UnicodeError):
        return 'ws://127.0.0.1:9090'


def _object(value):
    return value if isinstance(value, dict) else {}


def entity_info(entity):
    """Read optional entity decorations without trusting every field's type."""
    decorations = _object(_object(entity).get('decorations'))
    covers = _object(decorations.get('visual_identity')).get('cover') or []
    covers = [c for c in covers if isinstance(c, dict) and isinstance(c.get('url'), str)] if isinstance(covers, list) else []
    cover = next((c['url'] for c in covers if c.get('size') == 'default'), covers[0]['url'] if covers else '')
    creators = decorations.get('creators') or []
    artists = [_object(_object(_object(_object(c).get('entity')).get('decorations')).get('identity')).get('name') for c in creators] if isinstance(creators, list) else []
    title = _object(decorations.get('identity')).get('name')
    duration = _object(decorations.get('playback')).get('duration_ms', 0)
    try:
        if isinstance(duration, bool) or not isinstance(duration, (float, int)) or not math.isfinite(duration):
            duration = 0
    except OverflowError:
        duration = 0
    return dict(title=title if isinstance(title, str) and title else 'Keine Wiedergabe',
                artist=', '.join(a for a in artists if isinstance(a, str) and a), cover=cover,
                duration_ms=max(0, duration))


class Soloist:
    UPDATE_TYPES = frozenset(('auth_state', 'track_changed', 'playback_changed', 'volume_changed', 'device_changed', 'context_changed', 'options_changed', 'position_sync', 'queue_changed'))
    QUERY_COMMANDS = frozenset(('get_auth_state', 'get_state', 'get_queue'))
    CONTROL_COMMANDS = frozenset(('play', 'pause', 'skip_next', 'skip_prev', 'seek', 'set_volume', 'set_shuffle', 'set_repeat_context', 'set_repeat_track', 'add_to_queue', 'activate', 'deactivate'))

    def __init__(self, notify):
        self.notify = notify
        self.connected = False
        self.state = {}
        self.generation = 0
        self.epoch = 0
        self.stop_event = threading.Event()
        self.outgoing = queue.Queue(maxsize=64)
        self.socket = None
        self.thread = None
        self.lock = threading.RLock()
        self.lifecycle_lock = threading.RLock()

    @property
    def ready(self):
        with self.lock:
            return self.connected and bool(self.state.get('logged_in', False))

    def is_current_event(self, event):
        """Reject old notifications queued before stop or transport reconnect."""
        with self.lock:
            return (event.get('_generation', self.generation) == self.generation
                    and event.get('_epoch', self.epoch) == self.epoch)

    def _current(self, generation, stop):
        return generation == self.generation and not stop.is_set()

    def _notification(self, event):
        return dict(event, _generation=self.generation, _epoch=self.epoch)

    @staticmethod
    def _clean_event(event):
        event = dict(event)
        for key in ('item', 'context', 'options', 'available_actions'):
            if key in event and not isinstance(event[key], dict):
                event[key] = {}
        for key in ('device_name', 'message'):
            if key in event and not isinstance(event[key], str):
                event[key] = ''
        for key in ('logged_in', 'is_active'):
            if key in event:
                event[key] = event[key] is True
        if 'position' in event:
            position = _object(event['position'])
            clean = {}
            for key in ('position_ms', 'timestamp_ms', 'speed'):
                value = position.get(key)
                try:
                    valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
                except OverflowError:
                    valid = False
                if valid:
                    clean[key] = value
            event['position'] = clean
        if 'volume' in event:
            volume = event['volume']
            try:
                valid = not isinstance(volume, bool) and isinstance(volume, (int, float)) and math.isfinite(volume)
            except OverflowError:
                valid = False
            event['volume'] = max(0, min(100, volume)) if valid else 0
        for key in ('previous', 'upcoming'):
            if key in event:
                entries = event[key]
                event[key] = [dict(entry, item=_object(entry.get('item'))) for entry in entries[:80] if isinstance(entry, dict)] if isinstance(entries, list) else []
        return event

    def emit(self, event, generation=None, stop=None):
        if not isinstance(event, dict) or not isinstance(event.get('type'), str):
            return
        event = self._clean_event(event)
        with self.lock:
            if generation is not None and not self._current(generation, stop):
                return
            kind = event.get('type')
            update = {k:v for k,v in event.items() if k not in ('type', '_generation', '_epoch')}
            if kind == 'playback_state':
                # Queue snapshots arrive separately; retain them across refreshes.
                state = {k: v for k, v in self.state.items() if k in ('logged_in', 'device_name', 'previous', 'upcoming')}
                state.update(update)
                self.state = state
            elif kind in self.UPDATE_TYPES:
                if kind == 'queue_changed':
                    update = {k: v[:80] for k, v in update.items() if k in ('previous', 'upcoming') and isinstance(v, list)}
                self.state = dict(self.state, **update)
                if kind == 'auth_state' and not event.get('logged_in'):
                    self.state = {k: event[k] for k in ('logged_in', 'is_active', 'device_name') if k in event}
            notification = self._notification(event)
        self.notify(notification)

    def start(self, endpoint=''):
        endpoint = local_endpoint(endpoint or discover_endpoint())
        if websocket is None:
            raise SoloistError('Abhängigkeit fehlt: python3-websocket installieren.')
        with self.lifecycle_lock:
            self.stop()
            with self.lock:
                generation = self.generation
                stop = self.stop_event = threading.Event()
                outgoing = self.outgoing = queue.Queue(maxsize=64)
                self.thread = threading.Thread(target=self.run, args=(endpoint, generation, stop, outgoing), daemon=True)
                self.thread.start()

    def run(self, endpoint, generation, stop, outgoing):
        while not stop.is_set():
            ws = None
            message = 'Soloist getrennt · erneuter Versuch in 3 Sekunden.'
            try:
                ws = websocket.create_connection(endpoint, timeout=2, http_no_proxy=['localhost', '127.0.0.1', '::1'], enable_multithread=True)
                ws.settimeout(.25)
                with self.lock:
                    if not self._current(generation, stop):
                        return
                    self.socket = ws
                    self.connected = True
                    self.epoch += 1
                    notification = self._notification({'type':'connection', 'connected':True})
                self.notify(notification)
                while not stop.is_set():
                    # A busy caller must not starve incoming state events.
                    for _ in range(8):
                        if stop.is_set():
                            break
                        try:
                            command = outgoing.get_nowait()
                        except queue.Empty:
                            break
                        ws.send(json.dumps(command, allow_nan=False))
                    try:
                        raw = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        continue
                    if not raw:
                        break
                    if len(raw) > 1024 * 1024:
                        raise SoloistError('Soloist-Nachricht überschreitet 1 MiB.')
                    try:
                        event = json.loads(raw)
                    except (ValueError, TypeError):
                        self.emit({'type':'error', 'message':'Soloist hat eine ungültige JSON-Nachricht gesendet.'}, generation, stop)
                        continue
                    self.emit(event, generation, stop)
            except Exception:
                message = 'Soloist nicht erreichbar · erneuter Versuch in 3 Sekunden.'
            finally:
                if ws:
                    self._close(ws)
                with self.lock:
                    if self._current(generation, stop):
                        self.socket = None
                        self.connected = False
                        self.state = {}
                        self.epoch += 1
                        notification = self._notification({'type':'connection', 'connected':False, 'message':message})
                    else:
                        notification = None
                    self._clear(outgoing)
                if notification:
                    self.notify(notification)
            if stop.wait(3):
                break

    @staticmethod
    def _clear(outgoing):
        while True:
            try:
                outgoing.get_nowait()
            except queue.Empty:
                return

    @staticmethod
    def _close(ws):
        try:
            ws.close(timeout=0)
        except Exception:
            # An already-disconnected transport must not prevent state cleanup.
            pass

    def command(self, command, **fields):
        if command not in self.QUERY_COMMANDS | self.CONTROL_COMMANDS:
            raise SoloistError('Unbekannter Soloist-Befehl.')
        message = dict(fields, type='command', command=command)
        try:
            json.dumps(message, allow_nan=False)
        except (ValueError, TypeError):
            raise SoloistError('Ungültige Soloist-Befehlsparameter.') from None
        with self.lock:
            if not self.connected:
                raise SoloistError('Soloist ist nicht verbunden.')
            try:
                self.outgoing.put_nowait(message)
            except queue.Full:
                raise SoloistError('Soloist-Befehlswarteschlange voll.') from None

    def stop(self):
        with self.lifecycle_lock:
            with self.lock:
                self.stop_event.set()
                self.generation += 1
                self.epoch += 1
                ws, self.socket = self.socket, None
                self.connected = False
                self.state = {}
                self._clear(self.outgoing)
            if ws:
                self._close(ws)
