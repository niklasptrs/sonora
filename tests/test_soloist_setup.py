"""Exercise setup failures and reuse without a network or a Spotify account."""
import io
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from soloist_setup import SoloistSetup, SoloistError


class SetupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        env = patch.dict(os.environ, XDG_DATA_HOME=directory.name, STATE_DIRECTORY=directory.name)
        env.start()
        self.addCleanup(env.stop)
        self.setup = SoloistSetup()
        self.addCleanup(self.setup.close)

    def test_existing_listener_is_reused_and_never_owned(self):
        with patch.object(self.setup, 'install', return_value=Path('/bin/true')), \
             patch.object(self.setup, 'endpoint', return_value='ws://127.0.0.1:3456'), \
             patch('soloist_setup.subprocess.Popen') as launch:
            self.assertEqual(self.setup.start(''), 'ws://127.0.0.1:3456')
            self.setup.close()
            launch.assert_not_called()

    def test_bad_archive_preserves_previous_build(self):
        self.setup.binary.parent.mkdir(parents=True)
        self.setup.binary.write_bytes(b'previous')
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w:gz') as package:
            item = tarfile.TarInfo('soloist')
            item.type = tarfile.SYMTYPE
            item.linkname = '/bin/true'
            package.addfile(item)
        archive.seek(0)
        with patch.object(self.setup, 'usable', return_value=False), \
             patch('soloist_setup.platform.machine', return_value='aarch64'), \
             patch('soloist_setup.urlopen', return_value=archive) as fetch:
            with self.assertRaises(SoloistError):
                self.setup.install()
            self.assertIn('release_arm64.tar.gz', fetch.call_args.args[0])
        self.assertEqual(self.setup.binary.read_bytes(), b'previous')
