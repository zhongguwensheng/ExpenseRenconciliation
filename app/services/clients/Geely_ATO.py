"""吉利 ATO（geely_dual_auto / Flyme 等）结算列识别 — 规则见 geely_ato.json。"""

import pandas as pd

from app.client_rules.models import ClientRule
from app.services.errors import ConversionError
from app.services import common as cm


def series_looks_like_flyme_row_kind(ser: pd.Series) -> bool:
    """列是否像「正常/加班」行标签：先关键字，再用宽松分类统计（应对非标文案）。"""
    raw = ser.dropna().astype(str).str.replace(r"\s+", "", regex=False)
    if len(raw) < 2:
        return False
    if raw.str.contains("加班", regex=False).any() and (
        raw.str.contains("正常", regex=False).any()
        or raw.str.contains("工作日", regex=False).any()
        or raw.str.contains("出勤", regex=False).any()
        or raw.str.contains("标准", regex=False).any()
    ):
        return True
    kinds = [cm._classify_dual_row_kind_loose(v) for v in raw.tolist()]
    return kinds.count("overtime") >= 1 and kinds.count("normal") >= 1


def discover_flyme_row_kind_column(
    settlement: pd.DataFrame,
    rule: ClientRule,
    *,
    exclude_norm_cols: set[str],
) -> str | None:
    """在表头符合 row_kind 别名的列中，扫描整列样本，挑出最像「正常/加班」标签的一列。"""
    rk_aliases = rule.columns.settlement.get("row_kind", tuple())
    cols = [cm._normalize_col(c) for c in settlement.columns]
    scored: list[tuple[int, int, str]] = []
    for col in cols:
        if not col or str(col).startswith("__") or col in exclude_norm_cols:
            continue
        if rk_aliases and not any(cm._alias_matches_cell(cm._normalize_col(a), col) for a in rk_aliases):
            continue
        if not series_looks_like_flyme_row_kind(settlement[col]):
            continue
        header_score = 0
        for a in rk_aliases:
            an = cm._normalize_col(a)
            if an and an == col:
                header_score = max(header_score, 200 + len(an))
            elif len(an) >= 2 and an in col:
                header_score = max(header_score, len(an))
        scored.append((-header_score, -len(col), col))
    if not scored:
        return None
    scored.sort()
    return scored[0][2]


def geely_flyme_dual_row_branch(
    settlement: pd.DataFrame,
    rule: ClientRule,
    *,
    settlement_sheet: str,
) -> tuple[bool, str | None, str | None]:
    """
    Flyme 等模板：存在「正常 / 加班」行标签列，工时在单独列（如 184 / 47.5），与「统计工时上下两行」不同。
    先解析工时列再解析类型列并排除同列，避免「工时类型」等合并表头被同时当成工时与类型导致退回垂直两行逻辑、后续人员丢失。
    """
    if "row_kind" not in rule.columns.settlement or "hours" not in rule.columns.settlement:
        return False, None, None
    try:
        sh = cm._pick_col_best_longest(
            settlement, rule.columns.settlement["hours"], kind="结算-工时", sheet=settlement_sheet
        )
    except ConversionError:
        return False, None, None
    shn = cm._normalize_col(sh)
    sk = discover_flyme_row_kind_column(settlement, rule, exclude_norm_cols={shn})
    if sk is None:
        try:
            sk_try = cm._pick_col_excluding(
                settlement,
                rule.columns.settlement["row_kind"],
                exclude_norm_cols={shn},
                kind="结算-行类型",
                sheet=settlement_sheet,
            )
            if series_looks_like_flyme_row_kind(settlement[sk_try]):
                sk = sk_try
        except ConversionError:
            sk = None
    if sk is None:
        try:
            sk0 = cm._pick_col(settlement, rule.columns.settlement["row_kind"], kind="结算-行类型", sheet=settlement_sheet)
        except ConversionError:
            return False, None, None
        try:
            sh0 = cm._pick_col_best_longest(
                settlement, rule.columns.settlement["hours"], kind="结算-工时", sheet=settlement_sheet
            )
        except ConversionError:
            return False, sk0, None
        if sk0 == sh0:
            return False, sk0, None
        if not series_looks_like_flyme_row_kind(settlement[sk0]):
            return False, sk0, None
        return True, sk0, sh0
    return True, sk, sh
