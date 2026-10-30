"""Bounded, non-content diagnostics for model rationale validation failures."""
from common.sanity_common import ReviewError

VERSION = 1
MAX_NUMBER = 1000000
CODE_FIELDS = {
    'RATIONALE_MISSING':'group_rationales',
    'RATIONALE_COUNT':'group_rationales',
    'RATIONALE_ENTRY_SHAPE':'group_rationales',
    'RATIONALE_GROUP_ID':'group_id',
    'RATIONALE_REASON':'reason',
    'RATIONALE_EVIDENCE_TYPE':'evidence_ids',
    'RATIONALE_EVIDENCE_COUNT':'evidence_ids',
    'RATIONALE_EVIDENCE_ITEM_TYPE':'evidence_ids',
    'RATIONALE_EVIDENCE_DUPLICATE':'evidence_ids',
    'RATIONALE_EVIDENCE_OUTSIDE_GROUP':'evidence_ids',
    'RATIONALE_REFERENCE_TYPE':'reference_groups',
    'RATIONALE_REFERENCE_COUNT':'reference_groups',
    'RATIONALE_REFERENCE_ENTRY_SHAPE':'reference_groups',
    'RATIONALE_REFERENCE_ENTRY_VALUE':'reference_groups',
    'RATIONALE_REFERENCE_CASE_UNKNOWN':'reference_groups',
    'RATIONALE_REFERENCE_GROUP_UNKNOWN':'reference_groups',
    'RATIONALE_REFERENCE_DUPLICATE':'reference_groups',
    'RATIONALE_REFERENCES_MISMATCH':'reference_ids',
}
_REQUIRED = frozenset(('schema_version','code','field'))
_OPTIONAL = frozenset(('group_index','actual_count','actual_length'))


def sanitize_validation_failure(value):
    """Return a safe copy of an exact known diagnostic, otherwise None."""
    if not isinstance(value,dict) or not _REQUIRED <= set(value) <= _REQUIRED | _OPTIONAL:
        return None
    code, field = value.get('code'), value.get('field')
    if (type(value.get('schema_version')) is not int or value['schema_version'] != VERSION
            or not isinstance(code,str) or code not in CODE_FIELDS
            or not isinstance(field,str) or CODE_FIELDS[code] != field):
        return None
    if any(type(value[key]) is not int or not 0 <= value[key] <= MAX_NUMBER
           for key in _OPTIONAL if key in value):
        return None
    return {key:value[key] for key in ('schema_version','code','field','group_index','actual_count','actual_length')
            if key in value}


def raise_rationale(code, field, *, group_index=None, actual_count=None, actual_length=None):
    """Raise the stable outer error while attaching a strictly bounded proof."""
    detail={'schema_version':VERSION,'code':code,'field':field}
    for key,value in (('group_index',group_index),('actual_count',actual_count),('actual_length',actual_length)):
        if value is not None:
            detail[key]=value
    detail=sanitize_validation_failure(detail)
    if detail is None:
        raise ValueError('Invalid static rationale diagnostic')
    messages={'RATIONALE_EVIDENCE_TYPE':'Representative IDs must belong to this group',
              'RATIONALE_EVIDENCE_COUNT':'Representative IDs must belong to this group',
              'RATIONALE_EVIDENCE_ITEM_TYPE':'Representative IDs must belong to this group',
              'RATIONALE_EVIDENCE_DUPLICATE':'Representative IDs must belong to this group',
              'RATIONALE_EVIDENCE_OUTSIDE_GROUP':'Representative IDs must belong to this group',
              'RATIONALE_REFERENCE_CASE_UNKNOWN':'Historical group must be an available confirmed example',
              'RATIONALE_REFERENCE_GROUP_UNKNOWN':'Historical group must be an available confirmed example',
              'RATIONALE_REFERENCE_DUPLICATE':'Historical group must be an available confirmed example',
              'RATIONALE_REFERENCES_MISMATCH':'Global references must equal the per-group references'}
    error=ReviewError('INVALID_RATIONALE',messages.get(code,'Invalid classification contract'))
    error.validation_failure=detail
    raise error
