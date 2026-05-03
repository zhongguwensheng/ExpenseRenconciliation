from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.client_rules.models import ClientRule, ColumnAliases, SheetKeywords
from app.services.errors import ConversionError


_RULES_DIR = Path(__file__).resolve().parent / "rules"


def _load_rule(path: Path) -> ClientRule:
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))

    def tup(x: Any) -> tuple[str, ...]:
        if x is None:
            return tuple()
        if isinstance(x, (list, tuple)):
            return tuple(str(i) for i in x)
        raise TypeError(f"Expected list/tuple, got {type(x)}")

    client_id = str(raw["client_id"]).strip()
    display_name = str(raw.get("display_name") or client_id).strip()
    filename_patterns = tup(raw.get("filename_patterns") or [])

    sheets_raw = raw.get("sheets") or {}
    sheets = SheetKeywords(
        settlement=tup(sheets_raw.get("settlement") or []),
        travel=tup(sheets_raw.get("travel") or []),
    )

    cols_raw = raw.get("columns") or {}
    settlement_cols = {str(k): tup(v) for k, v in (cols_raw.get("settlement") or {}).items()}
    travel_cols = {str(k): tup(v) for k, v in (cols_raw.get("travel") or {}).items()}
    columns = ColumnAliases(settlement=settlement_cols, travel=travel_cols)

    sm = str(raw.get("settlement_mode") or "").strip() or None

    hsr_raw = raw.get("header_scan_rows")
    header_scan_rows: int | None = None
    if hsr_raw is not None and str(hsr_raw).strip() != "":
        try:
            header_scan_rows = max(1, int(hsr_raw))
        except (TypeError, ValueError):
            header_scan_rows = None

    return ClientRule(
        client_id=client_id,
        display_name=display_name,
        filename_patterns=filename_patterns,
        sheets=sheets,
        columns=columns,
        ym_from_filename=bool(raw.get("ym_from_filename") or False),
        monthly_price_includes_vat=bool(raw.get("monthly_price_includes_vat", True)),
        settlement_mode=sm,
        header_scan_rows=header_scan_rows,
    )


def load_all_rules() -> list[ClientRule]:
    if not _RULES_DIR.exists():
        return []
    rules: list[ClientRule] = []
    for p in sorted(_RULES_DIR.glob("*.json")):
        rules.append(_load_rule(p))
    return rules


def list_clients() -> list[dict[str, str]]:
    return [{"client_id": r.client_id, "display_name": r.display_name} for r in load_all_rules()]


def get_display_name(client_id: str) -> str | None:
    for r in load_all_rules():
        if r.client_id == client_id:
            return r.display_name
    return None


def get_rule(client_id: str) -> ClientRule:
    client_id = (client_id or "").strip()
    for r in load_all_rules():
        if r.client_id == client_id:
            return r
    raise ConversionError(f"未知客户项目：{client_id}", code="unknown_client")


def detect_client_id_from_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    s = str(filename)
    matches: list[str] = []
    for r in load_all_rules():
        for pat in r.filename_patterns:
            if not pat:
                continue
            if re.search(pat, s, flags=re.IGNORECASE):
                matches.append(r.client_id)
                break
    if not matches:
        return None
    if len(matches) > 1:
        raise ConversionError(f"文件名命中多个客户规则：{matches}。请手动选择客户项目。", code="client_ambiguous")
    return matches[0]


def export_rules_debug() -> list[dict[str, Any]]:
    # Useful for debugging / future API usage.
    return [asdict(r) for r in load_all_rules()]

