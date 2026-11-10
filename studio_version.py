"""Application version shared by Studio, its local API and MCP bridge."""
import json
from pathlib import Path

VERSION = json.loads(Path(__file__).with_name('package.json').read_text(encoding='utf-8'))['version']
