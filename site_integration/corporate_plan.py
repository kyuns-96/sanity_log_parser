"""Company VWP expected-inventory hook; not automatically loaded by collection.

Read INTERNAL_AI_HANDOFF.md and vwp-bridge/PLAN_INTEGRATION.md before implementing.
The calling company launcher must validate the returned plan explicitly. Real
run metadata, SDK details and credentials remain in the company environment.
"""
from sanity_common import ReviewError
from sanity_integration_contracts import CollectionPlan, SanityPlanProvider


class CorporatePlanProvider(SanityPlanProvider):
    def get_plan(self, run_reference: str) -> CollectionPlan:
        raise ReviewError("PLAN_NOT_CONFIGURED", "Company expected-inventory provider is not connected")
