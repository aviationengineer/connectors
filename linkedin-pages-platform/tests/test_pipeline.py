"""End-to-end pipeline tests — handler faked, everything else real."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from src.pipeline.linkedin_pages_pipeline import (
    LinkedInPagesPipeline,
    run_summary,
)
from src.transformers.linkedin_transformer import LinkedInTransformer
from src.utils.exceptions import ConfigError, DataQualityError, LinkedInAuthError
from src.utils.pipeline_metadata import PipelineMetadata
from src.writers.metadata_writer import MetadataWriter


class FakeHandler:
    """Stand-in for LinkedInPagesHandler backed by fixtures."""

    def __init__(self, fixtures: dict[str, Any], fail_on: str | None = None) -> None:
        self.fixtures = fixtures
        self.fail_on = fail_on
        self.api_version = "202610"
        self.transformer = LinkedInTransformer()

    def test_connection(self) -> dict[str, Any]:
        if self.fail_on == "connection":
            raise LinkedInAuthError("bad token", status_code=401)
        pages = [
            {"organization_urn": "urn:li:organization:12345678",
             "organization_id": "12345678", "role": "ADMINISTRATOR",
             "state": "APPROVED"}
        ]
        if self.fail_on == "no_pages":
            pages = []
        return {"member": self.fixtures["member"], "administered_pages": len(pages),
                "pages": pages}

    def get_organization(self, organization_urn: str) -> dict[str, Any]:
        return self.fixtures["organization"]

    def list_all_posts(
        self, organization_urn: str, max_posts: int | None = None, page_size: int = 50
    ) -> list[dict[str, Any]]:
        posts = list(self.fixtures["posts"])
        if self.fail_on == "no_ids":
            posts = [{"commentary": {"text": "no id"}} for _ in posts]
        if max_posts is not None:
            posts = posts[:max_posts]
        return posts

    def posts_to_dataframe(
        self, posts: list[dict[str, Any]], organization: dict[str, Any] | None = None
    ) -> pd.DataFrame:
        return self.transformer.transform_posts(posts, organization)


@pytest.fixture()
def fake_handler(all_posts, member_payload, organization_payload) -> FakeHandler:
    return FakeHandler(
        {"posts": all_posts, "member": member_payload,
         "organization": organization_payload}
    )


def _read_csv(path: str) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def test_full_run_success(base_config, fake_handler, tmp_path: Path) -> None:
    pipeline = LinkedInPagesPipeline(base_config, handler=fake_handler)
    metadata = pipeline.run(mode="full")
    assert metadata["status"] == "SUCCESS"
    assert metadata["records_extracted"] == 3
    assert metadata["records_inserted"] == 3
    assert metadata["records_updated"] == 0
    assert metadata["records_skipped"] == 0
    assert metadata["data_quality_status"] == "PASS"
    assert json.loads(metadata["organization_names"]) == ["Acme Corp"]
    csv_path = Path(base_config["linkedin"]["output_csv"])
    assert len(_read_csv(str(csv_path))) == 3
    raw_files = list(Path(base_config["paths"]["raw_dir"]).rglob("run_*.json"))
    assert len(raw_files) == 1
    raw = json.loads(raw_files[0].read_text(encoding="utf-8"))
    assert raw["record_count"] == 3
    assert raw["organization_urn"] == "urn:li:organization:12345678"
    latest = json.loads(
        (Path(base_config["paths"]["metadata_dir"]) / "latest_run.json").read_text(
            encoding="utf-8"
        )
    )
    assert latest["status"] == "SUCCESS"


def test_second_run_upsert_skips_identical(base_config, fake_handler) -> None:
    pipeline = LinkedInPagesPipeline(base_config, handler=fake_handler)
    pipeline.run(mode="full")
    pipeline2 = LinkedInPagesPipeline(base_config, handler=fake_handler)
    metadata = pipeline2.run(mode="full")
    assert metadata["status"] == "SUCCESS"
    assert metadata["records_inserted"] == 0
    assert metadata["records_updated"] == 0
    assert metadata["records_skipped"] == 3


def test_changed_post_is_updated(base_config, fake_handler) -> None:
    pipeline = LinkedInPagesPipeline(base_config, handler=fake_handler)
    pipeline.run(mode="full")
    edited = [dict(fake_handler.fixtures["posts"][0],
                   commentary={"text": "Acme launches the new Rocket-Powered Anvil. Edited."})]
    fake_handler.fixtures["posts"] = edited + fake_handler.fixtures["posts"][1:]
    pipeline2 = LinkedInPagesPipeline(base_config, handler=fake_handler)
    metadata = pipeline2.run(mode="full")
    assert metadata["records_updated"] == 1
    assert metadata["records_inserted"] == 0
    assert metadata["records_skipped"] == 2
    frame = _read_csv(base_config["linkedin"]["output_csv"])
    assert "Edited." in frame.iloc[0]["text"]


def test_incremental_run_filters_old_posts(base_config, fake_handler) -> None:
    # Seed a successful run whose watermark is after every fixture post.
    writer = MetadataWriter(
        metadata_dir=base_config["paths"]["metadata_dir"],
        output_metadata_path=base_config["paths"]["output_metadata"],
    )
    seed = PipelineMetadata(
        pipeline_run_id="20261110_000000_seed",
        pipeline_name="linkedin_pages_ingestion",
        current_watermark=json.dumps(
            {"urn:li:organization:12345678": "2026-11-13T00:00:00Z"}
        ),
    )
    seed.finish("SUCCESS")
    writer.complete_run(seed)
    pipeline = LinkedInPagesPipeline(base_config, handler=fake_handler)
    metadata = pipeline.run(mode="incremental")
    assert metadata["status"] == "SUCCESS"
    assert metadata["records_extracted"] == 0
    assert metadata["records_inserted"] == 0


def test_auth_failure_marks_run_failed(base_config, fake_handler, tmp_path: Path) -> None:
    fake_handler.fail_on = "connection"
    pipeline = LinkedInPagesPipeline(base_config, handler=fake_handler)
    with pytest.raises(LinkedInAuthError):
        pipeline.run(mode="full")
    history = (
        Path(base_config["paths"]["metadata_dir"]) / "pipeline_runs.json"
    ).read_text(encoding="utf-8")
    record = json.loads(history.strip().splitlines()[-1])
    assert record["status"] == "FAILED"
    assert "bad token" in record["error_message"]
    assert not (Path(base_config["paths"]["metadata_dir"]) / "latest_run.json").exists()


def test_no_organizations_configured_and_none_administered(
    base_config, fake_handler
) -> None:
    base_config["linkedin"]["organizations"] = []
    fake_handler.fail_on = "no_pages"
    pipeline = LinkedInPagesPipeline(base_config, handler=fake_handler)
    with pytest.raises(ConfigError):
        pipeline.run(mode="full")


def test_data_quality_failure_fails_run(base_config, fake_handler) -> None:
    fake_handler.fail_on = "no_ids"
    pipeline = LinkedInPagesPipeline(base_config, handler=fake_handler)
    with pytest.raises(DataQualityError):
        pipeline.run(mode="full")


def test_max_posts_cap(base_config, fake_handler) -> None:
    pipeline = LinkedInPagesPipeline(base_config, handler=fake_handler)
    metadata = pipeline.run(mode="full", max_posts=2)
    assert metadata["records_extracted"] == 2
    assert metadata["records_inserted"] == 2


def test_run_summary_renders(base_config, fake_handler) -> None:
    pipeline = LinkedInPagesPipeline(base_config, handler=fake_handler)
    metadata = pipeline.run(mode="full")
    summary = run_summary(metadata)
    assert "LINKEDIN PAGES PIPELINE" in summary
    assert "Records: 3" in summary
    assert "Inserted: 3" in summary
    assert "Data Quality: PASS" in summary
    assert "Pipeline Status: SUCCESS" in summary
