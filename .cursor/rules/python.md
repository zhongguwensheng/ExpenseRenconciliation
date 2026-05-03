---
description: Python conventions for FastAPI + pandas/openpyxl project.
alwaysApply: false
---

## Style

- Prefer type hints for public functions.
- Keep modules small and cohesive; avoid giant files.

## Error handling

- Use user-friendly errors for expected data issues (missing columns/sheets, parsing).
- Avoid leaking sensitive data in error messages (file contents, full customer data).

## Numeric accuracy

- For financial amounts, prefer deterministic rounding; if strict reconciliation is required, use `Decimal` end-to-end.

