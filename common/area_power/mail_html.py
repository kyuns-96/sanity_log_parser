"""Email-client-safe HTML template primitives for Design Atlas notifications.

All layouts use table structures and inline CSS so messages render cleanly across
corporate desktop/web mail clients (Outlook, Exchange/OWA, Knox, Gmail, Apple Mail)
where <style> tags, Flexbox, CSS Grid, and CSS custom properties are stripped.
Every untrusted or data-derived string must pass through ``esc()``.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import html
from typing import Any, Sequence
from urllib.parse import urlparse

FONT_SANS = (
    "'Malgun Gothic','Apple SD Gothic Neo','Pretendard',"
    "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
)
FONT_MONO = "Consolas,'SFMono-Regular',Menlo,Monaco,'Courier New',monospace"

TONE_STYLES: dict[str, dict[str, str]] = {
    "danger": {"bg": "#fef2f2", "border": "#fecaca", "accent": "#dc2626", "fg": "#b91c1c", "bar": "#dc2626"},
    "warning": {"bg": "#fffbeb", "border": "#fde68a", "accent": "#d97706", "fg": "#b45309", "bar": "#d97706"},
    "success": {"bg": "#f0fdf4", "border": "#bbf7d0", "accent": "#16a34a", "fg": "#15803d", "bar": "#16a34a"},
    "info": {"bg": "#eff6ff", "border": "#bfdbfe", "accent": "#2563eb", "fg": "#1d4ed8", "bar": "#2563eb"},
    "neutral": {"bg": "#f8fafc", "border": "#cbd5e1", "accent": "#64748b", "fg": "#334155", "bar": "#64748b"},
    "dark": {"bg": "#1e293b", "border": "#334155", "accent": "#60a5fa", "fg": "#f1f5f9", "bar": "#60a5fa"},
    "dark_danger": {"bg": "#450a0a", "border": "#991b1b", "accent": "#f87171", "fg": "#fecaca", "bar": "#f87171"},
    "dark_warning": {"bg": "#451a03", "border": "#92400e", "accent": "#fbbf24", "fg": "#fde68a", "bar": "#fbbf24"},
    "dark_success": {"bg": "#052e16", "border": "#166534", "accent": "#4ade80", "fg": "#bbf7d0", "bar": "#4ade80"},
    "dark_info": {"bg": "#172554", "border": "#1d4ed8", "accent": "#60a5fa", "fg": "#bfdbfe", "bar": "#60a5fa"},
}

VERDICT_META: dict[str, tuple[str, str]] = {
    "unexpected": ("이상", "danger"),
    "needs_review": ("확인 필요", "warning"),
    "expected": ("설명됨", "success"),
    "minor": ("변화 미미", "neutral"),
}


def esc(value: Any) -> str:
    """Escape text safely for HTML body and attribute contexts."""
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def safe_dashboard_url(dashboard_url: str | None, relative_path: str) -> str | None:
    """Return an absolute http(s) deep link when dashboard_url is configured cleanly."""
    base = (dashboard_url or "").strip().rstrip("/")
    if not base:
        return None
    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    rel = relative_path if relative_path.startswith("/") else "/" + relative_path
    return base + rel


def pill(label: str, tone: str = "neutral", *, mono: bool = False) -> str:
    style = TONE_STYLES.get(tone, TONE_STYLES["neutral"])
    font = FONT_MONO if mono else FONT_SANS
    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:999px;'
        f'background-color:{style["bg"]};border:1px solid {style["border"]};color:{style["fg"]};'
        f'font-family:{font};font-size:11px;font-weight:700;line-height:16px;'
        f'white-space:nowrap;vertical-align:middle;">{esc(label)}</span>'
    )


def verdict_pill(verdict: str | None) -> str:
    key = str(verdict or "")
    label, tone = VERDICT_META.get(key, (key or "—", "neutral"))
    return pill(label, tone)


def delta_html(text_value: str, *, raw_value: Any = None, positive_is_bad: bool = True, bold: bool = True) -> str:
    """Format a signed delta or percentage with semantic engineering color."""
    if not text_value or text_value == "—":
        return f'<span style="font-family:{FONT_MONO};color:#94a3b8;">—</span>'
    sign = 0
    if raw_value is not None:
        try:
            dec = raw_value if isinstance(raw_value, Decimal) else Decimal(str(raw_value))
            sign = 1 if dec > 0 else (-1 if dec < 0 else 0)
        except InvalidOperation:
            sign = 0
    elif text_value.startswith("+"):
        sign = 1
    elif text_value.startswith("-"):
        sign = -1
    if sign > 0:
        color = "#c2410c" if positive_is_bad else "#0369a1"
    elif sign < 0:
        color = "#0369a1" if positive_is_bad else "#c2410c"
    else:
        color = "#64748b"
    weight = "700" if bold and sign != 0 else "500"
    return (
        f'<span style="font-family:{FONT_MONO};color:{color};font-weight:{weight};'
        f'font-variant-numeric:tabular-nums;white-space:nowrap;">{esc(text_value)}</span>'
    )


def share_bar(pct_value: Any, *, tone: str = "warning", width_px: int = 84) -> str:
    """Outlook-safe inline horizontal bar representing 0..100% share."""
    try:
        dec = pct_value if isinstance(pct_value, Decimal) else Decimal(str(pct_value))
        pct = max(0.0, min(100.0, float(dec)))
    except (InvalidOperation, ValueError, TypeError):
        pct = 0.0
    fill_px = int(round(width_px * pct / 100.0))
    if pct > 0 and fill_px < 3:
        fill_px = 3
    rest_px = max(0, width_px - fill_px)
    color = TONE_STYLES.get(tone, TONE_STYLES["warning"])["bar"]
    cells = ""
    if fill_px > 0:
        cells += f'<td width="{fill_px}" height="6" style="background-color:{color};font-size:1px;line-height:1px;border-radius:3px;">&nbsp;</td>'
    if rest_px > 0:
        cells += f'<td width="{rest_px}" height="6" style="background-color:#e2e8f0;font-size:1px;line-height:1px;">&nbsp;</td>'
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="{width_px}" '
        f'style="display:inline-table;vertical-align:middle;border-collapse:collapse;background-color:#e2e8f0;border-radius:3px;overflow:hidden;">'
        f'<tr>{cells}</tr></table>'
    )


def section_heading(title: str, *, badge_html: str = "", subtitle: str = "") -> str:
    badge_part = f'&nbsp;&nbsp;{badge_html}' if badge_html else ""
    sub_part = (
        f'<div style="margin-top:3px;font-family:{FONT_SANS};font-size:12px;color:#64748b;line-height:1.5;">'
        f'{esc(subtitle)}</div>'
        if subtitle
        else ""
    )
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:22px 0 10px 0;border-collapse:collapse;">'
        f'<tr><td style="padding-bottom:7px;border-bottom:2px solid #e2e8f0;">'
        f'<div style="font-family:{FONT_SANS};font-size:14px;font-weight:700;color:#0f172a;line-height:1.4;">'
        f'{esc(title)}{badge_part}</div>{sub_part}'
        f'</td></tr></table>'
    )


def kpi_strip(cards: Sequence[dict[str, str]]) -> str:
    """Render 2..4 equal-width summary KPI cards in one table row."""
    if not cards:
        return ""
    width_pct = int(100 // len(cards))
    cells = []
    for idx, card in enumerate(cards):
        tone = TONE_STYLES.get(card.get("tone", "neutral"), TONE_STYLES["neutral"])
        pad_left = "0" if idx == 0 else "8px"
        sub = card.get("sub") or ""
        sub_html = (
            f'<div style="margin-top:3px;font-family:{FONT_SANS};font-size:11px;color:#64748b;line-height:1.4;">'
            f'{esc(sub)}</div>'
            if sub
            else ""
        )
        cells.append(
            f'<td width="{width_pct}%" valign="top" style="padding:0 0 0 {pad_left};">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border-collapse:separate;border-spacing:0;background-color:{tone["bg"]};'
            f'border:1px solid {tone["border"]};border-top:3px solid {tone["accent"]};border-radius:8px;">'
            f'<tr><td style="padding:10px 12px;">'
            f'<div style="font-family:{FONT_SANS};font-size:11px;font-weight:600;color:#475569;line-height:1.3;">{esc(card.get("label", ""))}</div>'
            f'<div style="margin-top:4px;font-family:{FONT_MONO};font-size:20px;font-weight:700;color:{tone["fg"]};line-height:1.2;font-variant-numeric:tabular-nums;">{esc(card.get("value", "0"))}</div>'
            f'{sub_html}'
            f'</td></tr></table>'
            f'</td>'
        )
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:0 0 16px 0;border-collapse:collapse;">'
        f'<tr>{"".join(cells)}</tr></table>'
    )


def callout(title: str, lines_html: Sequence[str], *, tone: str = "info") -> str:
    """Left-accented status or warning banner."""
    style = TONE_STYLES.get(tone, TONE_STYLES["info"])
    body = "".join(
        f'<div style="margin-top:4px;font-family:{FONT_SANS};font-size:12.5px;color:#1e293b;line-height:1.6;">{line}</div>'
        for line in lines_html
        if line
    )
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:0 0 12px 0;border-collapse:separate;border-spacing:0;background-color:{style["bg"]};'
        f'border:1px solid {style["border"]};border-left:4px solid {style["accent"]};border-radius:6px;">'
        f'<tr><td style="padding:10px 14px;">'
        f'<div style="font-family:{FONT_SANS};font-size:13px;font-weight:700;color:{style["fg"]};line-height:1.4;">{esc(title)}</div>'
        f'{body}'
        f'</td></tr></table>'
    )


def meta_table(rows: Sequence[tuple[str, str]]) -> str:
    """Compact two-column metadata box where values are pre-escaped HTML."""
    trs = []
    for idx, (label, value_html) in enumerate(rows):
        border = "border-top:1px solid #e2e8f0;" if idx > 0 else ""
        trs.append(
            f'<tr>'
            f'<td width="150" valign="top" style="{border}padding:7px 12px;background-color:#f8fafc;'
            f'font-family:{FONT_SANS};font-size:12px;font-weight:600;color:#475569;white-space:nowrap;">{esc(label)}</td>'
            f'<td valign="top" style="{border}padding:7px 12px;background-color:#ffffff;'
            f'font-family:{FONT_SANS};font-size:12.5px;color:#0f172a;line-height:1.5;word-break:break-word;">{value_html}</td>'
            f'</tr>'
        )
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:0 0 14px 0;border-collapse:collapse;border:1px solid #e2e8f0;border-radius:6px;">'
        f'{"".join(trs)}</table>'
    )


def data_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    aligns: Sequence[str] | None = None,
    row_bgs: Sequence[str | None] | None = None,
    empty_text: str = "해당 항목이 없습니다.",
) -> str:
    """Render a bordered, scannable HTML table where cell strings are pre-escaped HTML."""
    col_aligns = list(aligns) if aligns else ["left"] * len(headers)
    th_cells = []
    for idx, head in enumerate(headers):
        align = col_aligns[idx] if idx < len(col_aligns) else "left"
        th_cells.append(
            f'<th align="{align}" style="padding:8px 10px;background-color:#f1f5f9;'
            f'border-bottom:1px solid #cbd5e1;font-family:{FONT_SANS};font-size:11.5px;'
            f'font-weight:700;color:#334155;text-align:{align};white-space:nowrap;">{esc(head)}</th>'
        )
    if not rows:
        body_html = (
            f'<tr><td colspan="{len(headers)}" style="padding:14px 12px;text-align:center;'
            f'font-family:{FONT_SANS};font-size:12.5px;color:#64748b;background-color:#ffffff;">{esc(empty_text)}</td></tr>'
        )
    else:
        trs = []
        for r_idx, row in enumerate(rows):
            bg = (row_bgs[r_idx] if row_bgs and r_idx < len(row_bgs) and row_bgs[r_idx] else ("#ffffff" if r_idx % 2 == 0 else "#f8fafc"))
            tds = []
            for c_idx, cell in enumerate(row):
                align = col_aligns[c_idx] if c_idx < len(col_aligns) else "left"
                tds.append(
                    f'<td align="{align}" valign="top" style="padding:8px 10px;background-color:{bg};'
                    f'border-top:1px solid #e2e8f0;font-family:{FONT_SANS};font-size:12px;'
                    f'color:#0f172a;text-align:{align};line-height:1.5;word-break:break-word;">{cell}</td>'
                )
            trs.append(f'<tr>{"".join(tds)}</tr>')
        body_html = "".join(trs)
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:0 0 14px 0;border-collapse:collapse;border:1px solid #cbd5e1;background-color:#ffffff;">'
        f'<thead><tr>{"".join(th_cells)}</tr></thead>'
        f'<tbody>{body_html}</tbody></table>'
    )


def cta_block(*, label: str, url: str | None, fallback_path: str | None = None, note: str = "") -> str:
    """Render a bulletproof table button when url is available, else a clean path reference box."""
    if url:
        note_html = (
            f'<div style="margin-top:8px;font-family:{FONT_SANS};font-size:11.5px;color:#64748b;">{esc(note)}</div>'
            if note
            else ""
        )
        return (
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="margin:18px 0 6px 0;border-collapse:collapse;">'
            f'<tr><td align="left" style="padding:14px 16px;background-color:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;">'
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="border-collapse:separate;">'
            f'<tr><td bgcolor="#2563eb" style="background-color:#2563eb;border-radius:6px;">'
            f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer" '
            f'style="display:inline-block;padding:9px 18px;font-family:{FONT_SANS};font-size:13px;'
            f'font-weight:700;color:#ffffff;text-decoration:none;">{esc(label)}</a>'
            f'</td></tr></table>{note_html}'
            f'</td></tr></table>'
        )
    if fallback_path:
        note_html = (
            f'<div style="margin-top:4px;font-family:{FONT_SANS};font-size:11.5px;color:#64748b;">{esc(note)}</div>'
            if note
            else ""
        )
        return (
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="margin:18px 0 6px 0;border-collapse:collapse;">'
            f'<tr><td style="padding:11px 14px;background-color:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;">'
            f'<div style="font-family:{FONT_SANS};font-size:11.5px;font-weight:700;color:#475569;">{esc(label)}</div>'
            f'<div style="margin-top:4px;font-family:{FONT_MONO};font-size:12px;color:#1d4ed8;word-break:break-all;">{esc(fallback_path)}</div>'
            f'{note_html}'
            f'</td></tr></table>'
        )
    return ""


def render_email_shell(
    *,
    preheader: str,
    category: str,
    title: str,
    subtitle: str = "",
    header_badges: Sequence[str] = (),
    body_html: str,
    footer_notes: Sequence[str] = (),
) -> str:
    """Wrap rendered sections in the standard Design Atlas corporate HTML email frame."""
    badges_html = (
        f'<div style="margin-top:10px;">{" ".join(header_badges)}</div>'
        if header_badges
        else ""
    )
    subtitle_html = (
        f'<div style="margin-top:5px;font-family:{FONT_SANS};font-size:13px;color:#cbd5e1;line-height:1.5;">{esc(subtitle)}</div>'
        if subtitle
        else ""
    )
    footer_lines = "".join(
        f'<div style="margin-top:{0 if idx == 0 else 4}px;font-family:{FONT_SANS};font-size:11.5px;color:#64748b;line-height:1.55;">{esc(note)}</div>'
        for idx, note in enumerate(footer_notes)
        if note
    )
    return (
        '<!DOCTYPE html>'
        '<html lang="ko">'
        '<head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1.0">'
        '<meta name="color-scheme" content="light">'
        f'<title>{esc(title)}</title>'
        '</head>'
        f'<body style="margin:0;padding:0;background-color:#f1f5f9;font-family:{FONT_SANS};color:#0f172a;-webkit-text-size-adjust:100%;">'
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;font-size:1px;line-height:1px;color:#f1f5f9;">{esc(preheader)}</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="background-color:#f1f5f9;border-collapse:collapse;">'
        '<tr><td align="center" style="padding:20px 12px 28px 12px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="max-width:780px;width:100%;background-color:#ffffff;border:1px solid #cbd5e1;border-radius:10px;overflow:hidden;border-collapse:separate;border-spacing:0;">'
        # Top brand bar
        '<tr><td style="padding:18px 24px 20px 24px;background-color:#0f172a;border-bottom:3px solid #2563eb;">'
        f'<div style="font-family:{FONT_SANS};font-size:11px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#93c5fd;">DESIGN ATLAS &middot; {esc(category)}</div>'
        f'<div style="margin-top:6px;font-family:{FONT_SANS};font-size:19px;font-weight:700;color:#ffffff;line-height:1.35;">{esc(title)}</div>'
        f'{subtitle_html}'
        f'{badges_html}'
        '</td></tr>'
        # Main content
        f'<tr><td style="padding:20px 24px 18px 24px;background-color:#ffffff;">{body_html}</td></tr>'
        # Footer
        '<tr><td style="padding:14px 24px 18px 24px;background-color:#f8fafc;border-top:1px solid #e2e8f0;">'
        f'{footer_lines}'
        '</td></tr>'
        '</table>'
        '</td></tr></table>'
        '</body></html>'
    )
