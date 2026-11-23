"""Validation and pin construction for OA AI job ledger events."""
from __future__ import annotations

import re

from common.sanity_ai_projection import source_policy_pin
from common.sanity_ai_retry import MAX_RESUMES, MAX_RETRIES, valid_receipt
from common.sanity_clustering import check
from common.sanity_common import ReviewError, digest
from common.sanity_validation_diagnostics import sanitize_validation_failure
from oa.sanity_ai_store import JOB_TERMINAL as TERMINAL, error_code, parse_timestamp


def source_pin(context):
    return {"scope": context["scope"], "source_sha256": context["source_sha256"],
            "report_id": context["report"]["id"], "version_id": context["report"]["version_id"],
            "group_id": context.get("group_id"), "cluster_revision": context.get("cluster_revision"),
            "cluster_event_id": context.get("cluster_event_id"),
            "membership_sha256": context.get("membership_sha256"),
            "clustering_rationale_sha256": digest(context.get("clustering_rationale")),
            **source_policy_pin(context)}


def case_pins(cases):
    return [{**{key: case[key] for key in ("id", "report_id", "revision", "source_sha256")},
             **({"group_id": case["group_id"]} if case.get("group_id") else {}), "sha256": digest(case)}
            for case in cases]


def validate_retry(previous, data):
    check(previous["data"].get("fail_safe") is True and "batch" not in data
          and (valid_receipt(data.get("retry_failure")) and data["retry_failure"]["model_called"] is data.get("model_called")
               and data["retry_failure"]["error"] == data.get("error")
               and data.get("model_attempted") is data.get("model_called")
               or "retry_failure" not in data and data.get("error") == "AI_CONNECT_FAILED" and data.get("model_called") is False)
          and data.get("retry_count") == previous["data"].get("retry_count", 0) + 1
          and 1 <= data["retry_count"] <= MAX_RETRIES and "retry_after" in data, "AI_JOB_CORRUPT")


def validate_job_data(event):
    """Fail closed on malformed application-owned ledger links, before use."""
    data = event.get("data")
    required = {"command", "target", "pins", "retrieval", "model", "created_at", "model_called"}
    optional = {"model_attempted", "call_token", "started_at", "deadline", "result", "error", "finished_at", "event_id", "cancelled_by", "batch", "result_ref", "connection_label",
                "worker_site", "total_records", "call_seconds", "fail_safe", "retry_count", "retry_after", "retry_failure",
                "resume_count", "resume_request_id", "resumed_by", "resume_checkpoint", "credential_pin", "unknown_reason", "validation_failure", "completion_reason"}
    check(isinstance(data, dict) and required <= set(data) <= required | optional, "AI_JOB_CORRUPT")
    if 'completion_reason' in data:
        check(data['completion_reason'] == 'MAX_ITERATIONS', 'AI_JOB_CORRUPT')
    command, pins = data["command"], data["pins"]
    command_keys = {"user_id", "block_id", "version_id", "report_id", "expected_revision",
                    "request_id", "group_id", "expected_cluster_revision"}
    check(isinstance(command, dict) and command_keys <= set(command) <= command_keys | {"model_id", "connection_id"}, "AI_JOB_CORRUPT")
    if "connection_id" in command:
        check(isinstance(command["connection_id"], str) and re.fullmatch(r"[0-9a-f-]{36}", command["connection_id"])
              and isinstance(data.get("connection_label"), str) and 1 <= len(data["connection_label"]) <= 100, "AI_JOB_CORRUPT")
    else:
        check("connection_label" not in data, "AI_JOB_CORRUPT")
    if "model_id" in command:
        check(isinstance(command["model_id"], str) and command["model_id"] == data["model"], "AI_JOB_CORRUPT")
    check(all(isinstance(command[key], str) and 1 <= len(command[key]) <= 100 for key in
              ("user_id", "block_id", "version_id", "report_id", "request_id"))
          and command["request_id"] == event["job_id"] and type(command["expected_revision"]) is int
          and command["expected_revision"] >= 0, "AI_JOB_CORRUPT")
    group = command["group_id"]
    check((group is None and command["expected_cluster_revision"] is None) or
          (isinstance(group, str) and 1 <= len(group) <= 100 and type(command["expected_cluster_revision"]) is int
           and command["expected_cluster_revision"] >= 1), "AI_JOB_CORRUPT")
    check(data["target"] == digest([command["report_id"], group]) and isinstance(data["retrieval"], dict)
          and isinstance(data["model"], str) and 1 <= len(data["model"]) <= 200
          and (data["model_called"] is None or type(data["model_called"]) is bool)
          and type(data.get("model_attempted", False)) is bool, "AI_JOB_CORRUPT")
    check(isinstance(pins, dict) and set(pins) == {"source", "cases", "criteria_sha256", "config_sha256",
          "identity_fields", "case_budget", "stage"} and pins["stage"] == ("REVIEW" if group is not None else "CLUSTER")
          and isinstance(pins["source"], dict) and isinstance(pins["cases"], list)
          and len(pins["cases"]) <= 100 and all(isinstance(case, dict) for case in pins["cases"])
          and isinstance(pins["identity_fields"], list) and type(pins["case_budget"]) is int
          and 1 <= pins["case_budget"] <= 1024 * 1024, "AI_JOB_CORRUPT")
    source = pins["source"]
    if 'ai_input_policy_sha256' in source:
        check(isinstance(source['ai_input_policy_sha256'], str)
              and re.fullmatch(r'[0-9a-f]{64}', source['ai_input_policy_sha256']), 'AI_JOB_CORRUPT')
    check(source.get("report_id") == command["report_id"] and source.get("version_id") == command["version_id"]
          and source.get("group_id") == group and isinstance(source.get("scope"), dict)
          and source["scope"].get("block_id") == command["block_id"], "AI_JOB_CORRUPT")
    for key in ("config_sha256", "criteria_sha256"):
        check(isinstance(pins[key], str) and re.fullmatch(r"[0-9a-f]{64}", pins[key]), "AI_JOB_CORRUPT")
    parse_timestamp(data["created_at"])
    if "worker_site" in data: check(data["worker_site"] in ("oa", "vwp"), "AI_JOB_CORRUPT")
    if "total_records" in data: check(type(data["total_records"]) is int and 0 <= data["total_records"] <= 1000000, "AI_JOB_CORRUPT")
    if "call_seconds" in data: check(type(data["call_seconds"]) in (int, float) and 0 <= data["call_seconds"] < 86400, "AI_JOB_CORRUPT")
    if "fail_safe" in data: check(data["fail_safe"] is True, "AI_JOB_CORRUPT")
    if "retry_count" in data: check(type(data["retry_count"]) is int and 0 <= data["retry_count"] <= MAX_RETRIES, "AI_JOB_CORRUPT")
    if "retry_failure" in data: check(valid_receipt(data["retry_failure"]), "AI_JOB_CORRUPT")
    if 'validation_failure' in data:
        check(data['validation_failure'] is not None and data.get('error') == 'INVALID_RATIONALE'
              and sanitize_validation_failure(data['validation_failure']) == data['validation_failure'], 'AI_JOB_CORRUPT')
    if 'unknown_reason' in data:
        check(event['status'] == 'UNKNOWN' and data['unknown_reason'] in
              ('READ_TIMEOUT', 'WRITE_TIMEOUT', 'TRANSPORT_ERROR', 'RESPONSE_DEADLINE', 'WORKER_LOST', 'RESERVED_EXPIRED'), 'AI_JOB_CORRUPT')
    if 'credential_pin' in data:
        pin = data['credential_pin']
        check(isinstance(pin, dict) and set(pin) == {'revision', 'scope'}
              and type(pin['revision']) is int and pin['revision'] >= 1
              and isinstance(pin['scope'], str) and re.fullmatch(r'[0-9a-f]{64}', pin['scope'])
              and 'connection_id' in command, 'AI_JOB_CORRUPT')
    if "resume_count" in data:
        check(type(data["resume_count"]) is int and 1 <= data["resume_count"] <= MAX_RESUMES
              and isinstance(data.get("resume_request_id"), str) and re.fullmatch(r"[0-9a-f-]{36}", data["resume_request_id"])
              and data.get("resumed_by") == command["user_id"], "AI_JOB_CORRUPT")
    if "retry_after" in data: parse_timestamp(data["retry_after"])
    if "error" in data:
        check(error_code(ReviewError(data["error"], "")) == data["error"], "AI_JOB_CORRUPT")
    if "call_token" in data:
        check(isinstance(data["call_token"], str) and re.fullmatch(r"[0-9a-f-]{36}", data["call_token"])
              and parse_timestamp(data.get("deadline")) > parse_timestamp(data.get("started_at")), "AI_JOB_CORRUPT")
    if event["status"] in ("RUNNING", "RESULT_READY", "COMPLETED"):
        check("call_token" in data, "AI_JOB_CORRUPT")
    if 'batch' in data:
        batch = data['batch']
        parallel_keys = {'execution', 'max_concurrency'} if isinstance(batch, dict) and 'execution' in batch else set()
        check(isinstance(batch, dict) and type(batch.get('version')) is int and batch['version'] in (1, 2)
              and (batch['version'] == 2 or command['group_id'] is None)
              and set(batch) - parallel_keys in ({'version'}, {'version', 'total_records', 'leaf_count', 'plan', 'credential_revision'}), 'AI_JOB_CORRUPT')
        if parallel_keys:
            check(batch['execution'] in ('parallel-v1', 'parallel-v2') and type(batch.get('max_concurrency')) is int
                  and 2 <= batch['max_concurrency'] <= 1000000, 'AI_JOB_CORRUPT')
        if 'plan' in batch:
            check(type(batch['total_records']) is int and 1 <= batch['total_records'] <= 1000000
                  and type(batch['leaf_count']) is int and 1 <= batch['leaf_count'] <= 100000
                  and isinstance(batch['plan'], dict) and set(batch['plan']) == {'key', 'sha256'}
                  and batch['plan']['key'] == 'round.000' and isinstance(batch['plan']['sha256'], str)
                  and re.fullmatch('[0-9a-f]{64}', batch['plan']['sha256'])
                  and (batch['credential_revision'] is None or type(batch['credential_revision']) is int
                       and batch['credential_revision'] >= 1), 'AI_JOB_CORRUPT')
        if event['status'] in ('RUNNING', 'RESULT_READY', 'COMPLETED'): check('plan' in batch, 'AI_JOB_CORRUPT')
        if 'result_ref' in data:
            check(isinstance(data['result_ref'], dict) and set(data['result_ref']) == {'key', 'sha256'}
                  and data['result_ref']['key'] == 'final' and isinstance(data['result_ref']['sha256'], str)
                  and re.fullmatch('[0-9a-f]{64}', data['result_ref']['sha256']), 'AI_JOB_CORRUPT')
        check('result' not in data, 'AI_JOB_CORRUPT')
    else:
        check('result_ref' not in data, 'AI_JOB_CORRUPT')
    if event["status"] in ("RESULT_READY", "COMPLETED") and 'batch' not in data:
        check(isinstance(data.get("result"), dict) and set(data["result"]) == {"advice", "metadata"}
              and all(isinstance(value, dict) for value in data["result"].values()) and data["model_called"] is True, "AI_JOB_CORRUPT")
    if event['status'] in ('RESULT_READY', 'COMPLETED') and 'batch' in data:
        check('result_ref' in data and data['model_called'] is True, 'AI_JOB_CORRUPT')
    if event["status"] == "COMPLETED":
        check(data.get("event_id") == event["job_id"], "AI_JOB_CORRUPT")
    if event["status"] in TERMINAL:
        parse_timestamp(data.get("finished_at"))
