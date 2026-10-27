"""Explicit, non-destructive import of an existing private archive into the DB."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from common.sanity_common import ReviewError
from common.sanity_raw_sources import CHUNK_BYTES, DatabaseUploadStore
from common.sanity_submission import VerifiedRemoteSubmitter, check
from common.sanity_submission_files import directory, load_config, regular, verify_bundle


def import_bundle(config, bundle):
    bundle = directory(bundle)
    info = bundle.stat()
    check(info.st_uid == config['service_uid'] and os.geteuid() == config['service_uid']
          and info.st_mode & 0o077 == 0,'SUBMISSION_STORAGE_NOT_CONFIGURED')
    submission,manifest = verify_bundle(bundle)
    actor = VerifiedRemoteSubmitter(**submission['actor'])
    sid = submission['submission_id']
    storage = DatabaseUploadStore(config)
    with storage.session(sid) as store:
        value = store.initialize(sid,actor,{'submission':submission,'manifest':manifest})
        if store.get('RAW_SUBMISSION','raws.'+sid)['status'] != 'VWP_UPLOADED':
            for index,record in enumerate(manifest['files']):
                if index in value['received_files']:
                    continue
                stream,_ = regular(bundle/'snapshot'/record['path'],record['size_bytes'])
                with stream,store.receiving(sid,actor,index) as (writer,_):
                    for chunk in iter(lambda:stream.read(CHUNK_BYTES),b''):
                        writer.write(chunk)
            # Source changes never replace an accepted UUID or silently alter the raw bytes.
            verify_bundle(bundle)
        result = store.commit(sid,actor)
        return {'submission_id':sid,'delivery_state':result['delivery_state'],
                'files':result['total_files'],'original_archive_removed':False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='/etc/sanity/vwp-bridge-submit.json')
    parser.add_argument('--bundle',type=Path,required=True)
    args = parser.parse_args(argv)
    try:
        from common.sanity_common import canonical_json
        print(canonical_json(import_bundle(load_config(args.config,bridge=True),args.bundle)).decode())
        return 0
    except ReviewError as exc:
        print('code='+exc.code,file=sys.stderr)
    except (OSError,ValueError,TypeError):
        print('code=SUBMISSION_IO_ERROR',file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
