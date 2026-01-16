import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRANSCRIBE_ROOT = ROOT / "transcribe-suite"
SRC = TRANSCRIBE_ROOT / "src"

for path in (ROOT, TRANSCRIBE_ROOT, SRC):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)
