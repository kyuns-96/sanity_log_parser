"""Start the VWP upload API and its lifespan-owned DB processing together."""
from __future__ import annotations

import argparse

import uvicorn


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1', help='Bind address (default: 127.0.0.1)')
    parser.add_argument('--port', type=int, default=8000, help='HTTP port (default: 8000)')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    uvicorn.run('area_power.vwp_server.app:create_app', factory=True, host=args.host, port=args.port)


if __name__ == '__main__':
    main()
