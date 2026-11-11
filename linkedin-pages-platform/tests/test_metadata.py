"""Tests for pipeline metadata records and the metadata writer."""

from __future__ import annotations

import json
from pathlib import Path

from src.utils.pipeline_metadata import PipelineMetadata
from src.writers.metadata_writer import MetadataWriter


def _record(run_id: str = "20261111_103000_a123") -> PipelineMetadata:
    return PipelineMetadata(
        pipeline_run_id=run_id,
        pipeline_name="linkedin_pages_ingestion",
        organization_urns=["urn:li:organization:12345678"],
        organization_names=["Acme Corp"],
        records_extracted=3,
        records_transformed=3,
    )


def test_metadata_finish_computes_duration() -> None:
    record = _record()
    record.finish("SUCCESS", "done")
    assert record.status == "SUCCESS"
    assert record.duration_seconds is not None
    assert record.duration_seconds >= 0
    assert record.end_timestamp is not None


def test_metadata_round_trip() -> None:
    record = _record()
    record.finish("SUCCESS")
    restored = PipelineMetadata.from_dict(record.to_dict())
    assert restored.pipeline_run_id == record.pipeline_run_id
    assert restored.organization_urns == ["urn:li:organization:12345678"]
    assert restored.records_extracted == 3


def test_stage_lifecycle() -> None:
    record = _record()
    started = record.start_stage("extract")
    record.end_stage("extract", started, records_extracted=3)
    assert record.stages["extract"]["status"] == "SUCCESS"
    assert record.stages["extract"]["records_extracted"] == 3
    started = record.start_stage("validate")
    record.fail_stage("validate", started, "boom")
    assert record.stages["validate"]["status"] == "FAILED"


def test_complete_run_writes_history_and_watermark(tmp_path: Path) -> None:
    writer = MetadataWriter(
        metadata_dir=tmp_path / "metadata",
        output_metadata_path=tmp_path / "output" / "metadata.json",
    )
    record = _record()
    record.finish("SUCCESS")
    assert writer.complete_run(record) is True
    assert (tmp_path / "metadata" / "pipeline_runs.csv").is_file()
    assert (tmp_path / "metadata" / "pipeline_runs.json").is_file()
    latest = json.loads(
        (tmp_path / "metadata" / "latest_run.json").read_text(encoding="utf-8")
    )
    assert latest["pipeline_run_id"] == "20261111_103000_a123"
    assert latest["status"] == "SUCCESS"
    assert writer.get_last_successful_run()["pipeline_run_id"] == record.pipeline_run_id


def test_fail_run_never_touches_watermark(tmp_path: Path) -> None:
    writer = MetadataWriter(
        metadata_dir=tmp_path / "metadata",
        output_metadata_path=tmp_path / "output" / "metadata.json",
    )
    good = _record("20261111_103000_aaaa")
    good.finish("SUCCESS")
    writer.complete_run(good)
    bad = _record("20261111_103001_bbbb")
    bad.finish("FAILED", "auth failed")
    assert writer.fail_run(bad, error=RuntimeError("auth failed")) is True
    latest = json.loads(
        (tmp_path / "metadata" / "latest_run.json").read_text(encoding="utf-8")
    )
    assert latest["pipeline_run_id"] == "20261111_103000_aaaa"
    assert writer.get_last_successful_run()["pipeline_run_id"] == "20261111_103000_aaaa"
    history = (tmp_path / "metadata" / "pipeline_runs.json").read_text(
        encoding="utf-8"
    )
    assert "20261111_103001_bbbb" in history  # failure IS recorded


def test_metadata_writer_never_raises_on_bad_path(tmp_path: Path) -> None:
    writer = MetadataWriter(
        metadata_dir=tmp_path / "metadata",
        output_metadata_path=tmp_path / "output" / "metadata.json",
    )
    record = _record()
    record.finish("SUCCESS")
    # Break the JSONL file location by making it a directory.
    (tmp_path / "metadata" / "pipeline_runs.json").mkdir(parents=True, exist_ok=True)
    assert writer.append_run(record) is False
