"""Company VWP submission hooks. SDK methods fail closed until implemented."""
from common.sanity_common import ReviewError
from common.sanity_submission import SubmissionAuthority, SubmissionInputProvider


class CorporateSubmissionInput(SubmissionInputProvider):
    def prepare(self, target):
        raise ReviewError("PLAN_NOT_CONFIGURED", "Company V2 source input is not connected")


class CorporateSubmissionAuthority(SubmissionAuthority):
    def current(self):
        raise ReviewError("SUBMISSION_AUTH_NOT_CONFIGURED", "Company submission identity is not connected")

    def inbox_owner(self, directory):
        raise ReviewError("SUBMISSION_AUTH_NOT_CONFIGURED", "Authenticated submission storage is not connected")

    def authorize(self, actor, collection):
        raise ReviewError("AUTHORIZATION_NOT_IMPLEMENTED", "Company block assignment is not connected")

    def approval_valid(self, plan):
        raise ReviewError("SUBMISSION_APPROVAL_NOT_CONFIGURED", "Company scope approval is not connected")


def create_submission_adapters():
    return CorporateSubmissionInput(), CorporateSubmissionAuthority()
