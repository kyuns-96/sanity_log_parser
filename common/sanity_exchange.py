"""common evidence exchange responsibilities."""
from common.sanity_common import ReviewError, require_id
from common.sanity_db import uuid_value


def request_message_id(request_id):
    return "request-" + str(uuid_value(request_id))


def validate_selector(value):
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ReviewError("INVALID_REQUEST", "Unsupported evidence request")
    require_id(value.get("review_unit_id"), "review_unit_id")
    records = value.get("record_ids")
    if not isinstance(records, list) or not 1 <= len(records) <= 100 or len(set(map(str, records))) != len(records):
        raise ReviewError("INVALID_REQUEST", "Select between 1 and 100 distinct record IDs")
    for record in records:
        require_id(record, "record_id")
    if not isinstance(value.get("reason"), str) or not value["reason"].strip() or len(value["reason"]) > 1000:
        raise ReviewError("INVALID_REQUEST", "A bounded, public request reason is required")
