"""Complete, line-addressed UTF-8 source text; never execute report/SDC content."""
import hashlib
import re

from common.sanity_common import canonical_json, digest
from common.sanity_privacy import MASK, PATTERNS, PRIVATE_KEY, SecretGuard
from common.sanity_submission import HEX, check

PRODUCER = 'postgresql-source-2.4'
FORMAT = 'utf8-text-v1'
KIND = 'preserved-text-v1'
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_SEGMENT_BYTES = 128 * 1024
TARGET_SEGMENT_BYTES = 32 * 1024
MAX_PARTS = 1024
TEXT_PART_BYTES = 512 * 1024
SEGMENTS_PER_PART = 16
EXTRACTION_KEYS = {'format', 'masking_version', 'source_sha256', 'source_bytes', 'line_count',
                   'text_sha256', 'text_bytes', 'redacted', 'parts'}


def sha(value):
    return hashlib.sha256(value).hexdigest()


def mask_text(value, guard):
    # Preserve source line numbering, including multi-line credentials/private keys.
    def mask(text):
        return re.sub(r'[^\r\n]+', lambda _: MASK, text)
    for secret in guard.known:
        value = value.replace(secret, mask(secret))
    value = PRIVATE_KEY.sub(lambda m: mask(m[0]), value)
    for pattern in PATTERNS:
        value = pattern.sub(lambda m: m[1] + mask(m[2]), value)
    guard.ensure_public(value)
    return value


def extract(file, raw, guard=None):
    """Return verified metadata and immutable masked parts, covering every byte."""
    check(len(raw) <= MAX_FILE_BYTES, 'PRESERVED_TEXT_LIMIT')
    check(len(raw) == file['size_bytes'] and sha(raw) == file['sha256'], 'FILE_INTEGRITY')
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        check(False, 'PRESERVED_TEXT_ENCODING')
    check(not re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', text), 'PRESERVED_TEXT_ENCODING')
    masked = mask_text(text, guard or SecretGuard())
    check(len(masked.encode()) <= MAX_FILE_BYTES * 2, 'PRESERVED_TEXT_LIMIT')
    lines, public = text.splitlines(keepends=True), masked.splitlines(keepends=True)
    check(len(lines) == len(public), 'PRESERVED_TEXT_MASKING')
    segments, offset, start, originals, texts, width = [], 0, 1, [], [], 0
    def emit(end):
        nonlocal offset, start, originals, texts, width
        original, output = ''.join(originals).encode(), ''.join(texts)
        value = {'source_id': file['id'], 'line_start': start, 'line_end': end,
                 'byte_start': offset, 'byte_end': offset + len(original), 'source_sha256': sha(original),
                 'text': output, 'text_sha256': sha(output.encode()), 'redacted': output.encode() != original}
        segment = {'id': 'pe-' + digest(value), **value}
        segments.append(segment)
        offset += len(original); start = end + 1; originals, texts, width = [], [], 0
    for index, (original, output) in enumerate(zip(lines, public), 1):
        size = max(len(original.encode()), len(output.encode()))
        check(size <= MAX_SEGMENT_BYTES, 'PRESERVED_TEXT_LINE_LIMIT')
        if originals and width + size > TARGET_SEGMENT_BYTES:
            emit(index - 1)
        originals.append(original); texts.append(output); width += size
    if originals:
        emit(len(lines))
    parts, pending = [], []
    for segment in segments:
        body = {'kind': KIND, 'source_id': file['id'], 'part_index': len(parts), 'segments': pending + [segment]}
        if pending and (len(pending) >= SEGMENTS_PER_PART or len(canonical_json(body)) > TEXT_PART_BYTES):
            parts.append({**body, 'segments': pending})
            pending = []
        pending.append(segment)
    if pending:
        parts.append({'kind': KIND, 'source_id': file['id'], 'part_index': len(parts), 'segments': pending})
    check(len(parts) <= MAX_PARTS and all(len(canonical_json(p)) <= TEXT_PART_BYTES for p in parts), 'PRESERVED_TEXT_LIMIT')
    extraction = {'format': FORMAT, 'masking_version': 'secret-guard-lines-v1',
        'source_sha256': file['sha256'], 'source_bytes': len(raw), 'line_count': len(lines),
        'text_sha256': sha(masked.encode()), 'text_bytes': len(masked.encode()), 'redacted': masked != text,
        'parts': [{'id': 'sp-'+digest(p), 'sha256': digest(p), 'count': len(p['segments'])} for p in parts]}
    return {**file, 'content_available_to_ai': True, 'extraction': extraction}, parts


def validate_extraction(file):
    value = file.get('extraction')
    check(isinstance(value, dict) and set(value) == EXTRACTION_KEYS, 'PRESERVED_TEXT_CORRUPT')
    check(value['format'] == FORMAT and value['masking_version'] == 'secret-guard-lines-v1'
          and value['source_sha256'] == file['sha256'] and value['source_bytes'] == file['size_bytes']
          and type(value['source_bytes']) is int and 0 <= value['source_bytes'] <= MAX_FILE_BYTES
          and type(value['line_count']) is int and value['line_count'] >= 0
          and type(value['text_bytes']) is int and 0 <= value['text_bytes'] <= MAX_FILE_BYTES * 2
          and type(value['redacted']) is bool and isinstance(value['text_sha256'], str)
          and HEX.fullmatch(value['text_sha256']), 'PRESERVED_TEXT_CORRUPT')
    refs = value['parts']
    check(isinstance(refs, list) and len(refs) <= MAX_PARTS, 'PRESERVED_TEXT_CORRUPT')
    for ref in refs:
        check(isinstance(ref, dict) and set(ref) == {'id', 'sha256', 'count'}
              and isinstance(ref['sha256'], str) and HEX.fullmatch(ref['sha256'])
              and ref['id'] == 'sp-'+ref['sha256'] and type(ref['count']) is int and 1 <= ref['count'] <= SEGMENTS_PER_PART,
              'PRESERVED_TEXT_CORRUPT')
    check(len({r['id'] for r in refs}) == len(refs)
          and bool(refs) == bool(value['source_bytes']) == bool(value['line_count']), 'PRESERVED_TEXT_CORRUPT')
    return value


def validate_part(body):
    check(isinstance(body, dict) and set(body) == {'kind', 'source_id', 'part_index', 'segments'}
          and body['kind'] == KIND and isinstance(body['source_id'], str)
          and body['source_id'].startswith('pf-') and HEX.fullmatch(body['source_id'][3:])
          and type(body['part_index']) is int and 0 <= body['part_index'] < MAX_PARTS
          and isinstance(body['segments'], list) and 1 <= len(body['segments']) <= SEGMENTS_PER_PART
          and len(canonical_json(body)) <= TEXT_PART_BYTES, 'PRESERVED_TEXT_CORRUPT')
    for value in body['segments']:
        check(isinstance(value, dict) and set(value) == {'id', 'source_id', 'line_start', 'line_end',
              'byte_start', 'byte_end', 'source_sha256', 'text', 'text_sha256', 'redacted'}, 'PRESERVED_TEXT_CORRUPT')
        check(value['source_id'] == body['source_id'] and type(value['redacted']) is bool
              and all(type(value[k]) is int for k in ('line_start', 'line_end', 'byte_start', 'byte_end'))
              and 1 <= value['line_start'] <= value['line_end'] and 0 <= value['byte_start'] < value['byte_end']
              and value['byte_end'] - value['byte_start'] <= MAX_SEGMENT_BYTES
              and isinstance(value['text'], str) and 0 < len(value['text'].encode()) <= MAX_SEGMENT_BYTES * 2
              and len(value['text'].splitlines()) == value['line_end'] - value['line_start'] + 1
              and value['text_sha256'] == sha(value['text'].encode())
              and isinstance(value['source_sha256'], str) and HEX.fullmatch(value['source_sha256'])
              and value['id'] == 'pe-'+digest({k: v for k, v in value.items() if k != 'id'}), 'PRESERVED_TEXT_CORRUPT')
        if not value['redacted']:
            check(value['source_sha256'] == value['text_sha256']
                  and value['byte_end'] - value['byte_start'] == len(value['text'].encode()), 'PRESERVED_TEXT_CORRUPT')
    SecretGuard().ensure_public(body)
    return body


def verify_parts(file, get_part):
    """Verify full ordered coverage before exposing any model input or download."""
    meta = validate_extraction(file)
    line, offset, size, redacted = 1, 0, 0, False
    hashed, originals, result = hashlib.sha256(), hashlib.sha256(), []
    for index, ref in enumerate(meta['parts']):
        body = validate_part(get_part(ref))
        check(digest(body) == ref['sha256'] and body['source_id'] == file['id']
              and body['part_index'] == index and len(body['segments']) == ref['count'], 'PRESERVED_TEXT_CORRUPT')
        for segment in body['segments']:
            check(segment['line_start'] == line and segment['byte_start'] == offset, 'PRESERVED_TEXT_CORRUPT')
            line, offset = segment['line_end'] + 1, segment['byte_end']
            encoded = segment['text'].encode(); size += len(encoded); hashed.update(encoded)
            if not segment['redacted']: originals.update(encoded)
            redacted |= segment['redacted']; result.append(segment)
    check(line - 1 == meta['line_count'] and offset == meta['source_bytes'] and size == meta['text_bytes']
          and hashed.hexdigest() == meta['text_sha256'] and redacted == meta['redacted'], 'PRESERVED_TEXT_CORRUPT')
    check(redacted or originals.hexdigest() == file['sha256'], 'PRESERVED_TEXT_CORRUPT')
    return result
