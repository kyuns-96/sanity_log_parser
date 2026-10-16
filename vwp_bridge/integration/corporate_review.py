"""VWP-only company review adapter. Implementation requires company facts and access.

Keep the common ReviewAdvisor interface and output validation unchanged.
Never invoke this on OA or substitute mock/reference output for a company AI response.
"""
from common.sanity_common import ReviewError
from common.sanity_integration_contracts import AdvisorContext, ReviewCase, ReviewProposal
from common.sanity_review_advice import ReviewAdvisor


class CorporateReviewAdvisor(ReviewAdvisor):
    def propose(self, context: AdvisorContext, cases: list[ReviewCase]) -> ReviewProposal:
        raise ReviewError("AI_NOT_CONFIGURED", "Company VWP review advisor is not connected")
