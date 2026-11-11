# Architecture

Short design notes for the Jira Data Ingestion Framework. The verbatim
specification is [`MASTER_BUILD_PROMPT.md`](MASTER_BUILD_PROMPT.md).

## Components

| Component | Responsibility |
| --- | --- |
| `src/clients/jira_client.py` — `JiraClient` | Thin HTTP wrapper: auth, retries, error mapping. Knows nothing about DataFrames, CSV or Sheets. Search uses token pagination (`nextPageToken` / `isLast`, no `startAt`) and defaults `fields` to `*all` — both verified live against Jira Cloud. |
| `src/handlers/jira_handler.py` — `JiraHandler` | Extraction only: paged JQL search, single-issue / comments / worklogs / changelog / sprints / users. Never writes output. |
| `src/transformers/jira_transformer.py` — `JiraTransformer` | Flattens nested Jira JSON (ADF text, users, lists, custom fields) into the 38-column standard schema. Missing fields become empty values, never errors. |
| `src/utils/data_quality.py` | `validate_dataframe` / `drop_invalid_rows` / `check_required_columns`; runs before any write. |
| `src/writers/` — `CSVWriter`, `GoogleSheetWriter`, `MetadataWriter` | Output destinations. All CSV/Sheets writes are upserts on `issue_id` (fallback `issue_key`). |
| `src/pipeline/jira_pipeline.py` — `JiraPipeline` | Orchestration only: config → connect → extract → transform → validate → write → metadata. Owns the watermark. |
| `src/pipeline/board_pipeline.py` — `BoardPipeline` | Specialised subclass: sources issues from one Agile board (`GET /rest/agile/1.0/board/{id}/issue`, offset pagination) instead of a platform JQL search. Own CSV, worksheet and metadata directory per board; shares the rest of the lifecycle. |
| `src/utils/` | `config_loader` (YAML + `${VAR}` expansion), `logger` (rotating logs, redaction, run id), `date_utils` (UTC normalisation, run ids, lookback), `pipeline_metadata` (`PipelineMetadata`, version `1.0.0`), `exceptions` (typed error hierarchy). |

## Data flow

```
config.yaml + .env + jira_fields.yaml
        │
        ▼
JiraPipeline.run(mode, jql, outputs)
        │  1. test_connection          (fails fast on bad credentials)
        │  2. build JQL                (full: configured JQL;
        │                              incremental: updated >= watermark - lookback)
        │  3. JiraHandler.get_all_issues (paged POST /rest/api/3/search/jql)
        │  4. raw JSON preserved       (data/raw/, when enabled)
        │  5. JiraTransformer.transform_issues
        │  6. validate_dataframe       (FAIL aborts before any write)
        │  7. CSVWriter.upsert and/or GoogleSheetWriter.upsert
        │  8. MetadataWriter: run record + latest_run.json watermark
        ▼
PipelineMetadata (status SUCCESS / FAILED)
```

## Dependency-injection points

`JiraPipeline` accepts injected `handler`, `transformer`, `csv_writer`,
`sheets_writer`, `metadata_writer` and `logger`; `JiraHandler` accepts an
injected `client` and `transformer`. Tests inject mocks/fakes through
these points, and operators can substitute implementations without
touching the pipeline logic.

## Extension points

* **New writer** — subclass `DataWriter` (`write` / `append` / `upsert`
  over a DataFrame) and pass it to the pipeline. The handler,
  transformer and quality checks do not change.
* **New dataset** — add the endpoint method to `JiraHandler`, register
  the dataset in `config.yaml` under `datasets` (`enabled`, `endpoint`,
  `jql`, `output`, `key`), and wire it into the pipeline's dataset loop.
* **New custom field** — configuration only: add the id → name mapping
  to `config/jira_fields.yaml` (see `docs/DATA_DICTIONARY.md`).

## Logging and metadata flow

* One `pipeline_run_id` (from `generate_run_id`) is created per run and
  attached to every log record and to the metadata record, so a run can
  be traced end to end.
* Logs go to the console plus three rotating files (application, error
  only, debug). API tokens, passwords and authorisation headers are
  redacted by a logging filter before they can reach any output.
* `PipelineMetadata` records per-stage timings, record counts
  (extracted / transformed / validated / inserted / updated / skipped /
  failed), the data-quality summary and any error, and is written under
  `data/metadata/` as one JSON record per run.

## Decisions

* **Search endpoint** — `POST /rest/api/3/search/jql` is the current
  Jira Cloud search endpoint; the older `GET /rest/api/3/search` is
  deprecated and is not used.
* **CSV is the default output** — Google Sheets is opt-in
  (`google_sheets.enabled: true` plus a service-account credential).
* **Upsert semantics** — outputs are merged on `issue_id` with
  `issue_key` as fallback, so repeated runs are idempotent and never
  duplicate rows.
* **Watermark never advances on failure** — `latest_run.json` is only
  updated after a successful run, so a failed run is retried from the
  last good watermark and no records are silently skipped.
* **Validation before writing** — a frame that fails data-quality
  checks aborts the run before any destination is touched, so outputs
  are never left half-written by bad data.
* **Secrets stay out of the repo** — credentials come from `.env` /
  environment variables only; `.env` and `credentials/` are ignored by
  git, and tokens are redacted from logs.
