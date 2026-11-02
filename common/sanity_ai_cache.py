"""Explicit, bounded LiteLLM-compatible response-cache bypass option."""
from common.sanity_clustering import check


def validate_wire_cache(body, *, code='AI_VWP_SCHEMA'):
    """Reject arbitrary cache controls before VWP dispatch."""
    check(isinstance(body,dict), code)
    if 'cache' in body:
        value=body['cache']
        check(isinstance(value,dict) and set(value)=={'no-cache'} and value['no-cache'] is True,
              code)
