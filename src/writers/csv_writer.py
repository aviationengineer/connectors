"""CSV output writer (spec §21).

Writes the standardised Jira DataFrame to a CSV file, with append and
upsert (merge on a key column) operations. Upsert is idempotent: running
it twice with the same data leaves the file unchanged.

Change detection
----------------
A row counts as ``updated`` only when its content actually differs from
the stored row. The ``ingestion_timestamp`` column is excluded from the
comparison: it is restamped on every run, so including it would mark
every row as updated on every run. ``skipped`` is the number of existing
rows left untouched, i.e. ``len(existing) - updated``.

Corrupt files
-------------
If the existing CSV cannot be parsed (``ParserError`` / ``EmptyDataError``),
the file is moved aside to ``<filepath>.corrupt-<YYYYMMDD_HHMMSS>`` and the
write starts fresh from the incoming data instead of crashing the pipeline.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from src.transformers.jira_transformer import SCHEMA_COLUMNS
from src.utils.exceptions import CSVWriterError
from src.utils.logger import get_logger
from src.writers.base_writer import DataWriter

logger = get_logger("jira_pipeline.csv_writer")

# Columns restamped on every run even when the issue itself did not
# change; excluded from upsert change detection (see module docstring).
_VOLATILE_COLUMNS = frozenset({"ingestion_timestamp"})


def _is_missing(value: object) -> bool:
    """True for None / NaN / NaT (never raises on list-like values)."""
    if value is None:
        return True
    if isinstance(value, (list, tuple, dict)):
        return False
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _key_str(value: object) -> str:
    """Normalise a key value for comparison (missing keys become "")."""
    return "" if _is_missing(value) else str(value)


def _row_signature(row: pd.Series, columns: list[str]) -> tuple[str, ...]:
    """Comparable content of a row over ``columns`` (missing -> "")."""
    return tuple(
        "" if _is_missing(row[column]) else str(row[column]) for column in columns
    )


class CSVWriter(DataWriter):
    """Write, append to and upsert Jira DataFrames in a CSV file."""

    def __init__(self) -> None:
        self.logger = logger

    # -- public API ---------------------------------------------------------
    def write(self, dataframe: pd.DataFrame, filepath: str | Path) -> int:
        """Overwrite (or create) ``filepath`` with ``dataframe``.

        An empty dataframe still writes the header: the schema header
        when the frame has no columns at all. Returns rows written.
        """
        path = Path(filepath)
        frame = dataframe
        if frame.empty and len(frame.columns) == 0:
            frame = pd.DataFrame(columns=SCHEMA_COLUMNS)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(path, index=False)
        except (OSError, ValueError) as exc:
            raise CSVWriterError(f"Failed to write CSV {path}: {exc}") from exc
        rows = len(frame)
        logger.info("Wrote %d rows to CSV %s", rows, path)
        return rows

    def append(
        self,
        dataframe: pd.DataFrame,
        filepath: str | Path,
        key_column: str = "issue_id",
    ) -> dict:
        """Append only rows whose key is not already in the file.

        If the file does not exist it is created (same as :meth:`write`).
        ``skipped`` counts incoming rows already present; an empty
        incoming frame is a no-op returning the existing row count.
        """
        path = Path(filepath)
        existing = self._read_existing(path)
        existing_rows = len(existing) if existing is not None else 0
        if dataframe.empty:
            return {
                "inserted": 0,
                "updated": 0,
                "skipped": existing_rows,
                "total": existing_rows,
            }
        key = self._resolve_key_column(dataframe, key_column)
        if existing is None:
            rows = self.write(dataframe, path)
            return {"inserted": rows, "updated": 0, "skipped": 0, "total": rows}

        original_columns = list(existing.columns)
        columns = list(original_columns)
        for column in dataframe.columns:
            if column not in columns:
                columns.append(column)
        existing = existing.reindex(columns=columns)
        incoming = (
            dataframe.reindex(columns=columns)
            .drop_duplicates(subset=[key], keep="last")
            .reset_index(drop=True)
        )
        existing_keys = (
            {_key_str(value) for value in existing[key]}
            if key in existing.columns
            else set()
        )
        if key not in existing.columns:
            logger.warning(
                "Key column %r not in existing CSV %s; appending all rows "
                "without deduplication",
                key,
                path,
            )
        new_rows = incoming[
            ~incoming[key].map(_key_str).isin(existing_keys)
        ].reset_index(drop=True)
        skipped = len(incoming) - len(new_rows)
        if len(new_rows) == 0:
            logger.info(
                "CSV append to %s: 0 new rows (%d skipped)", path, skipped
            )
            return {
                "inserted": 0,
                "updated": 0,
                "skipped": skipped,
                "total": existing_rows,
            }
        try:
            if columns == original_columns:
                # Common case: same columns, plain append preserves the file.
                new_rows.to_csv(path, mode="a", header=False, index=False)
            else:
                # New columns arrived: rewrite so old rows stay aligned.
                pd.concat([existing, new_rows], ignore_index=True).to_csv(
                    path, index=False
                )
        except (OSError, ValueError) as exc:
            raise CSVWriterError(f"Failed to append to CSV {path}: {exc}") from exc
        total = existing_rows + len(new_rows)
        logger.info(
            "CSV append to %s: inserted=%d skipped=%d total=%d",
            path,
            len(new_rows),
            skipped,
            total,
        )
        return {
            "inserted": len(new_rows),
            "updated": 0,
            "skipped": skipped,
            "total": total,
        }

    def upsert(
        self,
        dataframe: pd.DataFrame,
        filepath: str | Path,
        key_column: str = "issue_id",
    ) -> dict:
        """Merge ``dataframe`` into the CSV file on ``key_column``.

        New keys are appended (``inserted``), existing keys whose content
        differs are replaced in place (``updated``), and existing rows
        left untouched count as ``skipped`` (``len(existing) - updated``).
        ``total`` is the final row count. New rows win on conflict
        (``keep="last"``); existing column order is preserved and any new
        columns from the incoming frame are added.
        """
        path = Path(filepath)
        existing = self._read_existing(path)
        existing_rows = len(existing) if existing is not None else 0
        if dataframe.empty:
            return {
                "inserted": 0,
                "updated": 0,
                "skipped": existing_rows,
                "total": existing_rows,
            }
        key = self._resolve_key_column(dataframe, key_column)
        if existing is None:
            rows = self.write(dataframe, path)
            return {"inserted": rows, "updated": 0, "skipped": 0, "total": rows}

        columns = list(existing.columns)
        for column in dataframe.columns:
            if column not in columns:
                columns.append(column)
        compare_columns = [c for c in columns if c not in _VOLATILE_COLUMNS]
        existing = existing.reindex(columns=columns)
        incoming = (
            dataframe.reindex(columns=columns)
            .drop_duplicates(subset=[key], keep="last")
            .reset_index(drop=True)
        )

        # Position and content signature of each existing row, by key.
        positions: dict[str, int] = {}
        signatures: dict[str, tuple[str, ...]] = {}
        for position, (_, row) in enumerate(existing.iterrows()):
            key_value = _key_str(row[key])
            if key_value not in positions:
                positions[key_value] = position
                signatures[key_value] = _row_signature(row, compare_columns)

        merged = existing.copy()
        new_rows: list[pd.Series] = []
        updated = 0
        for _, row in incoming.iterrows():
            key_value = _key_str(row[key])
            if key_value in positions:
                if _row_signature(row, compare_columns) != signatures[key_value]:
                    merged.iloc[positions[key_value]] = row.values
                    updated += 1
                # Identical content: row left untouched.
            else:
                new_rows.append(row)
        if new_rows:
            merged = pd.concat(
                [merged, pd.DataFrame(new_rows, columns=columns)],
                ignore_index=True,
            )
        try:
            merged.to_csv(path, index=False)
        except (OSError, ValueError) as exc:
            raise CSVWriterError(f"Failed to upsert CSV {path}: {exc}") from exc
        total = len(merged)
        result = {
            "inserted": len(new_rows),
            "updated": updated,
            "skipped": existing_rows - updated,
            "total": total,
        }
        logger.info(
            "CSV upsert to %s: inserted=%d updated=%d skipped=%d total=%d",
            path,
            result["inserted"],
            updated,
            result["skipped"],
            total,
        )
        return result

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _resolve_key_column(dataframe: pd.DataFrame, key_column: str) -> str:
        """Return the key column, falling back to ``issue_key``."""
        if key_column in dataframe.columns:
            return key_column
        if "issue_key" in dataframe.columns:
            logger.warning(
                "Key column %r not in DataFrame; falling back to 'issue_key'",
                key_column,
            )
            return "issue_key"
        raise CSVWriterError(
            f"DataFrame has neither {key_column!r} nor 'issue_key' column; "
            "cannot deduplicate by key"
        )

    @staticmethod
    def _read_existing(filepath: Path) -> pd.DataFrame | None:
        """Read the existing CSV, or None when there is no usable file.

        A corrupt file is moved aside (``<filepath>.corrupt-<timestamp>``)
        with a warning and None is returned so the caller starts fresh.
        """
        if not filepath.is_file():
            return None
        try:
            return pd.read_csv(filepath, dtype=str)
        except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as exc:
            backup = filepath.with_name(
                f"{filepath.name}.corrupt-"
                f"{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            )
            logger.warning(
                "Existing CSV %s is corrupt (%s); moving it aside to %s "
                "and starting fresh",
                filepath,
                exc,
                backup,
            )
            try:
                filepath.rename(backup)
            except OSError as rename_exc:
                raise CSVWriterError(
                    f"Corrupt CSV {filepath} could not be moved aside to "
                    f"{backup}: {rename_exc}"
                ) from rename_exc
            return None
        except (OSError, ValueError) as exc:
            raise CSVWriterError(f"Failed to read existing CSV {filepath}: {exc}") from exc
