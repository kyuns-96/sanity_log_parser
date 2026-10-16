#!/usr/bin/env python3
"""Analyze the explicitly synthetic public-reference report profile."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from main import main
from site_integration.public_parsers import create_public_registry

if __name__ == "__main__":
    raise SystemExit(main(parser_registry=create_public_registry()))
