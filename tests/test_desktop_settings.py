import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop_settings import (
    DEFAULT_SETTINGS, MAX_PROFILE_BYTES, MAX_PROFILES, ProfileError,
    ProfileStore, autostart_enabled, autostart_path, desktop_exec,
    set_autostart, validate_settings,
)


class DesktopSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {'XDG_CONFIG_HOME': self.temp.name})
        self.environment.start()
        self.store = ProfileStore()

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def test_profile_round_trip_discards_credentials_and_unknown_data(self):
        path = self.store.save('Abend Musik', {'theme': 'Ocean', 'fps': 15,
                                               'access_token': 'private', 'password': 'private',
                                               'nested': {'api_key': 'private'}})
        loaded = self.store.load('Abend Musik')
        self.assertEqual(loaded, DEFAULT_SETTINGS | {'theme': 'Ocean', 'fps': 15})
        self.assertNotIn('private', path.read_text())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.list(), ['Abend Musik'])
        self.store.delete('Abend Musik')
        self.assertEqual(self.store.list(), [])

    def test_bad_profile_name_cannot_escape_directory(self):
        for name in ('../spotify', 'a/b', '.hidden', '---', 'a\nb', 'a' * 65):
            with self.assertRaises(ProfileError):
                self.store.save(name, {})
        self.assertFalse(self.store.directory.exists())

    def test_invalid_settings_do_not_replace_previous_profile(self):
        self.store.save('Work', {'fps': 20})
        for fields in ({'fps': True}, {'fps': 100}, {'fps': 24.5}, {'theme': 'missing'},
                       {'clock': 'true'}, {'density': 1}, {'accent': '#123'},
                       {'smoothing': float('nan')}, {'source': 'a' * 513}, {'glow': float('inf')}):
            with self.assertRaises(ProfileError): self.store.save('Work', fields)
        self.assertEqual(self.store.load('Work')['fps'], 20)

    def test_new_presentation_settings_are_bounded(self):
        fields = {'density': 128, 'backdrop': .5, 'glow': .4, 'opacity': .75,
                  'accent': '#Ab12CD', 'album_colours': True}
        self.store.save('Look', fields)
        loaded = self.store.load('Look')
        self.assertEqual({key: loaded[key] for key in fields}, fields)

    def test_oversized_and_symlink_profiles_are_rejected(self):
        self.store.directory.mkdir(parents=True)
        path = self.store.directory / 'Huge.json'
        path.write_bytes(b' ' * (MAX_PROFILE_BYTES + 1))
        with self.assertRaises(ProfileError): self.store.load('Huge')
        target = Path(self.temp.name) / 'credentials.json'
        target.write_text('{"access_token": "private"}')
        (self.store.directory / 'Link.json').symlink_to(target)
        with self.assertRaises(ProfileError): self.store.load('Link')
        self.assertNotIn('Link', self.store.list())

    def test_unknown_profile_version_is_rejected(self):
        path = self.store.save('Work', {})
        document = json.loads(path.read_text())
        document['version'] = 2
        path.write_text(json.dumps(document))
        with self.assertRaises(ProfileError): self.store.load('Work')

    def test_profile_count_is_bounded_but_existing_profiles_can_be_updated(self):
        for index in range(MAX_PROFILES): self.store.save('P%d' % index, {})
        with self.assertRaises(ProfileError): self.store.save('Extra', {})
        self.store.save('P0', {'fps': 10})
        self.assertEqual(self.store.load('P0')['fps'], 10)

    def test_atomic_failure_preserves_saved_profile(self):
        self.store.save('Work', {'fps': 20})
        with patch('desktop_settings.os.replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError): self.store.save('Work', {'fps': 30})
        self.assertEqual(self.store.load('Work')['fps'], 20)
        self.assertEqual([p.name for p in self.store.directory.iterdir()], ['Work.json'])

    def test_autostart_uses_actual_executable_and_can_be_disabled(self):
        executable = Path(self.temp.name) / 'My Music' / 'run.sh'
        executable.parent.mkdir()
        executable.write_text('#!/bin/sh\nexit 0\n')
        executable.chmod(0o700)
        self.assertFalse(autostart_enabled())
        path = set_autostart(True, executable)
        self.assertEqual(path, autostart_path())
        self.assertIn('Exec="' + str(executable) + '"\n', path.read_text())
        self.assertTrue(autostart_enabled())
        set_autostart(False)
        self.assertFalse(path.exists())
        self.assertFalse(autostart_enabled())

    def test_exec_path_escapes_field_codes_and_quoted_special_characters(self):
        self.assertEqual(desktop_exec('/tmp/50% Music/run.sh'), '"/tmp/50%% Music/run.sh"')
        self.assertEqual(desktop_exec('/tmp/a"b'), '"/tmp/a' + '\\' * 2 + '"b"')
        self.assertEqual(desktop_exec('/tmp/a\\b'), '"/tmp/a' + '\\' * 4 + 'b"')
        with self.assertRaises(ValueError): desktop_exec('/tmp/a\nb')

    def test_missing_executable_does_not_create_autostart_entry(self):
        with self.assertRaises(ValueError): set_autostart(True, Path(self.temp.name) / 'missing')
        self.assertFalse(autostart_path().exists())

    def test_settings_input_must_be_a_mapping(self):
        with self.assertRaises(ProfileError): validate_settings(['bad'])
