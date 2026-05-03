from __future__ import annotations

import io
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.exporter import export_internal_xlsx
from app.services.parser import parse_all
from app.services.transform import build_internal_rows


def make_client_excel_geely_ito() -> bytes:
    settlement = pd.DataFrame(
        [
            {"姓名": "张三", "月结算价": 10600, "月度": "4月", "实际出勤天数": 20},
            {"姓名": "张三", "月结算价": 10600, "月度": "5月", "实际出勤天数": 21},
        ]
    )
    travel = pd.DataFrame(
        [
            {"月份": "4月", "出差人": "张三", "报销合计": "￥1,060.00", "补贴金额": "￥106.00"},
            {"月份": "4月", "出差人": "张三", "报销合计": "￥0.00", "补贴金额": "￥53.00"},
        ]
    )

    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as w:
        settlement.to_excel(w, sheet_name="客户结算单(示例)", index=False)
        travel.to_excel(w, sheet_name="差旅明细-示例", index=False)
    return bio.getvalue()


def make_client_excel_demo_alt() -> bytes:
    settlement = pd.DataFrame(
        [
            {"员工姓名": "张三", "月结算金额": 10600, "结算月份": "4月", "出勤天数": 20},
            {"员工姓名": "张三", "月结算金额": 10600, "结算月份": "5月", "出勤天数": 21},
        ]
    )
    travel = pd.DataFrame(
        [
            {"月份": "4月", "姓名": "张三", "报销金额": "￥1,060.00", "补贴": "￥106.00"},
            {"月份": "4月", "姓名": "张三", "报销金额": "￥0.00", "补贴": "￥53.00"},
        ]
    )

    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as w:
        settlement.to_excel(w, sheet_name="结算-示例客户B", index=False)
        travel.to_excel(w, sheet_name="出差-示例客户B", index=False)
    return bio.getvalue()


def make_client_excel_geely_ato() -> bytes:
    # 吉利 ATO：每人两行，统计工时列上行正常、下行加班；单价等纵向合并（下行可为空）
    settlement = pd.DataFrame(
        [
            {"姓名": "张三", "月度": "4月", "统计工时": 160, "单价": 106, "其他费用": 0},
            {"姓名": "", "月度": "4月", "统计工时": 8, "单价": None, "其他费用": None},
        ]
    )
    travel = pd.DataFrame(
        [
            {"月份": "4月", "出差人": "张三", "报销合计": "￥106.00", "补贴金额": "￥0.00"},
        ]
    )
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as w:
        settlement.to_excel(w, sheet_name="客户结算单ATO", index=False)
        travel.to_excel(w, sheet_name="差旅明细-示例", index=False)
    return bio.getvalue()


def make_client_excel_geely_ato_two_month_blocks() -> bytes:
    """Two header blocks in one sheet; title contains 2025年9&10月 for segment month mapping."""
    rows = [
        ["项目2025年9&10月外包结算", "", "", "", "", ""],
        ["姓名", "月度", "统计工时", "单价", "其他费用", "金额"],
        ["张三", "9月", 72, 106, 0, 0],
        ["", "", 4, None, None, None],
        ["", "", "", "", "", ""],
        ["姓名", "月度", "统计工时", "单价", "其他费用", "金额"],
        ["张三", "10月", 80, 106, 0, 0],
        ["", "", 4, None, None, None],
    ]
    df = pd.DataFrame(rows)
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="结算汇总9-10", header=False, index=False)
    return bio.getvalue()


def make_client_excel_geely_ato_merged_multiline_header() -> bytes:
    """Simulate title rows + two-row merged-style header, then vertical-pair hourly data."""
    rows = [
        ["产品开发部门FlymeAuto主线专项项目租赁人员汇总表", "", "", "", "", ""],
        ["", "", "", "", "", ""],
        ["基本信息", "", "考勤", "", "费用", ""],
        ["姓名", "月度", "统计工时", "单价", "其他费用", "金额"],
        ["张三", "4月", 160, 106, 0, 0],
        ["", "", 8, None, None, None],
    ]
    df = pd.DataFrame(rows)
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="Flyme结算汇总", header=False, index=False)
    return bio.getvalue()


def make_client_excel_desay() -> bytes:
    # Simulate headers that include line breaks as in customer sheet.
    settlement = pd.DataFrame(
        [
            {
                "姓名": "张三",
                "月服务费\n（不含税）": "24,310.00",
                "标准出勤\n（天数）": 21,
                "实际出勤\n（天数）": 20.84,
                "当月法定节假日天数": 1,
                "加班费汇总\n（含税6%）": "2660.17",
                "差旅费\n（含税6%）": "0.00",
                "差旅补贴\n（含税6%）": "0.00",
            }
        ]
    )

    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as w:
        settlement.to_excel(w, sheet_name="诚迈科技股份有限公司外包工作人员-2026年1月对账单", index=False)
    return bio.getvalue()

def make_client_excel_geely_flyme_rowkind_normal_ot() -> bytes:
    """Flyme 类：「类型」列 正常/加班 + 「考勤工时」列各行工时（合并姓名空第二行）。"""
    settlement = pd.DataFrame(
        [
            {
                "姓名": "刘飞",
                "月度": "10月",
                "类型": "正常",
                "考勤工时": 184.0,
                "单价（元/人/小时）": 175.5,
            },
            {
                "姓名": "",
                "月度": "10月",
                "类型": "加班",
                "考勤工时": 47.5,
                "单价（元/人/小时）": None,
            },
        ]
    )
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as w:
        settlement.to_excel(w, sheet_name="客户结算单ATO", index=False)
    return bio.getvalue()


def make_client_excel_geely_flyme_empty_ot_row_kind() -> bytes:
    """合并格导致加班行「类型」为空时，仍应读出加班工时（与 Flyme 宽表常见情况一致）。"""
    settlement = pd.DataFrame(
        [
            {
                "姓名": "刘飞",
                "月度": "9月",
                "类型": "正常",
                "考勤工时": 184.0,
                "单价（元/人/小时）": 175.5,
            },
            {
                "姓名": "",
                "月度": "9月",
                "类型": "",
                "考勤工时": 47.5,
                "单价（元/人/小时）": None,
            },
            {
                "姓名": "杨毅坤",
                "月度": "9月",
                "类型": "正常",
                "考勤工时": 176.0,
                "单价（元/人/小时）": 165.0,
            },
            {
                "姓名": "",
                "月度": "9月",
                "类型": "",
                "考勤工时": 34.5,
                "单价（元/人/小时）": None,
            },
            {
                "姓名": "",
                "月度": "",
                "类型": "出勤总工时数",
                "考勤工时": 9999.0,
                "单价（元/人/小时）": None,
            },
            {
                "姓名": "",
                "月度": "月度金额合计（大写）",
                "类型": "",
                "考勤工时": None,
                "单价（元/人/小时）": None,
            },
        ]
    )
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as w:
        settlement.to_excel(w, sheet_name="客户结算单ATO", index=False)
    return bio.getvalue()


def make_client_excel_geely_ato_ot_row_serial_in_name_col() -> bytes:
    """加班行「姓名」格为纯序号（合并错位常见）：仍应并入上一人，加班工时不可丢。"""
    settlement = pd.DataFrame(
        [
            {"姓名": "张三", "月度": "4月", "统计工时": 160, "单价": 106, "其他费用": 0},
            {"姓名": "1", "月度": "4月", "统计工时": 8, "单价": None, "其他费用": None},
        ]
    )
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as w:
        settlement.to_excel(w, sheet_name="客户结算单ATO", index=False)
    return bio.getvalue()


def make_client_excel_geely_ato_seal_footer_row() -> bytes:
    """表尾「盖章」等非数字：应跳过，不报错。"""
    settlement = pd.DataFrame(
        [
            {"姓名": "张三", "月度": "4月", "统计工时": 160, "单价": 106, "其他费用": 0},
            {"姓名": "", "月度": "4月", "统计工时": 8, "单价": None, "其他费用": None},
            {"姓名": "", "月度": "4月", "统计工时": "盖章", "单价": None, "其他费用": None},
            {"姓名": "盖章", "月度": "4月", "统计工时": "", "单价": None, "其他费用": None},
        ]
    )
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as w:
        settlement.to_excel(w, sheet_name="客户结算单ATO", index=False)
    return bio.getvalue()


def make_client_excel_geely_ato_blank_between_nh_ot() -> bytes:
    """首行正常工时与加班行之间夹纯空行时，加班仍应计入（旧逻辑只接一行后续会丢加班）。"""
    settlement = pd.DataFrame(
        [
            {"姓名": "张三", "月度": "4月", "统计工时": 160, "单价": 106, "其他费用": 0},
            {"姓名": "", "月度": "4月", "统计工时": "", "单价": None, "其他费用": None},
            {"姓名": "", "月度": "4月", "统计工时": 8, "单价": None, "其他费用": None},
        ]
    )
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as w:
        settlement.to_excel(w, sheet_name="客户结算单ATO", index=False)
    return bio.getvalue()


def make_client_excel_geely_ato_spacer_between_people() -> bytes:
    """姓名 ffill 会把分隔空行算进上一人；切段后应仍为两人各两行。"""
    settlement = pd.DataFrame(
        [
            {"姓名": "张三", "月度": "4月", "统计工时": 160, "单价": 106, "其他费用": 0},
            {"姓名": "", "月度": "4月", "统计工时": 8, "单价": None, "其他费用": None},
            {"姓名": "", "月度": "4月", "统计工时": "", "单价": None, "其他费用": None},
            {"姓名": "李四", "月度": "4月", "统计工时": 40, "单价": 100, "其他费用": 0},
            {"姓名": "", "月度": "4月", "统计工时": 2, "单价": None, "其他费用": None},
        ]
    )
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as w:
        settlement.to_excel(w, sheet_name="客户结算单ATO", index=False)
    return bio.getvalue()


def make_mapping_excel() -> bytes:
    df = pd.DataFrame([{"工号": "E001", "姓名": "张三"}])
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="Sheet1", index=False)
    return bio.getvalue()


def make_mapping_excel_two_people() -> bytes:
    df = pd.DataFrame(
        [
            {"工号": "E001", "姓名": "张三"},
            {"工号": "E002", "姓名": "李四"},
        ]
    )
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="Sheet1", index=False)
    return bio.getvalue()


if __name__ == "__main__":
    mapping = make_mapping_excel()

    client1 = make_client_excel_geely_ito()
    parsed1 = parse_all(client1, mapping, client_filename="吉利ITO_客户结算单_2025年.xlsx", client_id="auto")
    rows1 = build_internal_rows(parsed1)
    out1 = export_internal_xlsx(rows1)
    assert out1[:2] == b"PK"

    client2 = make_client_excel_demo_alt()
    parsed2 = parse_all(client2, mapping, client_filename="DEMO_ALT_客户结算单_2025年.xlsx", client_id="auto")
    rows2 = build_internal_rows(parsed2)
    out2 = export_internal_xlsx(rows2)
    assert out2[:2] == b"PK"

    client3 = make_client_excel_desay()
    parsed3 = parse_all(client3, mapping, client_filename="南京德赛-城迈开发-2026年1月对账单.xlsx", client_id="auto")
    rows3 = build_internal_rows(parsed3)
    out3 = export_internal_xlsx(rows3)
    assert out3[:2] == b"PK"
    assert len(rows3) == 1
    # basic sanity checks for custom formulas
    v = rows3[0].values
    assert float(v.get("应出勤工时(天)##ycqgst") or 0) > 0
    assert float(v.get("结算日单价(不含税)##jsrdjbhs1") or 0) > 0
    assert float(v.get("加班费(不含税)##jbfbhs1") or 0) > 0
    assert float(v.get("加班工时(天)##jbgst1") or 0) > 0

    client4 = make_client_excel_geely_ato()
    parsed4 = parse_all(client4, mapping, client_filename="吉利ATO_客户结算单_2025年.xlsx", client_id="auto")
    assert parsed4.client_id == "geely_ato"
    rows4 = build_internal_rows(parsed4)
    out4 = export_internal_xlsx(rows4)
    assert out4[:2] == b"PK"
    assert len(rows4) == 1
    v4 = rows4[0].values
    hourly_tax = 106.0
    nh, oh = 160.0, 8.0
    expect_fee_ex = (nh + oh) * hourly_tax / 1.06
    got_fee_ex = float(v4.get("工时费(不含税)##gsfbhs1") or 0) + float(v4.get("加班费(不含税)##jbfbhs1") or 0)
    assert abs(got_fee_ex - expect_fee_ex) < 0.05
    assert abs(float(v4.get("结算工时(天)##jsgst1") or 0) - nh / 8.0) < 0.01
    assert abs(float(v4.get("加班工时(天)##jbgst1") or 0) - oh / 8.0) < 0.01
    assert abs(float(v4.get("结算日单价(不含税)##jsrdjbhs1") or 0) - (hourly_tax / 1.06 * 8.0)) < 0.01

    client5 = make_client_excel_geely_ato_merged_multiline_header()
    parsed5 = parse_all(client5, mapping, client_filename="吉利ATO_mergedheader_2025年.xlsx", client_id="geely_ato")
    rows5 = build_internal_rows(parsed5)
    assert len(rows5) == 1
    assert export_internal_xlsx(rows5)[:2] == b"PK"

    client6 = make_client_excel_geely_ato_two_month_blocks()
    parsed6 = parse_all(client6, mapping, client_filename="吉利ATO_9-10_2025年.xlsx", client_id="geely_ato")
    rows6 = build_internal_rows(parsed6)
    assert len(rows6) == 2
    assert export_internal_xlsx(rows6)[:2] == b"PK"

    mapping2 = make_mapping_excel_two_people()
    client7 = make_client_excel_geely_ato_spacer_between_people()
    parsed7 = parse_all(client7, mapping2, client_filename="吉利ATO_spacer_2025年.xlsx", client_id="geely_ato")
    assert len(parsed7.settlement) == 2
    rows7 = build_internal_rows(parsed7)
    assert len(rows7) == 2

    client8 = make_client_excel_geely_ato_blank_between_nh_ot()
    parsed8 = parse_all(client8, mapping, client_filename="吉利ATO_nh_blank_ot_2025年.xlsx", client_id="geely_ato")
    assert len(parsed8.settlement) == 1
    assert abs(float(parsed8.settlement.iloc[0]["overtime_hours"]) - 8.0) < 0.01

    client9 = make_client_excel_geely_ato_seal_footer_row()
    parsed9 = parse_all(client9, mapping, client_filename="吉利ATO_seal_2025年.xlsx", client_id="geely_ato")
    assert len(parsed9.settlement) == 1

    client10 = make_client_excel_geely_ato_ot_row_serial_in_name_col()
    parsed10 = parse_all(client10, mapping, client_filename="吉利ATO_serialname_2025年.xlsx", client_id="geely_ato")
    assert len(parsed10.settlement) == 1
    assert abs(float(parsed10.settlement.iloc[0]["overtime_hours"]) - 8.0) < 0.01

    mapping_lf = pd.DataFrame([{"工号": "E099", "姓名": "刘飞"}])
    bio_m = io.BytesIO()
    with pd.ExcelWriter(bio_m, engine="openpyxl") as w:
        mapping_lf.to_excel(w, sheet_name="Sheet1", index=False)
    mapping_lf_bytes = bio_m.getvalue()

    client11 = make_client_excel_geely_flyme_rowkind_normal_ot()
    parsed11 = parse_all(client11, mapping_lf_bytes, client_filename="吉利ATO_flyme_2025年.xlsx", client_id="geely_ato")
    assert len(parsed11.settlement) == 1
    r11 = parsed11.settlement.iloc[0]
    assert abs(float(r11["normal_hours"]) - 184.0) < 0.01
    assert abs(float(r11["overtime_hours"]) - 47.5) < 0.01

    mapping_3 = pd.DataFrame(
        [
            {"工号": "E099", "姓名": "刘飞"},
            {"工号": "E100", "姓名": "杨毅坤"},
        ]
    )
    bio_m3 = io.BytesIO()
    with pd.ExcelWriter(bio_m3, engine="openpyxl") as w:
        mapping_3.to_excel(w, sheet_name="Sheet1", index=False)
    mapping_3_bytes = bio_m3.getvalue()
    client12 = make_client_excel_geely_flyme_empty_ot_row_kind()
    parsed12 = parse_all(client12, mapping_3_bytes, client_filename="吉利ATO_flyme_2025年9月.xlsx", client_id="geely_ato")
    assert len(parsed12.settlement) == 2
    by_name = {str(r["name"]).strip(): r for _, r in parsed12.settlement.iterrows()}
    assert abs(float(by_name["刘飞"]["normal_hours"]) - 184.0) < 0.01
    assert abs(float(by_name["刘飞"]["overtime_hours"]) - 47.5) < 0.01
    assert abs(float(by_name["杨毅坤"]["normal_hours"]) - 176.0) < 0.01
    assert abs(float(by_name["杨毅坤"]["overtime_hours"]) - 34.5) < 0.01

    print(
        "ok:",
        f"geely_ito rows={len(rows1)} bytes={len(out1)}",
        f"demo_alt rows={len(rows2)} bytes={len(out2)}",
        f"desay rows={len(rows3)} bytes={len(out3)}",
        f"geely_ato rows={len(rows4)} bytes={len(out4)}",
        f"geely_ato_mergedhdr rows={len(rows5)}",
        f"geely_ato_2mo rows={len(rows6)}",
        f"geely_ato_spacer rows={len(rows7)}",
        f"geely_ato_nhblankot ot={parsed8.settlement.iloc[0]['overtime_hours']}",
        f"geely_ato_seal rows={len(parsed9.settlement)}",
        f"geely_ato_serialname ot={parsed10.settlement.iloc[0]['overtime_hours']}",
        f"geely_flyme nh={r11['normal_hours']} oh={r11['overtime_hours']}",
        f"geely_flyme_empty_ot_kind rows={len(parsed12.settlement)}",
    )

