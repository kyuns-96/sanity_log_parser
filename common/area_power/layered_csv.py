"""Layered CSV helpers: 26-column Power-only parsing, 85→26 auto-stripping, and merge reconstruction."""
from __future__ import annotations

import csv
import gzip
from hashlib import sha256
import io
from decimal import Decimal, localcontext
from typing import Any

from common.area_power.errors import DomainError
from common.area_power.jsonutil import digest
from common.area_power.models import UploadMetadata
from .csv_input import (
    AREA_FIELDS,
    AREA_ONLY_HEADERS,
    CORNER_FIELDS,
    COUNT_FIELDS,
    DRIVE_NAMES,
    HEADERS,
    LEGACY_COUNT_FIELDS,
    LEGACY_DRIVE_NAMES,
    LEGACY_HEADERS,
    LEGACY_PCT_FIELDS,
    OBSERVED_EXTRA,
    PCT_FIELDS,
    POWER_FIELDS,
    POWER_IDENTITIES,
    ObservedCSV,
    ObservedRow,
    ParsedCSV,
    _EXTRA_SEPARATOR,
    _conditions,
    _half_unit,
    _invalid,
    _number,
    _row_precision,
    _unpack,
    parse_csv,
    parse_hierarchy,
    read_observations,
    structural_metadata,
)

POWER_ONLY_HEADERS = ("hier_name", "level") + CORNER_FIELDS + POWER_FIELDS


def is_power_layer_upload(upload: dict[str, Any] | None) -> bool:
    if not isinstance(upload, dict):
        return False
    contract = upload.get("input_contract")
    return isinstance(contract, dict) and contract.get("storage_mode") == "power_layer"


def base_upload_id_of(upload: dict[str, Any] | None) -> str | None:
    if not is_power_layer_upload(upload):
        return None
    contract = upload["input_contract"]
    raw = contract.get("base_upload_id")
    return str(raw) if raw else None


def is_power_only_csv_bytes(data: bytes, max_uncompressed_bytes: int) -> bool:
    """Fast header check for 26-column Power-only CSV exports."""
    original, _ = _unpack(data, max_uncompressed_bytes)
    try:
        text = original.decode("utf-8-sig")
    except UnicodeError:
        return False
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader)
    except (StopIteration, csv.Error):
        return False
    return len(header) == len(POWER_ONLY_HEADERS) and set(header) == set(POWER_ONLY_HEADERS)


def strip_to_power_only_csv(original_bytes: bytes) -> tuple[bytes, bytes]:
    """Strip an 85/81-column Area+Power CSV down to a compact 26-column Power-only CSV."""
    text = original_bytes.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    header = next(reader)
    indices = [header.index(col) for col in POWER_ONLY_HEADERS]
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(POWER_ONLY_HEADERS)
    for row in reader:
        writer.writerow([row[idx] for idx in indices])
    thin_bytes = out.getvalue().encode("utf-8")
    thin_gzip = gzip.compress(thin_bytes, compresslevel=6, mtime=0)
    return thin_bytes, thin_gzip


def _read_power_only_rows(
    original: bytes,
    metadata: UploadMetadata,
    *,
    validate_identities: bool,
) -> tuple[dict[str, dict[str, Any]], dict[str, str], dict[str, Any], list[str], int, int]:
    try:
        text = original.decode("utf-8-sig")
    except UnicodeError as exc:
        raise DomainError("CSV must use UTF-8 encoding (a UTF-8 BOM is supported).", code="invalid_csv") from exc
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader)
    except (StopIteration, csv.Error) as exc:
        raise DomainError("CSV is empty or its header is invalid.", code="invalid_csv") from exc
    if len(header) != len(set(header)) or len(header) != len(POWER_ONLY_HEADERS) or set(header) != set(POWER_ONLY_HEADERS):
        _invalid(f"Power-only CSV must contain exactly the {len(POWER_ONLY_HEADERS)} documented columns.")

    separator = metadata.hierarchy_separator
    power_rows: dict[str, dict[str, Any]] = {}
    raw_power_strings: dict[str, dict[str, str]] = {}
    root_paths: list[str] = []
    corner: dict[str, Any] | None = None
    numeric_highest, numeric_lowest = 0, 0
    try:
        for fields in reader:
            line = reader.line_num
            if len(fields) != len(header):
                _invalid(f"Row {line}: column count does not match the header.")
            raw = dict(zip(header, fields))
            path = raw["hier_name"]
            if not path or not path.strip() or any(not part for part in path.split(separator)):
                _invalid(f"Row {line}: hierarchy path is empty or has an empty segment.")
            if path in power_rows:
                _invalid(f"Row {line}: duplicate hierarchy path.")
            level_dec = _number(raw["level"], "level", line)
            if level_dec < 0 or level_dec != level_dec.to_integral_value():
                _invalid(f"Row {line}: level must be a nonnegative integer.")
            level = int(level_dec)
            if level != len(path.split(separator)) - 1:
                _invalid(f"Row {line}: level does not match the literal hierarchy path depth.")
            if level == 0:
                root_paths.append(path)
            p_vals = {key: _number(raw[key], key, line) for key in POWER_FIELDS}
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

            for val in list(p_vals.values()) + [this_corner["voltage_v"], this_corner["temp_c"]]:
                if isinstance(val, Decimal):
                    if val.adjusted() > numeric_highest:
                        numeric_highest = val.adjusted()
                    if val.as_tuple().exponent < numeric_lowest:
                        numeric_lowest = val.as_tuple().exponent

            if validate_identities:
                decimals = list(p_vals.values())
                if metadata.power_tolerance_mw is not None:
                    decimals.append(metadata.power_tolerance_mw)
                highest = max((v.adjusted() for v in decimals), default=0)
                lowest = min((v.as_tuple().exponent for v in decimals), default=0)
                precision = max(50, highest - lowest + 50)
                with localcontext() as context:
                    context.prec = precision
                    context.Emax = max(context.Emax, highest + precision)
                    context.Emin = min(context.Emin, lowest - precision)
                    half_units = {k: _half_unit(p_vals[k]) for k in POWER_FIELDS} if metadata.power_tolerance_mw is None else None
                    for lhs, rhs in POWER_IDENTITIES:
                        tol = metadata.power_tolerance_mw
                        if tol is None:
                            tol = sum((half_units[k] for k in (lhs, *rhs)), Decimal(0))
                        if abs(p_vals[lhs] - sum((p_vals[k] for k in rhs), Decimal(0))) > tol:
                            _invalid(f"Row {line}: power identity for {lhs} exceeds report-rounding tolerance.")
            power_rows[path] = {"level": level, **p_vals, **this_corner}
            raw_power_strings[path] = {key: raw[key].strip() for key in POWER_FIELDS}
    except csv.Error as exc:
        raise DomainError("CSV quoting or record syntax is invalid.", code="invalid_csv") from exc
    if len(root_paths) != 1:
        _invalid("CSV must contain exactly one level=0 design root.")
    root_path = root_paths[0]
    if any(path != root_path and not path.startswith(root_path + separator) for path in power_rows):
        _invalid("Every hierarchy path must belong to the single design root.")
    return power_rows, raw_power_strings, corner or {"process_label": None, "voltage_v": None, "temp_c": None, "pex": None}, root_paths, numeric_highest, numeric_lowest


def parse_power_only_against_base(
    data: bytes,
    filename: str,
    metadata: UploadMetadata,
    base_upload: dict[str, Any],
    base_metadata: UploadMetadata,
    max_uncompressed_bytes: int,
    *,
    raw_original_sha256: str | None = None,
) -> ParsedCSV:
    """Validate a 26-column Power-only CSV against a version's Area Base upload."""
    if metadata.hierarchy_separator != base_metadata.hierarchy_separator:
        raise DomainError(
            "Power-only CSV hierarchy_separator does not match this version's latest Area base.",
            code="area_base_mismatch",
            status_code=409,
        )
    original, compressed = _unpack(data, max_uncompressed_bytes)
    power_rows, _, corner, _, _, _ = _read_power_only_rows(original, metadata, validate_identities=True)
    base_parsed = parse_csv(bytes(base_upload["csv_gzip"]), base_upload["original_filename"], base_metadata, max_uncompressed_bytes)
    if bytes(base_upload["original_sha256"]) != sha256(base_parsed.original_bytes).digest():
        raise DomainError("Area base upload checksum is inconsistent.", code="stored_upload_mismatch")
    if set(power_rows) != set(base_parsed.rows):
        raise DomainError(
            "Power-only CSV hierarchy paths do not match this version's latest Area base; upload the updated Area CSV first.",
            code="area_base_mismatch",
            status_code=409,
        )
    for path, p_row in power_rows.items():
        if p_row["level"] != base_parsed.rows[path]["level"]:
            raise DomainError(
                "Power-only CSV hierarchy levels do not match this version's latest Area base.",
                code="area_base_mismatch",
                status_code=409,
            )

    merged_rows: dict[str, dict[str, Any]] = {}
    for path, b_row in base_parsed.rows.items():
        merged = dict(b_row)
        merged.update(power_rows[path])
        merged_rows[path] = merged

    conditions, missing = _conditions(corner, metadata)
    root_path = base_parsed.root_path
    root = merged_rows[root_path]
    base_headers = (base_upload.get("input_contract") or {}).get("headers") or HEADERS
    is_full_drive = "drv_D0P5_cnt" in set(base_headers)
    active_count = COUNT_FIELDS if is_full_drive else LEGACY_COUNT_FIELDS
    active_pct = PCT_FIELDS if is_full_drive else LEGACY_PCT_FIELDS
    root_summary = {
        "hier_name": root_path,
        "level": 0,
        "metrics": {key: root[key] for key in active_count + AREA_FIELDS + active_pct + POWER_FIELDS},
        "quality": root["quality"],
    }
    contract = {
        "version": 1,
        "metadata": metadata.model_dump(mode="python"),
        "encoding": "utf-8-sig",
        "headers": list(POWER_ONLY_HEADERS),
        "has_power": True,
        "data_mode": "area_power",
        "storage_mode": "power_layer",
        "base_upload_id": str(base_upload["id"]),
        "raw_original_sha256": raw_original_sha256 or sha256(original).hexdigest(),
        "percentage_basis": "logic_area",
        "percentage_scale": 100,
        "percentage_rounding": "explicit_tolerance" if metadata.pct_tolerance is not None else "half_last_reported_digit",
        "power_rounding": "explicit_identity_tolerance_mw" if metadata.power_tolerance_mw is not None else "sum_of_half_last_reported_digits",
        "structural_metadata": structural_metadata(metadata),
        "required_analysis_conditions": ["mode", "clocks", "activity", "rc", "tool", "voltage_domains"],
    }
    report = {
        "valid": True,
        "row_count": len(merged_rows),
        "has_power": True,
        "data_mode": "area_power",
        "storage_mode": "power_layer",
        "base_upload_id": str(base_upload["id"]),
        "vth_undercoverage_rows": (base_upload.get("validation_report") or {}).get("vth_undercoverage_rows", 0),
        "drive_undercoverage_rows": (base_upload.get("validation_report") or {}).get("drive_undercoverage_rows", 0),
        "unavailable_ratio_count": (base_upload.get("validation_report") or {}).get("unavailable_ratio_count", 0),
        "missing_conditions": missing,
        "quality": (base_upload.get("validation_report") or {}).get("quality", {}),
    }
    return ParsedCSV(
        merged_rows,
        root_path,
        root_summary,
        compressed,
        original,
        base_parsed.structure_digest,
        corner,
        conditions,
        digest(conditions),
        not missing,
        contract,
        report,
        len(merged_rows),
        has_power=True,
    )


def read_layered_hierarchy(upload: dict[str, Any], maximum: int, *, base_upload: dict[str, Any] | None = None):
    """Project hierarchy items for either a standalone CSV or a thin power_layer referencing its Area base."""
    if not is_power_layer_upload(upload):
        metadata = UploadMetadata.model_validate(upload["input_contract"]["metadata"])
        items = parse_hierarchy(
            bytes(upload["csv_gzip"]), metadata, maximum,
            original_sha256=bytes(upload["original_sha256"]),
            original_byte_length=upload["original_byte_length"],
            row_count=upload["row_count"],
        )
        return metadata, items

    if base_upload is None:
        raise DomainError("Area base upload is required to read a power-layer upload.", code="stored_upload_mismatch")
    metadata = UploadMetadata.model_validate(upload["input_contract"]["metadata"])
    base_meta = UploadMetadata.model_validate(base_upload["input_contract"]["metadata"])
    original, _ = _unpack(bytes(upload["csv_gzip"]), maximum)
    if len(original) != upload["original_byte_length"] or sha256(original).digest() != bytes(upload["original_sha256"]):
        raise DomainError("Stored upload no longer matches validated snapshot", code="stored_upload_mismatch")
    power_rows, _, _, _, _, _ = _read_power_only_rows(original, metadata, validate_identities=False)
    base_items = parse_hierarchy(
        bytes(base_upload["csv_gzip"]), base_meta, maximum,
        original_sha256=bytes(base_upload["original_sha256"]),
        original_byte_length=base_upload["original_byte_length"],
        row_count=base_upload["row_count"],
    )
    merged = []
    for item in base_items:
        p_row = power_rows.get(item["path"])
        merged.append({**item, "power_mw": p_row["pwr_all_total_mw"] if p_row else None})
    return metadata, merged


def read_layered_observations(
    upload: dict[str, Any],
    metadata: UploadMetadata,
    maximum: int,
    *,
    base_upload: dict[str, Any] | None = None,
) -> ObservedCSV:
    """Read observations for either a standalone CSV or a thin power_layer merged with its Area base."""
    if not is_power_layer_upload(upload):
        return read_observations(upload, metadata, maximum)
    if base_upload is None:
        raise DomainError("Area base upload is required to read a power-layer upload.", code="stored_upload_mismatch")
    original, _ = _unpack(bytes(upload["csv_gzip"]), maximum)
    if len(original) != upload["original_byte_length"] or sha256(original).digest() != bytes(upload["original_sha256"]):
        raise DomainError("Stored upload no longer matches validated snapshot", code="stored_upload_mismatch")
    power_rows, raw_power_strings, corner, _, p_hi, p_lo = _read_power_only_rows(original, metadata, validate_identities=False)
    base_meta = UploadMetadata.model_validate(base_upload["input_contract"]["metadata"])
    base_obs = read_observations(base_upload, base_meta, maximum)
    if set(power_rows) != set(base_obs.rows):
        raise DomainError("Stored power layer no longer matches its Area base.", code="stored_upload_mismatch")

    merged_rows: dict[str, dict[str, Any]] = {}
    for path, b_row in base_obs.rows.items():
        p_row = power_rows[path]
        raw_p = raw_power_strings[path]
        if isinstance(b_row, ObservedRow) and b_row._extra is not None:
            base_extra = dict(zip(OBSERVED_EXTRA, b_row._extra.split(_EXTRA_SEPARATOR)))
            for key in POWER_FIELDS:
                if key != "pwr_all_total_mw":
                    base_extra[key] = raw_p[key]
            merged_rows[path] = ObservedRow(
                {"level": b_row["level"], "total_area": b_row["total_area"], "pwr_all_total_mw": p_row["pwr_all_total_mw"]},
                _EXTRA_SEPARATOR.join(base_extra.get(key, "") for key in OBSERVED_EXTRA),
            )
        else:
            merged_rows[path] = {**b_row, **p_row}

    conditions, missing = _conditions(corner, metadata)
    key = digest(conditions)
    if key != bytes(upload["condition_key"]):
        raise DomainError("Stored conditions no longer match validated snapshot", code="stored_upload_mismatch")
    return ObservedCSV(
        merged_rows,
        conditions,
        key,
        not missing,
        max(base_obs.numeric_highest, p_hi),
        min(base_obs.numeric_lowest, p_lo),
        base_obs.root_path,
        has_power=True,
    )


def reconstruct_full_csv_gzip(upload: dict[str, Any], base_upload: dict[str, Any], maximum: int) -> bytes:
    """Reassemble a complete Area+Power CSV gzip for downloading a power_layer upload."""
    base_orig, _ = _unpack(bytes(base_upload["csv_gzip"]), maximum)
    pwr_orig, _ = _unpack(bytes(upload["csv_gzip"]), maximum)
    b_reader = csv.reader(io.StringIO(base_orig.decode("utf-8-sig"), newline=""), strict=True)
    b_header = next(b_reader)
    p_reader = csv.reader(io.StringIO(pwr_orig.decode("utf-8-sig"), newline=""), strict=True)
    p_header = next(p_reader)
    p_by_path = {row[p_header.index("hier_name")]: dict(zip(p_header, row)) for row in p_reader}
    is_full_drive = "drv_D0P5_cnt" in set(b_header)
    target_header = HEADERS if is_full_drive else LEGACY_HEADERS
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(target_header)
    for row in b_reader:
        b_map = dict(zip(b_header, row))
        p_map = p_by_path.get(b_map["hier_name"], {})
        merged = {**b_map, **{k: p_map.get(k, "") for k in CORNER_FIELDS + POWER_FIELDS}}
        writer.writerow([merged.get(col, "") for col in target_header])
    return gzip.compress(out.getvalue().encode("utf-8"), compresslevel=6, mtime=0)


def load_base_upload(db: Any, conn: Any, upload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Fetch the Area Base upload (including blob) when upload uses power_layer storage."""
    bid = base_upload_id_of(upload)
    if not bid:
        return None
    from uuid import UUID
    return db.upload(conn, UUID(bid), include_blob=True)
