"""Authored humanoid animation catalogue and bundled source metadata."""
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parent
ANIMATIONS=json.loads((ROOT/'animations/library.json').read_text(encoding='utf-8'))
BY_ID={a['id']:a for a in ANIMATIONS}
