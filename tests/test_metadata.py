"""Tests for MetadataWriter — filesystem only, no live services."""

from __future__ import annotations

import json
from pathlib import Path

from src.utils.pipeline_metadata import PipelineMetadata
from src.writers.metadata_writer import MetadataWriter


def make_writer(tmp_path: Path) -> MetadataWriter:
    return MetadataWriter(
        metadata_dir=tmp_path / "metadata",
        output_metadata_path=tmp_path / "output" / "metadata.json",
    )


def make_run(run_id: str, **fields) -> PipelineMetadata:
    metadata = PipelineMetadata(pipeline_run_id=run_id)
    for name, value in fields.items():
        setattr(metadata, name, value)
    return metadata


def test_append_run_writes_csv_row_and_jsonl_line(tmp_path: Path):
    writer = make_writer(tmp_path)
    assert writer.append_run(make_run("run-1", records_transformed=3)) is True

    csv_lines = (tmp_path / "metadata" / "pipeline_runs.csv").read_text(
        encoding="utf-8"
    ).splitlines()
    assert csv_lines[0].split(",")[0] == "pipeline_run_id"
    assert len(csv_lines) == 2
    assert "run-1" in csv_lines[1]

    json_lines = (tmp_path / "metadata" / "pipeline_runs.json").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(json_lines) == 1
    assert json.loads(json_lines[0])["pipeline_run_id"] == "run-1"


def test_second_run_appends_header_once(tmp_path: Path):
    writer = make_writer(tmp_path)
    assert writer.append_run(make_run("run-1")) is True
    assert writer.append_run(make_run("run-2")) is True

    csv_lines = (tmp_path / "metadata" / "pipeline_runs.csv").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(csv_lines) == 3  # header + 2 rows, header written once
    assert csv_lines[0].count("pipeline_run_id") == 1
    assert "run-1" in csv_lines[1]
    assert "run-2" in csv_lines[2]

    json_lines = (tmp_path / "metadata" / "pipeline_runs.json").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(json_lines) == 2


def test_get_last_successful_run_returns_latest_success(tmp_path: Path):
    writer = make_writer(tmp_path)
    first = make_run("run-1")
    first.finish("SUCCESS")
    second = make_run("run-2")
    second.finish("SUCCESS")
    failed = make_run("run-3")
    failed.finish("FAILED", "boom")
    for run in (first, second, failed):
        assert writer.append_run(run) is True

    latest = writer.get_last_successful_run()
    assert latest is not None
    assert latest["pipeline_run_id"] == "run-2"
    assert latest["status"] == "SUCCESS"


def test_get_last_successful_run_returns_none_when_no_success(tmp_path: Path):
    writer = make_writer(tmp_path)
    assert writer.get_last_successful_run() is None
    failed = make_run("run-1")
    failed.finish("FAILED", "boom")
    assert writer.append_run(failed) is True
    assert writer.get_last_successful_run() is None


def test_fail_run_preserves_watermark(tmp_path: Path):
    writer = make_writer(tmp_path)
    success = make_run("run-1", records_transformed=3)
    success.finish("SUCCESS")
    assert writer.complete_run(success) is True

    latest = json.loads(
        (tmp_path / "metadata" / "latest_run.json").read_text(encoding="utf-8")
    )
    assert latest["pipeline_run_id"] == "run-1"
    output = json.loads(
        (tmp_path / "output" / "metadata.json").read_text(encoding="utf-8")
    )
    assert output["last_successful_run"] == "run-1"

    # A later failure is recorded in the history but must not move the
    # watermark: latest_run.json and output metadata.json still hold run-1.
    assert writer.fail_run(make_run("run-2"), RuntimeError("boom")) is True
    latest = json.loads(
        (tmp_path / "metadata" / "latest_run.json").read_text(encoding="utf-8")
    )
    assert latest["pipeline_run_id"] == "run-1"
    assert latest["status"] == "SUCCESS"
    output = json.loads(
        (tmp_path / "output" / "metadata.json").read_text(encoding="utf-8")
    )
    assert output["last_successful_run"] == "run-1"

    json_lines = (tmp_path / "metadata" / "pipeline_runs.json").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(json_lines) == 2
    failed_record = json.loads(json_lines[1])
    assert failed_record["status"] == "FAILED"
    assert failed_record["pipeline_run_id"] == "run-2"


def test_complete_run_updates_latest_and_output_metadata(tmp_path: Path):
    writer = make_writer(tmp_path)
    run = make_run("run-1", records_transformed=5)
    run.finish("SUCCESS")
    assert writer.complete_run(run) is True

    latest = json.loads(
        (tmp_path / "metadata" / "latest_run.json").read_text(encoding="utf-8")
    )
    assert latest["pipeline_run_id"] == "run-1"
    assert latest["status"] == "SUCCESS"

    output = json.loads(
        (tmp_path / "output" / "metadata.json").read_text(encoding="utf-8")
    )
    assert output == {
        "last_successful_run": "run-1",
        "records_processed": 5,
        "status": "success",
    }
    assert writer.get_last_successful_run()["pipeline_run_id"] == "run-1"


def test_metadata_write_failure_returns_false_instead_of_raising(tmp_path: Path):
    # metadata_dir is a file, not a directory: construction must not
    # raise, but every write must fail gracefully with False.
    blocker = tmp_path / "metadata"
    blocker.write_text("not a directory", encoding="utf-8")
    writer = MetadataWriter(
        metadata_dir=blocker,
        output_metadata_path=tmp_path / "output" / "metadata.json",
    )
    assert writer.append_run(make_run("run-1")) is False
    assert writer.complete_run(make_run("run-2")) is False
    assert writer.get_last_successful_run() is None

    # Output path under a file: parent creation fails, still False.
    writer2 = MetadataWriter(
        metadata_dir=tmp_path / "metadata2",
        output_metadata_path=blocker / "metadata.json",
    )
    run = make_run("run-3")
    run.finish("SUCCESS")
    assert writer2.write_output_metadata(run) is False


def test_to_dict_from_dict_round_trip():
    run = make_run(
        "run-1",
        records_transformed=7,
        data_quality={"status": "PASS", "total_records": 7},
        stages={"extract": {"status": "SUCCESS", "duration_seconds": 1.25}},
        error={"type": "JiraAPIError", "message": "earlier failure"},
    )
    run.finish("SUCCESS")
    record = run.to_dict()
    restored = PipelineMetadata.from_dict(record)
    assert restored.pipeline_run_id == "run-1"
    assert restored.status == "SUCCESS"
    assert restored.records_transformed == 7
    assert restored.data_quality == {"status": "PASS", "total_records": 7}
    assert restored.stages == {
        "extract": {"status": "SUCCESS", "duration_seconds": 1.25}
    }
    assert restored.error == {"type": "JiraAPIError", "message": "earlier failure"}
    assert restored.start_timestamp == run.start_timestamp
    assert restored.end_timestamp == run.end_timestamp


def test_create_and_update_run_lifecycle_hooks(tmp_path: Path):
    writer = make_writer(tmp_path)
    run = make_run("run-1")
    assert writer.create_run(run) is run
    updated = writer.update_run(run, records_transformed=4, status="SUCCESS")
    assert updated is run
    assert run.records_transformed == 4
    assert run.status == "SUCCESS"


def test_prune_old_metadata_drops_only_old_lines(tmp_path: Path):
    writer = make_writer(tmp_path)
    old = make_run("run-old")
    old.created_timestamp = "2025-01-01T00:00:00Z"
    old.start_timestamp = "2025-01-01T00:00:00Z"
    new = make_run("run-new")
    assert writer.append_run(old) is True
    assert writer.append_run(new) is True
    assert writer.prune_old_metadata(retention_days=365) == 1
    remaining = (tmp_path / "metadata" / "pipeline_runs.json").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(remaining) == 1
    assert json.loads(remaining[0])["pipeline_run_id"] == "run-new"
