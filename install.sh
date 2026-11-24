#!/bin/sh
set -eu
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec /usr/bin/python3 - "$base" <<'PY'
import os
from pathlib import Path
import shutil
import sys
import tempfile

base = Path(sys.argv[1])
sys.path.insert(0, str(base))
from desktop_settings import desktop_exec
configured = os.environ.get('XDG_DATA_HOME', '')
data = Path(configured) if configured and Path(configured).is_absolute() else Path.home()/'.local/share'
target = data/'sonora'
target.mkdir(parents=True, exist_ok=True)
for name in ('sonora.py', 'soloist_backend.py', 'soloist_setup.py', 'interface_language.py', 'english.json', 'desktop_settings.py', 'lyrics.py', 'audio_engine.py', 'desktop_widget.py', 'scene_media.py', 'scene_windows.py', 'visualizer_panel.py', 'run.sh'):
    source, destination = base/name, target/name
    if source.resolve() != destination.resolve():
        shutil.copy2(source, destination)
applications = data/'applications'
applications.mkdir(parents=True, exist_ok=True)
content = ('[Desktop Entry]\nType=Application\nName=Sonora\n'
           'Comment=Native music visualizer with MPRIS and Soloist controls\n'
           'Exec='+desktop_exec(target/'run.sh')+'\n'
           'Icon=audio-x-generic\nTerminal=false\n'
           'Categories=AudioVideo;Audio;Player;\nStartupWMClass=sonora.py\n')
fd, temporary = tempfile.mkstemp(prefix='.sonora-', dir=applications)
try:
    with os.fdopen(fd, 'w') as stream:
        stream.write(content)
    os.replace(temporary, applications/'sonora.desktop')
except BaseException:
    Path(temporary).unlink(missing_ok=True)
    raise
print('Installiert: '+str(target))
PY
