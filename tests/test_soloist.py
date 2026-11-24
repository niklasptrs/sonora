import json
from pathlib import Path
import queue
import sys
import threading
import types
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from soloist_backend import Soloist, SoloistError, entity_info, local_endpoint


class SoloistTests(unittest.TestCase):
    def test_only_local_api(self):
        for url in ('ws://127.0.0.1:9090','ws://localhost:9090','ws://[::1]:9090'):
            self.assertEqual(local_endpoint(url),url)
        for url in ('ws://example.org:9090','ws://127.0.0.1:99999','http://127.0.0.1:9090','ws://user@localhost:9090','ws://localhost:9090/foo'):
            with self.assertRaises(SoloistError): local_endpoint(url)

    def test_entity_decorations(self):
        item = {'decorations':{'identity':{'name':'Song'},'creators':[{'entity':{'decorations':{'identity':{'name':'Artist'}}}}], 'visual_identity':{'cover':[{'url':'https://example.org/cover','size':'default'}]}, 'playback':{'duration_ms':123}}}
        self.assertEqual(entity_info(item), {'title':'Song','artist':'Artist','cover':'https://example.org/cover','duration_ms':123})

    def test_state_events_and_logout_clear_metadata(self):
        client = Soloist(lambda _: None)
        client.connected = True
        client.emit({'type':'auth_state','logged_in':True})
        client.emit({'type':'playback_state','status':'playing','item':{'uri':'spotify:track:x'}})
        self.assertTrue(client.ready)
        client.emit({'type':'playback_changed','status':'paused'})
        self.assertEqual(client.state['status'],'paused')
        client.emit({'type':'auth_state','logged_in':False})
        self.assertFalse(client.ready)
        self.assertNotIn('item',client.state)

    def test_commands_are_queued_and_enveloped(self):
        client = Soloist(lambda _: None)
        with self.assertRaises(SoloistError): client.command('pause')
        client.connected = True
        client.command('play', uri='spotify:track:x')
        self.assertEqual(client.outgoing.get_nowait(), {'type':'command','command':'play','uri':'spotify:track:x'})

    def test_worker_receives_events_and_dispatches_controls(self):
        auth = threading.Event()
        sent = threading.Event()
        class Timeout(Exception): pass
        class Socket:
            def __init__(self):
                self.events = queue.Queue()
                self.events.put(json.dumps({'type':'auth_state','logged_in':True}))
                self.messages = []
            def settimeout(self, value): pass
            def recv(self):
                try: return self.events.get(timeout=.01)
                except queue.Empty: raise Timeout()
            def send(self, raw):
                self.messages.append(json.loads(raw))
                sent.set()
            def close(self, timeout=0): pass
        ws = Socket()
        fake = types.SimpleNamespace(create_connection=lambda *a, **kw: ws, WebSocketTimeoutException=Timeout)
        def notify(event):
            if event.get('type') == 'auth_state': auth.set()
        client = Soloist(notify)
        with patch('soloist_backend.websocket', fake):
            client.start('ws://127.0.0.1:9090')
            try:
                self.assertTrue(auth.wait(2))
                client.command('seek',position_ms=15000)
                self.assertTrue(sent.wait(2))
                self.assertEqual(ws.messages[0], {'type':'command','command':'seek','position_ms':15000})
            finally: client.stop()
        self.assertFalse(client.connected)

    def test_invalid_ipv6_is_reported_as_user_error(self):
        with self.assertRaises(SoloistError):
            local_endpoint('ws://[::1:9090')

    def test_partial_or_malformed_entity_is_safe(self):
        self.assertEqual(entity_info(None)['duration_ms'], 0)
        item = {'decorations':{'identity':{'name':42}, 'creators':[None, {'entity':{'decorations':{'identity':{'name':12}}}}], 'visual_identity':{'cover':[None, {'url':False}]}, 'playback':{'duration_ms':float('nan')}}}
        self.assertEqual(entity_info(item), {'title':'Keine Wiedergabe', 'artist':'', 'cover':'', 'duration_ms':0})

    def test_state_snapshot_does_not_mutate_previous_reader_snapshot(self):
        client = Soloist(lambda _: None)
        client.emit({'type':'playback_state','status':'playing'})
        before = client.state
        client.emit({'type':'playback_changed','status':'paused'})
        self.assertEqual(before['status'], 'playing')
        self.assertEqual(client.state['status'], 'paused')

    def test_stop_invalidates_queued_gui_events_and_commands(self):
        events = []
        client = Soloist(events.append)
        client.connected = True
        client.emit({'type':'auth_state','logged_in':True})
        client.command('pause')
        self.assertTrue(client.is_current_event(events[0]))
        client.stop()
        self.assertFalse(client.is_current_event(events[0]))
        self.assertTrue(client.outgoing.empty())
        self.assertFalse(client.ready)

    def test_unknown_and_unserializable_commands_fail_before_enqueue(self):
        client = Soloist(lambda _: None)
        client.connected = True
        for command, fields in [('unknown', {}), ('set_volume', {'volume':float('nan')}), ('play', {'uri':object()})]:
            with self.assertRaises(SoloistError): client.command(command, **fields)
        self.assertTrue(client.outgoing.empty())

    def test_failed_socket_close_cannot_prevent_stop(self):
        client = Soloist(lambda _: None)
        client.socket = types.SimpleNamespace(close=lambda **_: (_ for _ in ()).throw(OSError('closed')))
        client.connected = True
        client.stop()
        self.assertFalse(client.connected)
        self.assertIsNone(client.socket)

    def test_stop_while_connecting_cannot_resurrect_old_session(self):
        entered = threading.Event()
        released = threading.Event()
        closed = threading.Event()
        notified = []
        class Socket:
            def settimeout(self, _): pass
            def close(self, timeout=0): closed.set()
        def connect(*args, **kwargs):
            entered.set()
            released.wait(2)
            return Socket()
        fake = types.SimpleNamespace(create_connection=connect, WebSocketTimeoutException=TimeoutError)
        client = Soloist(notified.append)
        with patch('soloist_backend.websocket', fake):
            client.start('ws://127.0.0.1:9090')
            self.assertTrue(entered.wait(2))
            thread = client.thread
            client.stop()
            released.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(closed.is_set())
        self.assertFalse(client.connected)
        self.assertEqual(notified, [])

    def test_queue_snapshots_are_bounded_and_retained_on_playback_refresh(self):
        client = Soloist(lambda _: None)
        client.emit({'type':'queue_changed', 'upcoming':[{'item':{}} for _ in range(100)]})
        client.emit({'type':'playback_state', 'status':'paused'})
        self.assertEqual(len(client.state['upcoming']), 80)

    def test_real_websocket_loopback_handshake_and_command(self):
        # Optional dependency: exercise the actual client over a local RFC 6455
        # connection; this is a protocol test and never contacts Spotify.
        import base64
        import hashlib
        import socketserver
        import struct
        from soloist_backend import websocket as transport
        if transport is None:
            self.skipTest('python3-websocket is not installed')
        received = []
        errors = []
        authenticated = threading.Event()
        dispatched = threading.Event()
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                try:
                    self.request.settimeout(2)
                    self.rfile.readline()
                    headers = {}
                    while True:
                        line = self.rfile.readline()
                        if line in (b'\r\n', b''):
                            break
                        key, value = line.decode().split(':', 1)
                        headers[key.lower()] = value.strip()
                    accept = base64.b64encode(hashlib.sha1((headers['sec-websocket-key']+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
                    self.request.sendall(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: '+accept+b'\r\n\r\n')
                    raw = json.dumps({'type':'auth_state','logged_in':True}).encode()
                    self.request.sendall(bytes((0x81,len(raw)))+raw)
                    prefix = self.rfile.read(2)
                    length = prefix[1] & 127
                    if length == 126:
                        length = struct.unpack('!H', self.rfile.read(2))[0]
                    elif length == 127:
                        length = struct.unpack('!Q', self.rfile.read(8))[0]
                    mask = self.rfile.read(4)
                    body = self.rfile.read(length)
                    received.append(json.loads(bytes(value ^ mask[i%4] for i,value in enumerate(body))))
                    dispatched.set()
                except Exception as exc:
                    errors.append(exc)
                    dispatched.set()
        def notify(event):
            if event.get('type') == 'auth_state':
                authenticated.set()
        with socketserver.TCPServer(('127.0.0.1', 0), Handler) as server:
            server.timeout = 2
            worker = threading.Thread(target=server.handle_request, daemon=True)
            worker.start()
            client = Soloist(notify)
            client.start('ws://127.0.0.1:%d' % server.server_address[1])
            try:
                self.assertTrue(authenticated.wait(2))
                client.command('seek', position_ms=12345)
                self.assertTrue(dispatched.wait(2))
            finally:
                client.stop()
                client.thread.join(2)
                worker.join(2)
        self.assertEqual(errors, [])
        self.assertEqual(received, [{'type':'command', 'command':'seek', 'position_ms':12345}])

    def test_malformed_optional_state_fields_are_safe_for_gui(self):
        events = []
        client = Soloist(events.append)
        client.emit({'type':'playback_state', 'item':False, 'position':[1], 'volume':'loud'})
        self.assertEqual(client.state['item'], {})
        self.assertEqual(client.state['position'], {})
        self.assertEqual(client.state['volume'], 0)
        client.emit({'type':'queue_changed', 'upcoming':[False, {'item':'invalid'}]})
        self.assertEqual(events[-1]['upcoming'], [{'item':{}}])
        client.emit({'type':[]})
        self.assertEqual(len(events), 2)
