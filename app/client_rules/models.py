from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SheetKeywords:
    settlement: tuple[str, ...]
    travel: tuple[str, ...]


@dataclass(frozen=True)
class ColumnAliases:
    # Standard keys:
    # - settlement: name, ym, monthly_price, actual_days
    # - travel: name, ym, reimburse_total, subsidy_total
    settlement: dict[str, tuple[str, ...]]
    travel: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class ClientRule:
    client_id: str
    display_name: str
    filename_patterns: tuple[str, ...]
    sheets: SheetKeywords
    columns: ColumnAliases
    # If true, use YYYY-MM parsed from filename for all rows (no ym column required)
    ym_from_filename: bool = False
    # If true, settlement monthly_price is VAT-included (needs /1.06 to get ex-tax)
    monthly_price_includes_vat: bool = True
    # "dual_row_hourly": 费用类型列区分正常/加班，每行一条工时
    # "dual_vertical_pair": 统计工时列两行（上正常下加班），单价等纵向合并
    # "geely_dual_auto": 吉利 ATO 自动识别 Flyme 类「正常/加班」行模板 或 垂直两行统计工时模板
    settlement_mode: str | None = None
    # Max rows to scan when locating the real header row (templates with title rows above columns).
    header_scan_rows: int | None = None

