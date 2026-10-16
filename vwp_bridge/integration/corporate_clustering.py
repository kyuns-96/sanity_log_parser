"""Company VWP clustering SDK hook; never executed by the OA reference engine."""
from common.sanity_common import ReviewError
from common.sanity_integration_contracts import AdvisorContext, ClusteringCase, ClusteringProposalV2
from common.sanity_clustering_advice import ClusteringAdvisor


class CorporateClusteringAdvisor(ClusteringAdvisor):
    def propose(self, context: AdvisorContext, cases: list[ClusteringCase],
                identity_fields: list[str]) -> ClusteringProposalV2:
        """Implement the V2 output in vwp_bridge/CLUSTERING_INTEGRATION.md on VWP."""
        raise ReviewError("AI_NOT_CONFIGURED", "Company VWP clustering advisor is not connected")
