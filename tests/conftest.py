"""Use one canonical src.* package identity in tests and production."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
