"""Excel output writer (spec §16 output formats).

:class:`ExcelWriter` writes a standardized DataFrame to a ``.xlsx``
workbook: one sheet, header row frozen, autofilter on, column widths
sized to the content (capped so long text does not blow the layout out).

Unlike :class:`~src.writers.csv_writer.CSVWriter`, this writer does
**not** upsert — it rewrites the sheet from the DataFrame it is given.
The pipeline upserts into CSV first and then regenerates the workbook
from the same DataFrame, so the workbook is always a snapshot of the
latest run's output.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from src.utils.logger import get_logger

logger = get_logger("jira_pipeline.writers.excel")

_MAX_WIDTH = 60
_MIN_WIDTH = 10
_DEFAULT_SHEET = "jira_issues"


class ExcelWriter:
    """Write a DataFrame to a ``.xlsx`` workbook."""

    def write(
        self,
        df: pd.DataFrame,
        path: str | Path,
        sheet_name: str = _DEFAULT_SHEET,
    ) -> dict[str, Any]:
        """Write ``df`` to ``path`` (replacing the sheet) and return counts.

        Returns ``{"written": <rows>, "sheet": <sheet_name>, "path": <str>}``.
        Raises :class:`ImportError` with a clear message when openpyxl
        is not installed.
        """
        try:
            import openpyxl  # noqa: F401  (imported here so the rest of the
            # framework never depends on openpyxl being present)
            from openpyxl.utils import get_column_letter
        except ImportError as exc:  # pragma: no cover - exercised by hand
            raise ImportError(
                "openpyxl is required for Excel output. "
                "Install it with: pip install openpyxl"
            ) from exc

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        sheet = (sheet_name or _DEFAULT_SHEET)[:31]  # Excel sheet-name limit

        # Excel cannot store timezone-aware datetimes: convert them to
        # naive UTC before writing (the values are already UTC).
        df = df.copy()
        for column in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[column]):
                series = df[column]
                if getattr(series.dt, "tz", None) is not None:
                    df[column] = series.dt.tz_convert(None)

        with pd.ExcelWriter(path, engine="openpyxl", mode="w") as writer:
            df.to_excel(writer, sheet_name=sheet, index=False)
            worksheet = writer.sheets[sheet]
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions
            for index, column in enumerate(df.columns, start=1):
                values = [str(column)] + [
                    str(value) for value in df[column].head(50).tolist()
                ]
                width = min(_MAX_WIDTH, max(_MIN_WIDTH, max(len(v) for v in values) + 2))
                worksheet.column_dimensions[get_column_letter(index)].width = width

        logger.info("Excel written: %d rows to %s (sheet %s)", len(df), path, sheet)
        return {"written": len(df), "sheet": sheet, "path": str(path)}
