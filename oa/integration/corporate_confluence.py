"""Corporate OA adapter entry point for Create-Only Confluence publishing.

SECURITY & PERMISSION CONTRACT:
- The internal Confluence token has CREATE-ONLY (`POST`) permission (NO `GET`, `PUT`, or `DELETE`).
- Implement `create_confluence_publisher()` and helper modules under `oa/integration/company_confluence/`:
  1. `POST /rest/api/content` with `type="page"`, `space={"key": req.space_key}`,
     `ancestors=[{"id": req.parent_page_id}]`, `title=req.title`,
     `body={"storage": {"value": req.storage_xhtml, "representation": "storage"}}`
     using `Authorization: Bearer <req.personal_pat>`.
  2. Extract the newly created `id` from the `POST` response JSON.
  3. For each item in `req.attachments`, `POST /rest/api/content/{page_id}/child/attachment`
     with header `X-Atlassian-Token: no-check` and multipart `file=(att.filename, att.content_bytes, att.mime_type)`.
  4. Never log `req.personal_pat` or echo raw SDK tracebacks.
"""
from __future__ import annotations

from common.floorplan.confluence import ConfluenceNotConfigured, ConfluencePublisher


def create_confluence_publisher() -> ConfluencePublisher:
    raise ConfluenceNotConfigured(
        "Corporate Confluence publisher is not configured yet. "
        "Wire oa/integration/corporate_confluence.py or use 'HTML 내보내기' for offline sharing."
    )
