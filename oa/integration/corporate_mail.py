"""Company-owned mail construction point; deliberately fails until connected.

Read oa/MAIL_INTEGRATION.md. Implement the approved company SDK wrapper in
company_mail/ and construct it here. Keep credentials in private company
configuration, not in the message, browser, repository, or worker code.
"""
from area_power.common.mail import MailNotConfigured, MailSender


def create_mail_sender() -> MailSender:
    raise MailNotConfigured("CORPORATE_MAIL_NOT_CONFIGURED")
