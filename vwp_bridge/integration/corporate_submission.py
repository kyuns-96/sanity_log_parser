"""Legacy identity hooks; internal_network intake never calls this authority.

Token-free deployments need the company parser and infrastructure settings.
OA's authenticated current OWNER confirms the submitted scope in workbench.
Keep these fail-closed hooks for explicitly selected legacy configurations.
"""
from common.sanity_common import ReviewError
from common.sanity_submission import SubmissionAuthority


class CorporateSubmissionAuthority(SubmissionAuthority):
    def authenticate_bearer(self, token):
        # Legacy Bearer mode only; do not implement for internal_network intake.
        raise ReviewError('SUBMISSION_AUTH_NOT_CONFIGURED', 'HTTPS submission authentication is not connected')

    def resolve_actor(self, actor):
        # Revalidate a service-stored issuer/subject/user ID, never a client claim.
        raise ReviewError('SUBMISSION_AUTH_NOT_CONFIGURED', 'HTTPS submitter lookup is not connected')

    def current(self):
        raise ReviewError("SUBMISSION_AUTH_NOT_CONFIGURED", "Company bridge identity is not connected")

    def inbox_owner(self, directory):
        raise ReviewError("SUBMISSION_AUTH_NOT_CONFIGURED", "Authenticated mount identity is not connected")

    def authorize(self, actor, collection):
        raise ReviewError("AUTHORIZATION_NOT_IMPLEMENTED", "Company block assignment is not connected")

    def approval_valid(self, plan):
        raise ReviewError("SUBMISSION_APPROVAL_NOT_CONFIGURED", "Company scope approval is not connected")
