"""Synthetic collection-only contracts; no company reports or model calls."""

from common.sanity_parsers import ParsedRecord, ParserRegistry, ReportParser
from vwp_bridge.integration.parser_example import SyntheticTextParser


class CollectionTextParser(ReportParser):
    def parse(self, source, context):
        for event in SyntheticTextParser().parse(source, context):
            if isinstance(event, ParsedRecord):
                fields = dict(event.fields)
                fields['message'] = fields.pop('status')
                yield ParsedRecord(fields, event.span, event.json_pointer)
            else:
                yield event


def collecting_registry(version='1'):
    registry = ParserRegistry()
    registry.register('synthetic_collection_v1', CollectionTextParser(), implementation_version=version,
        evaluation='none', item_ids=('gca_constraints',), tool='synthetic',
        tool_versions=('1',), rule_versions=('1',))
    return registry


def prepare_collection(fixture, body=None):
    unit = next(u for u in fixture['prepared'].plan['inventory']['units'] if u['source']['layout']=='POST')
    unit.update(parser='synthetic_collection_v1',evaluation=None)
    body = body if body is not None else (
        'SYNTHETIC REPORT v1\nA|P1|M1|C1|WARNING|clock fact A\n'
        'B|P1|M1|C1|INFO|clock fact B\nEND records=2\n')
    fixture['prepared'].reports[unit['report']].write_bytes(body if isinstance(body,bytes) else body.encode())
    return unit
