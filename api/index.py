"""Vercel serverless entry point.

Adds ``src/`` to the Python path so all project imports resolve,
then re-exports the FastAPI ``app`` object for the @vercel/python runtime.
"""

import sys
from pathlib import Path

# Add src/ to Python path so imports like `from config import ...` work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from main import app  # noqa: E402, F401
