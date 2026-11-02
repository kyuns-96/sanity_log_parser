"""Relay human VWP CLI commands through the existing PostgreSQL queues."""
import argparse
import sys
import time

from common.sanity_common import ReviewError
from common.sanity_config import load_environment
from common.sanity_db import Database
from oa.sanity_access import AccessStore, administrator_id
from oa.sanity_cli_review import receive_one
from oa.sanity_production_workspace import ProductionWorkspaceStore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--interval', type=int, default=1)
    args = parser.parse_args(argv)
    if not 1 <= args.interval <= 60:
        parser.error('--interval must be between 1 and 60')
    try:
        admin = administrator_id(load_environment())
        if admin is None:
            raise ReviewError('AUTHORIZATION_NOT_IMPLEMENTED', 'Production authorization is required')
        while True:
            with Database('oa') as db:
                worked = receive_one(db, lambda connection: ProductionWorkspaceStore(
                    connection, AccessStore(connection, admin)))
            if args.once:
                return 0
            if not worked:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        return 130
    except ReviewError as error:
        print('code='+error.code, file=sys.stderr)
    except Exception:
        print('code=CLI_REVIEW_FAILED', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
