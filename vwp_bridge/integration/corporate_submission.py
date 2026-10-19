"""Bridge identity/assignment hooks, separate from VWP workstation SDK setup."""
from common.sanity_common import ReviewError
from common.sanity_submission import SubmissionAuthority


class CorporateSubmissionAuthority(SubmissionAuthority):
    def current(self):
        raise ReviewError("SUBMISSION_AUTH_NOT_CONFIGURED", "Company bridge identity is not connected")

    def inbox_owner(self, directory):
        raise ReviewError("SUBMISSION_AUTH_NOT_CONFIGURED", "Authenticated mount identity is not connected")

    def authorize(self, actor, collection):
        raise ReviewError("AUTHORIZATION_NOT_IMPLEMENTED", "Company block assignment is not connected")

    def approval_valid(self, plan):
        raise ReviewError("SUBMISSION_APPROVAL_NOT_CONFIGURED", "Company scope approval is not connected")
