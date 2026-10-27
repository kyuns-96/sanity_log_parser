"""Token-free internal intake labels; these are never authenticated identities."""
from dataclasses import dataclass
import copy
import ipaddress
import os
import pwd

from common.sanity_common import digest
from common.sanity_submission import actor_value, check, text, validate_plan

UNVERIFIED_ISSUER = 'urn:sanity:unverified-vwp-local'
PENDING_BLOCKER = 'OA_CONFIRMATION_REQUIRED'


def tokenless(config):
    return config.get('submission_auth') == 'internal_network'


def unverified(actor):
    return isinstance(actor,dict) and actor.get('issuer') == UNVERIFIED_ISSUER


@dataclass(frozen=True)
class UnverifiedSubmitter:
    """An untrusted display label, without company identity or block grants."""
    issuer: str
    subject: str
    user_id: str

    def __post_init__(self):
        actor_value(self.actor)
        check(self.issuer == UNVERIFIED_ISSUER and text(self.subject,100)
              and self.user_id == 'unverified-'+digest([self.issuer,self.subject])[:32],
              'INVALID_SUBMISSION')

    @property
    def actor(self):
        return dict(issuer=self.issuer,subject=self.subject,user_id=self.user_id)

    @property
    def key(self):
        return digest([self.issuer,self.subject])


def local_label():
    # Environment USER/LOGNAME and input usernames are not identity assertions.
    name = pwd.getpwuid(os.geteuid()).pw_name
    return UnverifiedSubmitter(UNVERIFIED_ISSUER,name,'unverified-'+digest([UNVERIFIED_ISSUER,name])[:32])


def pending_plan(plan):
    validate_plan(plan)
    value = copy.deepcopy(plan)
    value['inventory']['confirmed'] = False
    value['approval'] = None
    return value


def require_pending_plan(plan):
    validate_plan(plan)
    check(plan['inventory']['confirmed'] is False and plan['approval'] is None,'INVALID_SUBMISSION')


def networks(values):
    check(isinstance(values,list) and 1 <= len(values) <= 32,'SUBMISSION_CONFIG')
    result = []
    for value in values:
        try:
            network = ipaddress.ip_network(value,strict=True) if isinstance(value,str) else None
        except ValueError:
            network = None
        check(network is not None and network.prefixlen > 0 and str(network) == value,'SUBMISSION_CONFIG')
        result.append(network)
    return result


def require_internal_peer(config, peer):
    try:
        address = ipaddress.ip_address(peer) if isinstance(peer,str) else None
    except ValueError:
        address = None
    if isinstance(address,ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    check(address is not None and any(address in net for net in networks(config['allowed_client_networks'])),
          'FORBIDDEN')
