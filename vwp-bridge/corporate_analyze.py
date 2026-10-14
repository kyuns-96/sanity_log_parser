#!/usr/bin/env python3
"""Company parser entry point; uses the existing VWP CLI and storage contracts."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sanity_parsers import ParserError
from main import main


def corporate_main(argv=None):
    try:
        from site_integration.corporate_parsers import create_parser_registry
        registry = create_parser_registry()
    except ParserError as exc:
        print("code=" + exc.code, file=sys.stderr)
        return 2
    except Exception:
        print("code=PARSER_FAILED", file=sys.stderr)
        return 2
    return main(argv, parser_registry=registry)


if __name__ == "__main__":
    raise SystemExit(corporate_main())
