"""Company mail boundary; no transport, credentials, or delivery implementation."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class MailMessage:
    job_id: str
    attempt_id: str
    recipients: tuple[str, ...]
    subject: str
    text_body: str


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
