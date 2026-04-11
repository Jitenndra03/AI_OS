"""conftest.py — Pytest configuration shared by all tests.

Adds the src/ directory to sys.path so that test files can import project
packages (monitor, ai, controller, etc.) without requiring pip install.
"""

import sys
import os

# Insert src/ at the front of the import path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
