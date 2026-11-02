"""Rule codes are a hard grouping boundary, independent of semantic similarity."""
from common.sanity_clustering import check

VERSION = 'rule-code-v1'


def rule_code(record, report):
    value = record.get('fields', {}).get('code')
    if value is None:
        value = report.get('rule_codes', {}).get(record['id'])
    # Old workspace snapshots represented a single rule at report level.
    # Transferred, parsed reports must supply the actual per-record code.
    if value is None and report.get('source_storage') != 'PARTS_V2':
        value = report.get('rule')
    check(isinstance(value, str) and value.strip(), 'RULE_CODE_REQUIRED',
          'A real rule code is required for every grouped record')
    value = value.strip()
    check(len(value) <= 110 and not any(ord(c) < 32 or ord(c) == 127 for c in value),
          'RULE_CODE_INVALID', 'Rule code cannot be represented in a group label')
    return value


def group_code(ids, index, report):
    codes = {rule_code(index[key], report) for key in ids}
    check(len(codes) == 1, 'RULE_CODE_CONFLICT',
          'Records from different rule codes cannot belong to one group')
    return next(iter(codes))


def label_for(code, label):
    text = label.strip()
    if text == code:
        text = '로그 그룹'
    else:
        for separator in (' · ', ' - ', ': ', ' '):
            if text.startswith(code + separator):
                text = text[len(code + separator):].strip()
                break
    text = text or '로그 그룹'
    prefix = code + ' · '
    return prefix + text[:120-len(prefix)].rstrip()


def enforce(groups, records, report):
    index = {row['id']: row for row in records}
    codes = {group['id']:group_code(group['record_ids'], index, report) for group in groups}
    return [{**group, 'label': label_for(codes[group['id']], group['label'])}
            for group in sorted(groups,key=lambda group:(codes[group['id']],group['id']))]


def model_context(context, original):
    """Expose only structural codes even when ai_fields omits code or raw."""
    rows = context['report']['records']
    check(len(rows) == len(original), 'INVALID_PARTITION')
    codes = {visible['id']: rule_code(source, context['report'])
             for visible, source in zip(rows, original)}
    return {**context, 'report': {**context['report'], 'rule_codes': codes},
            'grouping_policy': VERSION}
