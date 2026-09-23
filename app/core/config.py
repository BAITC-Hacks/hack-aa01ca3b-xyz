"""Canonical paths for runtime data and local model files."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
SAMPLES_DIR = DATA_DIR / "samples"
TEMPLATES_DIR = DATA_DIR / "templates"
FONTS_DIR = TEMPLATES_DIR / "fonts"
MODELS_DIR = PROJECT_ROOT / "models"
