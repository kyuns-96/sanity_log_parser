"""Company VWP clustering SDK hook; never executed by the OA reference engine."""
from sanity_common import ReviewError
from sanity_integration_contracts import AdvisorContext, ClusteringCase, ClusteringProposalV2
from sanity_workspace_clustering import ClusteringAdvisor


class CorporateClusteringAdvisor(ClusteringAdvisor):
    def propose(self, context: AdvisorContext, cases: list[ClusteringCase],
                identity_fields: list[str]) -> ClusteringProposalV2:
        """Implement the V2 output in vwp-bridge/CLUSTERING_INTEGRATION.md on VWP."""
        raise ReviewError("AI_NOT_CONFIGURED", "Company VWP clustering advisor is not connected")
