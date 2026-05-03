from __future__ import annotations


class ConversionError(Exception):
    def __init__(self, message: str, *, code: str = "conversion_error"):
        super().__init__(message)
        self.code = code

