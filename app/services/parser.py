from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import pandas as pd

from app.client_rules.models import ClientRule
from app.client_rules.registry import detect_client_id_from_filename, get_rule, list_clients
from app.services import common as cm
from app.services.clients import Geely_ATO, Geely_ITO
from app.services.errors import ConversionError

@dataclass(frozen=True)
class ParsedInputs:
    # Standardized dataframes:
    # settlement: name, ym, monthly_price, actual_days;
    # dual hourly modes: +normal_hours, overtime_hours, hourly_price_tax; dual_vertical_pair may add other_fee_tax
    # travel columns: name(str), ym(tuple[int,int] | None), reimburse_total(float), subsidy_total(float)
    settlement: pd.DataFrame
    travel: pd.DataFrame
    name_to_empno: dict[str, str]
    default_year: int | None
    client_id: str
    parse_debug: dict[str, Any] | None = None

def _resolve_rule(*, client_id: str | None, client_filename: str | None) -> ClientRule:
    # Manual override wins.
    if client_id and client_id.strip() and client_id.strip().lower() != "auto":
        return get_rule(client_id.strip())

    detected = detect_client_id_from_filename(client_filename)
    if detected:
        return get_rule(detected)

    # Backward compatible default: only when client_id is omitted (old clients).
    if client_id is None or str(client_id).strip() == "":
        return get_rule("geely_ito")

    # When user explicitly selects "auto", we must detect; otherwise prompt to select manually.
    opts = [c["client_id"] for c in list_clients()]
    raise ConversionError(
        f"无法从文件名识别客户项目。请手动选择客户项目（可选：{opts}）。",
        code="client_not_detected",
    )


def parse_client_excel(
    content: bytes,
    *,
    client_filename: str | None,
    client_id: str | None,
    debug_settlement_data_rows_1based: tuple[int, ...] = (),
) -> tuple[pd.DataFrame, pd.DataFrame, int | None, str, dict[str, Any] | None]:
    default_year = cm._extract_year_from_filename(client_filename)
    rule = _resolve_rule(client_id=client_id, client_filename=client_filename)
    xls = pd.ExcelFile(io.BytesIO(content))

    # Build required columns per rule. Some clients (e.g., 德赛) take ym from filename
    # and compute days from std_days + holiday_days. ATO: hourly templates use the wide reader.
    is_dual_row = rule.settlement_mode == "dual_row_hourly"
    is_dual_vertical = rule.settlement_mode == "dual_vertical_pair"
    is_geely_dual_auto = rule.settlement_mode == "geely_dual_auto"
    uses_dual_reader = is_dual_row or is_dual_vertical or is_geely_dual_auto
    if is_dual_vertical or is_geely_dual_auto:
        settlement_required = [
            rule.columns.settlement.get("name", tuple()),
            rule.columns.settlement.get("stat_hours", tuple()),
            rule.columns.settlement.get("hourly_rate_tax", tuple()),
        ]
        settlement_required = [g for g in settlement_required if g]
    elif is_dual_row:
        # 表头匹配不要求「月度」列：多个月可在同一 sheet 分块，月份由分区标题或行内列推断
        settlement_required = [
            rule.columns.settlement.get("name", tuple()),
            rule.columns.settlement.get("row_kind", tuple()),
            rule.columns.settlement.get("hours", tuple()),
            rule.columns.settlement.get("hourly_rate_tax", tuple()),
        ]
        settlement_required = [g for g in settlement_required if g]
    else:
        settlement_required = [
            rule.columns.settlement.get("name", tuple()),
            rule.columns.settlement.get("monthly_price", tuple()),
        ]
        if not rule.ym_from_filename:
            settlement_required.append(rule.columns.settlement.get("ym", tuple()))
        if "std_days" in rule.columns.settlement and "holiday_days" in rule.columns.settlement:
            settlement_required.append(rule.columns.settlement.get("std_days", tuple()))
            settlement_required.append(rule.columns.settlement.get("holiday_days", tuple()))
        else:
            settlement_required.append(rule.columns.settlement.get("actual_days", tuple()))

    # Travel required groups (if travel sheet exists)
    travel_required: list[tuple[str, ...]] = [
        rule.columns.travel.get("ym", tuple()),
        rule.columns.travel.get("name", tuple()),
        rule.columns.travel.get("reimburse_total", tuple()),
        rule.columns.travel.get("subsidy_total", tuple()),
    ]

    settlement_sheet = cm._find_sheet_smart(
        xls,
        keywords=rule.sheets.settlement,
        required_col_groups=settlement_required,
        kind="结算",
    )
    travel_sheet = cm._find_sheet_optional_smart(
        xls,
        keywords=rule.sheets.travel,
        required_col_groups=travel_required,
        kind="差旅",
    )

    if rule.header_scan_rows is not None:
        hdr_scan = max(200, int(rule.header_scan_rows))
    elif uses_dual_reader:
        hdr_scan = 8000
    else:
        hdr_scan = 120
    header_depth = 8 if uses_dual_reader else 6
    if uses_dual_reader:
        settlement = cm._read_dual_row_sheet_as_concat_segments(
            xls,
            settlement_sheet,
            settlement_required_header=settlement_required,
            max_scan_rows=hdr_scan,
            max_header_depth=header_depth,
            default_year=default_year,
            client_filename=client_filename,
        )
    else:
        settlement = cm._read_excel_with_header_detection_any(
            xls,
            sheet_name=settlement_sheet,
            required_col_groups=settlement_required,
            max_scan_rows=hdr_scan,
            max_header_depth=header_depth,
        )
    if travel_sheet:
        travel = cm._read_excel_with_header_detection_any(
            xls,
            sheet_name=travel_sheet,
            required_col_groups=travel_required,
            max_scan_rows=hdr_scan,
            max_header_depth=header_depth,
        )
    else:
        travel = pd.DataFrame()

    settlement.columns = [cm._normalize_col(c) for c in settlement.columns]
    settlement = cm._drop_settlement_skip_marker_rows(settlement)
    if rule.client_id == "geely_ito" and not uses_dual_reader:
        Geely_ITO.require_wide_attendance_if_wide_signature(settlement, sheet_name=settlement_sheet)
    if not travel.empty:
        travel.columns = [cm._normalize_col(c) for c in travel.columns]

    if uses_dual_reader and "_ord" not in settlement.columns:
        settlement["_ord"] = pd.RangeIndex(len(settlement))

    s_name = cm._pick_col(settlement, rule.columns.settlement["name"], kind="结算-姓名", sheet=settlement_sheet)
    s_kind: str | None = None
    s_hours: str | None = None
    s_stat: str | None = None
    s_rate: str | None = None
    s_other: str | None = None
    s_monthly_price: str | None = None
    s_actual_days: str | None = None
    flyme_dual_branch = False

    if is_geely_dual_auto:
        s_rate = cm._pick_col(
            settlement, rule.columns.settlement["hourly_rate_tax"], kind="结算-单价", sheet=settlement_sheet
        )
        flyme_dual_branch, s_kind, s_hours = Geely_ATO.geely_flyme_dual_row_branch(
            settlement, rule, settlement_sheet=settlement_sheet
        )
        if flyme_dual_branch:
            s_stat = None
        else:
            s_kind = None
            s_hours = None
            s_stat = cm._pick_col_best_longest(
                settlement, rule.columns.settlement["stat_hours"], kind="结算-统计工时", sheet=settlement_sheet
            )
        if "other_fee_tax" in rule.columns.settlement:
            try:
                s_other = cm._pick_col(
                    settlement,
                    rule.columns.settlement["other_fee_tax"],
                    kind="结算-其他费用",
                    sheet=settlement_sheet,
                )
            except ConversionError:
                s_other = None
    elif is_dual_vertical:
        s_stat = cm._pick_col_best_longest(
            settlement, rule.columns.settlement["stat_hours"], kind="结算-统计工时", sheet=settlement_sheet
        )
        s_rate = cm._pick_col(
            settlement, rule.columns.settlement["hourly_rate_tax"], kind="结算-单价", sheet=settlement_sheet
        )
        if "other_fee_tax" in rule.columns.settlement:
            try:
                s_other = cm._pick_col(
                    settlement,
                    rule.columns.settlement["other_fee_tax"],
                    kind="结算-其他费用",
                    sheet=settlement_sheet,
                )
            except ConversionError:
                s_other = None
    elif is_dual_row:
        s_kind = cm._pick_col(settlement, rule.columns.settlement["row_kind"], kind="结算-行类型", sheet=settlement_sheet)
        s_hours = cm._pick_col_best_longest(
            settlement, rule.columns.settlement["hours"], kind="结算-工时", sheet=settlement_sheet
        )
        s_rate = cm._pick_col(
            settlement, rule.columns.settlement["hourly_rate_tax"], kind="结算-含税小时单价", sheet=settlement_sheet
        )
    else:
        s_monthly_price = cm._pick_col(
            settlement, rule.columns.settlement["monthly_price"], kind="结算-月结算价", sheet=settlement_sheet
        )
        if "actual_days" in rule.columns.settlement:
            try:
                s_actual_days = cm._pick_col(
                    settlement, rule.columns.settlement["actual_days"], kind="结算-实际出勤天数", sheet=settlement_sheet
                )
            except Exception:
                s_actual_days = None

    if not travel.empty:
        t_ym = cm._pick_col(travel, rule.columns.travel["ym"], kind="差旅-月份", sheet=travel_sheet or "差旅")
        t_name = cm._pick_col(travel, rule.columns.travel["name"], kind="差旅-出差人", sheet=travel_sheet or "差旅")
        t_reimburse = cm._pick_col(travel, rule.columns.travel["reimburse_total"], kind="差旅-报销合计", sheet=travel_sheet or "差旅")
        t_subsidy = cm._pick_col(travel, rule.columns.travel["subsidy_total"], kind="差旅-补贴金额", sheet=travel_sheet or "差旅")

    # normalize key fields
    settlement = settlement.copy()
    travel = travel.copy()
    if is_dual_vertical or (is_geely_dual_auto and not flyme_dual_branch):
        # 垂直两行版式不在此做姓名 ffill：聚合按「姓名单元格非空」切段，避免空行误入上一人。
        settlement["_name"] = settlement[s_name].map(cm._raw_person_name_cell)
    elif is_dual_row or (is_geely_dual_auto and flyme_dual_branch):
        sraw = settlement[s_name]
        mask_empty = sraw.isna() | (sraw.astype(str).str.strip() == "") | (
            sraw.astype(str).str.strip().str.lower() == "nan"
        )
        settlement["_name"] = sraw.mask(mask_empty, pd.NA).ffill().astype(str).str.strip()
    else:
        settlement["_name"] = settlement[s_name].astype(str).str.strip()
    if not travel.empty:
        travel["_name"] = travel[t_name].astype(str).str.strip()

    # parse year-month
    s_ym: str | None = None
    if rule.ym_from_filename:
        ym_from_fn = cm._extract_year_month_from_filename(client_filename)
        if ym_from_fn is None:
            ym_from_sheet = cm._detect_ym_from_sheet_cells(xls, settlement_sheet)
            if ym_from_sheet is None:
                raise ConversionError(
                    "该客户项目需要识别年月（例如：2026年1月），但未能从文件名或sheet内容中识别。",
                    code="ym_missing_in_filename",
                )
            ym_from_fn = ym_from_sheet
        settlement["_ym"] = [ym_from_fn] * len(settlement)
        if not travel.empty:
            travel["_ym"] = [ym_from_fn] * len(travel)
    else:
        if is_dual_row or is_dual_vertical or is_geely_dual_auto:
            try:
                s_ym = cm._pick_col(settlement, rule.columns.settlement["ym"], kind="结算-月度", sheet=settlement_sheet)
            except ConversionError:
                s_ym = None
            parsed_list: list[tuple[int, int] | None]
            if s_ym is not None:
                if is_dual_vertical or is_geely_dual_auto:
                    sy = settlement[s_ym].astype(object)
                    sy = sy.where(
                        sy.notna() & (sy.astype(str).str.strip() != "") & (sy.astype(str).str.strip().str.lower() != "nan"),
                        pd.NA,
                    ).ffill()
                    settlement[s_ym] = sy
                parsed_list = [cm._parse_year_month(v, default_year=default_year) for v in settlement[s_ym].tolist()]
            else:
                parsed_list = [None] * len(settlement)
            if "_segment_ym" in settlement.columns:
                seg_list = list(settlement["_segment_ym"].tolist())
                settlement["_ym"] = [
                    p if p is not None else (seg if isinstance(seg, tuple) and len(seg) == 2 else None)
                    for p, seg in zip(parsed_list, seg_list)
                ]
            else:
                settlement["_ym"] = parsed_list
            if any(v is None for v in settlement["_ym"]):
                raise ConversionError(
                    "吉利 ATO 结算表无法确定部分行的年月：请增加「月度/月份」列，或在分区标题中写明「2025年9月」等，"
                    "并保证文件名含年份。",
                    code="ato_ym_unresolved",
                )
            settlement = settlement.drop(columns=["_segment_ym"], errors="ignore")
        else:
            s_ym = cm._pick_col(settlement, rule.columns.settlement["ym"], kind="结算-月度", sheet=settlement_sheet)
            settlement["_ym"] = settlement[s_ym].apply(lambda v: cm._parse_year_month(v, default_year=default_year))
        if not travel.empty:
            travel["_ym"] = travel[t_ym].apply(lambda v: cm._parse_year_month(v, default_year=default_year))

    if (not rule.ym_from_filename) and default_year is None:
        has_short_month = False
        if not (is_dual_row or is_dual_vertical or is_geely_dual_auto):
            has_short_month = (
                settlement["_ym"].isna().any()
                and settlement[s_ym].astype(str).str.contains(r"^\s*\d{1,2}\s*月\s*$", regex=True).any()
            ) or (
                (not travel.empty)
                and travel["_ym"].isna().any()
                and travel[t_ym].astype(str).str.contains(r"^\s*\d{1,2}\s*月\s*$", regex=True).any()
            )
        elif s_ym is not None:
            has_short_month = settlement["_ym"].isna().any() and settlement[s_ym].astype(str).str.contains(
                r"^\s*\d{1,2}\s*月\s*$", regex=True
            ).any()
        if has_short_month:
            raise ConversionError(
                "检测到“4月”这类无年份月份，但文件名中没有年份(例如 2025)。请将客户结算表文件名包含年份后重试。",
                code="year_missing_in_filename",
            )

    if settlement["_ym"].isna().all() and (travel.empty or travel["_ym"].isna().all()):
        raise ConversionError(
            "无法解析月份/年月：请确保文件名包含年份(例如 2025)，且“月度/月份”列为“4月”或“2025-04”等格式。",
            code="year_month_parse_failed",
        )

    if not travel.empty:
        travel["_reimburse_total"] = travel[t_reimburse].apply(cm._money_to_float)
        travel["_subsidy_total"] = travel[t_subsidy].apply(cm._money_to_float)

    if is_geely_dual_auto and flyme_dual_branch:
        assert s_kind is not None and s_hours is not None and s_rate is not None
        settlement_std = cm._aggregate_settlement_dual_row_hourly(
            settlement, s_kind=s_kind, s_hours=s_hours, s_rate=s_rate
        )
    elif is_dual_vertical or (is_geely_dual_auto and not flyme_dual_branch):
        assert s_stat is not None and s_rate is not None
        settlement_std = cm._aggregate_settlement_vertical_merged_pairs(
            settlement, s_name=s_name, s_stat=s_stat, s_rate=s_rate, s_other=s_other
        )
    elif is_dual_row:
        assert s_kind is not None and s_hours is not None and s_rate is not None
        settlement_std = cm._aggregate_settlement_dual_row_hourly(
            settlement, s_kind=s_kind, s_hours=s_hours, s_rate=s_rate
        )
    else:
        assert s_monthly_price is not None
        settlement["_monthly_price"] = settlement[s_monthly_price].apply(cm._money_to_float)
        if s_actual_days:
            settlement["_actual_days"] = settlement[s_actual_days].apply(cm._money_to_float)
        else:
            settlement["_actual_days"] = 0.0

        settlement_std = pd.DataFrame(
            {
                "name": settlement["_name"],
                "ym": settlement["_ym"],
                "monthly_price": settlement["_monthly_price"],
                "actual_days": settlement["_actual_days"],
            }
        )
    if not travel.empty:
        travel_std = pd.DataFrame(
            {
                "name": travel["_name"],
                "ym": travel["_ym"],
                "reimburse_total": travel["_reimburse_total"],
                "subsidy_total": travel["_subsidy_total"],
            }
        )
    else:
        travel_std = pd.DataFrame({"name": [], "ym": [], "reimburse_total": [], "subsidy_total": []})

    # Extra fields from settlement sheet (optional): overtime / travel in same sheet, expected days override.
    if not (is_dual_row or is_dual_vertical or is_geely_dual_auto):
        if "std_days" in rule.columns.settlement and "holiday_days" in rule.columns.settlement:
            try:
                c_std = cm._pick_col(settlement, rule.columns.settlement["std_days"], kind="结算-标准出勤", sheet=settlement_sheet)
                c_hol = cm._pick_col(settlement, rule.columns.settlement["holiday_days"], kind="结算-法定节假日天数", sheet=settlement_sheet)
                hol = settlement[c_hol].apply(cm._money_to_float)
                settlement_std["expected_days"] = settlement[c_std].apply(cm._money_to_float) + hol

                # For clients like 德赛, settlement_days differs from expected_days.
                if "actual_days" in rule.columns.settlement:
                    c_act = cm._pick_col(settlement, rule.columns.settlement["actual_days"], kind="结算-实际出勤", sheet=settlement_sheet)
                    settlement_std["settlement_days"] = settlement[c_act].apply(cm._money_to_float) + hol
            except Exception:
                pass

        if "overtime_total" in rule.columns.settlement:
            try:
                c_ot = cm._pick_col(settlement, rule.columns.settlement["overtime_total"], kind="结算-加班费汇总", sheet=settlement_sheet)
                settlement_std["overtime_total"] = settlement[c_ot].apply(cm._money_to_float)
            except Exception:
                pass

        # If travel columns exist in settlement, fill travel_std from settlement rows.
        if ("travel_fee" in rule.columns.settlement) and ("travel_subsidy" in rule.columns.settlement):
            try:
                c_tf = cm._pick_col(settlement, rule.columns.settlement["travel_fee"], kind="结算-差旅费", sheet=settlement_sheet)
                c_ts = cm._pick_col(settlement, rule.columns.settlement["travel_subsidy"], kind="结算-差旅补贴", sheet=settlement_sheet)
                travel_std = pd.DataFrame(
                    {
                        "name": settlement_std["name"],
                        "ym": settlement_std["ym"],
                        "reimburse_total": settlement[c_tf].apply(cm._money_to_float),
                        "subsidy_total": settlement[c_ts].apply(cm._money_to_float),
                    }
                )
            except Exception:
                pass

    pick_map = {
        "s_name": s_name,
        "s_kind": s_kind,
        "s_hours": s_hours,
        "s_stat": s_stat,
        "s_rate": s_rate,
        "s_other": s_other,
    }
    dbg_base: dict[str, Any] = {
        "settlement_sheet": settlement_sheet,
        "flyme_dual_branch": flyme_dual_branch,
        "settlement_body_row_count": len(settlement),
        "picked_columns": {k: v for k, v in pick_map.items() if v},
    }
    dbg: dict[str, Any] | None
    if debug_settlement_data_rows_1based:
        dbg = cm._build_settlement_parse_debug(
            settlement=settlement,
            settlement_std=settlement_std,
            settlement_sheet=settlement_sheet,
            rows_1based=debug_settlement_data_rows_1based,
            pick_map=pick_map,
            flyme_dual_branch=flyme_dual_branch,
        )
        dbg = {**dbg_base, **dbg}
    else:
        dbg = dbg_base
    return settlement_std, travel_std, default_year, rule.client_id, dbg


def parse_all(
    client_content: bytes,
    mapping_content: bytes,
    *,
    client_filename: str | None,
    client_id: str | None,
    debug_settlement_data_rows_1based: tuple[int, ...] = (),
) -> ParsedInputs:
    name_to_empno = cm.parse_mapping_excel(mapping_content)
    settlement, travel, default_year, resolved_client_id, dbg = parse_client_excel(
        client_content,
        client_filename=client_filename,
        client_id=client_id,
        debug_settlement_data_rows_1based=debug_settlement_data_rows_1based,
    )
    return ParsedInputs(
        settlement=settlement,
        travel=travel,
        name_to_empno=name_to_empno,
        default_year=default_year,
        client_id=resolved_client_id,
        parse_debug=dbg,
    )
