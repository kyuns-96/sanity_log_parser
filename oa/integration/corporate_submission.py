"""Map trusted bridge assertions to the same stable account as OA SSO."""
from common.sanity_common import ReviewError
from common.sanity_submission import SourceIdentityResolver


class CorporateSourceIdentity(SourceIdentityResolver):
    def resolve(self, actor):
        raise ReviewError("SUBMISSION_IDENTITY_NOT_CONFIGURED", "Company persistent identity mapping is not connected")
