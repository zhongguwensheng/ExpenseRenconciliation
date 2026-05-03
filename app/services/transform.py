from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from app.client_rules.registry import get_rule
from app.services.calendar_cn import count_workdays_cn
from app.services.errors import ConversionError
from app.services.parser import ParsedInputs


INTERNAL_HEADERS: list[str] = [
    "序号",
    "员工工号##yggh",
    "结算日单价(不含税)##jsrdjbhs1",
    "应出勤工时(天)##ycqgst",
    "结算工时(天)##jsgst1",
    "工时费(不含税)##gsfbhs1",
    "加班工时(天)##jbgst1",
    "加班费(不含税)##jbfbhs1",
    "差旅费(不含税)##clfbhs1",
    "差旅补贴(不含税)##clbtbhs1",
    "采购费(不含税)##cgfbhs1",
    "奖金(不含税)##jjbhs1",
    "其他费用(不含税)##qtfybhs1",
    "其他扣款(不含税)##qtkkbhs1",
    "扣款说明##kksm",
    "费用发生年##fyfsn",
    "费用发生月##fyfsy",
    "订单号(PO)##ddhpo",
    "备注##bz",
]


@dataclass(frozen=True)
class InternalRow:
    values: dict[str, Any]


def _round2(x: float) -> float:
    return float(f"{x:.2f}")


def _ym_to_year_month(ym: tuple[int, int] | None) -> tuple[int, int] | None:
    if ym is None or (isinstance(ym, float) and pd.isna(ym)):  # type: ignore[truthy-bool]
        return None
    return ym


def build_internal_rows(parsed: ParsedInputs) -> list[InternalRow]:
    settlement = parsed.settlement.copy()
    travel = parsed.travel.copy()
    rule = get_rule(parsed.client_id)
    is_dual_hourly = rule.settlement_mode in ("dual_row_hourly", "dual_vertical_pair", "geely_dual_auto")

    _require = {
        "settlement": (
            ["name", "ym", "normal_hours", "overtime_hours", "hourly_price_tax"]
            if is_dual_hourly
            else ["name", "ym", "monthly_price", "actual_days"]
        ),
        "travel": ["name", "ym", "reimburse_total", "subsidy_total"],
    }
    for k, cols in _require.items():
        df = settlement if k == "settlement" else travel
        missing = [c for c in cols if c not in set(df.columns)]
        if missing:
            raise ConversionError(f"解析后的数据缺少标准列：{k} missing={missing}", code="normalized_missing_columns")

    settlement["ym"] = settlement["ym"].apply(_ym_to_year_month)
    travel["ym"] = travel["ym"].apply(_ym_to_year_month)

    settlement = settlement[settlement["name"].notna() & (settlement["name"].astype(str).str.strip() != "")]
    travel = travel[travel["name"].notna() & (travel["name"].astype(str).str.strip() != "")]

    # Build settlement group
    sset = settlement.dropna(subset=["ym"]).copy()
    sset["_key"] = sset["name"].astype(str).str.strip()
    sdup = sset[sset.duplicated(subset=["_key", "ym"], keep=False)]
    if not sdup.empty:
        dup_cols = (
            ["_key", "ym", "normal_hours", "overtime_hours", "hourly_price_tax"]
            if is_dual_hourly
            else ["_key", "ym", "monthly_price", "actual_days"]
        )
        examples = sdup.sort_values(["_key"]).head(20)[dup_cols].to_dict("records")
        raise ConversionError(
            f"结算明细表存在同一人员同一月份的多行记录，无法确定取值规则。示例：{examples}",
            code="settlement_duplicate_rows",
        )

    agg_map: dict[str, tuple[str, str]] = {}
    if is_dual_hourly:
        agg_map.update(
            {
                "normal_hours": ("normal_hours", "max"),
                "overtime_hours": ("overtime_hours", "max"),
                "hourly_price_tax": ("hourly_price_tax", "max"),
            }
        )
        if "other_fee_tax" in set(sset.columns):
            agg_map["other_fee_tax"] = ("other_fee_tax", "max")
    else:
        agg_map.update(
            {
                "monthly_price": ("monthly_price", "max"),
                "actual_days": ("actual_days", "max"),
            }
        )
    if "expected_days" in set(sset.columns):
        agg_map["expected_days"] = ("expected_days", "max")
    if "settlement_days" in set(sset.columns):
        agg_map["settlement_days"] = ("settlement_days", "max")
    if "overtime_total" in set(sset.columns):
        agg_map["overtime_total"] = ("overtime_total", "max")

    sgrp = (
        sset.groupby(["_key", "ym"], as_index=False)
        .agg(**agg_map)
        .rename(columns={"_key": "name"})
    )

    # Travel group (sum)
    tgrp = (
        travel.dropna(subset=["ym"])
        .groupby(["name", "ym"], as_index=False)
        .agg(reimburse_total=("reimburse_total", "sum"), subsidy_total=("subsidy_total", "sum"))
    )

    # Union keys
    keys: set[tuple[str, tuple[int, int]]] = set()
    for r in sgrp.to_dict("records"):
        keys.add((str(r["name"]).strip(), r["ym"]))
    for r in tgrp.to_dict("records"):
        keys.add((str(r["name"]).strip(), r["ym"]))

    rows: list[InternalRow] = []

    # helper maps
    s_map: dict[tuple[str, tuple[int, int]], dict[str, float]] = {}
    for r in sgrp.to_dict("records"):
        base = {
            "expected_days": float(r.get("expected_days") or 0.0),
            "settlement_days": float(r.get("settlement_days") or 0.0),
            "overtime_total": float(r.get("overtime_total") or 0.0),
        }
        if is_dual_hourly:
            base.update(
                {
                    "normal_hours": float(r.get("normal_hours") or 0.0),
                    "overtime_hours": float(r.get("overtime_hours") or 0.0),
                    "hourly_price_tax": float(r.get("hourly_price_tax") or 0.0),
                    "other_fee_tax": float(r.get("other_fee_tax") or 0.0),
                    "monthly_price": 0.0,
                    "actual_days": 0.0,
                }
            )
        else:
            base.update(
                {
                    "monthly_price": float(r.get("monthly_price") or 0.0),
                    "actual_days": float(r.get("actual_days") or 0.0),
                }
            )
        s_map[(str(r["name"]).strip(), r["ym"])] = base

    t_map: dict[tuple[str, tuple[int, int]], dict[str, float]] = {}
    for r in tgrp.to_dict("records"):
        t_map[(str(r["name"]).strip(), r["ym"])] = {
            "reimburse_total": float(r.get("reimburse_total") or 0.0),
            "subsidy_total": float(r.get("subsidy_total") or 0.0),
        }

    for idx, (name, (year, month)) in enumerate(sorted(keys, key=lambda x: (x[1][0], x[1][1], x[0])), start=1):
        empno = parsed.name_to_empno.get(name)
        if not empno:
            # If employee number is missing, exclude this employee entirely.
            continue

        default_s = {
            "monthly_price": 0.0,
            "actual_days": 0.0,
            "expected_days": 0.0,
            "settlement_days": 0.0,
            "overtime_total": 0.0,
            "normal_hours": 0.0,
            "overtime_hours": 0.0,
            "hourly_price_tax": 0.0,
            "other_fee_tax": 0.0,
        }
        s = s_map.get((name, (year, month)), default_s)
        t = t_map.get((name, (year, month)), {"reimburse_total": 0.0, "subsidy_total": 0.0})

        monthly_price = float(s["monthly_price"])
        actual_days = float(s["actual_days"])
        expected_days = float(s.get("expected_days") or 0.0)
        settlement_days_override = float(s.get("settlement_days") or 0.0)
        overtime_total = float(s.get("overtime_total") or 0.0)
        normal_hours = float(s.get("normal_hours") or 0.0)
        overtime_hours = float(s.get("overtime_hours") or 0.0)
        hourly_price_tax = float(s.get("hourly_price_tax") or 0.0)
        other_fee_tax = float(s.get("other_fee_tax") or 0.0)

        # 应出勤：优先使用解析出的 expected_days（例如德赛），否则用日历计算
        workdays = expected_days if expected_days > 0 else count_workdays_cn(year, month)
        if workdays <= 0:
            raise ConversionError(f"计算到的应出勤天数为0：{year}-{month:02d}", code="workdays_zero")

        daily_rate_ex_tax = 0.0
        gs_fee_ex_tax = 0.0
        settlement_days = 0.0
        overtime_fee_ex_tax = 0.0
        overtime_days = 0.0
        other_fee_ex_tax = 0.0

        if is_dual_hourly:
            hourly_ex = hourly_price_tax / 1.06 if rule.monthly_price_includes_vat else hourly_price_tax
            daily_rate_ex_tax = hourly_ex * 8.0
            settlement_days = normal_hours / 8.0
            gs_fee_ex_tax = hourly_ex * normal_hours
            overtime_fee_ex_tax = hourly_ex * overtime_hours
            overtime_days = overtime_hours / 8.0 if overtime_hours else 0.0
            other_fee_ex_tax = (
                other_fee_tax / 1.06 if (other_fee_tax and rule.monthly_price_includes_vat) else other_fee_tax
            )
        else:
            # 结算工时（天）：优先使用解析出的 settlement_days（例如德赛：实际出勤+法定节假日），否则用 actual_days
            settlement_days = settlement_days_override if settlement_days_override > 0 else actual_days
            if monthly_price > 0:
                price_ex_tax = monthly_price / 1.06 if rule.monthly_price_includes_vat else monthly_price
                daily_rate_ex_tax = price_ex_tax / workdays
                gs_fee_ex_tax = daily_rate_ex_tax * settlement_days

            overtime_fee_ex_tax = overtime_total / 1.06 if overtime_total else 0.0
            overtime_days = (overtime_fee_ex_tax / daily_rate_ex_tax) if (daily_rate_ex_tax and overtime_fee_ex_tax) else 0.0

        travel_fee_ex_tax = float(t["reimburse_total"]) / 1.06 if t["reimburse_total"] else 0.0
        travel_subsidy_ex_tax = float(t["subsidy_total"]) / 1.06 if t["subsidy_total"] else 0.0

        values: dict[str, Any] = {h: "" for h in INTERNAL_HEADERS}
        # Re-number after exclusions: use current output row count + 1
        values["序号"] = len(rows) + 1
        values["员工工号##yggh"] = empno
        values["结算日单价(不含税)##jsrdjbhs1"] = _round2(daily_rate_ex_tax) if daily_rate_ex_tax else 0.0
        values["应出勤工时(天)##ycqgst"] = _round2(workdays) if workdays else 0.0
        values["结算工时(天)##jsgst1"] = _round2(settlement_days) if settlement_days else 0.0
        values["工时费(不含税)##gsfbhs1"] = _round2(gs_fee_ex_tax) if gs_fee_ex_tax else 0.0
        values["加班工时(天)##jbgst1"] = _round2(overtime_days) if overtime_days else 0.0
        values["加班费(不含税)##jbfbhs1"] = _round2(overtime_fee_ex_tax) if overtime_fee_ex_tax else 0.0
        values["差旅费(不含税)##clfbhs1"] = _round2(travel_fee_ex_tax) if travel_fee_ex_tax else 0.0
        values["差旅补贴(不含税)##clbtbhs1"] = _round2(travel_subsidy_ex_tax) if travel_subsidy_ex_tax else 0.0
        values["其他费用(不含税)##qtfybhs1"] = _round2(other_fee_ex_tax) if other_fee_ex_tax else 0.0
        values["费用发生年##fyfsn"] = year
        values["费用发生月##fyfsy"] = month
        rows.append(InternalRow(values=values))

    return rows

