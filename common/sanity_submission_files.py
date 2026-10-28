"""Bounded immutable file handoff inside VWP. No network or application DB."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import stat
import tempfile

from common.sanity_common import _decode_json, canonical_json, digest
from common.sanity_submission import HEX, MAX_METADATA, actor_value, check, relative_report, submission_uuid, text, validate_plan

DEFAULT_TOTAL_BYTES = 16 * 1024**3
DEFAULT_FILES = 10000


def directory(path, *, create=False, mode=0o700):
    path = Path(path).absolute()
    check(not any(p == ".." for p in path.parts), "UNSAFE_SUBMISSION_PATH")
    for parent in [*reversed(path.parents), path]:
        check(not parent.is_symlink(), "UNSAFE_SUBMISSION_PATH")
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=mode)
    check(path.is_dir(), "SUBMISSION_STORAGE_NOT_CONFIGURED")
    return path


def regular(path, maximum=MAX_METADATA):
    path = Path(path).absolute()
    check('..' not in path.parts,'UNSAFE_SUBMISSION_PATH')
    # Resolve every component relative to an already-open directory. Checking
    # is_symlink then opening an absolute path leaves parent rename races.
    # Ancestors need search access, not directory-listing access (e.g. Android's
    # root directory). Keep DIRECTORY/NOFOLLOW on every pinned path descriptor.
    directory_flags = getattr(os, 'O_PATH', os.O_RDONLY) | os.O_DIRECTORY | os.O_NOFOLLOW
    parent = None
    try:
        parent = os.open('/', directory_flags)
        for component in path.parent.parts[1:]:
            following = os.open(component, directory_flags, dir_fd=parent)
            os.close(parent)
            parent = following
        descriptor = os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
    except OSError:
        check(False,'UNSAFE_SUBMISSION_FILE')
    finally:
        if parent is not None:
            os.close(parent)
    info = os.fstat(descriptor)
    if not (stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= maximum):
        os.close(descriptor)
        check(False, "UNSAFE_SUBMISSION_FILE")
    return os.fdopen(descriptor, "rb"), info


def read_json_file(path, maximum=MAX_METADATA):
    stream, _ = regular(path, maximum)
    with stream:
        value = stream.read(maximum + 1)
    check(len(value) <= maximum, "SUBMISSION_LIMIT")
    return _decode_json(value)


def fsync_dir(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_json_file(path, value, *, mode=0o600):
    path = Path(path)
    directory(path.parent)
    check(not path.is_symlink(), "UNSAFE_SUBMISSION_PATH")
    data = canonical_json(value) + b"\n"
    descriptor, temporary = tempfile.mkstemp(prefix=".sanity-", dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_dir(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def copy_file(source, destination, *, maximum, mode=0o600):
    """Check source before/after copying; never follow links or normalize bytes."""
    stream, before = regular(source, maximum)
    destination = Path(destination)
    directory(destination.parent, create=True, mode=0o750 if mode == 0o640 else 0o700)
    with stream:
        first = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            first.update(block)
        stream.seek(0)
        output = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        copied = hashlib.sha256()
        with os.fdopen(output, "wb") as target:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                target.write(block)
                copied.update(block)
            target.flush()
            os.fsync(target.fileno())
        stream.seek(0)
        last = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            last.update(block)
        after = os.fstat(stream.fileno())
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    check(signature(before) == signature(after) and first.digest() == copied.digest() == last.digest(), "SOURCE_CHANGED")
    check(destination.stat().st_size == before.st_size, "SOURCE_CHANGED")
    return {"size_bytes": before.st_size, "sha256": copied.hexdigest()}


def snapshot(prepared, output, *, max_bytes=DEFAULT_TOTAL_BYTES, max_files=DEFAULT_FILES):
    validate_plan(prepared.plan)
    from common.sanity_preserved_sources import declared_paths, metadata
    check(isinstance(prepared.reports, dict) and len(prepared.reports) <= max_files, "SUBMISSION_LIMIT")
    check(declared_paths(prepared.plan) <= set(prepared.reports), 'PRESERVED_FILE_MISSING')
    output = Path(output)
    check(not output.exists(), "SUBMISSION_CONFLICT")
    output.mkdir(mode=0o700)
    (output / "reports").mkdir(mode=0o700)
    files, total = [], 0
    for relative, original in sorted(prepared.reports.items()):
        relative_report(relative)
        check(not (Path(original).absolute() == output or output in Path(original).absolute().parents), "UNSAFE_SUBMISSION_PATH")
        record = copy_file(original, output / relative, maximum=max_bytes - total)
        total += record["size_bytes"]
        check(total <= max_bytes, "SUBMISSION_LIMIT")
        files.append({"path": relative, **record})
    expected = {u["report"] for u in prepared.plan["inventory"]["units"]}
    expected |= declared_paths(prepared.plan)
    manifest = {"schema_version": prepared.plan['schema_version'], "plan": prepared.plan, "files": files,
                "unexpected_files": [f["path"] for f in files if f["path"] not in expected]}
    if prepared.plan['schema_version'] >= 3:
        manifest['preserved_files'] = metadata(prepared.plan, files)
    write_json_file(output / "collection.json", manifest)
    for parent in sorted((p for p in output.rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True):
        fsync_dir(parent)
    fsync_dir(output)
    return manifest


def validate_manifest(manifest, *, max_bytes=DEFAULT_TOTAL_BYTES, max_files=DEFAULT_FILES):
    check(isinstance(manifest, dict))
    check(type(manifest.get('schema_version')) is int and manifest['schema_version'] in (2, 3, 4))
    keys = {"schema_version", "plan", "files", "unexpected_files"}
    if manifest['schema_version'] >= 3:
        keys.add('preserved_files')
    check(set(manifest) == keys)
    validate_plan(manifest["plan"])
    check(manifest['schema_version'] == manifest['plan']['schema_version'])
    files = manifest["files"]
    check(isinstance(files, list) and len(files) <= max_files, "SUBMISSION_LIMIT")
    by_path, total = {}, 0
    for record in files:
        check(isinstance(record, dict) and set(record) == {"path", "size_bytes", "sha256"})
        relative = relative_report(record["path"])
        check(relative not in by_path and type(record["size_bytes"]) is int and record["size_bytes"] >= 0)
        check(isinstance(record['sha256'],str) and HEX.fullmatch(record['sha256']))
        by_path[relative] = record
        total += record['size_bytes']
        check(total <= max_bytes, 'SUBMISSION_LIMIT')
    expected = {u['report'] for u in manifest['plan']['inventory']['units']}
    from common.sanity_preserved_sources import declared_paths, metadata
    expected |= declared_paths(manifest['plan'])
    if manifest['schema_version'] >= 3:
        check(canonical_json(manifest['preserved_files']) == canonical_json(metadata(manifest['plan'], files)),
              'PRESERVED_SOURCE_MISMATCH')
    check(manifest['unexpected_files'] == sorted(set(by_path) - expected))
    return manifest


def validate_submission(value, manifest):
    check(isinstance(value,dict) and set(value) == {"schema_version", "submission_id", "actor", "content_sha256", "plan"}
          and value['schema_version'] == 2)
    submission_uuid(value['submission_id']); actor_value(value['actor'])
    validate_manifest(manifest)
    check(value['content_sha256'] == digest(manifest) and value['plan'] == manifest['plan'], 'FILE_INTEGRITY')
    return value


def verify_snapshot(root, *, max_bytes=DEFAULT_TOTAL_BYTES, max_files=DEFAULT_FILES):
    root = directory(root)
    manifest = validate_manifest(read_json_file(root / "collection.json"), max_bytes=max_bytes, max_files=max_files)
    by_path, total = {}, 0
    for record in manifest['files']:
        relative = record['path']
        stream, info = regular(root / relative, max_bytes - total)
        value = hashlib.sha256()
        with stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                value.update(block)
        check(info.st_size == record["size_bytes"] and value.hexdigest() == record["sha256"], "FILE_INTEGRITY")
        total += info.st_size
        check(total <= max_bytes, "SUBMISSION_LIMIT")
        by_path[relative] = record
    actual = set()
    for path in root.rglob("*"):
        check(not path.is_symlink(), "UNSAFE_SUBMISSION_PATH")
        if path.is_dir():
            continue
        check(path.is_file(), "UNSAFE_SUBMISSION_FILE")
        actual.add(path.relative_to(root).as_posix())
    check(actual == set(by_path) | {"collection.json"}, "FILE_INTEGRITY")
    expected = {u["report"] for u in manifest["plan"]["inventory"]["units"]}
    from common.sanity_preserved_sources import declared_paths
    expected |= declared_paths(manifest['plan'])
    check(manifest["unexpected_files"] == sorted(set(by_path) - expected))
    return manifest


def verify_bundle(root, **limits):
    root = directory(root)
    check({p.name for p in root.iterdir()} == {"submission.json", "snapshot"}, "FILE_INTEGRITY")
    value = read_json_file(root / "submission.json")
    manifest = verify_snapshot(root / "snapshot", **limits)
    validate_submission(value, manifest)
    return value, manifest


def copy_bundle(source, destination, *, max_bytes=DEFAULT_TOTAL_BYTES, max_files=DEFAULT_FILES, mode=0o600):
    submission, manifest = verify_bundle(source, max_bytes=max_bytes, max_files=max_files)
    destination = Path(destination)
    check(not destination.exists(), "SUBMISSION_CONFLICT")
    destination.mkdir(mode=0o750 if mode == 0o640 else 0o700)
    (destination / "snapshot").mkdir(mode=0o750 if mode == 0o640 else 0o700)
    (destination / "snapshot/reports").mkdir(mode=0o750 if mode == 0o640 else 0o700)
    for record in manifest["files"]:
        copy_file(Path(source) / "snapshot" / record["path"], destination / "snapshot" / record["path"],
                  maximum=max_bytes, mode=mode)
    write_json_file(destination / "snapshot/collection.json", manifest, mode=mode)
    write_json_file(destination / "submission.json", submission, mode=mode)
    verify_bundle(destination, max_bytes=max_bytes, max_files=max_files)
    for parent in sorted((p for p in destination.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        fsync_dir(parent)
    fsync_dir(destination)
    return submission


def storage(config):
    root = directory(config["spool_root"])
    marker_path = root / "storage.json"
    marker = read_json_file(marker_path, 4096)
    check(marker == {"schema_version": 1, "storage_id": config["storage_id"]}
          and marker_path.stat().st_uid == config["service_uid"]
          and marker_path.stat().st_mode & 0o022 == 0, "SUBMISSION_STORAGE_NOT_CONFIGURED")
    directory(root / "inbox"); directory(root / "receipts")
    return root


def load_config(path, *, bridge=False):
    value = read_json_file(path, 16384)
    info = Path(path).stat()
    check(info.st_uid == 0 and info.st_mode & 0o022 == 0, "UNTRUSTED_SUBMISSION_CONFIG")
    if value.get('schema_version') in (2,3,4):
        from common.sanity_submission_https import validate_https_config
        return validate_https_config(value, bridge=bridge)
    keys = {"schema_version", "spool_root", "storage_id", "service_uid", "default_profile"}
    if bridge:
        keys |= {"archive_root", "node_id"}
    check(set(value) == keys and value["schema_version"] == 1, "SUBMISSION_CONFIG")
    check(text(value["spool_root"]) and Path(value["spool_root"]).is_absolute())
    check(text(value["storage_id"], 100) and text(value["default_profile"], 100))
    check(type(value["service_uid"]) is int and value["service_uid"] >= 0)
    if bridge:
        check(text(value["archive_root"]) and Path(value["archive_root"]).is_absolute() and text(value["node_id"], 100))
    return value


def remove_own_partial(path, uid):
    path = Path(path)
    directory(path)
    check(path.name.startswith(".") and path.name.endswith(".partial") and path.stat().st_uid == uid, "UNSAFE_SUBMISSION_PATH")
    shutil.rmtree(path)
