"""Lossless CSV ingestion, with report-precision-aware validation.

Only UTF-8 (optionally BOM-prefixed) is accepted. Paths are literal: the caller
must supply the exporter's hierarchy separator. Original CSV bytes are retained.
"""
from __future__ import annotations

import csv
import gzip
import io
import re
import zlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from area_power.common.errors import DomainError
from area_power.common.jsonutil import digest
from area_power.common.models import UploadMetadata

CELL_GROUPS = ("total", "logic", "cts", "sram", "io", "others")
VTH_NAMES = tuple(f"vt_{v}{suffix}" for v in ("slvt", "lvt", "rvt", "hvt") for suffix in ("", "_llp"))
DRIVE_NAMES = tuple(f"drv_{v}" for v in ("D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "D10", "D12", "D14", "D16", "other"))
POWER_GROUPS = ("all", "logic", "sram", "io", "others")
POWER_COMPONENTS = ("internal", "switching", "leakage", "total")
COUNT_FIELDS = tuple(f"{g}_cells" for g in CELL_GROUPS) + tuple(f"{g}_cnt" for g in VTH_NAMES + DRIVE_NAMES)
AREA_FIELDS = tuple(f"{g}_area" for g in CELL_GROUPS)
PCT_FIELDS = ("cts_area_pct",) + tuple(f"{g}_pct" for g in VTH_NAMES + DRIVE_NAMES)
POWER_FIELDS = tuple(f"pwr_{g}_{c}_mw" for g in POWER_GROUPS for c in POWER_COMPONENTS)
STRUCTURE_FIELDS = ("hier_name", "level") + COUNT_FIELDS + AREA_FIELDS
HEADERS = ("hier_name", "level", "process_label", "voltage_v", "temp_c", "pex") + tuple(
    field for g in CELL_GROUPS for field in (f"{g}_cells", f"{g}_area")
) + PCT_FIELDS + tuple(f"{g}_cnt" for g in VTH_NAMES + DRIVE_NAMES) + POWER_FIELDS
_NUMERIC = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")


@dataclass
class ParsedCSV:
    rows: dict[str, dict[str, Any]]
    root_path: str
    root_summary: dict[str, Any]
    gzip_bytes: bytes
    original_bytes: bytes
    structure_digest: bytes
    corner: dict[str, Any]
    conditions: dict[str, Any]
    condition_key: bytes
    conditions_complete: bool
    input_contract: dict[str, Any]
    validation_report: dict[str, Any]
    row_count: int


def _invalid(message: str) -> None:
    raise DomainError(message, code="invalid_csv", status_code=422)


def _number(text: str, field: str, line: int, *, optional: bool = False) -> Decimal | None:
    text = text.strip()
    if optional and not text:
        return None
    if not _NUMERIC.fullmatch(text):
        _invalid(f"Row {line}: {field} must be a finite number.")
    try:
        value = Decimal(text)
    except InvalidOperation:
        _invalid(f"Row {line}: {field} must be a finite number.")
    if not value.is_finite():
        _invalid(f"Row {line}: {field} must be finite.")
    return value


def _half_unit(value: Decimal) -> Decimal:
    # The exponent retains the precision in the actual report, including e notation.
    return Decimal((0, (5,), value.as_tuple().exponent - 1))


def structural_rows(parsed: ParsedCSV) -> dict[str, dict[str, Any]]:
    """Return all structure, without power, PVT, or reported percentage formatting."""
    return {path: {key: row[key] for key in STRUCTURE_FIELDS} for path, row in parsed.rows.items()}


def structural_metadata(metadata: UploadMetadata) -> dict[str, Any]:
    return {key: getattr(metadata, key) for key in (
        "design_stage", "hierarchy_separator", "area_unit", "area_basis", "library",
        "classification", "constraint_context",
    )}


def _known(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip()) and value.strip().lower() not in {"unknown", "unavailable", "unspecified", "n/a"}
    if isinstance(value, dict):
        return bool(value) and all(_known(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return bool(value) and all(_known(v) for v in value)
    return True


def _conditions(corner: dict[str, Any], metadata: UploadMetadata) -> tuple[dict[str, Any], list[str]]:
    # CSV corner is authoritative, never guessed from a filename or overwritten.
    supplied = dict(metadata.analysis_conditions)
    for key in corner:
        if key in supplied and supplied[key] != corner[key]:
            raise DomainError("Analysis conditions conflict with the CSV corner.", code="condition_conflict")
    if "corner" in supplied and supplied["corner"] != corner:
        raise DomainError("Analysis conditions conflict with the CSV corner.", code="condition_conflict")
    supplied.pop("corner", None)
    for key in corner:
        supplied.pop(key, None)
    required = ("mode", "clocks", "activity", "rc", "tool", "voltage_domains")
    expected_types = {
        "mode": (str,), "clocks": (dict, list), "activity": (dict,),
        "rc": (str, dict), "tool": (dict,), "voltage_domains": (dict, list),
    }
    missing = [key for key, value in corner.items() if not _known(value)]
    missing += [key for key in required if not isinstance(supplied.get(key), expected_types[key]) or not _known(supplied.get(key))]
    tool = supplied.get("tool")
    if isinstance(tool, dict):
        missing += [f"tool.{key}" for key in ("name", "version", "method") if not _known(tool.get(key))]
    conditions = {"corner": corner, **{key: supplied.get(key) for key in required}}
    conditions.update(supplied)
    return conditions, missing


def _unpack(data: bytes, maximum: int) -> tuple[bytes, bytes]:
    if maximum <= 0:
        raise DomainError("Invalid decompression size limit.", code="configuration_error", status_code=503)
    if data.startswith(b"\x1f\x8b"):
        parts: list[bytes] = []
        total = 0
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb") as stream:
                while True:
                    chunk = stream.read(min(65536, maximum - total + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > maximum:
                        raise DomainError("Uncompressed CSV exceeds the configured size limit.", code="file_too_large", status_code=413)
                    parts.append(chunk)
        except (OSError, EOFError, zlib.error) as exc:
            raise DomainError("Invalid gzip stream.", code="invalid_csv") from exc
        return b"".join(parts), data
    if len(data) > maximum:
        raise DomainError("CSV exceeds the configured uncompressed size limit.", code="file_too_large", status_code=413)
    return data, gzip.compress(data, compresslevel=6, mtime=0)


def parse_csv(data: bytes, filename: str, metadata: UploadMetadata, max_uncompressed_bytes: int) -> ParsedCSV:
    """Validate a complete export. Compression is detected by magic, not filename."""
    original, compressed = _unpack(data, max_uncompressed_bytes)
    try:
        text = original.decode("utf-8-sig")
    except UnicodeError as exc:
        raise DomainError("CSV must use UTF-8 encoding (a UTF-8 BOM is supported).", code="invalid_csv") from exc
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader)
    except (StopIteration, csv.Error) as exc:
        raise DomainError("CSV is empty or its header is invalid.", code="invalid_csv") from exc
    if len(header) != len(set(header)):
        _invalid("CSV contains duplicate column names.")
    if len(header) != 81 or set(header) != set(HEADERS):
        _invalid("CSV must contain exactly the 81 documented column names, without missing or unknown columns.")
    rows: dict[str, dict[str, Any]] = {}
    root_paths: list[str] = []
    corner: dict[str, Any] | None = None
    quality_totals = {"vth_undercoverage_rows": 0, "drive_undercoverage_rows": 0, "unavailable_ratio_count": 0}
    separator = metadata.hierarchy_separator
    try:
        for fields in reader:
            line = reader.line_num
            if len(fields) != len(header):
                _invalid(f"Row {line}: column count does not match the header.")
            raw = dict(zip(header, fields))
            path = raw["hier_name"]
            if not path or not path.strip() or any(not part for part in path.split(separator)):
                _invalid(f"Row {line}: hierarchy path is empty or has an empty segment.")
            if path in rows:
                _invalid(f"Row {line}: duplicate hierarchy path.")
            values = {key: _number(raw[key], key, line) for key in COUNT_FIELDS + AREA_FIELDS + POWER_FIELDS + ("level",)}
            reported = {key: _number(raw[key], key, line, optional=True) for key in PCT_FIELDS}
            for key in COUNT_FIELDS + ("level",):
                value = values[key]
                if value < 0 or value != value.to_integral_value():
                    _invalid(f"Row {line}: {key} must be a nonnegative integer.")
                values[key] = int(value)
            for key in AREA_FIELDS:
                if values[key] < 0:
                    _invalid(f"Row {line}: {key} must be nonnegative.")
            this_corner = {
                "process_label": raw["process_label"] if raw["process_label"].strip() else None,
                "voltage_v": _number(raw["voltage_v"], "voltage_v", line, optional=True),
                "temp_c": _number(raw["temp_c"], "temp_c", line, optional=True),
                "pex": raw["pex"] if raw["pex"].strip() else None,
            }
            if corner is None:
                corner = this_corner
            elif this_corner != corner:
                _invalid(f"Row {line}: every row must have the same corner, including missing values.")
            if values["level"] != len(path.split(separator)) - 1:
                _invalid(f"Row {line}: level does not match the literal hierarchy path depth.")
            if values["level"] == 0:
                root_paths.append(path)
            row = {"hier_name": path, **values, **this_corner}
            decimal_values = [v for v in values.values() if isinstance(v, Decimal)] + [v for v in reported.values() if v is not None]
            decimal_values.extend(value for value in (metadata.pct_tolerance, metadata.power_tolerance_mw) if value is not None)
            # Enough precision for exact finite input sums and tolerance products;
            # the extra digits also make recomputed repeating ratios useful.
            highest = max((v.adjusted() for v in decimal_values), default=0)
            lowest = min((v.as_tuple().exponent for v in decimal_values), default=0)
            count_digits = max(len(str(values[key])) for key in COUNT_FIELDS)
            with localcontext() as context:
                context.prec = max(50, highest - lowest + count_digits + 50)
                context.Emax = max(context.Emax, highest + context.prec)
                context.Emin = min(context.Emin, lowest - context.prec)
                logic = values["logic_cells"]
                vt_sum = sum(values[f"{key}_cnt"] for key in VTH_NAMES)
                drive_sum = sum(values[f"{key}_cnt"] for key in DRIVE_NAMES)
                if vt_sum > logic or drive_sum > logic:
                    _invalid(f"Row {line}: Vth or drive count sum exceeds logic_cells.")
                unavailable = []
                for key in PCT_FIELDS:
                    numerator = values["cts_area"] if key == "cts_area_pct" else values[key[:-4] + "_cnt"]
                    denominator = values["logic_area"] if key == "cts_area_pct" else logic
                    pct = reported[key]
                    if pct is not None and not Decimal(0) <= pct <= Decimal(100):
                        _invalid(f"Row {line}: {key} must use the 0..100 scale.")
                    if denominator == 0:
                        if pct not in (None, Decimal(0)):
                            _invalid(f"Row {line}: {key} must be blank or zero with a zero denominator.")
                        row[key] = None
                        unavailable.append(key)
                    else:
                        if pct is None:
                            _invalid(f"Row {line}: {key} is required with a nonzero denominator.")
                        tolerance = metadata.pct_tolerance if metadata.pct_tolerance is not None else _half_unit(pct)
                        if abs(pct * denominator - Decimal(100) * numerator) > tolerance * denominator:
                            _invalid(f"Row {line}: {key} disagrees with its numerator and denominator beyond report precision.")
                        row[key] = Decimal(100) * numerator / denominator
                def check_power(lhs: str, rhs: list[str]) -> None:
                    tolerance = metadata.power_tolerance_mw
                    if tolerance is None:
                        tolerance = sum((_half_unit(values[key]) for key in [lhs, *rhs]), Decimal(0))
                    if abs(values[lhs] - sum((values[key] for key in rhs), Decimal(0))) > tolerance:
                        _invalid(f"Row {line}: power identity for {lhs} exceeds report-rounding tolerance.")
                for group in POWER_GROUPS:
                    check_power(f"pwr_{group}_total_mw", [f"pwr_{group}_{c}_mw" for c in POWER_COMPONENTS[:-1]])
                for component in POWER_COMPONENTS:
                    check_power(f"pwr_all_{component}_mw", [f"pwr_{g}_{component}_mw" for g in POWER_GROUPS[1:]])
                row["quality"] = {"vth_unclassified_cnt": logic - vt_sum, "drive_unclassified_cnt": logic - drive_sum, "unavailable_ratios": unavailable}
                row["reported_pct"] = reported
                quality_totals["vth_undercoverage_rows"] += int(vt_sum < logic)
                quality_totals["drive_undercoverage_rows"] += int(drive_sum < logic)
                quality_totals["unavailable_ratio_count"] += len(unavailable)
            rows[path] = row
    except csv.Error as exc:
        raise DomainError("CSV quoting or record syntax is invalid.", code="invalid_csv") from exc
    if len(root_paths) != 1:
        _invalid("CSV must contain exactly one level=0 design root.")
    root_path = root_paths[0]
    if any(path != root_path and not path.startswith(root_path + separator) for path in rows):
        _invalid("Every hierarchy path must belong to the single design root.")
    conditions, missing = _conditions(corner, metadata)
    root = rows[root_path]
    root_summary = {"hier_name": root_path, "level": 0, "metrics": {key: root[key] for key in COUNT_FIELDS + AREA_FIELDS + PCT_FIELDS + POWER_FIELDS}, "quality": root["quality"]}
    structure = {path: {key: row[key] for key in STRUCTURE_FIELDS} for path, row in rows.items()}
    contract = {
        "version": 1, "metadata": metadata.model_dump(mode="python"),
        "encoding": "utf-8-sig", "headers": header,
        "percentage_scale": 100, "percentage_rounding": "explicit_tolerance" if metadata.pct_tolerance is not None else "half_last_reported_digit",
        "power_rounding": "explicit_identity_tolerance_mw" if metadata.power_tolerance_mw is not None else "sum_of_half_last_reported_digits",
        "structural_metadata": structural_metadata(metadata),
        "required_analysis_conditions": ["mode", "clocks", "activity", "rc", "tool", "voltage_domains"],
    }
    report = {"valid": True, "row_count": len(rows), **quality_totals, "missing_conditions": missing, "quality": {path: row["quality"] for path, row in rows.items() if row["quality"]["vth_unclassified_cnt"] or row["quality"]["drive_unclassified_cnt"] or row["quality"]["unavailable_ratios"]}}
    return ParsedCSV(rows, root_path, root_summary, compressed, original, digest(structure), corner, conditions, digest(conditions), not missing, contract, report, len(rows))
