"""AI services package.

Adds the ai_services directory to sys.path so that part1–part5 modules
can import each other with bare ``from part1_groq_client import …``
statements (they were designed to be independently runnable scripts).
"""

import sys
from pathlib import Path

_ai_dir = str(Path(__file__).resolve().parent)
if _ai_dir not in sys.path:
    sys.path.insert(0, _ai_dir)
