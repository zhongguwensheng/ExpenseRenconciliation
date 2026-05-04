# Geely ITO wide two-row settlement header — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add support for the supplementary ITO settlement layout (two-row merged header + field row with `月份` / `岗位结算单价` / `月度实际出勤天数`) under `geely_ito` without changing legacy single-row template behavior.

**Architecture:** Extend `geely_ito.json` settlement column aliases so `_find_sheet_smart` / `_read_excel_with_header_detection_any` can match both templates. After reading the settlement body and normalizing column names in `parse_client_excel`, run a small guard in `Geely_ITO` that implements spec §2 + §6: if the wide signature (`姓名` + `月份` + `岗位结算单价`) is present, require `月度实际出勤天数` with a dedicated `ConversionError` code. No changes to `transform.py` or `settlement_mode`; travel remains a separate sheet.

**Tech stack:** Python 3.x, pandas, openpyxl (existing); smoke driver `scripts/smoke_test.py` (no pytest in repo).

**Spec:** `docs/superpowers/specs/2026-05-03-geely-ito-wide-settlement-design.md`

---

## File map (what changes where)

| File | Role |
|------|------|
| `app/client_rules/rules/geely_ito.json` | Add **extra aliases only** on existing keys `ym`, `monthly_price`, `actual_days` (legacy strings first, new strings second). |
| `app/services/clients/Geely_ITO.py` | Add `require_wide_attendance_if_wide_signature(df, *, sheet_name: str) -> None` using `app.services.common._normalize_col` + `app.services.errors.ConversionError`. |
| `app/services/parser.py` | After `settlement.columns = [cm._normalize_col(c) for c in settlement.columns]` and `cm._drop_settlement_skip_marker_rows`, if `rule.client_id == "geely_ito"` and not `uses_dual_reader`, call the new guard once. |
| `scripts/smoke_test.py` | Add `make_client_excel_geely_ito_wide_two_row_header()` (minimal 4-column body + group row + header row), assert `parse_all` → `build_internal_rows` → `export_internal_xlsx` for `client_id="geely_ito"`; add negative case expecting `code=="geely_ito_wide_missing_attendance"`. |

---

### Task 1: Failing smoke — wide template happy path

**Files:**

- Modify: `scripts/smoke_test.py` (add factory + `__main__` block assertions + extend final `print`)

- [ ] **Step 1: Add Excel factory for wide two-row header**

Insert below `make_client_excel_geely_ito()` (after line ~36):

```python
def make_client_excel_geely_ito_wide_two_row_header() -> bytes:
    """Two-row header (group row + field names), then one data row — spec 2026-05-03."""
    rows = [
        ["基础信息", "", "", ""],
        ["姓名", "月份", "岗位结算单价", "月度实际出勤天数"],
        ["张三", "2025-10", 31800, 22],
    ]
    df = pd.DataFrame(rows)
    travel = pd.DataFrame(
        [
            {"月份": "10月", "出差人": "张三", "报销合计": "￥100.00", "补贴金额": "￥10.00"},
        ]
    )
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="客户结算单ITO宽表", header=False, index=False)
        travel.to_excel(w, sheet_name="差旅明细-示例", index=False)
    return bio.getvalue()
```

- [ ] **Step 2: Wire smoke assertions (before product code, expect failure)**

In `if __name__ == "__main__":` after `mapping = make_mapping_excel()`, add:

```python
    client_wide = make_client_excel_geely_ito_wide_two_row_header()
    parsed_wide = parse_all(
        client_wide,
        mapping,
        client_filename="吉利ITO_宽表_2025年.xlsx",
        client_id="geely_ito",
    )
    assert parsed_wide.client_id == "geely_ito"
    assert list(parsed_wide.settlement.columns) == ["name", "ym", "monthly_price", "actual_days"]
    assert len(parsed_wide.settlement) == 1
    r = parsed_wide.settlement.iloc[0]
    assert str(r["name"]).strip() == "张三"
    assert r["ym"] == (2025, 10)
    assert abs(float(r["monthly_price"]) - 31800.0) < 0.01
    assert abs(float(r["actual_days"]) - 22.0) < 0.01
    rows_wide = build_internal_rows(parsed_wide)
    out_wide = export_internal_xlsx(rows_wide)
    assert out_wide[:2] == b"PK"
```

- [ ] **Step 3: Run smoke (expect failure)**

Run:

```bash
cd /Users/zhangshukai/Downloads/Project_Develop/ExpenseRenconciliation && .venv/bin/python scripts/smoke_test.py
```

Expected before fixes: `ConversionError` about missing required columns / sheet not found (because `geely_ito.json` does not yet list `月份`, `岗位结算单价`, `月度实际出勤天数` in the required groups).

- [ ] **Step 4: Commit (optional checkpoint)**

```bash
git add scripts/smoke_test.py
git commit -m "test(smoke): add Geely ITO wide header case (expected fail until rules wired)"
```

---

### Task 2: Rule aliases — sheet detection + column pick

**Files:**

- Modify: `app/client_rules/rules/geely_ito.json`

- [ ] **Step 1: Extend settlement aliases (preserve order: legacy first)**

Replace the `columns.settlement` block with:

```json
    "settlement": {
      "name": ["姓名"],
      "ym": ["月度", "月份"],
      "monthly_price": ["月结算价", "岗位结算单价"],
      "actual_days": ["实际出勤天数", "月度实际出勤天数"]
    },
```

Leave `travel`, `sheets`, `client_id`, `display_name`, `filename_patterns` unchanged.

- [ ] **Step 2: Re-run smoke (wide path may pass; negative test not added yet)**

Run:

```bash
.venv/bin/python scripts/smoke_test.py
```

Expected: wide happy path passes; legacy `make_client_excel_geely_ito()` still passes (aliases still match old headers).

- [ ] **Step 3: Commit**

```bash
git add app/client_rules/rules/geely_ito.json
git commit -m "feat(geely_ito): alias wide settlement columns for ym, price, attendance"
```

---

### Task 3: Wide signature guard + negative smoke

**Files:**

- Modify: `app/services/clients/Geely_ITO.py`
- Modify: `app/services/parser.py`
- Modify: `scripts/smoke_test.py`

- [ ] **Step 1: Implement guard in `Geely_ITO.py`**

Replace file body with (keep `CLIENT_ID`, add imports and function):

```python
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
```

- [ ] **Step 2: Call guard from `parser.py`**

Near existing `from app.services.clients import Geely_ATO`, change to:

```python
from app.services.clients import Geely_ATO, Geely_ITO
```

Immediately after:

```python
    settlement.columns = [cm._normalize_col(c) for c in settlement.columns]
    settlement = cm._drop_settlement_skip_marker_rows(settlement)
```

insert:

```python
    if rule.client_id == "geely_ito" and not uses_dual_reader:
        Geely_ITO.require_wide_attendance_if_wide_signature(settlement, sheet_name=settlement_sheet)
```

- [ ] **Step 3: Add negative smoke factory + assertion**

In `scripts/smoke_test.py`, add:

```python
def make_client_excel_geely_ito_wide_missing_attendance() -> bytes:
    # Header must still satisfy rule alias groups (incl. 实际出勤天数), else locate fails before guard.
    rows = [
        ["基础信息", "", "", ""],
        ["姓名", "月份", "岗位结算单价", "实际出勤天数"],
        ["张三", "2025-10", 31800, 22],
    ]
    df = pd.DataFrame(rows)
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="客户结算单ITO宽表缺勤", header=False, index=False)
    return bio.getvalue()
```

In `__main__`, after wide success block, add:

```python
    from app.services.errors import ConversionError

    try:
        parse_all(
            make_client_excel_geely_ito_wide_missing_attendance(),
            mapping,
            client_filename="吉利ITO_宽表缺勤_2025年.xlsx",
            client_id="geely_ito",
        )
    except ConversionError as e:
        assert e.code == "geely_ito_wide_missing_attendance"
    else:
        raise AssertionError("expected ConversionError geely_ito_wide_missing_attendance")
```

- [ ] **Step 4: Run smoke**

```bash
.venv/bin/python scripts/smoke_test.py
```

Expected: PASS; final `print` line should include a token like `geely_ito_wide rows=1` (add manually when editing the print tuple).

- [ ] **Step 5: Commit**

```bash
git add app/services/clients/Geely_ITO.py app/services/parser.py scripts/smoke_test.py
git commit -m "feat(geely_ito): enforce 月度实际出勤天数 when wide signature; smoke coverage"
```

---

### Task 4: Documentation touch-up (optional)

**Files:**

- Modify: `docs/superpowers/specs/2026-05-03-geely-ito-wide-settlement-design.md` (append one line under §9 changelog: "实现：规则别名 + Geely_ITO 校验 + smoke")

- [ ] Single commit message: `docs: note implementation of geely_ito wide header`

---

## Plan self-review (spec coverage)

| Spec section | Task covering it |
|--------------|------------------|
| §2 识别（表结构 A，三列） | JSON aliases make header block match; guard encodes fourth column requirement. |
| §2.1 歧义 | `_pick_col` prefers first alias in tuple; legacy columns `月度` / `月结算价` / `实际出勤天数` remain first — wide sheets use `月份` etc., no overlap in practice. |
| §3 列映射 | Aliases map to same internal `name` / `ym` / `monthly_price` / `actual_days` pipeline. |
| §3.1 `YYYY-MM` | Existing `cm._parse_year_month` path used via `月份` column. |
| §3.2 含税 | No JSON change to `monthly_price_includes_vat`. |
| §4 非目标 | No extra columns read; no new `client_id`. |
| §5 差旅 | No change to travel sheet logic. |
| §6 错误 | `geely_ito_wide_missing_attendance` + message text. |
| §7 测试 | Smoke wide + negative. |

**Placeholder scan:** None intentional.

**Type / name consistency:** `require_wide_attendance_if_wide_signature(settlement, sheet_name=...)` matches parser call; `ConversionError.code` string stable for smoke.

---

## Execution handoff

**Plan complete and saved to** `docs/superpowers/plans/2026-05-03-geely-ito-wide-settlement.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks. **REQUIRED SUB-SKILL:** superpowers:subagent-driven-development.

2. **Inline Execution** — run tasks in this session with checkpoints. **REQUIRED SUB-SKILL:** superpowers:executing-plans.

**Which approach do you want?**
