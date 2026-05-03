from __future__ import annotations

import io
import re
import unicodedata
from typing import Any, Iterable

import pandas as pd

from app.services.errors import ConversionError

def _normalize_col(col: object) -> str:
    if col is None:
        return ""
    # Normalize common excel headers with line breaks/spaces.
    s = str(col).strip()
    s = s.replace("\r", "").replace("\n", "")
    s = re.sub(r"\s+", "", s)
    return s


def _alias_matches_cell(alias: str, cell_norm: str) -> bool:
    """Exact match, or alias (len>=2) as substring of normalized cell/header."""
    a = _normalize_col(alias)
    c = cell_norm or ""
    if not a or not c:
        return False
    if a == c:
        return True
    if len(a) < 2:
        return False
    return a in c


def _row_matches_required_groups(row_vals: list[object], required_col_groups: list[tuple[str, ...]]) -> bool:
    row_norm = [_normalize_col(v) for v in row_vals]
    for group in required_col_groups:
        if not group:
            continue
        if not any(any(_alias_matches_cell(a, cell) for cell in row_norm) for a in group):
            return False
    return True


def _column_names_match_required(norm_cols: list[str], required_col_groups: list[tuple[str, ...]]) -> bool:
    for group in required_col_groups:
        if not group:
            continue
        if not any(any(_alias_matches_cell(a, col) for col in norm_cols) for a in group):
            return False
    return True


def _horizontal_ffill_row_norm(raw: pd.DataFrame, r: int, ncols: int) -> list[str]:
    """Left-to-right carry of last non-empty cell (handles horizontally merged header cells)."""
    out: list[str] = []
    carry = ""
    for c in range(ncols):
        t = _normalize_col(raw.iloc[r, c])
        if t:
            carry = t
        out.append(carry)
    return out


def _texts_from_dataframe_block(block: pd.DataFrame) -> list[str]:
    """All normalized non-empty cell strings in a rectangular block (merged cells → sparse; union across rows)."""
    texts: list[str] = []
    ncols = block.shape[1]
    for r in range(len(block)):
        for t in _horizontal_ffill_row_norm(block, r, ncols):
            if t:
                texts.append(t)
    return texts


def _header_block_matches_union(block: pd.DataFrame, required_col_groups: list[tuple[str, ...]]) -> bool:
    texts = _texts_from_dataframe_block(block)
    for group in required_col_groups:
        if not group:
            continue
        if not any(any(_alias_matches_cell(a, t) for t in texts) for a in group):
            return False
    return True


def _build_merged_header_labels(raw: pd.DataFrame, i_start: int, depth: int) -> list[str]:
    """Per column: after horizontal ffill on each header row, take bottom row's label (merged multi-row headers)."""
    ncols = raw.shape[1]
    labels = [""] * ncols
    for r in range(i_start, min(i_start + depth, len(raw))):
        row_ff = _horizontal_ffill_row_norm(raw, r, ncols)
        for j in range(ncols):
            if row_ff[j]:
                labels[j] = row_ff[j]
    return labels


def _dedupe_column_names(names: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    out: list[str] = []
    for n in names:
        base = n if n else "__空列__"
        c = counts.get(base, 0)
        counts[base] = c + 1
        out.append(base if c == 0 else f"{base}__{c}")
    return out


def _locate_header_block_any(
    raw: pd.DataFrame,
    required_col_groups: list[tuple[str, ...]],
    *,
    scan_limit: int,
    max_header_depth: int,
) -> tuple[int, int] | None:
    """
    Find (start_row, depth) such that the union of cells in raw[start:start+depth] matches all required groups.
    Uses the same rules as multi-segment header detection (title row, data row guards, minimal depth).
    Picks best candidate by: most non-empty merged header labels, then smallest start row, then smallest depth.
    """
    candidates: list[tuple[int, int, int]] = []
    n = min(scan_limit, len(raw))
    for i in range(n):
        d = _header_match_depth_at(
            raw,
            i,
            required_col_groups=required_col_groups,
            max_header_depth=max_header_depth,
        )
        if d is None:
            continue
        hv = _build_merged_header_labels(raw, i, d)
        score = sum(1 for x in hv if x)
        candidates.append((i, d, score))
    if not candidates:
        return None
    candidates.sort(key=lambda t: (-t[2], t[0], t[1]))
    i0, d0, _ = candidates[0]
    return (i0, d0)


def _parse_multi_month_pairs_from_text(text: object) -> list[tuple[int, int]]:
    """e.g. '2025年9&10月' / '2025年9和10月' -> [(2025,9),(2025,10)]."""
    if text is None or (isinstance(text, float) and pd.isna(text)):  # type: ignore[truthy-bool]
        return []
    s = str(text)
    if not s.strip():
        return []
    m = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*[&＆和、]\s*(\d{1,2})\s*月", s)
    if m:
        y, a, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= a <= 12 and 1 <= b <= 12:
            return [(y, a), (y, b)]
    return []


def _discover_ordered_months_for_sheet(
    raw: pd.DataFrame,
    first_header_i: int,
    *,
    default_year: int | None,
    client_filename: str | None,
) -> list[tuple[int, int]]:
    """Collect (year, month) in scan order from title area above first header (multi-month sheets)."""
    ordered: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()

    def add(ym: tuple[int, int] | None) -> None:
        if ym is None or not (1 <= ym[1] <= 12):
            return
        if ym not in seen:
            seen.add(ym)
            ordered.append(ym)

    scan_hi = min(first_header_i, min(150, len(raw)))
    for r in range(scan_hi):
        for c in range(min(raw.shape[1], 120)):
            cell = raw.iloc[r, c]
            for ym in _parse_multi_month_pairs_from_text(cell):
                add(ym)
            add(_parse_year_month(cell, default_year=default_year))
            add(_extract_year_month_from_text(cell))
    add(_extract_year_month_from_filename(client_filename))
    return ordered


def _infer_segment_ym(
    raw: pd.DataFrame,
    abs_i: int,
    d0: int,
    *,
    segment_idx: int,
    month_chain: list[tuple[int, int]],
    default_year: int | None,
    client_filename: str | None,
) -> tuple[int, int] | None:
    if segment_idx < len(month_chain):
        return month_chain[segment_idx]
    if month_chain:
        return month_chain[-1]
    for r in range(max(0, abs_i - 25), abs_i):
        for c in range(min(raw.shape[1], 80)):
            ym = _parse_year_month(raw.iloc[r, c], default_year=default_year)
            if ym:
                return ym
            ym2 = _extract_year_month_from_text(raw.iloc[r, c])
            if ym2:
                return ym2
    for r in range(abs_i + d0, min(abs_i + d0 + 15, len(raw))):
        for c in range(min(raw.shape[1], 40)):
            ym = _parse_year_month(raw.iloc[r, c], default_year=default_year)
            if ym:
                return ym
    return _extract_year_month_from_filename(client_filename)


def _row_is_all_empty(raw: pd.DataFrame, r: int) -> bool:
    for c in range(raw.shape[1]):
        if _normalize_col(raw.iloc[r, c]):
            return False
    return True


def _row_has_any_visible_cell(row_vals: list[object]) -> bool:
    """表体行是否含至少一个非空可见单元格（用于逐行读取时跳过整行空白）。"""
    for v in row_vals:
        if v is None or (isinstance(v, float) and pd.isna(v)):  # type: ignore[truthy-bool]
            continue
        s = str(v).strip()
        if s and s.lower() != "nan":
            return True
    return False


def _cell_float_hour_like(v: object) -> bool:
    """是否为像「单格工时」的合理数值（排除金额列常见大数）。"""
    x = _money_to_float_loose(v)
    return 0.01 <= x <= 400.0


def _can_start_header_block(raw: pd.DataFrame, i: int, required_col_groups: list[tuple[str, ...]]) -> bool:
    """
    Avoid treating data rows as header: the row above a real header must not itself be a full header row
    (e.g. 姓名/月度/… on consecutive rows would otherwise merge into a fake deep block).
    """
    if i <= 0:
        return True
    if _row_is_all_empty(raw, i - 1):
        return True
    prev_vals = raw.iloc[i - 1].tolist()
    return not _row_matches_required_groups(prev_vals, required_col_groups)


def _row_looks_like_dual_hourly_data_row(row_vals: list[object]) -> bool:
    """
    True if this row looks like ATO/Flyme 表体数据行（不应作为新表头起点）。
    整合表里「类型=正常/加班」且同行存在工时数时，极易被误识别为第二段表头并吞掉两行，此处显式判为数据行。
    """
    for v in row_vals:
        s = _normalize_col(v)
        if "正常工作日出勤" in s or "加班工时" in s:
            return True
    has_short_kind = False
    for v in row_vals:
        s = _normalize_col(v)
        if not s or len(s) > 16:
            continue
        if re.match(r"^(正常|加班|加点|工作日|标准)", s):
            has_short_kind = True
            break
    if has_short_kind and any(_cell_float_hour_like(x) for x in row_vals):
        return True
    return False


def _dataframe_from_raw_body_rowwise(
    raw: pd.DataFrame,
    r0: int,
    r1: int,
    header_vals: list[str],
) -> pd.DataFrame:
    """
    从 raw[r0:r1) 逐行拷贝为 DataFrame（列名同分段表头）。
    与「整块 iloc + dropna」相比：逐格读取、仅跳过整行无可见内容的行，避免整合表边界与全空判定差异导致丢行。
    """
    n = len(raw)
    ncols_raw = raw.shape[1]
    if len(header_vals) < ncols_raw:
        hv = header_vals + [f"__空列_{j}" for j in range(len(header_vals), ncols_raw)]
    elif len(header_vals) > ncols_raw:
        hv = header_vals[:ncols_raw]
    else:
        hv = list(header_vals)
    rows: list[list[object]] = []
    for ri in range(r0, min(r1, n)):
        row_vals = [raw.iat[ri, c] for c in range(ncols_raw)]
        if not _row_has_any_visible_cell(row_vals):
            continue
        rows.append(row_vals)
    if not rows:
        return pd.DataFrame(columns=hv)
    return pd.DataFrame(rows, columns=hv)


def _header_match_depth_at(
    raw: pd.DataFrame,
    i: int,
    *,
    required_col_groups: list[tuple[str, ...]],
    max_header_depth: int,
) -> int | None:
    if not _can_start_header_block(raw, i, required_col_groups):
        return None
    if _row_looks_like_dual_hourly_data_row(raw.iloc[i].tolist()):
        return None
    candidates: list[int] = []
    for d in range(1, max_header_depth + 1):
        if i + d > len(raw):
            break
        block = raw.iloc[i : i + d, :]
        if not _header_block_matches_union(block, required_col_groups):
            continue
        if not _row_matches_required_groups(block.iloc[len(block) - 1].tolist(), required_col_groups):
            continue
        # Avoid (title row + header row) as one block starting at row 0
        if i == 0 and d > 1 and (not _row_matches_required_groups(raw.iloc[0].tolist(), required_col_groups)):
            continue
        candidates.append(d)
    return min(candidates) if candidates else None


def _enumerate_header_segment_starts(
    raw: pd.DataFrame,
    required_col_groups: list[tuple[str, ...]],
    *,
    max_header_depth: int,
) -> list[tuple[int, int]]:
    """
    Each (start_row, depth) is one settlement table header block.
    Scans top-to-bottom: after a header block, walks data rows until the next row that starts a header block.
    """
    n = len(raw)
    segments: list[tuple[int, int]] = []
    i = 0
    while i < n:
        d0 = _header_match_depth_at(
            raw,
            i,
            required_col_groups=required_col_groups,
            max_header_depth=max_header_depth,
        )
        if d0 is None:
            i += 1
            continue
        segments.append((i, d0))
        j = i + d0
        while j < n:
            d1 = _header_match_depth_at(
                raw,
                j,
                required_col_groups=required_col_groups,
                max_header_depth=max_header_depth,
            )
            if d1 is not None:
                break
            j += 1
        i = j
    return segments


def _read_dual_row_sheet_as_concat_segments(
    xls: pd.ExcelFile,
    sheet_name: str,
    *,
    settlement_required_header: list[tuple[str, ...]],
    max_scan_rows: int,
    max_header_depth: int,
    default_year: int | None,
    client_filename: str | None,
) -> pd.DataFrame:
    row_read_limit = max(200, min(int(max_scan_rows), 20_000))
    raw = pd.read_excel(xls, sheet_name=sheet_name, header=None, dtype=object, nrows=row_read_limit)
    segments = _enumerate_header_segment_starts(
        raw,
        settlement_required_header,
        max_header_depth=max_header_depth,
    )
    if not segments:
        df0 = pd.read_excel(xls, sheet_name=sheet_name, dtype=object)
        cols_norm = [_normalize_col(c) for c in df0.columns]
        raise ConversionError(
            f"sheet「{sheet_name}」缺少必要列（已读入{len(raw)}行；多段表头匹配不要求「月度」列）。"
            f"首行解析列：{list(cols_norm)}",
            code="missing_columns",
        )

    first_hi = segments[0][0]
    month_chain = _discover_ordered_months_for_sheet(
        raw,
        first_hi,
        default_year=default_year,
        client_filename=client_filename,
    )

    parts: list[pd.DataFrame] = []
    for idx, (abs_i, d0) in enumerate(segments):
        end_abs = segments[idx + 1][0] if idx + 1 < len(segments) else len(raw)
        header_vals = _dedupe_column_names(_build_merged_header_labels(raw, abs_i, d0))
        body = _dataframe_from_raw_body_rowwise(raw, abs_i + d0, end_abs, header_vals)
        if body.empty:
            continue
        seg_ym = _infer_segment_ym(
            raw,
            abs_i,
            d0,
            segment_idx=idx,
            month_chain=month_chain,
            default_year=default_year,
            client_filename=client_filename,
        )
        if seg_ym is not None:
            body["_segment_ym"] = [seg_ym] * len(body)
        parts.append(body)

    if not parts:
        raise ConversionError(
            f"sheet「{sheet_name}」识别到表头块但未解析到有效数据行。",
            code="ato_empty_segments",
        )
    return pd.concat(parts, ignore_index=True)


def _find_sheet(sheet_names: Iterable[str], keywords: tuple[str, ...], *, kind: str) -> str:
    matches: list[str] = []
    for name in sheet_names:
        if any(k in name for k in keywords):
            matches.append(name)

    if not matches:
        raise ConversionError(f"未找到包含关键字 {keywords} 的{kind}sheet。实际sheet：{list(sheet_names)}", code="sheet_not_found")
    if len(matches) > 1:
        raise ConversionError(
            f"找到多个可能的{kind}sheet：{matches}。请保证只有一个sheet名称包含 {keywords}。",
            code="sheet_ambiguous",
        )
    return matches[0]


def _find_sheet_by_content(
    xls: pd.ExcelFile,
    *,
    required_col_groups: list[tuple[str, ...]],
    kind: str,
    max_scan_rows: int = 400,
) -> str:
    """
    Fallback sheet detection: scan each sheet's first N rows to find a header block
    (single- or multi-row, merged-cell tolerant) that contains all required columns.
    """
    matches: list[str] = []
    nrows = max(120, min(int(max_scan_rows), 2000))
    for name in xls.sheet_names:
        try:
            raw = pd.read_excel(xls, sheet_name=name, header=None, dtype=object, nrows=nrows)
        except Exception:
            continue
        if _locate_header_block_any(
            raw,
            required_col_groups,
            scan_limit=len(raw),
            max_header_depth=6,
        ) is not None:
            matches.append(name)

    if not matches:
        raise ConversionError(
            f"未找到可用的{kind}sheet：既未命中sheet名关键字，也未扫描到满足所需列的sheet。",
            code="sheet_not_found",
        )
    if len(matches) > 1:
        raise ConversionError(
            f"扫描到多个可能的{kind}sheet：{matches}。请重命名sheet或减少歧义。",
            code="sheet_ambiguous",
        )
    return matches[0]


def _find_sheet_smart(
    xls: pd.ExcelFile,
    *,
    keywords: tuple[str, ...],
    required_col_groups: list[tuple[str, ...]],
    kind: str,
) -> str:
    # Prefer keywords by sheet name; fallback to content scan.
    try:
        return _find_sheet(xls.sheet_names, keywords, kind=kind)
    except ConversionError as e:
        if e.code not in ("sheet_not_found",):
            raise
        return _find_sheet_by_content(xls, required_col_groups=required_col_groups, kind=kind)


def _find_sheet_optional_smart(
    xls: pd.ExcelFile,
    *,
    keywords: tuple[str, ...],
    required_col_groups: list[tuple[str, ...]],
    kind: str,
) -> str | None:
    if not keywords:
        return None
    try:
        return _find_sheet_smart(xls, keywords=keywords, required_col_groups=required_col_groups, kind=kind)
    except ConversionError as e:
        # Optional sheet: if nothing matches by name or content, treat as absent.
        if e.code == "sheet_not_found":
            return None
        raise


def _require_columns(df: pd.DataFrame, required: list[str], *, sheet: str) -> None:
    cols = {_normalize_col(c) for c in df.columns}
    missing = [c for c in required if c not in cols]
    if missing:
        raise ConversionError(f"sheet「{sheet}」缺少列：{missing}。实际列：{list(df.columns)}", code="missing_columns")


def _read_excel_with_header_detection(
    xls: pd.ExcelFile,
    *,
    sheet_name: str,
    required_cols: list[str],
    max_scan_rows: int = 20,
) -> pd.DataFrame:
    """
    Some customer sheets use multi-row headers:
    - row 0: group headers like '基本信息'/'服务费'
    - row 1: actual field headers like '姓名'/'月结算价'/...
    This function detects the first row that contains all required columns and uses it as header.
    """
    df = pd.read_excel(xls, sheet_name=sheet_name, dtype=object)
    df.columns = [_normalize_col(c) for c in df.columns]

    cols = set(df.columns)
    if all(c in cols for c in required_cols):
        return df

    # Fallback: read without header, scan for a row that contains required column names
    raw = pd.read_excel(xls, sheet_name=sheet_name, header=None, dtype=object)
    scan_limit = min(max_scan_rows, len(raw))
    required_set = set(required_cols)
    header_row_idx: int | None = None
    for i in range(scan_limit):
        row_vals = [_normalize_col(v) for v in raw.iloc[i].tolist()]
        if required_set.issubset(set(row_vals)):
            header_row_idx = i
            break

    if header_row_idx is None:
        # keep original error details for easier debugging
        raise ConversionError(
            f"sheet「{sheet_name}」缺少列：{[c for c in required_cols if c not in cols]}。实际列：{list(df.columns)}",
            code="missing_columns",
        )

    header_vals = [_normalize_col(v) for v in raw.iloc[header_row_idx].tolist()]
    data = raw.iloc[header_row_idx + 1 :].copy()
    data.columns = header_vals
    return data


def _read_excel_with_header_detection_any(
    xls: pd.ExcelFile,
    *,
    sheet_name: str,
    required_col_groups: list[tuple[str, ...]],
    max_scan_rows: int = 2000,
    max_header_depth: int = 6,
) -> pd.DataFrame:
    """
    Like _read_excel_with_header_detection, but each required field can have multiple alias columns.
    required_col_groups: list of (alias1, alias2, ...)
    Supports title rows and merged multi-row headers: reads up to max_scan_rows, unions cells in a sliding
    row block (depth up to max_header_depth) to match required fields, then builds per-column labels from
    the last non-empty cell in each column within that block.
    """
    df = pd.read_excel(xls, sheet_name=sheet_name, dtype=object)
    cols_norm = [_normalize_col(c) for c in df.columns]
    if _column_names_match_required(cols_norm, required_col_groups):
        df.columns = cols_norm
        return df

    row_read_limit = max(200, min(int(max_scan_rows), 20_000))
    raw = pd.read_excel(xls, sheet_name=sheet_name, header=None, dtype=object, nrows=row_read_limit)
    scan_limit = len(raw)
    loc = _locate_header_block_any(
        raw,
        required_col_groups,
        scan_limit=scan_limit,
        max_header_depth=max_header_depth,
    )

    if loc is None:
        raise ConversionError(
            f"sheet「{sheet_name}」缺少必要列（已读入{scan_limit}行，表头块深度1–{max_header_depth}行联合匹配）。"
            f"首行解析列：{list(cols_norm)}",
            code="missing_columns",
        )

    i0, d0 = loc
    header_vals = _dedupe_column_names(_build_merged_header_labels(raw, i0, d0))
    data = raw.iloc[i0 + d0 :].copy()
    ncols = data.shape[1]
    if len(header_vals) < ncols:
        header_vals = header_vals + [f"__空列_{j}" for j in range(len(header_vals), ncols)]
    elif len(header_vals) > ncols:
        header_vals = header_vals[:ncols]
    data.columns = header_vals
    return data


def _extract_year_from_filename(filename: str | None) -> int | None:
    if not filename:
        return None
    m = re.search(r"(20\d{2})", filename)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _parse_year_month(raw: object, *, default_year: int | None) -> tuple[int, int] | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = str(raw).strip()
    if not s:
        return None

    # 2025-04 / 2025/04 / 2025年4月
    m = re.search(r"(20\d{2})\D{0,3}(\d{1,2})", s)
    if m:
        y = int(m.group(1))
        mo = int(m.group(2))
        return (y, mo)

    # 4月 / 04月
    m2 = re.search(r"^(\d{1,2})\s*月$", s)
    if m2 and default_year is not None:
        return (default_year, int(m2.group(1)))

    # 2025-4 (not strict)
    m3 = re.search(r"^(20\d{2})[-/](\d{1,2})$", s)
    if m3:
        return (int(m3.group(1)), int(m3.group(2)))

    return None


def _extract_year_month_from_filename(filename: str | None) -> tuple[int, int] | None:
    if not filename:
        return None
    s = str(filename)
    m = re.search(r"(20\d{2}).{0,6}?(\d{1,2})\s*月", s)
    if m:
        try:
            return (int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    m2 = re.search(r"(20\d{2})[-_/\.](\d{1,2})", s)
    if m2:
        try:
            return (int(m2.group(1)), int(m2.group(2)))
        except ValueError:
            return None
    return None


def _extract_year_month_from_text(text: object) -> tuple[int, int] | None:
    if text is None or (isinstance(text, float) and pd.isna(text)):  # type: ignore[truthy-bool]
        return None
    s = str(text)
    if not s.strip():
        return None
    m = re.search(r"(20\d{2}).{0,12}?(\d{1,2})\s*月", s)
    if m:
        try:
            return (int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    m2 = re.search(r"(20\d{2})[-_/\.](\d{1,2})", s)
    if m2:
        try:
            return (int(m2.group(1)), int(m2.group(2)))
        except ValueError:
            return None
    m3 = re.search(r"(20\d{2})\D{0,3}(\d{1,2})\D{0,3}", s)
    if m3:
        try:
            mo = int(m3.group(2))
            if 1 <= mo <= 12:
                return (int(m3.group(1)), mo)
        except ValueError:
            return None
    return None


def _detect_ym_from_sheet_cells(xls: pd.ExcelFile, sheet_name: str, *, max_scan_rows: int = 15) -> tuple[int, int] | None:
    ym_from_sheet_name = _extract_year_month_from_text(sheet_name)
    if ym_from_sheet_name:
        return ym_from_sheet_name

    try:
        raw = pd.read_excel(xls, sheet_name=sheet_name, header=None, dtype=object, nrows=max_scan_rows)
    except Exception as e:
        return None
    for i in range(min(max_scan_rows, len(raw))):
        row = raw.iloc[i].tolist()
        for cell in row:
            ym = _extract_year_month_from_text(cell)
            if ym:
                return ym
    return None


def _raw_person_name_cell(v: object) -> str:
    """姓名列原始值（不 ffill）：用于按「新姓名行」切段，避免空行被误并入上一人。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):  # type: ignore[truthy-bool]
        return ""
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return ""
    return s


def _ym_equal(a: object, b: object) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if isinstance(a, float) and pd.isna(a):
        return isinstance(b, float) and pd.isna(b)
    if isinstance(b, float) and pd.isna(b):
        return isinstance(a, float) and pd.isna(a)
    return bool(a == b)


def _money_to_float(raw: object) -> float:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if not s:
        return 0.0
    s = s.replace("￥", "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        raise ConversionError(f"无法解析金额：{raw}", code="money_parse_error")


def _money_to_float_loose(raw: object) -> float:
    """结算表体中偶发页脚、合并格文字（如「盖章」）等非数字：不计入金额，不中断整表解析。"""
    try:
        return _money_to_float(raw)
    except ConversionError:
        return 0.0


_SETTLEMENT_BODY_ROW_SKIP_MARKERS: tuple[str, ...] = (
    # 表尾/汇总说明行（非人员明细），整行跳过不参与聚合
    "出勤总工时数",
    "月度金额合计",
    "金额合计（大写）",
    "合计（大写）",
)


def _settlement_row_has_skip_marker(row: pd.Series) -> bool:
    """若行内任一单元格合并文本命中汇总/表尾标记，则本行不参与解析。"""
    parts: list[str] = []
    for c, v in row.items():
        if str(c).startswith("__"):
            continue
        if v is None or (isinstance(v, float) and pd.isna(v)):  # type: ignore[truthy-bool]
            continue
        s = str(v).strip()
        if not s or s.lower() == "nan":
            continue
        s = unicodedata.normalize("NFKC", s)
        s = re.sub(r"\s+", "", s)
        parts.append(s)
    if not parts:
        return False
    blob = "|".join(parts)
    for m in _SETTLEMENT_BODY_ROW_SKIP_MARKERS:
        mn = re.sub(r"\s+", "", unicodedata.normalize("NFKC", m))
        if mn and mn in blob:
            return True
    return False


def _drop_settlement_skip_marker_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    mask = ~df.apply(_settlement_row_has_skip_marker, axis=1)
    out = df.loc[mask].copy().reset_index(drop=True)
    if "_ord" in out.columns:
        out["_ord"] = pd.RangeIndex(len(out))
    return out


_SETTLEMENT_NAME_NOISE_MARKERS: tuple[str, ...] = (
    "盖章",
    "签章",
    "签字",
    "手签",
    "审核",
    "审批",
    "核准",
    "经办",
    "制表",
    "填表",
    "负责人",
    "编制",
    "确认",
)


def _is_settlement_noise_name(name_s: str) -> bool:
    """姓名列出现签章/审核等页脚文案时，不作为人员数据行处理。"""
    s = name_s.strip()
    if not s:
        return False
    return any(m in s for m in _SETTLEMENT_NAME_NOISE_MARKERS)


def _is_pure_serial_name_cell(s: str) -> bool:
    """
    垂直两行模板里，加班行「姓名」格常为合并/错位导致的纯序号（1、2、12）。
    不应视为「他人姓名」而截断本段，否则第一人等首条记录的加班行会被丢。
    """
    t = s.strip()
    return bool(re.fullmatch(r"\d{1,4}$", t))


def _classify_dual_row_kind(raw: object) -> str:
    """Return 'normal' | 'overtime' | '' (blank row)."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):  # type: ignore[truthy-bool]
        return ""
    s = str(raw).strip().replace("\r", "").replace("\n", "")
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", "", s)
    if not s:
        return ""
    if "加班" in s or "加点" in s or s.lower() in ("ot", "o.t"):
        return "overtime"
    if (
        "正常" in s
        or "工作日出勤" in s
        or ("工作" in s and "出勤" in s)
        or "工作日" in s
        or "平日" in s
        or "标准工时" in s
    ):
        return "normal"
    raise ConversionError(
        f"无法识别的结算行类型：{raw!r}（应为「正常工作日出勤」或「加班工时」等）",
        code="unknown_row_kind",
    )


def _classify_dual_row_kind_loose(raw: object) -> str:
    """表体偶发非标文案：不抛错，按无类型行跳过。"""
    try:
        return _classify_dual_row_kind(raw)
    except ConversionError:
        return ""


def _pick_col_best_longest(
    df: pd.DataFrame,
    candidates: tuple[str, ...],
    *,
    kind: str,
    sheet: str,
) -> str:
    """
    在「精确列名」与「唯一子串匹配」中优先选**更长别名**命中者，避免宽表里短词「工时」先匹配到错误窄列。
    若无唯一子串匹配则退回 _pick_col（可能报歧义）。
    """
    cols = [_normalize_col(c) for c in df.columns]
    colset = set(cols)
    best_exact: str | None = None
    best_exact_len = -1
    for c in candidates:
        cn = _normalize_col(c)
        if cn in colset and len(cn) > best_exact_len:
            best_exact_len = len(cn)
            best_exact = cn
    if best_exact is not None:
        return best_exact
    best_alias_len = -1
    best_col: str | None = None
    for c in sorted(candidates, key=lambda x: -len(_normalize_col(x))):
        cn = _normalize_col(c)
        if len(cn) < 2:
            continue
        hits = [col for col in cols if _alias_matches_cell(cn, col)]
        if len(hits) != 1:
            continue
        if len(cn) > best_alias_len:
            best_alias_len = len(cn)
            best_col = hits[0]
    if best_col is not None:
        return best_col
    return _pick_col(df, candidates, kind=kind, sheet=sheet)


def _pick_col_excluding(
    df: pd.DataFrame,
    candidates: tuple[str, ...],
    *,
    exclude_norm_cols: set[str],
    kind: str,
    sheet: str,
) -> str:
    """Like _pick_col but never returns a column whose normalized name is in exclude_norm_cols."""
    cols = [_normalize_col(c) for c in df.columns]
    colset = set(cols)
    for c in candidates:
        cn = _normalize_col(c)
        if cn in colset and cn not in exclude_norm_cols:
            return cn
    for c in sorted(candidates, key=lambda x: -len(_normalize_col(x))):
        cn = _normalize_col(c)
        if len(cn) < 2:
            continue
        hits = [col for col in cols if _alias_matches_cell(cn, col) and col not in exclude_norm_cols]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise ConversionError(
                f"sheet「{sheet}」{kind}列候选「{c}」匹配到多列：{hits}（已排除{sorted(exclude_norm_cols)}）。"
                f"请在客户规则里补充更精确的列别名。",
                code="ambiguous_column",
            )
    raise ConversionError(
        f"sheet「{sheet}」未找到{kind}列（候选：{list(candidates)}，已排除{sorted(exclude_norm_cols)}）。实际列：{cols}",
        code="missing_columns",
    )



def _aggregate_settlement_dual_row_hourly(
    settlement: pd.DataFrame,
    *,
    s_kind: str,
    s_hours: str,
    s_rate: str,
) -> pd.DataFrame:
    rows_out: list[dict[str, object]] = []
    tol = 0.02

    for (name, ym), grp in settlement.groupby(["_name", "_ym"], sort=False):
        if ym is None or (isinstance(ym, float) and pd.isna(ym)):  # type: ignore[truthy-bool]
            continue
        name_s = str(name).strip()
        if not name_s:
            continue

        normal_h = 0.0
        ot_h = 0.0
        rates: list[float] = []

        grp2 = grp.sort_values("_ord", ignore_index=True) if "_ord" in grp.columns else grp.reset_index(drop=True)
        h_positive_idx = [
            i2
            for i2, (_, r) in enumerate(grp2.iterrows())
            if _money_to_float_loose(r.get(s_hours)) > 0
        ]
        use_pair_infer = len(h_positive_idx) == 2

        last_rate = 0.0
        for i2, (_, r) in enumerate(grp2.iterrows()):
            kind = _classify_dual_row_kind_loose(r.get(s_kind))
            h = _money_to_float_loose(r.get(s_hours))
            if use_pair_infer and (not kind) and h > 0:
                pos = h_positive_idx.index(i2)
                kind = "normal" if pos == 0 else "overtime"
            if not kind:
                continue
            rate_cell = _money_to_float_loose(r.get(s_rate))
            if rate_cell > 0:
                last_rate = rate_cell
            eff_rate = rate_cell if rate_cell > 0 else last_rate
            if h > 0 and eff_rate <= 0:
                raise ConversionError(
                    f"人员「{name_s}」{ym} 存在工时已填写但含税小时单价无效。",
                    code="ato_hourly_rate_missing",
                )
            if h > 0 and eff_rate > 0:
                rates.append(eff_rate)
            if kind == "normal":
                normal_h += h
            else:
                ot_h += h

        if normal_h <= 0 and ot_h <= 0:
            continue

        if not rates:
            raise ConversionError(
                f"人员「{name_s}」{ym} 的结算行缺少有效的含税小时单价。",
                code="ato_hourly_rate_missing",
            )
        r_min, r_max = min(rates), max(rates)
        if r_max - r_min > tol:
            raise ConversionError(
                f"人员「{name_s}」{ym} 的含税小时单价不一致：最小{r_min}，最大{r_max}。",
                code="ato_hourly_rate_mismatch",
            )
        hourly_tax = sum(rates) / len(rates)

        rows_out.append(
            {
                "name": name_s,
                "ym": ym,
                "monthly_price": 0.0,
                "actual_days": 0.0,
                "normal_hours": normal_h,
                "overtime_hours": ot_h,
                "hourly_price_tax": hourly_tax,
            }
        )

    if not rows_out:
        raise ConversionError("结算表未解析到任何有效的人员工时行。", code="ato_no_rows")

    return pd.DataFrame(rows_out)


def _aggregate_settlement_vertical_merged_pairs(
    settlement: pd.DataFrame,
    *,
    s_name: str,
    s_stat: str,
    s_rate: str,
    s_other: str | None,
) -> pd.DataFrame:
    """
    模板：每人占两行，「统计工时」列第一行为正常出勤小时、第二行为加班小时；
    「单价」等列纵向合并，pandas 通常只在首行有值。

    不按「姓名 ffill + groupby」聚合：否则姓名列与下一行之间的空行、分隔行会继承上一人姓名，
    被误算成同一人多行。改为：仅在「姓名单元格非空且与上一段不同」的行开启新人员段；其后可接多行
    （姓名为空、或姓名与首行相同——合并格重复、或姓名为纯数字序号 1～9999——常见于加班行错位），
    直到下一「他人姓名」行。

    加班工时 = 首行之后所有数据行上「统计工时」之和（支持首行与加班行之间夹空行、或多行加班工时）；
    正常工时 = 首行统计工时。
    """
    df = settlement.reset_index(drop=True).copy()
    df["_ord"] = df.index
    df = df.sort_values("_ord")
    rows_out: list[dict[str, object]] = []
    tol = 0.02
    n = len(df)
    i = 0
    max_group_rows = 24
    max_blank_skip = 10

    while i < n:
        r0 = df.iloc[i]
        ym0 = r0["_ym"]
        if ym0 is None or (isinstance(ym0, float) and pd.isna(ym0)):  # type: ignore[truthy-bool]
            i += 1
            continue
        name_s = _raw_person_name_cell(r0[s_name])
        if not name_s:
            i += 1
            continue
        if _is_settlement_noise_name(name_s):
            i += 1
            continue

        idxs = [i]
        i += 1
        blank_run = 0
        while i < n and len(idxs) < max_group_rows:
            r1 = df.iloc[i]
            cont_name = _raw_person_name_cell(r1[s_name])
            if cont_name and cont_name != name_s and not _is_pure_serial_name_cell(cont_name):
                break
            ym1 = r1["_ym"]
            if not (
                ym1 is None
                or (isinstance(ym1, float) and pd.isna(ym1))  # type: ignore[truthy-bool]
                or _ym_equal(ym1, ym0)
            ):
                break
            st1 = _money_to_float_loose(r1.get(s_stat))
            rt1 = _money_to_float_loose(r1.get(s_rate))
            ot1 = _money_to_float_loose(r1.get(s_other)) if s_other else 0.0
            row_blank = st1 <= 0 and rt1 <= 0 and ot1 <= 0
            if row_blank:
                blank_run += 1
                if blank_run > max_blank_skip:
                    break
                i += 1
                continue
            blank_run = 0
            idxs.append(i)
            i += 1

        grp = df.loc[idxs]
        stats = [_money_to_float_loose(x) for x in grp[s_stat].tolist()]
        rates: list[float] = []
        other_tax = 0.0
        for _, r in grp.iterrows():
            v = _money_to_float_loose(r.get(s_rate))
            if v > 0:
                rates.append(v)
            if s_other:
                other_tax = max(other_tax, _money_to_float_loose(r.get(s_other)))

        has_hours = any(s > 0 for s in stats) if stats else False
        if not (has_hours or rates):
            continue

        nh = float(stats[0]) if stats else 0.0
        oh = float(sum(stats[1:])) if len(stats) > 1 else 0.0

        if not rates:
            raise ConversionError(
                f"人员「{name_s}」{ym0} 缺少有效的单价。",
                code="ato_hourly_rate_missing",
            )
        r_min, r_max = min(rates), max(rates)
        if r_max - r_min > tol:
            raise ConversionError(
                f"人员「{name_s}」{ym0} 的单价不一致：最小{r_min}，最大{r_max}。",
                code="ato_hourly_rate_mismatch",
            )
        hourly_tax = sum(rates) / len(rates)

        rows_out.append(
            {
                "name": name_s,
                "ym": ym0,
                "monthly_price": 0.0,
                "actual_days": 0.0,
                "normal_hours": nh,
                "overtime_hours": oh,
                "hourly_price_tax": hourly_tax,
                "other_fee_tax": other_tax,
            }
        )

    if not rows_out:
        raise ConversionError("结算表未解析到任何有效的人员两行工时记录。", code="ato_no_rows")

    out = pd.DataFrame(rows_out)
    for (_nk, ym_k), grp in out.groupby(["name", "ym"], sort=False):
        r_min, r_max = grp["hourly_price_tax"].min(), grp["hourly_price_tax"].max()
        if r_max - r_min > tol:
            name_err = str(grp["name"].iloc[0])
            raise ConversionError(
                f"人员「{name_err}」{ym_k} 存在多段记录且单价不一致：最小{r_min}，最大{r_max}。",
                code="ato_hourly_rate_mismatch",
            )

    return (
        out.groupby(["name", "ym"], as_index=False)
        .agg(
            monthly_price=("monthly_price", "max"),
            actual_days=("actual_days", "max"),
            normal_hours=("normal_hours", "sum"),
            overtime_hours=("overtime_hours", "sum"),
            hourly_price_tax=("hourly_price_tax", "max"),
            other_fee_tax=("other_fee_tax", "max"),
        )
    )


def _jsonable_cell(v: object) -> object:
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    if isinstance(v, (str, int, bool)):
        return v
    if isinstance(v, float):
        return v
    if isinstance(v, tuple) and len(v) == 2 and all(isinstance(x, int) for x in v):
        return [v[0], v[1]]
    return str(v)[:500]


def _build_settlement_parse_debug(
    *,
    settlement: pd.DataFrame,
    settlement_std: pd.DataFrame,
    settlement_sheet: str,
    rows_1based: tuple[int, ...],
    pick_map: dict[str, str | None],
    flyme_dual_branch: bool,
) -> dict[str, Any]:
    """
    data_row_1based：解析并剥除表头后的结算表 DataFrame 行号（第 1 行 = 首条数据行），
    与 Excel 左侧「第 31 行」通常相差表头/标题占用行数。
    """
    pick_clean = {k: v for k, v in pick_map.items() if v}
    rows_out: list[dict[str, Any]] = []
    for rn in rows_1based:
        if rn < 1:
            continue
        i0 = rn - 1
        if i0 >= len(settlement):
            rows_out.append(
                {
                    "data_row_1based": rn,
                    "error": "out_of_range",
                    "settlement_len": len(settlement),
                }
            )
            continue
        row = settlement.iloc[i0]
        cells: dict[str, Any] = {}
        for j, c in enumerate(settlement.columns):
            if str(c).startswith("__"):
                continue
            if j >= 120:
                cells["__truncated_columns_after__"] = str(c)
                break
            cells[str(c)] = _jsonable_cell(row.get(c))
        sk = pick_map.get("s_kind")
        sh = pick_map.get("s_hours")
        sc = pick_map.get("s_stat")
        sr = pick_map.get("s_rate")
        sn = pick_map.get("s_name")
        interpreted: dict[str, Any] = {
            "_name_after_parse": _jsonable_cell(row.get("_name")),
            "_ym_after_parse": _jsonable_cell(row.get("_ym")),
        }
        if sn and sn in settlement.columns:
            interpreted["name_column_cell"] = _jsonable_cell(row.get(sn))
        if sk and sk in settlement.columns:
            interpreted["row_kind_column_cell"] = _jsonable_cell(row.get(sk))
            interpreted["row_kind_classified"] = _classify_dual_row_kind_loose(row.get(sk))
        if sh and sh in settlement.columns:
            interpreted["hours_column_loose"] = _money_to_float_loose(row.get(sh))
        if sc and sc in settlement.columns:
            interpreted["stat_hours_column_loose"] = _money_to_float_loose(row.get(sc))
        if sr and sr in settlement.columns:
            interpreted["rate_column_loose"] = _money_to_float_loose(row.get(sr))
        nm = str(row.get("_name") or "").strip()
        agg_match: dict[str, Any] | None = None
        if nm and not settlement_std.empty and "name" in settlement_std.columns:
            m = settlement_std[settlement_std["name"].astype(str).str.strip() == nm]
            if not m.empty:
                agg_match = {str(k): _jsonable_cell(v) for k, v in m.iloc[0].items()}
        rows_out.append(
            {
                "data_row_1based": rn,
                "frame_index0": i0,
                "interpreted": interpreted,
                "aggregated_std_row_for__name": agg_match,
                "cells_all_columns": cells,
            }
        )
    return {
        "note": "data_row_1based 为解析后结算表中的行号（首条数据=1），一般不等于 Excel 工作表左侧行号。",
        "settlement_sheet": settlement_sheet,
        "settlement_body_row_count": len(settlement),
        "flyme_dual_row_branch": flyme_dual_branch,
        "picked_columns": pick_clean,
        "requested_data_rows_1based": list(rows_1based),
        "rows": rows_out,
    }


def parse_mapping_excel(content: bytes) -> dict[str, str]:
    df = pd.read_excel(io.BytesIO(content), dtype=str)
    df.columns = [_normalize_col(c) for c in df.columns]
    _require_columns(df, ["工号", "姓名"], sheet="员工工号关联表")

    df = df[["工号", "姓名"]].copy()
    df["工号"] = df["工号"].astype(str).str.strip()
    df["姓名"] = df["姓名"].astype(str).str.strip()
    df = df[(df["姓名"] != "") & (df["工号"] != "")]

    dup = df[df.duplicated(subset=["姓名"], keep=False)]
    if not dup.empty:
        pairs = dup.sort_values(["姓名", "工号"])[["姓名", "工号"]].drop_duplicates().to_dict("records")
        raise ConversionError(f"员工工号关联表存在同名对应多个工号：{pairs}", code="mapping_name_conflict")

    return dict(zip(df["姓名"], df["工号"]))


def _pick_col(df: pd.DataFrame, candidates: tuple[str, ...], *, kind: str, sheet: str) -> str:
    cols = [_normalize_col(c) for c in df.columns]
    colset = set(cols)
    for c in candidates:
        cn = _normalize_col(c)
        if cn in colset:
            return cn
    for c in candidates:
        cn = _normalize_col(c)
        if len(cn) < 2:
            continue
        hits = [col for col in cols if _alias_matches_cell(cn, col)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise ConversionError(
                f"sheet「{sheet}」{kind}列候选「{c}」匹配到多列：{hits}。请在客户规则里补充更精确的列别名。",
                code="ambiguous_column",
            )
    raise ConversionError(
        f"sheet「{sheet}」未找到{kind}列（候选：{list(candidates)}）。实际列：{cols}",
        code="missing_columns",
    )


def _find_sheet_optional(sheet_names: Iterable[str], keywords: tuple[str, ...], *, kind: str) -> str | None:
    if not keywords:
        return None
    return _find_sheet(sheet_names, keywords, kind=kind)
