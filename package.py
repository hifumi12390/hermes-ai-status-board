"""Build a runtime-only ZIP from an explicit file allowlist. Does not install."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parent
RUNTIME_FILES = [
    'plugin.yaml', '__init__.py', 'README.md', 'LICENSE',
    'ai_status_board/__init__.py', 'ai_status_board/adapters.py',
    'ai_status_board/core.py', 'ai_status_board/history.py',
    'ai_status_board/model.py', 'ai_status_board/transport.py',
    'dashboard/manifest.json', 'dashboard/plugin_api.py', 'desktop/plugin.js',
]


def build():
    destination = ROOT / 'artifacts' / 'ai-status-board-0.2.1.zip'
    destination.parent.mkdir(exist_ok=True)
    with ZipFile(destination, 'w', compression=ZIP_DEFLATED) as archive:
        for name in RUNTIME_FILES:
            archive.write(ROOT / name, name)
    print(destination)


if __name__ == '__main__':
    build()
