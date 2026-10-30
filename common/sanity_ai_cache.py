"""Explicit, bounded LiteLLM-compatible response-cache bypass option."""
from dataclasses import dataclass

from common.sanity_clustering import check


@dataclass(frozen=True)
class CacheSettings:
    no_cache: bool = True

    def __post_init__(self):
        check(self.no_cache is True, 'AI_CACHE_CONFIG')

    @classmethod
    def from_config(cls, value):
        check(isinstance(value,dict) and set(value)=={'no-cache'} and value['no-cache'] is True,
              'AI_CACHE_CONFIG')
        return cls()

    def public(self):
        return {'no-cache':True}

    def body(self):
        return {'cache':self.public()}


def validate_wire_cache(body):
    """Reject arbitrary cache controls before VWP dispatch."""
    check(isinstance(body,dict), 'AI_VWP_SCHEMA')
    if 'cache' in body:
        value=body['cache']
        check(isinstance(value,dict) and set(value)=={'no-cache'} and value['no-cache'] is True,
              'AI_VWP_SCHEMA')
