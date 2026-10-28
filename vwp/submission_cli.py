"""User submission commands. Standard library only; no DB login on VWP clients."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time
import uuid

from common.sanity_common import ReviewError, digest
from common.sanity_submission import SubmissionInput, VerifiedSubmitter, approved, check, submission_uuid, validate_target
from common.sanity_submission_intake import local_label, pending_plan, tokenless, unverified
from common.sanity_submission_files import (copy_bundle, directory, fsync_dir, load_config, read_json_file,
    remove_own_partial, snapshot, storage, verify_bundle, write_json_file)


class SubmissionClient:
    def __init__(self, config, provider, authority, *, state_root=None, transport=None):
        self.config, self.provider, self.authority = config, provider, authority
        self.transport = transport
        if config.get('transport') == 'https' and transport is None:
            from vwp.submission_https import HttpsTransport
            self.transport = HttpsTransport(config)
        state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
        self.state_root = Path(state_root) if state_root is not None else state / "sanity/submissions"

    def actor_storage(self):
        if tokenless(self.config):
            return local_label(), None, None
        actor = self.authority.current()
        check(isinstance(actor,VerifiedSubmitter) and actor.os_uid==os.geteuid(),'SUBMISSION_IDENTITY_MISMATCH')
        if self.transport is not None:
            return actor, None, None
        root = storage(self.config)
        inbox = directory(root / "inbox" / actor.key)
        receipts = directory(root / "receipts" / actor.key)
        check(inbox.stat().st_uid == actor.os_uid and inbox.stat().st_mode & 0o007 == 0,
              "SUBMISSION_STORAGE_NOT_CONFIGURED")
        owner = self.authority.inbox_owner(inbox)
        check(isinstance(owner,VerifiedSubmitter) and owner==actor,'SUBMISSION_IDENTITY_MISMATCH')
        return actor, inbox, receipts

    def prepare(self, target):
        actor, _, _ = self.actor_storage()
        prepared = self.provider.prepare(dict(target))
        check(isinstance(prepared,SubmissionInput),'INVALID_SUBMISSION_INPUT')
        validate_target(target, prepared.plan)
        if tokenless(self.config):
            prepared = SubmissionInput(pending_plan(prepared.plan),prepared.reports)
            confirmed = False
        else:
            self.authority.authorize(actor, prepared.plan["collection"])
            confirmed = approved(prepared.plan, self.authority)
        expected = {unit["report"] for unit in prepared.plan["inventory"]["units"]}
        from common.sanity_preserved_sources import declared_paths
        preserved = declared_paths(prepared.plan)
        found = set(prepared.reports)
        summary = {"actor": actor.actor, "target": dict(target), "scope_sha256": prepared.plan["approval"]["scope_sha256"]
                   if prepared.plan["approval"] else None, "inventory_confirmed": confirmed,
                   "profile_id":prepared.plan['collection']['profile_id'],
                   "profile_version":prepared.plan['collection']['profile_version'],
                   "pre_revision":prepared.plan['collection']['pre_revision'],
                   "approval_reference":prepared.plan['approval']['reference'] if prepared.plan['approval'] else None,
                   "expected": len(expected), "found": len(found & expected), "missing": len(expected - found),
                   "incomplete":sum(u['applicable'] and u['execution_status']!='COMPLETED' for u in prepared.plan['inventory']['units']),
                   "unexpected": len(found - expected - preserved),
                   "bytes": sum(Path(p).stat().st_size for p in prepared.reports.values()),
                   "units": [{"item": u["item_id"], "mode": u["source"]["mode"], "revision": u["source"]["revision"],
                              "layout": u["source"]["layout"], "eco": u["source"]["eco"],
                              "found":u['report'] in found,'execution_ref':u['source']['execution_ref'],
                              'tool':u['source']['tool'],'tool_version':u['source']['tool_version'],
                              "execution_status": u["execution_status"]} for u in prepared.plan["inventory"]["units"]]}
        if prepared.plan['schema_version'] == 3:
            summary.update(preserved=len(found & preserved), preserved_missing=len(preserved - found),
                           preserved_files=prepared.plan['inventory']['preserved_files'])
        return actor, prepared, summary

    def submit(self, target, *, confirm, preview=print, dry_run=False):
        actor, prepared, summary = self.prepare(target)
        preview(summary)
        if dry_run:
            return {"delivery_state": "DRY_RUN", "summary": summary}
        check(confirm(summary) is True, "SUBMISSION_CANCELLED", "Submission cancelled")
        # Recheck rights after user interaction; inspection is not authorization.
        if not tokenless(self.config):
            self.authority.authorize(actor, prepared.plan["collection"])
        directory(self.state_root, create=True)
        sid = str(uuid.uuid4())
        local = self.state_root / sid
        local.mkdir(mode=0o700)
        bundle = local / "bundle"
        bundle.mkdir(mode=0o700)
        manifest = snapshot(prepared, bundle / "snapshot")
        value = {"schema_version": 2, "submission_id": sid, "actor": actor.actor,
                 "content_sha256": digest(manifest), "plan": prepared.plan}
        write_json_file(bundle / "submission.json", value)
        fsync_dir(local)
        fsync_dir(self.state_root)
        return self.enqueue(sid)

    def enqueue(self, sid):
        sid = submission_uuid(sid)
        actor, inbox, _ = self.actor_storage()
        local = self.state_root / sid / "bundle"
        value, _ = verify_bundle(local)
        check(value["actor"] == actor.actor and value["submission_id"] == sid, "FORBIDDEN")
        if not tokenless(self.config):
            self.authority.authorize(actor, value["plan"]["collection"])
        if self.transport is not None:
            return self.transport.enqueue(local,sid,actor)
        ready, partial = inbox / (sid + ".ready"), inbox / ("." + sid + ".partial")
        if ready.exists():
            existing, _ = verify_bundle(ready)
            check(existing == value, "SUBMISSION_CONFLICT")
        else:
            if partial.exists() or partial.is_symlink():
                remove_own_partial(partial, actor.os_uid)
            copy_bundle(local, partial, mode=0o640)
            check(not ready.exists(), "SUBMISSION_CONFLICT")
            os.rename(partial, ready)
            fsync_dir(inbox)
        return {"submission_id": sid, "content_sha256": value["content_sha256"], "delivery_state": "LOCAL_QUEUED"}

    def status(self, sid):
        sid = submission_uuid(sid)
        actor, inbox, receipts = self.actor_storage()
        if self.transport is not None:
            return self.transport.status(sid,actor)
        path = receipts / (sid + ".json")
        if path.exists() or path.is_symlink():
            check(path.stat().st_uid == self.config["service_uid"], "UNTRUSTED_RECEIPT")
            value = read_json_file(path, 65536)
            check(value.get("submission_id") == sid and value.get("actor_key") == actor.key, "UNTRUSTED_RECEIPT")
            return value
        bundle = inbox / (sid + ".ready")
        if bundle.exists():
            value, _ = verify_bundle(bundle)
            check(value["actor"] == actor.actor, "FORBIDDEN")
            return {"submission_id": sid, "delivery_state": "LOCAL_QUEUED", "verified_oa_state": False}
        check(False, "NOT_FOUND", "Submission is not known to this account")

    def retry(self, sid):
        sid = submission_uuid(sid)
        try:
            value = self.status(sid)
        except ReviewError as exc:
            if exc.code != "NOT_FOUND":
                raise
        else:
            if value["delivery_state"] not in ("LOCAL_QUEUED", "UPLOADING"):
                return value
        return self.enqueue(sid)


def display_preview(value):
    target = value["target"]
    print(('제출자 표시(신원 미확인): '+value['actor']['subject']) if unverified(value['actor'])
          else '계정: '+value['actor']['user_id'])
    print(f"{target['project']} / {target['block']} / {target['net_ver']} / {target['revision']} / {target['layout']}")
    print(f"프로필: {value['profile_id']} @ {value['profile_version']} · 연결 PRE: {value['pre_revision'] or '없음'}")
    print(f"대상 ECO: {target['eco'] or '없음'} · 예상 {value['expected']} · 발견 {value['found']} · 누락 {value['missing']}"
          f" · 실행 미완료 {value['incomplete']} · 예상 밖 {value['unexpected']} · {value['bytes']} bytes")
    if 'preserved_files' in value:
        print(f"보존 근거 파일 {value['preserved']}개 · 보존 파일 누락 {value['preserved_missing']}개")
        for file in value['preserved_files']:
            bind = file['binding']
            print(f"  {file['path']} · {file['role']} · {bind['kind']} "
                  f"{bind.get('review_unit_id', bind.get('anchor_unit_id'))}")
    print("범위 승인: " + ("검증됨" if value["inventory_confirmed"] else "미승인 — 수신 후 검토 차단")
          + ' · 참조: '+(value['approval_reference'] or '없음'))
    if unverified(value['actor']):
        print('제출 토큰 없이 접수합니다. OA에 로그인한 블록 담당자가 검사 범위를 확인한 뒤 공개됩니다.')
    for unit in value["units"]:
        print(f"  {unit['layout']} {unit['revision']} {unit['eco'] or '-'} / {unit['mode']} / {unit['item']}"
              f" [{unit['execution_status']}, {'파일 발견' if unit['found'] else '파일 누락'}]"
              f" · 실행 {unit['execution_ref']} · {unit['tool']} {unit['tool_version']}")


def main(argv=None, *, provider=None, authority=None, state_root=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="/etc/sanity/vwp-submit.json")
    commands = parser.add_subparsers(dest="command", required=True)
    submit = commands.add_parser("submit", help="confirm and enqueue completed VWP reports")
    for key in ("project", "block", "net-ver", "revision"):
        submit.add_argument("--" + key, required=True)
    submit.add_argument("--layout", choices=("pre", "post"), required=True)
    submit.add_argument("--eco")
    submit.add_argument("--profile")
    submit.add_argument("--dry-run", action="store_true")
    status = commands.add_parser("status", help="read your service-generated submission receipt")
    status.add_argument("submission_id")
    status.add_argument("--wait", action="store_true")
    status.add_argument("--timeout", type=int, default=60)
    retry = commands.add_parser("retry", help="resume exactly the same immutable submission")
    retry.add_argument("submission_id")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if provider is None or authority is None:
            try:
                from vwp.integration.corporate_submission import create_submission_adapters
            except ImportError:
                raise ReviewError('SUBMISSION_AUTH_NOT_CONFIGURED','Company submission adapters are not installed') from None
            provider, authority = create_submission_adapters()
        client = SubmissionClient(config, provider, authority, state_root=state_root)
        if args.command == "submit":
            check((args.layout == "post" and args.eco) or (args.layout == "pre" and args.eco is None))
            target = {key: getattr(args, key) for key in ("project", "block", "net_ver", "revision", "eco")}
            target.update(layout=args.layout.upper(), profile=args.profile or config["default_profile"])
            value = client.submit(target, confirm=lambda _: input("이 범위로 제출할까요? [y/N] ").strip().lower() == "y",
                                  preview=display_preview, dry_run=args.dry_run)
        elif args.command == "retry":
            value = client.retry(args.submission_id)
        else:
            check(1 <= args.timeout <= 86400, "INVALID_SUBMISSION")
            deadline = time.monotonic() + args.timeout
            while True:
                value = client.status(args.submission_id)
                if not args.wait or value["delivery_state"] in ("OA_STORED", "REJECTED"):
                    break
                if time.monotonic() >= deadline:
                    print("code=SUBMISSION_PENDING", file=sys.stderr)
                    from common.sanity_common import canonical_json
                    print(canonical_json(value).decode())
                    return 3
                time.sleep(min(2, max(0, deadline - time.monotonic())))
        from common.sanity_common import canonical_json
        if value.get('delivery_state')=='LOCAL_QUEUED':
            print(f"VWP 인계 완료 · OA 반영 전입니다. sanity status {value['submission_id']}로 확인하세요.")
        elif value.get('delivery_state')=='VWP_UPLOADED':
            print(f"VWP 서버 업로드 완료 · 분석·OA 반영 상태는 sanity status {value['submission_id']}로 확인하세요.")
        print(canonical_json(value).decode())
        return 2 if value.get("delivery_state") == "REJECTED" else 0
    except (EOFError, KeyboardInterrupt):
        print("code=SUBMISSION_INTERRUPTED", file=sys.stderr)
    except ReviewError as exc:
        print("code=" + exc.code, file=sys.stderr)
    except (OSError, ValueError, TypeError):
        print("code=SUBMISSION_IO_ERROR", file=sys.stderr)
    except Exception:
        print('code=SUBMISSION_ADAPTER_FAILED',file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
