from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from math import isfinite
from uuid import UUID

import simplejson


def _decimal(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("Non-finite decimal is not valid application data")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return Decimal("0" if value == 0 else text)


def _prepare(value, canonical=False):
    if isinstance(value, Decimal):
        return _decimal(value) if canonical else value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("Non-finite float is not valid application data")
        return _decimal(Decimal(str(value))) if canonical else Decimal(str(value))
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, dict):
        return {str(key): _prepare(item, canonical) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_prepare(item, canonical) for item in value]
    return value


def dumps(value, canonical=False) -> str:
    return simplejson.dumps(
        _prepare(value, canonical), use_decimal=True, allow_nan=False,
        ensure_ascii=False, sort_keys=canonical, separators=(",", ":"),
    )


def loads(value):
    return simplejson.loads(value, use_decimal=True, allow_nan=False)


def digest(value) -> bytes:
    return sha256(dumps(value, canonical=True).encode("utf-8")).digest()


def public(value):
    """HTTP display contract: preserve Decimal precision as strings, not JS floats."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Non-finite decimal is not valid application data")
        return format(value, "f")
    if isinstance(value, dict):
        return {str(key): public(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [public(item) for item in value]
    return _prepare(value)
