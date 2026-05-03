from __future__ import annotations

import math
import re
from urllib.parse import quote

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response

from app.client_rules.registry import detect_client_id_from_filename, get_display_name
from app.services.errors import ConversionError
from app.services.exporter import export_internal_xlsx
from app.services.parser import parse_all, parse_client_excel
from app.services.transform import build_internal_rows

router = APIRouter()


def _parse_data_rows_1based(s: str | None) -> tuple[int, ...]:
    if not s or not str(s).strip():
        return (31,)
    parts = str(s).replace("，", ",").split(",")
    out: list[int] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        try:
            out.append(int(p))
        except ValueError:
            continue
    return tuple(out) if out else (31,)


def _infer_single_ym(parsed) -> tuple[int, int] | None:
    yms: set[tuple[int, int]] = set()
    for df in (parsed.settlement, parsed.travel):
        if df is None or df.empty or "ym" not in df.columns:
            continue
        for v in df["ym"].tolist():
            if isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], int) and isinstance(v[1], int):
                yms.add((v[0], v[1]))
    if not yms:
        return None
    if len(yms) == 1:
        return next(iter(yms))
    # multiple months: pick earliest for filename
    return sorted(yms)[0]


def _sanitize_out_name(name: str) -> str:
    s = (name or "").strip()
    if not s:
        return ""
    if not s.lower().endswith(".xlsx"):
        s += ".xlsx"
    return s


def _format_ym_cn(ym: object) -> str:
    if ym is None:
        return ""
    if isinstance(ym, tuple) and len(ym) == 2:
        y, m = ym[0], ym[1]
        if isinstance(y, int) and isinstance(m, int):
            return f"{y}年{m}月"
    return str(ym)


def _num_preview(v: object) -> float:
    if v is None:
        return 0.0
    try:
        x = float(v)
        if isinstance(x, float) and math.isnan(x):
            return 0.0
        return x
    except (TypeError, ValueError):
        return 0.0


def _ascii_fallback_filename(name: str) -> str:
    """
    Some clients/browsers may only honor the ASCII `filename=` parameter and ignore `filename*`.
    Provide a stable ASCII fallback derived from the desired name.
    """
    base = (name or "").strip()
    base = re.sub(r"\.xlsx$", "", base, flags=re.IGNORECASE)
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base)
    base = base.strip("._-") or "internal_control"
    return f"{base}.xlsx"


@router.post("/detect-client")
async def detect_client(client_filename: str | None = Form(None)) -> dict:
    """
    Detect client_id using the same server-side rules as conversion.
    Returns:
      - ok: bool
      - client_id, display_name when detected
      - message when not detected / ambiguous
    """
    try:
        cid = detect_client_id_from_filename(client_filename)
        if not cid:
            return {"ok": False, "client_id": None, "display_name": None, "message": "无法自动识别，请手动选择客户项目。"}
        return {"ok": True, "client_id": cid, "display_name": get_display_name(cid) or cid, "message": ""}
    except ConversionError as e:
        # e.g. ambiguous matches
        return {"ok": False, "client_id": None, "display_name": None, "message": str(e)}


@router.post("/debug/settlement-rows")
async def debug_settlement_rows(
    client_file: UploadFile = File(...),
    client_filename: str | None = Form(None),
    client_id: str | None = Form(None),
    data_rows: str | None = Form("31"),
) -> JSONResponse:
    """
    返回解析后结算表中指定「数据行号」（首条数据=1）的单元格快照与聚合结果对照。
    用于对照 Excel 左侧行号时，请减去表头/标题占用的行数。
    """
    try:
        client_content = await client_file.read()
        rows = _parse_data_rows_1based(data_rows)
        _std, _tr, _dy, cid, dbg = parse_client_excel(
            client_content,
            client_filename=client_filename,
            client_id=client_id,
            debug_settlement_data_rows_1based=rows,
        )
        payload = dbg or {"error": "no_debug_payload", "client_id": cid}
        payload["resolved_client_id"] = cid
        return JSONResponse(content=payload)
    except ConversionError as e:
        return JSONResponse(content={"ok": False, "error": str(e), "code": getattr(e, "code", "")}, status_code=400)
    except Exception as e:
        return JSONResponse(content={"ok": False, "error": f"系统错误：{e}"}, status_code=500)


@router.post("/preview/settlement-table")
async def preview_settlement_table(
    client_file: UploadFile = File(...),
    client_filename: str | None = Form(None),
    client_id: str | None = Form(None),
) -> JSONResponse:
    """
    将客户结算表按当前规则完整解析，返回标准化后的全部行（仅姓名、月份、正常工时、加班工时、小时单价含税）。
    """
    try:
        client_content = await client_file.read()
        std, _tr, _dy, cid, dbg = parse_client_excel(
            client_content,
            client_filename=client_filename,
            client_id=client_id,
            debug_settlement_data_rows_1based=(),
        )
        rows: list[dict[str, object]] = []
        for _, r in std.iterrows():
            rows.append(
                {
                    "name": str(r.get("name") or "").strip(),
                    "month": _format_ym_cn(r.get("ym")),
                    "normal_hours": _num_preview(r.get("normal_hours")),
                    "overtime_hours": _num_preview(r.get("overtime_hours")),
                    "hourly_price_tax": _num_preview(r.get("hourly_price_tax")),
                }
            )
        return JSONResponse(
            content={
                "ok": True,
                "client_id": cid,
                "row_count": len(rows),
                "rows": rows,
                "parse_debug": dbg,
            }
        )
    except ConversionError as e:
        return JSONResponse(content={"ok": False, "error": str(e), "code": getattr(e, "code", "")}, status_code=400)
    except Exception as e:
        return JSONResponse(content={"ok": False, "error": f"系统错误：{e}"}, status_code=500)


@router.post("/convert")
async def convert(
    client_file: UploadFile = File(...),
    mapping_file: UploadFile = File(...),
    client_filename: str | None = Form(None),
    mapping_filename: str | None = Form(None),
    client_id: str | None = Form(None),
    out_filename: str | None = Form(None),
) -> Response:
    try:
        client_content = await client_file.read()
        mapping_content = await mapping_file.read()

        parsed = parse_all(
            client_content,
            mapping_content,
            client_filename=client_filename,
            client_id=client_id,
        )
        rows = build_internal_rows(parsed)
        out_bytes = export_internal_xlsx(rows)

        custom = _sanitize_out_name(out_filename or "")
        if custom:
            out_name = custom
        else:
            ym = _infer_single_ym(parsed)
            display = get_display_name(parsed.client_id) or parsed.client_id
            if ym:
                out_name = f"{display}{ym[0]}年{ym[1]}月结算内控表.xlsx"
            else:
                out_name = f"{display}结算内控表.xlsx"

        out_name_encoded = quote(out_name, safe="")
        out_name_ascii = _ascii_fallback_filename(out_name)
        headers = {
            # Starlette encodes headers as latin-1; keep header ASCII-only.
            # Provide both an ASCII fallback filename and RFC5987 encoded filename*.
            "Content-Disposition": f"attachment; filename=\"{out_name_ascii}\"; filename*=UTF-8''{out_name_encoded}",
            # Frontend can always prefer this for the download attribute.
            "X-Output-Filename": out_name_encoded,
        }
        return Response(
            content=out_bytes,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=headers,
        )
    except ConversionError as e:
        return Response(
            content=str(e),
            media_type="text/plain; charset=utf-8",
            status_code=400,
        )
    except Exception as e:
        return Response(
            content=f"系统错误：{e}",
            media_type="text/plain; charset=utf-8",
            status_code=500,
        )
