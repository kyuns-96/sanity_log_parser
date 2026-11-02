"""Paged human classification and judgment in VWP. No DB or model credentials."""
from __future__ import annotations

import argparse
import fcntl
import getpass
import json
import os
from pathlib import Path
import shlex
import stat
import sys
import time
import unicodedata
from uuid import uuid4

from common.sanity_cli_review import PROTOCOL, SESSION_SECONDS, alive, identifier, signature, signing_key, validate_envelope
from common.sanity_common import ReviewError, canonical_json
from common.sanity_submission import check
from common.sanity_submission_files import directory, load_config, regular, write_json_file


def read_text(path, maximum=256*1024):
    stream, _ = regular(path, maximum)
    with stream:
        raw = stream.read(maximum+1)
    check(len(raw) <= maximum, 'CLI_REVIEW_LIMIT')
    return raw.decode('utf-8')


class LocalSession:
    def __init__(self, root):
        self.root = directory(root, create=True)
        info = self.root.stat()
        check(info.st_uid == os.geteuid() and not info.st_mode & 0o077, 'CLI_REVIEW_UNSAFE_STATE')
        self.path = self.root/'session.json'

    def __enter__(self):
        descriptor = os.open(self.root/'lock', os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW, 0o600)
        self.lock = os.fdopen(descriptor, 'r+b')
        info = os.fstat(descriptor)
        try:
            check(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
                  and not info.st_mode & 0o077, 'CLI_REVIEW_UNSAFE_STATE')
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                check(False, 'CLI_REVIEW_BUSY')
            self.value = None
            if self.path.exists() or self.path.is_symlink():
                stream, info = regular(self.path, 1024*1024)
                with stream:
                    check(info.st_uid == os.geteuid() and not info.st_mode & 0o077, 'CLI_REVIEW_UNSAFE_STATE')
                    from common.sanity_common import _decode_json
                    self.value = _decode_json(stream.read(1024*1024+1))
            return self
        except BaseException:
            self.lock.close()
            raise

    def save(self):
        write_json_file(self.path, self.value)

    def __exit__(self, *args):
        self.lock.close()


class ReviewClient:
    def __init__(self, local, config, transport):
        self.local, self.config, self.transport = local, config, transport

    @property
    def state(self):
        value = self.local.value
        check(isinstance(value, dict) and value.get('schema_version') == 1, 'CLI_REVIEW_CONNECT_REQUIRED')
        check(value['endpoint'] == self.config['endpoint'], 'CLI_REVIEW_CONFIG_CHANGED')
        alive(value['expires_at'])
        return value

    def connect(self, sid, expires_at, secret):
        identifier(sid)
        alive(expires_at)
        check(expires_at <= time.time()+SESSION_SECONDS+60, 'INVALID_REQUEST')
        check(not self.local.value or not self.local.value.get('pending'), 'CLI_REVIEW_PENDING')
        self.local.value = {'schema_version': 1, 'session_id': sid, 'expires_at': expires_at,
            'key': signing_key(secret), 'endpoint': self.config['endpoint'], 'revision': None, 'groups': {}, 'pending': None}
        self.local.save()
        return self.send('STATUS', {})

    def send(self, action, args, *, wait_seconds=30):
        state = self.state
        check(not state.get('pending'), 'CLI_REVIEW_PENDING')
        envelope = {'schema_version': 1, 'protocol': PROTOCOL, 'session_id': state['session_id'],
            'request_id': str(uuid4()), 'expires_at': state['expires_at'], 'command': {'action': action, 'args': args}}
        envelope['signature'] = signature(state['key'], envelope)
        validate_envelope(envelope)
        state['pending'] = envelope
        self.local.save()  # Persist UUID and exact content before the first network write.
        return self.resume(wait_seconds=wait_seconds)

    def resume(self, *, wait_seconds=30):
        state = self.state
        envelope = state.get('pending')
        check(isinstance(envelope, dict), 'CLI_REVIEW_NO_PENDING_REQUEST')
        deadline = time.monotonic()+wait_seconds
        value = self.transport.request(envelope)
        while value['state'] == 'PENDING':
            if time.monotonic() >= deadline:
                raise ReviewError('CLI_REVIEW_PENDING', 'Run sanity review resume')
            time.sleep(min(1, max(0, deadline-time.monotonic())))
            value = self.transport.request(envelope, poll=True)
        state['pending'] = None
        if value['state'] == 'ERROR':
            self.local.save()
            raise ReviewError(value['code'], 'Review command rejected')
        result = value['result']
        action = envelope['command']['action']
        if action in ('STATUS', 'EDIT', 'RATIONALE', 'CONFIRM'):
            if state['revision'] != result['revision']:
                state['groups'] = {}
            state['revision'] = result['revision']
        elif action == 'GROUP':
            # Only the active group needs a write precondition. Keeping every
            # viewed group makes the credential file grow with report size.
            state['groups'] = {result['group_id']: {'revision': result['review']['revision'],
                'cluster_revision': result['cluster_revision'], 'evidence': result['evidence']}}
        elif action in ('JUDGE', 'EVIDENCE'):
            state['groups'].pop(envelope['command']['args']['group_id'], None)
        if action == 'CLOSE':
            self.local.path.unlink()
            self.local.value = None
        else:
            self.local.save()
        return result

    def pinned(self):
        revision = self.state.get('revision')
        check(type(revision) is int, 'CLI_REVIEW_REFRESH_REQUIRED')
        return revision


def parser():
    result = argparse.ArgumentParser(description=__doc__, prog='sanity review')
    result.add_argument('--config', default='/etc/sanity/vwp-submit.json')
    state = Path(os.environ.get('XDG_STATE_HOME') or Path.home()/'.local/state')/'sanity/review'
    result.add_argument('--state', type=Path, default=state, help='private local session directory')
    result.add_argument('--json', action='store_true', help='structured output for scripts')
    sub = result.add_subparsers(dest='command', required=True)
    connect = sub.add_parser('connect', help='connect using an OA report session; key is prompted privately')
    connect.add_argument('session_id')
    connect.add_argument('--expires-at', type=int, required=True)
    for name, help_text in (('refresh', 'reload the current partition and revision'), ('resume', 'resume the same saved request'),
                            ('close', 'revoke this CLI session'), ('shell', 'interactive review without a browser')):
        sub.add_parser(name, help=help_text)
    groups = sub.add_parser('groups', help='paged groups; filter unreviewed decisions or unresolved rationales')
    groups.add_argument('--status', choices=('UNREVIEWED', 'PENDING', 'PASS', 'FAIL', 'DEFER', 'STALE'), default='')
    groups.add_argument('--rationale-status', choices=('UNRESOLVED', 'MISSING', 'DRAFT', 'CONFIRMED', 'STALE'), default='')
    records = sub.add_parser('records', help='paged logs, including multiline previews and source locations')
    records.add_argument('--group', dest='group_id')
    for command in (groups, records):
        command.add_argument('--search', default='')
    history = sub.add_parser('history', help='paged immutable classification history')
    for command in (groups, records, history):
        command.add_argument('--offset', type=int, default=0)
        command.add_argument('--limit', type=int, choices=range(1, 101), default=25, metavar='1..100')
    record = sub.add_parser('record', help='read the complete stored log in explicit character ranges')
    record.add_argument('record_id')
    record.add_argument('--offset', type=int, default=0)
    record.add_argument('--limit', type=int, default=16384)
    group = sub.add_parser('group', help='read rationale, AI proposal, current judgment and evidence choices')
    group.add_argument('group_id')
    for name in ('rename', 'move', 'split', 'merge', 'rationale', 'accept', 'confirm', 'judge'):
        command = sub.add_parser(name)
        if name in ('rename', 'rationale', 'accept', 'judge'):
            command.add_argument('group_id')
        if name in ('rename', 'split'):
            command.add_argument('--label', required=True)
        if name in ('move', 'split'):
            selection = command.add_mutually_exclusive_group(required=True)
            selection.add_argument('--record', dest='record_ids', action='append')
            selection.add_argument('--records-file', type=Path, help='one explicit record ID per line')
        if name in ('move', 'merge'):
            command.add_argument('--to', required=True)
        if name == 'merge':
            command.add_argument('--group', dest='group_ids', action='append', required=True)
        if name == 'rationale':
            command.add_argument('--text-file', type=Path, required=True)
            command.add_argument('--evidence', action='append', required=True)
            command.add_argument('--summary')
        if name == 'judge':
            command.add_argument('conclusion', choices=('PASS', 'FAIL', 'DEFER'))
            command.add_argument('--exception-file', type=Path)
            command.add_argument('--proposal-id', help='bind the human result to the displayed AI proposal UUID')
        reason = command.add_mutually_exclusive_group(required=True)
        reason.add_argument('--reason')
        reason.add_argument('--reason-file', type=Path)
    evidence = sub.add_parser('evidence', help='explicitly enable/disable a group rationale as shared AI knowledge')
    evidence.add_argument('group_id')
    evidence.add_argument('stage', choices=('CLUSTER', 'REVIEW'))
    evidence.add_argument('enabled', choices=('on', 'off'))
    return result


def command(client, args):
    name = args.command
    if name == 'connect':
        return client.connect(args.session_id, args.expires_at, getpass.getpass('CLI 연결키: '))
    if name == 'resume':
        return client.resume()
    if name in ('refresh', 'close'):
        return client.send('STATUS' if name == 'refresh' else 'CLOSE', {})
    revision = client.pinned()
    body = {'expected_revision': revision}
    if name in ('groups', 'records', 'history'):
        body.update(offset=args.offset, limit=args.limit)
        if name == 'history':
            body.pop('expected_revision')
        else:
            body['search'] = args.search
        if name == 'groups':
            body.update(status=args.status, rationale_status=args.rationale_status)
        if name == 'records':
            body['group_id'] = args.group_id
        return client.send(name.upper(), body)
    if name in ('group', 'record'):
        body.update({'group_id': args.group_id} if name == 'group' else
                    {'record_id': args.record_id, 'offset': args.offset, 'limit': args.limit})
        return client.send(name.upper(), body)
    if name in ('judge', 'evidence'):
        viewed = client.state['groups'].get(args.group_id)
        check(viewed is not None and viewed['cluster_revision'] == revision, 'CLI_REVIEW_GROUP_REQUIRED')
        if name == 'evidence':
            choice = viewed['evidence'].get(args.stage)
            check(choice is not None, 'AI_EVIDENCE_NOT_READY')
            return client.send('EVIDENCE', {'group_id': args.group_id, 'stage': args.stage,
                'enabled': args.enabled == 'on', 'expected_revision': choice['revision'],
                'binding_sha256': choice['binding_sha256']})
    reason = read_text(args.reason_file, 8192) if args.reason_file else args.reason
    body['reason'] = reason
    if name in ('rename', 'move', 'split', 'merge'):
        operation = {'kind': 'RENAME' if name == 'rename' else 'MERGE' if name == 'merge' else 'MOVE'}
        if name == 'rename':
            operation.update(destination_id=args.group_id, label=args.label)
        elif name == 'merge':
            operation.update(group_ids=args.group_ids, destination_id=args.to)
        else:
            ids = read_text(args.records_file).splitlines() if args.records_file else args.record_ids
            check(ids and all(key and key == key.strip() for key in ids) and len(ids) == len(set(ids)), 'INVALID_PARTITION')
            operation['record_ids'] = ids
            operation.update({'new_group_id': 'cli-'+str(uuid4()), 'label': args.label} if name == 'split'
                             else {'destination_id': args.to})
        body['operation'] = operation
        return client.send('EDIT', body)
    if name in ('rationale', 'accept'):
        body.update(group_id=args.group_id, action='ACCEPT' if name == 'accept' else 'SAVE')
        if name == 'rationale':
            body['content'] = {'reason': read_text(args.text_file, 8192), 'evidence_ids': args.evidence, 'reference_groups': []}
            if args.summary is not None:
                body['content']['summary'] = args.summary
        return client.send('RATIONALE', body)
    if name == 'judge':
        body.update(group_id=args.group_id, conclusion=args.conclusion, expected_revision=viewed['revision'],
                    expected_cluster_revision=revision)
        if args.proposal_id:
            body['proposal_id'] = identifier(args.proposal_id)
        if args.exception_file:
            from common.sanity_common import _decode_json
            body['exception'] = _decode_json(read_text(args.exception_file, 16384).encode('utf-8'))
        return client.send('JUDGE', body)
    return client.send('CONFIRM', body)


def terminal_text(value):
    return ''.join(c if c in '\n\t' or unicodedata.category(c)[0] != 'C' else '\\u%04x' % ord(c) for c in str(value))


def display(value, *, structured=False):
    if structured:
        print(canonical_json(value).decode('utf-8'))
    elif 'items' in value:
        print(f"{value['offset']+1 if value['total'] else 0}–{min(value['offset']+value['limit'], value['total'])} / {value['total']}")
        for row in value['items']:
            print(terminal_text(json.dumps(row, ensure_ascii=False, indent=2)))
        if value['offset']+value['limit'] < value['total']:
            print(f"다음 페이지: --offset {value['offset']+value['limit']}")
    elif 'raw' in value:
        print(terminal_text(json.dumps(value['record'], ensure_ascii=False, indent=2)))
        print(terminal_text(value['raw']))
        end = min(value['offset']+value['limit'], value['total'])
        print(f"원문 문자 {value['offset']}–{end} / {value['total']}")
        if end < value['total']:
            print(f"계속 읽기: record {value['record']['id']} --offset {end}")
    else:
        print(terminal_text(json.dumps(value, ensure_ascii=False, indent=2)))


def shell(client, structured=False):
    print('groups · records --group ID · group ID · help · refresh · resume · exit')
    while True:
        try:
            line = input('review> ').strip()
            if line in ('exit', 'quit'):
                return 0
            if not line:
                continue
            if line == 'help':
                parser().print_help()
                continue
            try:
                args = parser().parse_args(shlex.split(line))
            except SystemExit:
                continue
            check(args.command not in ('shell', 'connect'), 'INVALID_REQUEST')
            display(command(client, args), structured=structured)
            if args.command == 'close':
                return 0
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        except ReviewError as error:
            print('code='+error.code, file=sys.stderr)
        except (OSError, ValueError, TypeError):
            print('code=CLI_REVIEW_IO_ERROR', file=sys.stderr)


def main(argv=None, *, config_path=None):
    args = parser().parse_args(argv)
    try:
        config = load_config(config_path or args.config)
        from vwp.review_https import ReviewTransport
        transport = ReviewTransport(config)
        with LocalSession(args.state) as local:
            client = ReviewClient(local, config, transport)
            if args.command == 'shell':
                client.state
                return shell(client, args.json)
            display(command(client, args), structured=args.json)
        return 0
    except (EOFError, KeyboardInterrupt):
        print('code=CLI_REVIEW_INTERRUPTED', file=sys.stderr)
    except ReviewError as error:
        print('code='+error.code, file=sys.stderr)
        if error.code in ('CLI_REVIEW_PENDING', 'CLI_REVIEW_CONNECTION_FAILED'):
            print('같은 요청 확인: sanity review resume', file=sys.stderr)
    except (OSError, ValueError, TypeError):
        print('code=CLI_REVIEW_IO_ERROR', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
