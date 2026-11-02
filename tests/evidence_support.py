"""Explicit synthetic human opt-in for tests that exercise shared knowledge."""
from uuid import uuid4

from oa.sanity_ai_evidence_choices import EvidenceChoices


def register_evidence(db,event,stage,*,ws=None,groups=None):
    store=EvidenceChoices(db,ws)
    keys=([g['id'] for g in event['groups']] if groups is None else groups) if stage=='CLUSTER' else [event.get('group_id')]
    for key in keys:
        path=(event['actor']['id'],event['block_id'],event['version_id'],event['report_id'],key,stage)
        current=store.view(*path)
        store.save(*path,enabled=True,expected_revision=current['revision'],
                   binding_sha256=current['binding_sha256'],request_id=uuid4())
