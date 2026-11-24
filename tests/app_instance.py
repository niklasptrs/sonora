"""Optional graphical test: run inside dbus-run-session on a Linux display."""
import os
from pathlib import Path
import subprocess
import tempfile
import time
import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib

root = Path(__file__).resolve().parents[1]
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
def owner():
    try:
        return bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'GetNameOwner', GLib.Variant('(s)', ('app.sonora.Client',)), None, Gio.DBusCallFlags.NONE, 500, None).unpack()[0]
    except GLib.Error:
        return None

if owner():
    raise SystemExit('A Sonora instance is already running on this test bus. Use dbus-run-session.')
with tempfile.TemporaryDirectory(prefix='sonora-instance-') as config:
    env = dict(os.environ, XDG_CONFIG_HOME=config)
    with tempfile.TemporaryFile(mode='w+') as log:
        process = subprocess.Popen([str(root/'run.sh'), '--demo'], env=env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic()+8
            while not owner() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.05)
            identity = owner()
            assert identity, 'Application did not register on the session bus'
            for option in ('--toggle-widget', '--toggle-widget', '--fullscreen', '--fullscreen'):
                subprocess.run([str(root/'run.sh'), option], env=env, check=True, timeout=8, stdout=log, stderr=log)
                assert process.poll() is None, 'Primary application exited unexpectedly'
                assert owner() == identity, 'A command replaced the primary instance'
            subprocess.run([str(root/'run.sh'), '--quit'], env=env, check=True, timeout=8, stdout=log, stderr=log)
            assert process.wait(timeout=8) == 0
            print('PASS: single instance, remote widget/fullscreen commands and graceful quit')
        except BaseException:
            log.seek(0)
            print(log.read())
            raise
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
