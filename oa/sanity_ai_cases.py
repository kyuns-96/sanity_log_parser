"""Read-only human-case retrieval for OA model execution.

The caller supplies an authorized, revision-pinned current-target context.
Historical knowledge scope is derived from the registered server catalog,
never model/browser lists. Internal source validation grants no original-source
access to the actor; only shared confirmed knowledge leaves this module.
"""
from collections import Counter
from functools import wraps

from common.sanity_ai_retrieval import (METHOD, SCOPE_KEYS, compatible, comparison,
    query_profile, rank_case, select_ranked, selection_key)
from common.sanity_ai_knowledge import shared_case
from common.sanity_clustering import check
from common.sanity_clustering_advice import evidence, partition
from common.sanity_common import ReviewError, canonical_json, digest
from common.sanity_db import json_value
from common.sanity_submission import source_digest
from common.sanity_review_advice import validate_stored_review_exception
import common.sanity_workspace_rationale as rationale
from oa.sanity_workspace import WorkspaceStore, record_id
from oa.sanity_workspace_review import WorkspaceReviewStore


def _validation_operation(method):
    """Keep graph validation memo strictly inside one public operation."""
    @wraps(method)
    def run(self, context, *args, **kwargs):
        self._begin_validation(context["report"]["id"], kwargs.get("stage"), context=context)
        try:
            return method(self, context, *args, **kwargs)
        finally:
            self._validation_stats = {"unique_nodes": len(self._validation_seen),
                                      "work_units": self._validation_work}
            self._validation_memo.clear()
            self._validation_active.clear()
            self._validation_seen.clear()
            self._validation_target = None
            self._validation_scopes = ()
    return run


class CaseRetriever:
    page_size = 32
    max_reference_nodes = 1000
    max_reference_work = 10000
    max_reference_depth = 128
    max_reference_bytes = 128 * 1024 * 1024

    def __init__(self, db, workspace=None):
        self.db = db
        self.workspace = workspace if workspace is not None else WorkspaceStore(db)
        self.reviews = WorkspaceReviewStore(db, ws=self.workspace)
        self._case_read_budget = 1024 * 1024

    def _begin_validation(self, target, stage, *, context=None):
        self._validation_target = (target, stage)
        self._validation_memo, self._validation_active, self._validation_seen = {}, set(), set()
        self._validation_work, self._validation_bytes = 0, 0
        scope = (context or {}).get("scope", {})
        actor = (context or {}).get("_actor_user_id")
        # Missing authority is a narrow diagnostic, never a global read grant.
        scopes = self.workspace.case_reference_scopes(actor) if actor and hasattr(self, "workspace") else [scope]
        self._validation_scopes = tuple(sorted({(value["project_id"], value["block_id"])
            for value in scopes if value.get("project_id") and value.get("block_id")}))

    def _scope_clause(self, alias):
        pairs = [(str(record_id("WS_PROJECT", project)), str(record_id("WS_BLOCK", block)))
                 for project, block in self._validation_scopes]
        return ("EXISTS (SELECT 1 FROM jsonb_array_elements(%s::jsonb) grant_scope "
                "WHERE grant_scope->>0=" + alias + ".workspace_project_ref::text "
                "AND grant_scope->>1=" + alias + ".workspace_block_ref::text)", (json_value(pairs),))

    def _report_authorized(self, report_id):
        """Internal registered-knowledge capability, not an actor raw grant."""
        condition, parameters = self._scope_clause("r")
        return self.db.execute("SELECT 1 FROM {oa} r WHERE r.record_type='WS_REPORT' "
            "AND r.workspace_key=%s AND " + condition, (report_id, *parameters)).fetchone() is not None

    def _charge_work(self, units):
        self._validation_work += units
        check(self._validation_work <= self.max_reference_work, "AI_REFERENCE_LIMIT",
              "Explicit reference validation work budget exceeded")

    def _charge_event(self, event):
        # Count bodies while their dependencies are active as well as after
        # memoization. A deep chain cannot retain unbounded event bodies.
        self._validation_bytes += len(canonical_json(event))
        check(self._validation_bytes <= self.max_reference_bytes, "AI_REFERENCE_LIMIT",
              "Explicit reference graph byte budget exceeded")

    def _head_row(self, kind, key):
        self._charge_work(1)
        return self.reviews.row(kind, key)

    def _validated(self, key, exclude_report, validate):
        if getattr(self, "_validation_target", None) is None:
            # Private validation helpers remain usable for diagnostics; public
            # methods always create and clear their own operation explicitly.
            self._begin_validation(exclude_report, key[0])
        check(self._validation_target[0] == exclude_report, "INVALID_REQUEST")
        if key in self._validation_memo:
            return self._validation_memo[key]
        if key in self._validation_active:
            return None
        check(len(self._validation_seen) < self.max_reference_nodes
              and len(self._validation_active) < self.max_reference_depth,
              "AI_REFERENCE_LIMIT", "Explicit unique-node or reference-depth budget exceeded")
        self._validation_seen.add(key)
        self._validation_active.add(key)
        try:
            result = validate()
            # Cache only completed validation, including stale results. Shared
            # ancestors in a DAG are distinct from a node on the active path.
            self._validation_memo[key] = result
            return result
        finally:
            self._validation_active.remove(key)

    def _bounded_row(self, kind, key):
        self._charge_work(2)
        condition, parameters = self._scope_clause("r")
        size = self.db.execute("SELECT octet_length(payload::text)"
            "+COALESCE((payload->>'rationale_bytes')::bigint,0)"
            "+COALESCE((payload->>'source_bytes')::bigint,0) AS bytes FROM {oa} r "
            "WHERE record_type=%s AND workspace_key=%s AND " + condition, (kind, key, *parameters)).fetchone()
        if size is None:
            return None
        check(size["bytes"] <= self._case_read_budget, "AI_CASE_BUDGET", "Complete example dependency exceeds the byte budget")
        return self.reviews.row(kind, key)

    def _source_version(self, event, source):
        """Compare a case to its own immutable source, never to the target."""
        check(source.get("id") == event["report_id"] and source.get("version_id") == event["version_id"]
              and all(source.get(key) == event["scope"].get(key) for key in (*SCOPE_KEYS, "eco") if key != "layout"),
              "STATE_CORRUPT")
        check(all(key not in event or event[key] == source.get(key) for key in ("project_id", "block_id", "eco")),
              "STATE_CORRUPT")
        version = self._bounded_row("WS_VERSION", event["version_id"])
        check(version and version["payload"]["id"] == event["version_id"]
              and version["payload"]["block_id"] == source["block_id"]
              and version["payload"]["project_id"] == source["project_id"]
              and version["payload"]["layout"] == event["scope"]["layout"], "STATE_CORRUPT")
        stored = event.get("source_version")
        if stored is not None:
            check(all(stored.get(key) == version["payload"].get(key)
                      for key in ("id", "project_id", "block_id", "net_ver", "revision", "layout", "source_pre_id")),
                  "STATE_CORRUPT")
        return stored if stored is not None else version["payload"]

    def _scan(self, context, stage, profile):
        """Keyset-scan all eligible heads, returning only bounded metadata.

        Only confirmed knowledge fields are searched, never original source
        text or extracted fields. PostgreSQL handles ordinary event JSON;
        compressed group rationales are unpacked one bounded event at a time.
        This first lexical implementation needs no new DB index or embedding
        service; large histories may require a later measured indexing change.
        """
        group = stage == "REVIEW" and bool(context.get("group_id"))
        kind = "WS_CLUSTER" if stage == "CLUSTER" else "WS_GROUP_REVIEW" if group else "WS_REVIEW"
        event_ref = "workspace_review_event_ref" if kind == "WS_REVIEW" else "workspace_pipeline_event_ref"
        report_ref = "workspace_review_report_ref" if kind == "WS_REVIEW" else "workspace_pipeline_report_ref"
        scope = context["scope"]
        check(all(key in scope for key in SCOPE_KEYS), "INVALID_SCOPE")
        scope_sql, scope_parameters = self._scope_clause("h")
        cursor = None
        while True:
            query = "SELECT e.created_at,e.workspace_key,e.record_id AS event_record_id," \
                "h.version AS head_revision,h.state->>'event_id' AS head_event_id," \
                "h.state->'task'->>'status' AS task_status," \
                "h.state->'codex_jobs'->(h.state->>'codex_job_id')->>'status' AS job_status," \
                "jsonb_build_object('id',e.payload->'id','report_id',e.payload->'report_id'," \
                "'version_id',e.payload->'version_id','scope',e.payload->'scope','revision',e.payload->'revision'," \
                "'confirmed',e.payload->'confirmed','conclusion',e.payload->'conclusion'," \
                "'group_id',e.payload->'group_id','compatible_versions',e.payload->'compatible_versions'," \
                "'reusable',e.payload->'reusable','source_sha256',e.payload->'source_sha256') AS candidate," \
                "e.payload->>'rationale_encoding' AS rationale_encoding," \
                "CASE WHEN jsonb_typeof(s.payload->'rule')='string' THEN s.payload->'rule' END AS report_rule," \
                "0 AS estimated_bytes," \
                "ARRAY(SELECT term FROM unnest(%s::text[]) term WHERE " \
                "strpos(lower(jsonb_build_object('reason',e.payload->'reason','criteria',e.payload->'criteria'," \
                "'scope',e.payload->'scope','report_rule',CASE WHEN jsonb_typeof(s.payload->'rule')='string' THEN s.payload->'rule' END," \
                "'conditions',e.payload->'conditions','exception',e.payload->'exception'," \
                "'group_labels',jsonb_path_query_array(e.payload,'$.groups[*].label')," \
                "'group_reasons',jsonb_path_query_array(e.payload,'$.rationales.*.content.reason')," \
                "'reference_reasons',jsonb_path_query_array(e.payload,'$.rationales.*.content.reference_groups[*].reason'))::text),term)>0) AS matched_terms " \
                "FROM {oa} h JOIN {oa} e ON e.record_id=h." + event_ref + " " \
                "JOIN {oa} s ON s.record_id=h." + report_ref + " " \
                "WHERE h.record_type=%s AND " + scope_sql + " " \
                "AND e.workspace_project_ref=h.workspace_project_ref AND e.workspace_block_ref=h.workspace_block_ref " \
                "AND s.workspace_project_ref=h.workspace_project_ref AND s.workspace_block_ref=h.workspace_block_ref " \
                "AND e.payload->>'report_id'<>%s AND " \
                "TRUE "
            parameters = (profile["terms"], kind) + scope_parameters + (
                context["report"]["id"],)
            query += "AND e.payload->>'confirmed'='true' " if stage == "CLUSTER" else \
                "AND e.payload->>'conclusion' IN ('PASS','FAIL') "
            if cursor:
                query += "AND (e.created_at,e.workspace_key)>(%s,%s) "
                parameters += cursor
            rows = self.db.execute(query + "ORDER BY e.created_at,e.workspace_key LIMIT %s",
                                   parameters + (self.page_size,)).fetchall()
            if not rows:
                return
            for row in rows:
                candidate = row["candidate"]
                check(candidate["id"] == row["head_event_id"]
                      and candidate["revision"] == row["head_revision"], "STATE_CORRUPT")
                score = rank_case(profile, candidate, matched_terms=row["matched_terms"])
                over_budget = False
                if row["rationale_encoding"]:
                    # Compressed rationale strings are opaque to SQL lexical
                    # matching. Unpack one bounded event at a time; raw source
                    # reports/parts are still never scanned for relevance.
                    try:
                        packed = self._bounded_row("WS_CLUSTER_EVENT", candidate["id"])
                        check(packed is not None, "STATE_CORRUPT")
                        knowledge = shared_case({**rationale.unpack(packed["payload"]), "report_rule": row.get("report_rule")})
                        score = rank_case(profile, knowledge)
                    except ReviewError as error:
                        if error.code != "AI_CASE_BUDGET":
                            raise
                        over_budget, score = True, 0.0
                yield {"case": candidate, "bytes": row["estimated_bytes"],
                       "score": score, "over_budget": over_budget,
                       "active": row["task_status"] in ("PENDING", "RUNNING", "FAILED", "STALE")
                                 or row["job_status"] in ("RUNNING", "RESULT_READY")}
            cursor = (rows[-1]["created_at"], rows[-1]["workspace_key"])

    def _cluster(self, report_id, exclude_report, visited):
        """Validate current reference chains without using the all-case catalog."""
        if report_id == exclude_report or not self._report_authorized(report_id):
            return None
        return self._validated(("CLUSTER", report_id), exclude_report,
                               lambda: self._validate_cluster(report_id, exclude_report))

    def _validate_cluster(self, report_id, exclude_report):
        head = self._head_row("WS_CLUSTER", report_id)
        if not head or head["state"].get("task", {}).get("status") in ("PENDING", "RUNNING", "FAILED", "STALE"):
            return None
        job = head["state"].get("codex_jobs", {}).get(head["state"].get("codex_job_id"), {})
        if job.get("status") in ("RUNNING", "RESULT_READY"):
            return None
        row = self._bounded_row("WS_CLUSTER_EVENT", head["state"].get("event_id"))
        if not row:
            return None
        event = rationale.unpack(row["payload"])
        self._charge_event(event)
        check(row["record_id"] == head["workspace_pipeline_event_ref"]
              and event["revision"] == head["version"] and event["report_id"] == report_id
              and event["id"] == head["state"].get("event_id"), "STATE_CORRUPT")
        if not event.get("confirmed") or not rationale.complete(event):
            return None
        source = self._bounded_row("WS_REPORT", report_id)
        check(source is not None and source_digest(source["payload"]) == event["source_sha256"], "STATE_CORRUPT")
        self._source_version(event, source["payload"])
        original = source["payload"]
        if original.get("source_storage") == "PARTS_V2":
            from oa.sanity_workspace_sources import hydrate
            original = hydrate(self.db, original)
        partition(event["groups"], evidence({"report": original}))
        del original
        del source
        dependencies = []
        for reference in event.get("references", []):
            current = self._cluster(reference["report_id"], exclude_report, None)
            if (not current or current["id"] != reference["id"]
                    or current["source_sha256"] != reference["source_sha256"]
                    ):
                return None
            dependencies.append(current)
        for group in event["groups"]:
            rationale.validate_content(event["rationales"][group["id"]]["content"], group, dependencies)
        return event

    def _review(self, report_id, group_id, exclude_report, visited):
        if report_id == exclude_report or not self._report_authorized(report_id):
            return None
        return self._validated(("REVIEW", report_id, group_id), exclude_report,
                               lambda: self._validate_review(report_id, group_id, exclude_report))

    def _validate_review(self, report_id, group_id, exclude_report):
        kind = "WS_GROUP_REVIEW" if group_id else "WS_REVIEW"
        head_key = "group-" + digest([report_id, group_id]) if group_id else report_id
        head = self._head_row(kind, head_key)
        if not head:
            return None
        row = self._bounded_row(kind + "_EVENT", head["state"].get("event_id"))
        check(row is not None, "STATE_CORRUPT")
        event = row["payload"]
        validate_stored_review_exception(event)
        self._charge_event(event)
        ref = "workspace_pipeline_event_ref" if group_id else "workspace_review_event_ref"
        check(head[ref] == row["record_id"] and head["version"] == event["revision"]
              and event["report_id"] == report_id and event.get("group_id") == group_id
              and event["id"] == head["state"].get("event_id"), "STATE_CORRUPT")
        if event.get("conclusion") not in ("PASS", "FAIL"):
            return None
        source = self._bounded_row("WS_REPORT", report_id)
        check(source and source_digest(source["payload"]) == event["source_sha256"], "STATE_CORRUPT")
        self._source_version(event, source["payload"])
        del source
        if group_id:
            cluster = self._cluster(report_id, exclude_report, set())
            if (not cluster or cluster["id"] != event["cluster_event_id"]
                    or cluster["membership_sha256"] != event["membership_sha256"]):
                return None
            group = next((group for group in cluster["groups"] if group["id"] == group_id), None)
            check(group == event["group_snapshot"], "STATE_CORRUPT")
        if event.get("proposal_id"):
            proposal = self._bounded_row(kind + "_PROPOSAL", event["proposal_id"])
            check(proposal and proposal["payload"]["report_id"] == report_id
                  and proposal["payload"].get("group_id") == group_id, "STATE_CORRUPT")
            references = [{key: reference[key] for key in ("id", "report_id", "source_sha256", "group_id")
                           if key in reference} for reference in proposal["payload"].get("references", [])]
            del proposal
            for reference in references:
                current = self._review(reference["report_id"], reference.get("group_id"), exclude_report, None)
                if (not current or current["id"] != reference["id"]
                        or current["source_sha256"] != reference["source_sha256"]
                        ):
                    return None
        return event

    def _hydrate(self, candidate, context, stage):
        meta = candidate["case"]
        target_id, source_id = context["report"]["id"], meta["report_id"]
        if not self._report_authorized(source_id):
            return None
        if stage == "CLUSTER":
            event = self._cluster(source_id, target_id, set())
            if not event or event["id"] != meta["id"]:
                return None
        else:
            event = self._review(source_id, meta.get("group_id"), target_id, set())
            if not event or event["id"] != meta["id"]:
                return None
            if context.get("group_id"):
                cluster = self._cluster(source_id, target_id, set())
        if not compatible(context, event, stage=stage):
            return None
        source_row = self._bounded_row("WS_REPORT", source_id)
        check(source_row is not None, "STATE_CORRUPT")
        source = source_row["payload"]
        check(source_digest(source) == event["source_sha256"], "STATE_CORRUPT")
        check(source.get("rule") is None or isinstance(source["rule"], str), "STATE_CORRUPT")
        source_version = self._source_version(event, source)
        if source.get("source_storage") == "PARTS_V2":
            from oa.sanity_workspace_sources import hydrate
            source = hydrate(self.db, source)
        records = evidence({"report": source})
        if stage == "CLUSTER":
            partition(event["groups"], records)
        elif context.get("group_id"):
            partition(cluster["groups"], records)
            members = set(event["group_snapshot"]["record_ids"])
            records = [record for record in records if record["id"] in members]
            check(len(records) == len(members), "STATE_CORRUPT")
        enriched = {**event, "source_version": source_version}
        return shared_case({**enriched, "source_record_count": source["record_count"], "report_rule": source.get("rule"),
                            "retrieval_comparison": comparison(context, enriched)})

    @_validation_operation
    def select(self, context, *, stage, limit=8, max_bytes=1024 * 1024):
        check(stage in ("CLUSTER", "REVIEW") and type(limit) is int and 1 <= limit <= 100
              and type(max_bytes) is int and 0 < max_bytes <= 128 * 1024 * 1024, "INVALID_REQUEST")
        profile = query_profile(context)
        # Validate full immutable sources internally, but only complete shared
        # human knowledge counts toward the model/browser example budget.
        self._case_read_budget = 128 * 1024 * 1024
        counts = Counter()
        selected, cursors, passes = [], {}, 0
        window_size = max(16, limit * 2)
        # Window cursors permit advancing past stale/oversized high scorers;
        # an arbitrary recent-N cutoff never hides older eligible examples.
        while len(selected) < limit:
            windows = {}
            scanned = 0
            for row in self._scan(context, stage, profile):
                scanned += 1
                if row.get("over_budget"):
                    if passes == 0:
                        counts["over_budget"] += 1
                    continue
                if row["active"] or not compatible(context, row["case"], stage=stage):
                    if passes == 0:
                        counts["ineligible"] += 1
                    continue
                if row["score"] <= 0:
                    if passes == 0:
                        counts["no_lexical_overlap"] += 1
                    continue
                if row["bytes"] > max_bytes:
                    if passes == 0:
                        counts["over_budget"] += 1
                    continue
                bucket = row["case"].get("conclusion") or "CLUSTER"
                if bucket in cursors and selection_key(row) <= cursors[bucket]:
                    continue
                window = windows.setdefault(bucket, [])
                window.append(row)
                window.sort(key=selection_key)
                del window[window_size:]
            if passes == 0:
                counts["scanned_heads"] = scanned
            passes += 1
            if not windows:
                break
            window_rows = []
            for bucket, rows in windows.items():
                cursors[bucket] = selection_key(rows[-1])
                window_rows.extend(rows)
            ordered = sorted(window_rows, key=selection_key)
            # Try a relevant opposing verdict immediately after the best case.
            # Hydrate one candidate at a time, keeping at most the output budget
            # plus one bounded candidate, not a window of complete reports.
            if limit >= 2 and ordered:
                first = selected[0] if selected else ordered[0]
                conclusion = first["case"].get("conclusion")
                if conclusion in ("PASS", "FAIL"):
                    opposite = next((row for row in ordered if row["case"].get("conclusion")
                                     in ("PASS", "FAIL") and row["case"]["conclusion"] != conclusion), None)
                    if opposite:
                        ordered.remove(opposite)
                        ordered.insert(0 if selected else 1, opposite)
            for row in ordered:
                try:
                    case = self._hydrate(row, context, stage)
                except ReviewError as error:
                    if error.code != "AI_CASE_BUDGET":
                        raise
                    counts["over_budget"] += 1
                    continue
                if case is None:
                    counts["stale_or_self_dependent"] += 1
                    continue
                size = len(canonical_json(case))
                if size > max_bytes:
                    counts["over_budget"] += 1
                    continue
                selected = select_ranked(selected + [{"case": case, "score": row["score"], "bytes": size}],
                                         limit=limit, max_bytes=max_bytes)
                if len(selected) >= limit:
                    break
            # Nothing remaining can fit once the smallest complete JSON object
            # would exceed the byte budget; otherwise continue through history.
            if sum(row["bytes"] for row in selected) >= max_bytes:
                break
        cases = [row["case"] for row in selected]
        metadata = {"schema_version": 1, "method": METHOD, "semantic_search": False,
                    "query_sha256": profile["query_sha256"], "query_terms": profile["terms"],
                    "query_feature_count": profile["feature_count"],
                    "omitted_query_features": profile["omitted_query_features"],
                    "source_record_count": profile["source_record_count"], "scope_keys": list(SCOPE_KEYS),
                    "eco_policy": "COMPARE_AS_EXECUTION_CONDITION",
                    "version_policy": "COMPARE_ALL_VERSIONS_WITHOUT_OPT_IN",
                    "reference_policy": "GLOBAL_REGISTERED_SHARED_HUMAN_CONFIRMED_KNOWLEDGE",
                    "evidence_mode": "SHARED_CONFIRMED_KNOWLEDGE",
                    "source_access_policy": "ORIGINAL_SOURCE_ENDPOINTS_KEEP_CURRENT_RBAC",
                    "scope_policy": "GLOBAL_REGISTERED_PROJECTS_BLOCKS_COMPARE_METADATA",
                    "search_fields": ["reason", "criteria", "conditions", "exception", "group_labels", "group_rationales", "scope", "report_rule"],
                    "validity_checked": "SELECTED_CASES_AND_REFERENCE_DEPENDENCIES",
                    "search_complete": True, "scan_passes": passes,
                    "counts": dict(counts), "limit": limit, "max_bytes": max_bytes,
                    "selected_bytes": sum(row["bytes"] for row in selected),
                    "selected": [{"id": row["case"]["id"], "revision": row["case"]["revision"],
                                  "report_id": row["case"]["report_id"], "group_id": row["case"].get("group_id"),
                                  "source_sha256": row["case"]["source_sha256"], "sha256": digest(row["case"]),
                                  "score": row["score"]}
                                 for row in selected]}
        return {"cases": cases, "metadata": metadata}

    @_validation_operation
    def revalidate(self, context, cases, *, stage):
        """Recheck pinned examples, independent of unrelated new confirmations.

        Call under the same mutation/authorization locks used to apply a model
        result. A case correction or changed dependency invalidates the result;
        a new, unrelated case does not cause catalog-hash churn.
        """
        check(stage in ("CLUSTER", "REVIEW") and isinstance(cases, list) and len(cases) <= 100,
              "INVALID_REQUEST")
        check(len(canonical_json(cases)) <= 128 * 1024 * 1024, "AI_CASE_BUDGET")
        check(len({case["id"] for case in cases}) == len(cases), "INVALID_REQUEST")
        self._case_read_budget = 128 * 1024 * 1024
        for case in cases:
            if not compatible(context, case, stage=stage):
                return False
            current = self._hydrate({"case": case}, context, stage)
            if current is None or digest(current) != digest(case):
                return False
        return True

    @_validation_operation
    def resolve_references(self, context, references, *, stage, max_bytes=1024 * 1024):
        """Resolve pinned IDs without a relevance search or a catalog hash.

        ``sha256`` may pin the entire hydrated example for background jobs.
        Views can display stale IDs and let a human explicitly replace them.
        No stale reference is silently substituted with a similar case.
        """
        check(stage in ("CLUSTER", "REVIEW") and isinstance(references, list) and len(references) <= 100
              and type(max_bytes) is int and 0 < max_bytes <= 128 * 1024 * 1024, "INVALID_REQUEST")
        check(all(isinstance(ref, dict) and all(key in ref for key in
                  ("id", "report_id", "revision", "source_sha256")) for ref in references), "INVALID_REQUEST")
        check(len({ref["id"] for ref in references}) == len(references), "INVALID_REQUEST")
        self._case_read_budget = 128 * 1024 * 1024
        kind = "WS_CLUSTER_EVENT" if stage == "CLUSTER" else \
            "WS_GROUP_REVIEW_EVENT" if context.get("group_id") else "WS_REVIEW_EVENT"
        cases, stale, over_budget, used = [], [], [], 0
        for ref in references:
            # Check permitted scope using a small projection before materializing
            # a caller-specified event or following its historical dependencies.
            condition, parameters = self._scope_clause("e")
            row = self.db.execute("SELECT jsonb_build_object('id',payload->'id','report_id',payload->'report_id',"
                "'revision',payload->'revision','source_sha256',payload->'source_sha256','scope',payload->'scope',"
                "'version_id',payload->'version_id','compatible_versions',payload->'compatible_versions',"
                "'confirmed',payload->'confirmed','conclusion',payload->'conclusion','group_id',payload->'group_id') AS candidate "
                "FROM {oa} e WHERE record_type=%s AND workspace_key=%s AND " + condition,
                (kind, ref["id"], *parameters)).fetchone()
            if (not row or not compatible(context, row["candidate"], stage=stage)
                    or any(row["candidate"].get(key) != ref[key] for key in
                           ("id", "report_id", "revision", "source_sha256"))
                    or "group_id" in ref and row["candidate"].get("group_id") != ref["group_id"]):
                stale.append(ref["id"])
                continue
            try:
                case = self._hydrate({"case": row["candidate"]}, context, stage)
            except ReviewError as error:
                if error.code != "AI_CASE_BUDGET":
                    raise
                over_budget.append(ref["id"])
                continue
            if case is None or "sha256" in ref and ref["sha256"] != digest(case):
                stale.append(ref["id"])
                continue
            size = len(canonical_json(case))
            if used + size > max_bytes:
                over_budget.append(ref["id"])
                continue
            cases.append(case)
            used += size
        return {"cases": cases, "stale_ids": stale, "over_budget_ids": over_budget}
