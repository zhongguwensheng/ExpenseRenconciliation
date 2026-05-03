---
description: Rules for Excel parsing and transformation pipeline (headers, mapping, month split).
alwaysApply: false
---

## Excel parsing

- Support multi-row headers when customer sheets include grouped header rows.
- Sheet detection should rely on stable keywords and return clear error messages when ambiguous.

## Mapping

- Employee mapping must be deterministic. If name -> multiple empno, fail fast with a clear conflict list.
- If a name is missing empno and product decision is to exclude it, surface a warning (preferred) or document the behavior.

## Month split

- Primary key is `(name, year, month)`; travel aggregates by `(name, year, month)` with sums.
- Avoid silently merging different months or different people.

