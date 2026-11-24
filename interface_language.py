"""Localize Sonora's display strings while preserving setting IDs and media content."""
import json
from pathlib import Path
import re

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk
from desktop_settings import atomic_write


class InterfaceLanguage:
    def __init__(self, path):
        self.path = path
        try:
            self.english = path.read_bytes() == b'en'
        except OSError:
            self.english = False
        self.catalog = json.loads(Path(__file__).with_name('english.json').read_text())
        self.prefixes = [s for s in self.catalog if s.endswith(' ')]

    def toggle(self):
        atomic_write(self.path, b'de' if self.english else b'en')
        self.english = not self.english

    def text(self, source):
        if not self.english or not source:
            return source
        if source in self.catalog:
            return self.catalog[source]
        for prefix in self.prefixes:
            if source.startswith(prefix):
                return self.catalog[prefix] + source[len(prefix):]
        if re.fullmatch(r'\d+ Strahlen', source):
            return source.split()[0] + ' rays'
        return source

    def message(self, label, source):
        label.sonora_message = source
        label.set_text(self.text(source))

    def apply(self, widget):
        if getattr(widget, 'sonora_verbatim', False):
            return
        # Cache the source caption, never reverse-translate user input or metadata.
        properties = getattr(widget, 'sonora_captions', None)
        if properties is None:
            properties = {}
            for prop in ('label', 'tooltip-text', 'placeholder-text', 'title'):
                if widget.find_property(prop):
                    source = widget.get_property(prop)
                    if isinstance(source, str) and source in self.catalog:
                        properties[prop] = source
            widget.sonora_captions = properties
        for prop, source in properties.items():
            if prop == 'label' and hasattr(widget, 'sonora_message'):
                continue
            widget.set_property(prop, self.text(source))
        if hasattr(widget, 'sonora_message'):
            widget.set_text(self.text(widget.sonora_message))
        if isinstance(widget, Gtk.ComboBoxText):
            model = widget.get_model()
            captions = getattr(widget, 'sonora_choices', None)
            if captions is None:
                captions = widget.sonora_choices = {row[1]:row[0] for row in model if row[0] in self.catalog}
            for row in model:
                if row[1] in captions:
                    translated = self.text(captions[row[1]])
                    if row[0] != translated:
                        row[0] = translated
            return
        if isinstance(widget, Gtk.Notebook):
            for child in widget.get_children():
                tab = widget.get_tab_label(child)
                if tab:
                    self.apply(tab)
        if isinstance(widget, Gtk.Container) and not (isinstance(widget, Gtk.Button) and widget.get_label()):
            for child in widget.get_children():
                self.apply(child)
