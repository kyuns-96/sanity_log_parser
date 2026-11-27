"""Create-Only (POST-only, zero GET/PUT/DELETE) Confluence Storage Format builder & Protocol."""
from __future__ import annotations

from dataclasses import dataclass
from html import escape
import re
from typing import Any, Protocol

_PAGE_ID_QUERY_RE = re.compile(r"[?&]pageId=(\d+)")
_PAGES_PATH_RE = re.compile(r"/pages/(?:viewpage\.action/)?(\d+)")
_SPACE_PATH_RE = re.compile(r"/(?:spaces|display)/([A-Za-z0-9_~-]+)")


class ConfluenceNotConfigured(Exception):
    """Raised when the corporate Confluence adapter has not been wired yet."""


class ConfluencePublishError(Exception):
    """Raised when the Confluence server rejects page or attachment creation."""


@dataclass(frozen=True)
class ConfluenceAttachment:
    filename: str
    mime_type: str
    content_bytes: bytes
    comment: str = ""


@dataclass(frozen=True)
class ConfluenceCreateRequest:
    """Strictly Create-Only payload: NO GET, PUT, or DELETE permissions are assumed."""
    space_key: str
    parent_page_id: str
    title: str
    storage_xhtml: str
    attachments: tuple[ConfluenceAttachment, ...]
    personal_pat: str
    actor_user_id: str


@dataclass(frozen=True)
class ConfluenceReceipt:
    page_id: str
    page_url: str | None = None
    uploaded_attachments: tuple[str, ...] = ()


class ConfluencePublisher(Protocol):
    def create_review_page(self, request: ConfluenceCreateRequest) -> ConfluenceReceipt: ...


def parse_confluence_parent_config(raw_url_or_id: str, explicit_space_key: str = "") -> tuple[str, str]:
    """Locally extract (space_key, parent_page_id) from a URL or ID without calling GET on Confluence."""
    text = (raw_url_or_id or "").strip()
    space = (explicit_space_key or "").strip()
    if not space and text:
        sm = _SPACE_PATH_RE.search(text)
        if sm:
            space = sm.group(1)

    page_id = ""
    if text.isdigit():
        page_id = text
    else:
        qm = _PAGE_ID_QUERY_RE.search(text)
        if qm:
            page_id = qm.group(1)
        else:
            pm = _PAGES_PATH_RE.search(text)
            if pm:
                page_id = pm.group(1)
    return space, page_id


def _verdict_badge_xhtml(verdict: str) -> str:
    v = (verdict or "PENDING").upper()
    colors = {
        "PASS": ("#dcfce7", "#166534"),
        "WARN": ("#fef3c7", "#92400e"),
        "ACTION": ("#fee2e2", "#991b1b"),
        "N/A": ("#f1f5f9", "#475569"),
    }
    bg, fg = colors.get(v, ("#e2e8f0", "#334155"))
    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:4px;'
        f'background:{bg};color:{fg};font-weight:700;font-size:12px;">{escape(v)}</span>'
    )


def _fmt_val(v: Any, suffix: str = "") -> str:
    if v is None or v == "":
        return "—"
    return f"{escape(str(v))}{suffix}"


def build_confluence_storage_xhtml(
    *,
    project_name: str,
    block_name: str,
    is_top: bool,
    fp_ver: str,
    prev_fp_ver: str | None,
    linked_net_ver: str | None,
    overall_verdict: str,
    summary_note: str,
    reviewer: str,
    published_at_display: str,
    publish_rev: int,
    dashboard_link: str | None,
    metrics: dict[str, Any] | None,
    checklist_rows: list[dict[str, Any]],
    review_pins: list[dict[str, Any]],
    bist_summary: list[dict[str, Any]],
    proposal_summary: dict[str, Any] | None,
    attachment_specs: list[tuple[str, str]],
) -> str:
    """Build Confluence Storage Format XHTML pre-referencing exact attachment filenames.

    Pre-referencing `<ac:image><ri:attachment ri:filename="..."/></ac:image>` inside the initial
    `POST /rest/api/content` body allows subsequent `POST .../child/attachment` calls to populate
    all images without ever needing `PUT` (update) or `GET` (read) permissions!
    """
    scope_label = "TOP (Full-chip)" if is_top else f"Sub-block ({block_name})"
    out = [
        f"<p><strong>Design Atlas Floorplan Review</strong> · 게시 회차 <code>Rev.{publish_rev}</code> · {escape(published_at_display)}</p>",
        "<table><tbody>",
        f"<tr><th>Project</th><td><strong>{escape(project_name)}</strong></td><th>대상</th><td>{escape(scope_label)}</td></tr>",
        f"<tr><th>Floorplan 버전</th><td><strong>{escape(fp_ver)}</strong>"
        + (f" (이전: {escape(prev_fp_ver)})" if prev_fp_ver else "")
        + f"</td><th>연결 NET_VER</th><td>{_fmt_val(linked_net_ver)}</td></tr>",
        f"<tr><th>종합 판정</th><td>{_verdict_badge_xhtml(overall_verdict)}</td><th>검토자</th><td>{escape(reviewer)}</td></tr>",
    ]
    if dashboard_link:
        out.append(f'<tr><th>Design Atlas 링크</th><td colspan="3"><a href="{escape(dashboard_link)}">{escape(dashboard_link)}</a></td></tr>')
    out.append("</tbody></table>")

    if summary_note.strip():
        out.append(f"<h3>종합 검토 의견</h3><p>{escape(summary_note).replace(chr(10), '<br/>')}</p>")

    # Quantitative metrics (Area, Vth/LVT, Drive Strength)
    if metrics:
        area_m = metrics.get("area") or {}
        vth_m = metrics.get("vth") or {}
        drv_m = metrics.get("drive") or {}
        out.extend([
            "<h3>물리 지표 요약 (Area · LVT Ratio · Drive Strength)</h3>",
            "<table><thead><tr><th>분류</th><th>주요 지표</th><th>수치</th></tr></thead><tbody>",
            "<tr><td><strong>Area</strong></td><td>Total / Logic / SRAM / CTS(%)</td><td>"
            f"Total {_fmt_val(area_m.get('total_area'))} · Logic {_fmt_val(area_m.get('logic_area'))} · "
            f"SRAM {_fmt_val(area_m.get('sram_area'))} · CTS {_fmt_val(area_m.get('cts_area_pct'), '%')}"
            + (f" · Gate Count {_fmt_val(area_m.get('gate_count'))}" if area_m.get("gate_count") else "")
            + "</td></tr>",
            "<tr><td><strong>Vth (LVT 비율)</strong></td><td>SLVT / LVT / RVT / HVT</td><td>"
            f"SLVT {_fmt_val(vth_m.get('vt_slvt_pct'), '%')} · <strong>LVT {_fmt_val(vth_m.get('vt_lvt_pct'), '%')}</strong> · "
            f"RVT {_fmt_val(vth_m.get('vt_rvt_pct'), '%')} · HVT {_fmt_val(vth_m.get('vt_hvt_pct'), '%')}</td></tr>",
            "<tr><td><strong>Drive 분포</strong></td><td>sub-1x (&lt;D1) / D1–D3 / D4+</td><td>"
            f"sub-1x {_fmt_val(drv_m.get('drv_sub1x_pct'), '%')} · D1–D3 {_fmt_val(drv_m.get('drv_d1_d3_pct'), '%')} · "
            f"D4+ {_fmt_val(drv_m.get('drv_d4_plus_pct'), '%')}</td></tr>",
            "</tbody></table>",
        ])

    # Pre-referenced Floorplan attachments
    if attachment_specs:
        out.append("<h3>Floorplan 도면 스냅샷</h3>")
        for fname, caption in attachment_specs:
            out.append(
                f"<p><strong>{escape(caption)}</strong></p>"
                f'<p><ac:image ac:width="920"><ri:attachment ri:filename="{escape(fname)}"/></ac:image></p>'
            )

    # BIST Group summary for sub-blocks
    if bist_summary:
        out.append("<h3>Memory BIST Group 배치 요약</h3>")
        out.append("<table><thead><tr><th>BIST Group</th><th>Controller</th><th>SRAM 수</th><th>최대 이격 거리 (µm)</th><th>상태</th></tr></thead><tbody>")
        for b in bist_summary:
            warn = bool(b.get("dispersed_warning"))
            out.append(
                f"<tr><td><code>{escape(str(b.get('bist_group') or ''))}</code></td>"
                f"<td>{_fmt_val(b.get('controller'))}</td>"
                f"<td>{_fmt_val(b.get('member_count'))}</td>"
                f"<td>{_fmt_val(b.get('max_manhattan_span_um'), ' µm')}</td>"
                f"<td>{_verdict_badge_xhtml('WARN' if warn else 'PASS')}</td></tr>"
            )
        out.append("</tbody></table>")

    # Checklist
    if checklist_rows:
        out.append("<h3>리뷰 체크리스트</h3>")
        out.append("<table><thead><tr><th>항목</th><th>판정</th><th>검토 코멘트 / 근거</th></tr></thead><tbody>")
        for row in checklist_rows:
            out.append(
                f"<tr><td><strong>{escape(str(row.get('title') or ''))}</strong></td>"
                f"<td>{_verdict_badge_xhtml(str(row.get('verdict') or 'PENDING'))}</td>"
                f"<td>{escape(str(row.get('comment') or '—'))}</td></tr>"
            )
        out.append("</tbody></table>")

    # Coordinate Review Pins
    if review_pins:
        out.append("<h3>도면 위치별 코멘트 (Pin Markers)</h3>")
        out.append("<table><thead><tr><th>#</th><th>구분</th><th>좌표 (µm)</th><th>제목 및 상세 의견</th><th>상태</th></tr></thead><tbody>")
        for pin in review_pins:
            out.append(
                f"<tr><td><strong>#{int(pin.get('number') or 0)}</strong></td>"
                f"<td>{_verdict_badge_xhtml(str(pin.get('severity') or 'ACTION'))}</td>"
                f"<td><code>({pin.get('x')}, {pin.get('y')})</code></td>"
                f"<td><strong>{escape(str(pin.get('title') or ''))}</strong>"
                + (f"<br/>{escape(str(pin.get('comment')))} " if pin.get("comment") else "")
                + f"</td><td>{'해결됨' if pin.get('resolved') else '미해결'}</td></tr>"
            )
        out.append("</tbody></table>")

    # Designer Placement Proposal
    if proposal_summary:
        out.extend([
            "<h3>Designer Memory 배치 제안안 요약</h3>",
            f"<p><strong>{escape(str(proposal_summary.get('title') or ''))}</strong> · "
            f"변경 매크로 {int(proposal_summary.get('changed_count') or 0)}개 · "
            f"경계 이탈 {int(proposal_summary.get('out_of_bounds_count') or 0)}건 · "
            f"충돌 {int(proposal_summary.get('overlap_count') or 0)}건</p>",
        ])

    return "\n".join(out)


def build_standalone_review_html(
    *,
    title: str,
    body_xhtml: str,
    inline_svgs: list[tuple[str, str]],
) -> str:
    """Build a self-contained HTML file with inline vector SVGs for local download/archive."""
    svg_blocks = "".join(
        f'<section style="margin:18px 0;"><h4>{escape(caption)}</h4><div style="border:1px solid #cbd5e1;border-radius:8px;overflow:auto;padding:8px;background:#f8fafc;">{svg_markup}</div></section>'
        for caption, svg_markup in inline_svgs
    )
    cleaned_body = re.sub(r"<p><ac:image[^>]*>.*?</ac:image></p>", "", body_xhtml, flags=re.DOTALL)
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<title>{escape(title)}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color:#0f172a; max-width:1280px; margin:24px auto; padding:0 20px; line-height:1.5; }}
table {{ border-collapse:collapse; width:100%; margin:12px 0 20px; font-size:13.5px; }}
th, td {{ border:1px solid #cbd5e1; padding:8px 12px; text-align:left; vertical-align:top; }}
th {{ background:#f1f5f9; font-weight:600; width:160px; }}
code {{ font-family: monospace; background:#f1f5f9; padding:1px 5px; border-radius:4px; }}
h1 {{ font-size:20px; border-bottom:2px solid #0f172a; padding-bottom:8px; }}
h3 {{ font-size:15.5px; margin-top:24px; color:#1e293b; }}
</style>
</head>
<body>
<h1>{escape(title)}</h1>
{cleaned_body}
{svg_blocks}
</body>
</html>"""
