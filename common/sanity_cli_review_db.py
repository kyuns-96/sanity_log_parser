"""Capability check for the opt-in CLI queue migration; never executes DDL."""
from common.sanity_submission import check


def require_schema(db):
    rows = db.execute("SELECT conname FROM pg_constraint WHERE conrelid IN "
        "(%s::regclass,%s::regclass) AND convalidated AND conname IN "
        "('sanity_vwp_message_kind_v4','sanity_vwp_message_scope_v4',"
        "'sanity_oa_message_kind_v4','sanity_oa_message_scope_v4')",
        (db.schema+'.sanity_vwp_to_oa', db.schema+'.sanity_oa_to_vwp')).fetchall()
    check(len(rows) == 4, 'CLI_REVIEW_SCHEMA_REQUIRED')
