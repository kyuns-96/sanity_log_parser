"""OA mail composition root: explicit company adapter, no transport fallback."""
from area_power.oa.mail import main as run_mail_worker
from oa.integration.corporate_mail import create_mail_sender


def main(argv=None):
    return run_mail_worker(sender_factory=create_mail_sender, argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
