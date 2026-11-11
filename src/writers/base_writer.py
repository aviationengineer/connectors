"""Abstract base class for output writers (spec §21).

Every output destination (CSV, Google Sheets, ...) implements this
interface so the pipeline coordinator can swap writers without
knowing the destination. Counts dicts use the shared shape::

    {"inserted": int, "updated": int, "skipped": int, "total": int}

where ``total`` is the final row count in the destination.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class DataWriter(ABC):
    """Common interface for all pipeline output writers."""

    @abstractmethod
    def write(self, dataframe: pd.DataFrame, *args, **kwargs) -> int:
        """Replace the destination content with ``dataframe``.

        Returns the number of rows written.
        """
        raise NotImplementedError

    @abstractmethod
    def append(self, dataframe: pd.DataFrame, *args, **kwargs) -> dict:
        """Append only rows not already present in the destination.

        Returns a counts dict (``inserted`` / ``updated`` / ``skipped`` /
        ``total``).
        """
        raise NotImplementedError

    @abstractmethod
    def upsert(
        self, dataframe: pd.DataFrame, key_column: str = "issue_id", *args, **kwargs
    ) -> dict:
        """Merge ``dataframe`` into the destination by ``key_column``.

        New keys are inserted, existing keys are updated, unchanged rows
        are left alone. Returns a counts dict (``inserted`` / ``updated`` /
        ``skipped`` / ``total``).
        """
        raise NotImplementedError
