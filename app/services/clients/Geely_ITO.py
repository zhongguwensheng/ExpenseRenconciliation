"""吉利 ITO（默认模板）客户结算 — 规则见 geely_ito.json。

宽表补充：若同时出现姓名/月份/岗位结算单价，则必须存在「月度实际出勤天数」列。
"""

from __future__ import annotations

import pandas as pd

from app.services import common as cm
from app.services.errors import ConversionError

CLIENT_ID = "geely_ito"

_WIDE_SIG = ("姓名", "月份", "岗位结算单价")
_ATTEND_WIDE = "月度实际出勤天数"


def require_wide_attendance_if_wide_signature(settlement: pd.DataFrame, *, sheet_name: str) -> None:
    """Spec §2–§6: wide triplet without 月度实际出勤天数 → explicit error."""
    cols = {cm._normalize_col(c) for c in settlement.columns}
    sig = {cm._normalize_col(x) for x in _WIDE_SIG}
    if not sig.issubset(cols):
        return
    need = cm._normalize_col(_ATTEND_WIDE)
    if need not in cols:
        raise ConversionError(
            f"sheet「{sheet_name}」检测到宽表头（含姓名、月份、岗位结算单价），但缺少列「{_ATTEND_WIDE}」。",
            code="geely_ito_wide_missing_attendance",
        )
