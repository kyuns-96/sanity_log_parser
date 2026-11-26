"""Company mail boundary; no transport, credentials, or delivery implementation."""
from dataclasses import dataclass
from typing import Protocol

# Corporate mail service limits: subject <= 300 UTF-8 bytes, body <= 1 MB.
# Keep HTML (700 KB) + plain-text (150 KB) safely under 1 MB even when sent
# together as multipart/alternative with envelope/header overhead.
MAIL_MAX_SUBJECT_BYTES = 300
MAIL_MAX_HTML_BODY_BYTES = 700_000
MAIL_MAX_TEXT_BODY_BYTES = 150_000


def utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def truncate_utf8_bytes(text: str, max_bytes: int, *, suffix: str = "…") -> str:
    """Truncate text cleanly on a Unicode codepoint boundary within max_bytes UTF-8 bytes."""
    if max_bytes <= 0:
        return ""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    suffix_bytes = suffix.encode("utf-8") if suffix else b""
    if len(suffix_bytes) >= max_bytes:
        return raw[:max_bytes].decode("utf-8", errors="ignore")
    budget = max_bytes - len(suffix_bytes)
    cut = raw[:budget].decode("utf-8", errors="ignore").rstrip()
    return cut + suffix


def format_mail_subject(
    prefix: str,
    middle: str,
    suffix: str = "",
    *,
    max_bytes: int = MAIL_MAX_SUBJECT_BYTES,
) -> str:
    """Normalize whitespace and fit subject within max_bytes UTF-8 bytes, preserving prefix and suffix."""
    p = " ".join(prefix.split())
    m = " ".join(middle.split())
    s = " ".join(suffix.split())
    full = " ".join(part for part in (p, m, s) if part)
    if utf8_len(full) <= max_bytes:
        return full
    fixed = " ".join(part for part in (p, s) if part)
    sep_bytes = 1 if (p and m) else 0
    sep_bytes += 1 if (m and s) else 0
    remaining = max_bytes - utf8_len(fixed) - sep_bytes
    if m and remaining >= 12:
        trimmed_middle = truncate_utf8_bytes(m, remaining)
        candidate = " ".join(part for part in (p, trimmed_middle, s) if part)
        if utf8_len(candidate) <= max_bytes:
            return candidate
    return truncate_utf8_bytes(full, max_bytes)


def cap_text_body(body: str, *, max_bytes: int = MAIL_MAX_TEXT_BODY_BYTES, notice: str | None = None) -> str:
    """Ensure a plain-text mail body fits within max_bytes UTF-8 bytes with an explicit omission notice."""
    if utf8_len(body) <= max_bytes:
        return body
    tail = notice or "\n\n... (메일 본문 크기 한도 1MB로 인해 일부 상세 내용이 생략되었습니다. 전체 결과는 Design Atlas에서 확인하세요.)"
    budget = max(0, max_bytes - utf8_len(tail))
    trimmed = truncate_utf8_bytes(body, budget, suffix="")
    if "\n" in trimmed:
        trimmed = trimmed.rsplit("\n", 1)[0]
    return trimmed + tail


@dataclass(frozen=True, slots=True)
class MailMessage:
    job_id: str
    attempt_id: str
    recipients: tuple[str, ...]
    subject: str
    text_body: str
    html_body: str | None = None


@dataclass(frozen=True, slots=True)
class MailReceipt:
    """Submission acceptance, not recipient delivery or acknowledgement.

    Accepted and rejected recipients must partition the request exactly.
    A whole-request acceptance maps all requested recipients to accepted.
    Only return after the company service has confirmed that outcome.
    """
    accepted_recipients: tuple[str, ...]
    rejected_recipients: tuple[str, ...]
    provider_message_id: str | None = None


class MailNotConfigured(Exception):
    """The company adapter is unavailable; no submission was attempted."""


class MailRejected(Exception):
    """The company service explicitly confirmed that none were accepted."""


class MailSender(Protocol):
    def send(self, message: MailMessage) -> MailReceipt:
        """Submit once using the approved company service.

        Use attempt_id for correlation, not as a promise of idempotency.
        Raise MailRejected only for a confirmed whole-request rejection,
        MailNotConfigured only before any submission, and another exception
        when the outcome is unknown. Never automatically resend an unknown
        request or include secrets/raw SDK responses in a receipt.
        """
        ...
