#!/bin/sh
# Build a local Debian package without root, fakeroot, or installing anything.
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec /usr/bin/python3 - "$project_dir" "$@" <<'PY'
import argparse
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

project = Path(__import__('sys').argv[1])
parser = argparse.ArgumentParser(description='Build the Sonora 0.7.0 architecture-all Debian package as the current user.')
parser.add_argument('output', nargs='?', default=str(project.parent / 'sonora_0.7.0_all.deb'), help='output .deb path')
arguments = parser.parse_args(__import__('sys').argv[2:])
output = Path(arguments.output).absolute()
if shutil.which('dpkg-deb') is None:
    parser.exit(1, 'dpkg-deb is required (Debian package: dpkg).\n')
modules = ('sonora.py', 'soloist_backend.py', 'soloist_setup.py', 'interface_language.py', 'english.json', 'lyrics.py', 'desktop_settings.py', 'audio_engine.py', 'desktop_widget.py', 'scene_media.py', 'scene_windows.py', 'visualizer_panel.py')
required = modules + ('run.sh', 'README.md')
for name in required:
    if not (project / name).is_file():
        parser.exit(1, 'Missing source file: ' + str(project / name) + '\n')
output.parent.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='sonora-deb-') as temporary:
    root = Path(temporary) / 'sonora'
    library = root / 'usr/lib/sonora'
    binary = root / 'usr/bin'
    applications = root / 'usr/share/applications'
    documentation = root / 'usr/share/doc/sonora'
    control = root / 'DEBIAN'
    for directory in (library, binary, applications, documentation, control):
        directory.mkdir(parents=True, exist_ok=True)
    for name in modules:
        shutil.copyfile(project / name, library / name)
        (library / name).chmod(0o644)
    shutil.copyfile(project / 'run.sh', library / 'run.sh')
    (library / 'run.sh').chmod(0o755)
    (binary / 'sonora').write_text('#!/bin/sh\nexec /usr/lib/sonora/run.sh "$@"\n', encoding='utf-8')
    (binary / 'sonora').chmod(0o755)
    (applications / 'sonora.desktop').write_text(
        '[Desktop Entry]\nType=Application\nName=Sonora\n'
        'Comment=Music visualizer with MPRIS and Soloist controls\n'
        'Exec=sonora\nIcon=audio-x-generic\nTerminal=false\n'
        'Categories=AudioVideo;Audio;Player;\nStartupWMClass=sonora.py\n',
        encoding='utf-8',
    )
    shutil.copyfile(project / 'README.md', documentation / 'README.md')
    for name in ('WEYV-VERGLEICH.md', 'CHANGELOG.md'):
        shutil.copyfile(project / name, documentation / name)
    shutil.copytree(project / 'docs/images', documentation / 'docs/images')
    # Regular installed files need world-read permission even with a private umask.
    for directory, directories, files in os.walk(root):
        Path(directory).chmod(0o755)
        for name in files:
            path = Path(directory) / name
            if path != binary / 'sonora' and path != library / 'run.sh':
                path.chmod(0o644)
    installed_bytes = sum(path.stat().st_size for path in (root / 'usr').rglob('*') if path.is_file())
    dependencies = ('python3 (>= 3.10), python3-gi, python3-gi-cairo, '
                    'gir1.2-gtk-3.0, gir1.2-gtklayershell-0.1, python3-numpy, pulseaudio-utils, python3-websocket')
    (control / 'control').write_text(
        'Package: sonora\nVersion: 0.7.0\nArchitecture: all\n'
        'Section: sound\nPriority: optional\nMaintainer: Local build\n'
        'Installed-Size: ' + str(math.ceil(installed_bytes / 1024)) + '\n'
        'Depends: ' + dependencies + '\n'
        'Description: Native Linux music visualizer with MPRIS and Soloist controls\n'
        ' A GTK music companion with audio visualization, desktop widgets,\n'
        ' MPRIS controls and a local Spotify Soloist integration.\n'
        ' The optional setup downloads Soloist directly from Spotify.\n',
        encoding='utf-8',
    )
    (control / 'control').chmod(0o644)
    subprocess.run(['dpkg-deb', '--root-owner-group', '--build', str(root), str(output)], check=True)
print('Built: ' + str(output))
PY
