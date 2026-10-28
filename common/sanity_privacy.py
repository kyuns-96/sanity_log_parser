"""Outbound secret guard. Known credentials + explicit credential syntax, not general DLP."""
import re
from urllib.parse import unquote, urlsplit

from common.sanity_common import ReviewError, canonical_json
from common.sanity_config import load_environment

MASK = "[REDACTED]"
SECRET_KEY = re.compile(r"(?i)(?:password|passwd|pwd|secret|token|api[_-]?key|authorization|private[_-]?key)$")
PATTERNS = (
    re.compile(r"(?i)(\b(?:[A-Z_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_KEY)|password|passwd|pwd|api-key)\b[\"']?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;}]+)"),
    re.compile(r"(?i)(\b(?:Bearer|Basic)\s+)([^\s\"']+)"),
    re.compile(r"(?i)(postgres(?:ql)?://[^\s:/]+:)([^@\s]+)(?=@)"),
)
PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z ]+)?PRIVATE KEY-----.*?(?:-----END (?:[A-Z ]+)?PRIVATE KEY-----|\Z)", re.S)


class SecretGuard:
    def __init__(self, settings=None):
        settings = load_environment() if settings is None else settings
        known = [v for k, v in settings.items() if SECRET_KEY.search(k) and isinstance(v, str) and v]
        for key in ("SANITY_VWP_DSN", "SANITY_OA_DSN"):
            dsn = settings.get(key) or ""
            if dsn.startswith(("postgres://", "postgresql://")):
                password = urlsplit(dsn).password
                if password:
                    known.append(unquote(password))
            else:
                match = re.search(r"(?:^|\s)password\s*=\s*('(?:\\.|[^'])*'|\S+)", dsn)
                if match:
                    known.append(match[1].strip("'"))
        self.known = sorted(set(known), key=len, reverse=True)

    def text(self, value):
        # Invalid UTF-8 evidence is retained privately as reversible hex. Its
        # bytes cannot be checked by the plaintext rules, so mask the whole raw.
        if (value.startswith("hex:") and len(value) % 2 == 0
                and re.fullmatch(r"[0-9a-fA-F]+", value[4:])):
            try:
                bytes.fromhex(value[4:]).decode("utf-8")
            except UnicodeDecodeError:
                return MASK
        for secret in self.known:
            value = value.replace(secret, MASK)
        value = PRIVATE_KEY.sub(MASK, value)
        for pattern in PATTERNS:
            value = pattern.sub(lambda m: m[1] + MASK, value)
        return value

    def redact(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.redact(v) for v in value]
        if isinstance(value, dict):
            # A bounded legacy example may end before the invalid byte. The
            # remaining hex prefix cannot establish that the full raw was text.
            truncated_hex = (type(value.get("raw_omitted_chars")) is int and value["raw_omitted_chars"] > 0
                             and isinstance(value.get("raw"), str)
                             and re.fullmatch(r"hex:[0-9a-fA-F]+", value["raw"]) is not None)
            return {self.text(k): MASK if (SECRET_KEY.search(k) and v) or (k == "raw" and truncated_hex)
                    else self.redact(v) for k, v in value.items()}
        return value

    def ensure_public_json(self, value):
        """Scan already validated JSON without building a second object tree.

        The canonical serializer/strict decoder remains the caller's boundary.
        Only short safe strings and key classifications are memoized per scan.
        Non-ASCII strings use the exact regexp path (Unicode IGNORECASE matters).
        """
        safe, keys = set(), {}
        triggers = ('password', 'passwd', 'pwd', 'secret', 'token', 'api_key', 'api-key',
                    'bearer', 'basic', 'postgres', 'private key')
        def fail():
            raise ReviewError('SENSITIVE_CONTENT', 'Outbound content contains credentials; no data was exported')
        def string(text):
            if text in safe: return
            lowered = text.lower()
            plain = (text.isascii() and not text.startswith('hex:')
                     and not any(secret in text for secret in self.known)
                     and not any(word in lowered for word in triggers))
            if not plain and self.text(text) != text: fail()
            if len(text) <= 256 and len(safe) < 4096: safe.add(text)
        def visit(item):
            if isinstance(item, str):
                string(item)
            elif isinstance(item, list):
                for child in item: visit(child)
            elif isinstance(item, dict):
                truncated_hex = (type(item.get('raw_omitted_chars')) is int and item['raw_omitted_chars'] > 0
                                 and isinstance(item.get('raw'), str)
                                 and re.fullmatch(r'hex:[0-9a-fA-F]+', item['raw']) is not None)
                for key, child in item.items():
                    string(key)
                    secret = keys.get(key)
                    if secret is None:
                        secret = bool(SECRET_KEY.search(key))
                        if len(keys) < 4096: keys[key] = secret
                    if (secret and child) or (key == 'raw' and truncated_hex):
                        if child != MASK: fail()
                        continue  # redact replaces this whole value, without scanning inside MASK.
                    visit(child)
        visit(value)

    def ensure_public(self, value):
        if self.redact(value) != value:
            raise ReviewError("SENSITIVE_CONTENT", "Outbound content contains credentials; no data was exported")
        canonical_json(value)
