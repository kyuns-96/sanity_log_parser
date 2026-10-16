"""Company implementation points. Read vwp_bridge/PARSER_INTEGRATION.md first.

Implement one adapter per verified format/version under company_parsers/, then
register it explicitly here. Real reports and private fixtures stay in company
storage. The public default registry is deliberately empty.
"""
from common.sanity_parsers import ParseContext, ParserError, ParserRegistry, ReportParser, ReportSource


class CorporateReportParser(ReportParser):
    def parse(self, source: ReportSource, context: ParseContext):
        # COMPANY TODO: implement the verified format, ordered source spans and
        # completion/count checks. Do not guess thresholds or turn unknowns into
        # PASS. Alternatively add a ReportParser subclass in company_parsers/.
        raise ParserError("PARSER_NOT_CONFIGURED")


def create_parser_registry() -> ParserRegistry:
    registry = ParserRegistry()
    # COMPANY TODO: explicit imports + registry.register(...) for approved
    # item IDs, tool, tool_versions and rule_versions. See the synthetic example.
    # Never register the example for a real item, or import a name from plan.json.
    return registry
