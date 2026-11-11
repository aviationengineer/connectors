"""Tests for the Excel output writer."""

from __future__ import annotations

import pandas as pd
import pytest

from src.writers.excel_writer import ExcelWriter


@pytest.fixture
def sample_df():
    return pd.DataFrame(
        {
            "issue_id": ["10001", "10002"],
            "issue_key": ["DATA-101", "DATA-102"],
            "summary": ["First issue", "Second issue"],
        }
    )


def test_write_creates_workbook_with_frozen_header(tmp_path, sample_df):
    path = tmp_path / "issues.xlsx"
    counts = ExcelWriter().write(sample_df, path, sheet_name="MAIDP_Board_8805")

    assert counts == {"written": 2, "sheet": "MAIDP_Board_8805", "path": str(path)}
    assert path.is_file()
    read_back = pd.read_excel(path, sheet_name="MAIDP_Board_8805")
    assert list(read_back.columns) == ["issue_id", "issue_key", "summary"]
    assert len(read_back) == 2
    assert list(read_back["issue_key"]) == ["DATA-101", "DATA-102"]


def test_write_freezes_header_and_sets_autofilter(tmp_path, sample_df):
    from openpyxl import load_workbook

    path = tmp_path / "issues.xlsx"
    ExcelWriter().write(sample_df, path)
    workbook = load_workbook(path)
    sheet = workbook["jira_issues"]
    assert sheet.freeze_panes == "A2"
    assert sheet.auto_filter.ref is not None
    assert sheet.auto_filter.ref.startswith("A1")


def test_write_replaces_existing_sheet(tmp_path, sample_df):
    path = tmp_path / "issues.xlsx"
    ExcelWriter().write(sample_df, path)
    smaller = sample_df.head(1)
    ExcelWriter().write(smaller, path)
    read_back = pd.read_excel(path, sheet_name="jira_issues")
    assert len(read_back) == 1


def test_write_truncates_overlong_sheet_names(tmp_path, sample_df):
    path = tmp_path / "issues.xlsx"
    counts = ExcelWriter().write(sample_df, path, sheet_name="x" * 40)
    assert counts["sheet"] == "x" * 31
