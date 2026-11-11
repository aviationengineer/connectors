# Enterprise Python Jira Data Ingestion Framework

A production-ready, class-based Python framework that extracts data from the
Jira Cloud REST API, normalises it into a standardised tabular schema, runs
data-quality checks, and writes it to CSV and/or Google Sheets — with
incremental (watermark-based) loads, raw-data preservation, rotating logs
and per-run metadata.

The framework lives at the root of the
[aviationengineer/connectors](https://github.com/aviationengineer/connectors)
repository:

```bash
git clone https://github.com/aviationengineer/connectors.git
cd connectors
```

The framework/project name is **jira-data-platform**. The full verbatim
specification is [`docs/MASTER_BUILD_PROMPT.md`](docs/MASTER_BUILD_PROMPT.md);
see also [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and
[`docs/DATA_DICTIONARY.md`](docs/DATA_DICTIONARY.md).

## Overview

```
CONFIG ──▶ JiraHandler ──▶ Transformer ──▶ DQ ──▶ Upsert ──▶ CSV / Google Sheets
   │                                                          │
   └────────────── raw JSON preserved ───────────────────────┴──▶ Metadata
```

* **Extract** — `JiraHandler` pages through `POST /rest/api/3/search/jql`
  (the current Jira Cloud search endpoint) with retries on 429/5xx and
  timeouts, and no retries on 401/403/400.
* **Transform** — `JiraTransformer` flattens nested Jira payloads
  (ADF descriptions, users, lists, custom fields) into 38 standard columns.
  Missing fields become empty values, never errors.
* **Validate** — `validate_dataframe` checks primary keys, duplicates and
  date parseability before anything is written.
* **Write** — `CSVWriter.upsert` merges on `issue_id` (falling back to
  `issue_key`); `GoogleSheetWriter` does the same in Google Sheets.
* **Audit** — every run produces a `PipelineMetadata` record (run id,
  timings, JQL, record counts, data-quality summary, error) and updates
  the incremental watermark only on success.

## Installation

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Or install the package (editable) from `pyproject.toml`:

```bash
pip install -e .
```

## Configuration

### Credentials — `.env`

```bash
cp .env.example .env
```

Then fill in `.env` (never commit it — it is in `.gitignore`):

| Variable | Meaning |
| --- | --- |
| `JIRA_BASE_URL` | Jira Cloud site, e.g. `https://your-company.atlassian.net` |
| `JIRA_EMAIL` | Email address for basic authentication |
| `JIRA_API_TOKEN` | Jira API token (see Authentication below) |
| `JIRA_AUTH_TYPE` | `basic` (default) or `bearer` |
| `GOOGLE_APPLICATION_CREDENTIALS` | Service-account JSON path (Google Sheets output only) |

### Pipeline — `config/config.yaml`

The example config covers every section: `jira` (site, auth, timeout,
page size, projects, fields, JQL), `incremental_load` (enabled,
timestamp field, lookback), `output` (default format, CSV path),
`google_sheets` (enabled, spreadsheet id, worksheet, credentials file),
`logging`, `raw_data`, `metadata` and `datasets` (one entry per dataset,
each with `enabled`, `endpoint`, `jql`, `output` and `key`).

Values may use `${VAR}` or `${VAR:-default}` placeholders, expanded from
the environment (`.env` is loaded automatically).

### Custom fields — `config/jira_fields.yaml`

Maps Jira custom-field ids to friendly column names, e.g.
`customfield_10001` → `story_points`. Set
`retain_unknown_custom_fields: true` to keep unmapped custom fields as
`custom_<field_id>` columns instead of dropping them.

### Live Jira Cloud notes (verified 2026-11-10 against westjet.atlassian.net)

- **Pagination is token-based.** `POST /rest/api/3/search/jql` does not
  accept `startAt` (sending it returns HTTP 400 "Invalid request
  payload"). The client pages with `nextPageToken` / `isLast` instead.
- **Fields default to `*all`.** A search with no `fields` list returns
  issues shaped exactly `{"id": ...}` — no key, no fields. The client
  sends `["*all"]` when no fields are given; the pipeline always sends
  the configured field list.
- **JQL must be bounded.** A bare `ORDER BY updated DESC` is rejected
  with "Unbounded JQL queries are not allowed here". Always include a
  restriction, e.g. `updated >= -30d ORDER BY updated DESC` or a
  `project = ...` clause.

## Authentication

Jira Cloud uses basic auth with an email address and an API token
(not your Atlassian password):

1. Go to <https://id.atlassian.com/manage-profile/security/api-tokens>.
2. Click **Create API token**, give it a label, and copy the token.
3. Put it in `.env` as `JIRA_API_TOKEN` (with `JIRA_EMAIL`).

The token is only ever read from the environment, is redacted from all
logs, and must never be pasted into config files, code or chat.

## Usage

### Full load (CLI)

```bash
python scripts/extract_jira.py
```

### Incremental load (CLI)

```bash
python scripts/extract_jira.py --mode incremental
# or the scheduled entry point:
python scripts/incremental_jira_load.py
```

### Custom JQL and outputs

```bash
python scripts/extract_jira.py --jql "project = TECH ORDER BY updated DESC"
python scripts/extract_jira.py --output google_sheets
python scripts/extract_jira.py --output both --log-level DEBUG
```

Exit codes: `0` success, `1` configuration/authentication failure
(clean `ERROR: ...` message, no traceback), `2` any other pipeline
failure.

### Python API

```python
from src.handlers.jira_handler import JiraHandler

handler = JiraHandler(
    base_url="https://your-company.atlassian.net",
    username="you@company.com",
    api_token="your-api-token",
)
df = handler.extract_to_dataframe("project = DATA ORDER BY updated DESC")
print(df.head())
```

```python
from src.writers.csv_writer import CSVWriter

writer = CSVWriter()
result = writer.upsert(df, "data/output/jira_issues.csv", key_column="issue_id")
print(result)  # {"inserted": ..., "updated": ..., "total": ...}
```

```python
from src.pipeline.jira_pipeline import JiraPipeline

pipeline = JiraPipeline(config_path="config/config.yaml")
metadata = pipeline.run_incremental_load()
print(metadata.status, metadata.records_extracted)
```

### Board pipelines (specialised)

`BoardPipeline` (`src/pipeline/board_pipeline.py`) pulls every issue
on one Jira Agile board instead of running a platform JQL search.
Transformation, data-quality checks, CSV upsert, optional Google
Sheets, metadata and the run lifecycle are shared with the generic
pipeline. Each board keeps its own CSV, worksheet and metadata
directory, so board watermarks never collide with the generic
pipeline's watermark.

```bash
python scripts/extract_maidp_board.py --mode full
python scripts/extract_maidp_board.py --mode incremental
python scripts/extract_maidp_board.py --board-id 8805 --output both
python scripts/extract_maidp_board.py --jql "project = MAIDP AND status = 'In Progress'"
```

Boards are configured under `boards:` in `config/config.yaml` — the
shipped `maidp` entry targets MAIDP board 8805
(`data/output/maidp_board_8805_issues.csv`,
worksheet `MAIDP_Board_8805`). Add another board by adding another
key and running it with `--board-key <key>`.

Note the board endpoint paginates by offset (`startAt`), unlike the
platform search endpoint, which is token-based.

## CSV output and upsert semantics

* The default output is `data/output/jira_issues.csv` (configurable via
  `output.csv.path`).
* Writes are **upserts**, not appends: each incoming row is matched
  against existing rows on the primary key `issue_id`. Matching rows are
  updated in place; new rows are inserted. Re-running a load therefore
  never creates duplicates.
* If `issue_id` is missing, `issue_key` is used as the fallback key.
* Column order follows the standard schema (see
  [`docs/DATA_DICTIONARY.md`](docs/DATA_DICTIONARY.md)); mapped custom
  fields are appended after the standard columns.

## Google Sheets

1. Create a Google Cloud service account and share the target
   spreadsheet with its email address (Editor).
2. Save the service-account JSON under `credentials/service_account.json`
   (never commit it) and set `GOOGLE_APPLICATION_CREDENTIALS` in `.env`.
3. In `config/config.yaml`, set `google_sheets.enabled: true` and fill in
   `spreadsheet_id` and `worksheet_name`.
4. Run with `--output google_sheets` or `--output both`.

The sheet is upserted on the same key semantics as the CSV, so repeated
runs stay idempotent.

## Incremental loads, watermark and lookback

* The first incremental run has no watermark and behaves like a full
  load for the configured JQL.
* After each **successful** run, the watermark (the newest `updated`
  timestamp seen) is stored in `data/metadata/latest_run.json`.
* The next incremental run queries
  `updated >= "<watermark minus lookback>"`, where the lookback
  (`incremental_load.lookback_minutes`, default 10) protects against
  records updated around the previous extraction boundary.
* The watermark **never advances on a failed run** — a failure is
  retried from the last good watermark, so no records are silently lost.
* Disable incremental loading with `incremental_load.enabled: false`.

## Raw data preservation

With `raw_data.enabled: true` (the default), the raw Jira JSON returned
by the API is written under `data/raw/` before transformation, so any
run can be replayed or audited without calling Jira again.

## Logging and metadata

* Logs live in `logs/` (configurable): `jira_pipeline.log`
  (application), `jira_pipeline_error.log` (errors only) and
  `jira_pipeline_debug.log` (debug level). Rotation is on by default
  (10 MB per file, 10 backups) with 30-day retention.
* Levels are configured in `config/config.yaml` under `logging`
  (`level`, `console_level`, `format: text|json`); `--log-level` on the
  CLI overrides them for one run.
* Every log record carries a `pipeline_run_id` (e.g.
  `20261110_193001_a82f`) so one execution can be traced across
  components. API tokens, passwords and authorisation headers are
  redacted before they can reach any log.
* Per-run metadata is written under `data/metadata/` (one JSON record
  per run plus `latest_run.json` for the watermark). Each record holds
  the run id, mode, JQL, record counts per stage, data-quality summary,
  stage timings, output status and any error.

## Data quality checks

Before writing, `validate_dataframe` reports:

* `status`: `PASS`, `WARN` (some invalid rows or duplicates) or `FAIL`
  (every row invalid).
* Counts of missing `issue_id` / `issue_key` / `summary`, duplicates,
  invalid `created` / `updated` dates, and missing assignee / reporter /
  status.

Rows with neither `issue_id` nor `issue_key` are dropped; a fully
invalid frame fails the run. The summary is stored in the run metadata.

## Adding custom fields

1. Find the field id in Jira (**Settings → Issues → Custom fields**,
   or inspect a raw issue payload — ids look like `customfield_10001`).
2. Add it to `config/jira_fields.yaml` under `custom_fields`, e.g.:

   ```yaml
   custom_fields:
     customfield_10042:
       name: customer_impact
   ```

3. The column appears in the output on the next run. No code changes
   are needed; unmapped fields are dropped unless
   `retain_unknown_custom_fields` is `true`.

## Adding another endpoint / dataset

1. Add a handler method for the endpoint (e.g. `get_issue_worklogs`
   already exists for worklogs) and, if needed, a transformer.
2. Register the dataset in `config/config.yaml` under `datasets` with
   `enabled`, `endpoint`, `jql`, `output` and `key`.
3. Wire it into the pipeline's dataset loop so it is extracted,
   transformed, validated and upserted like `issues`.

For a new output destination, subclass `DataWriter` (see
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)) and register it with
the pipeline — the handler and transformer do not change.

## Testing

```bash
.venv/bin/python -m pytest -q
```

The suite is fully mocked (no Jira credentials or network needed) and
covers the client (retries, error mapping), handler (pagination,
delegation), transformer (ADF text, custom fields, dates), logger
(levels, redaction, JSON format, rotation), data quality, config
loading and the pipeline (upsert, watermark, failure handling).

## Project structure

```
.
├── config/
│   ├── config.yaml          # Pipeline configuration (example)
│   └── jira_fields.yaml     # Custom-field mapping
├── data/
│   ├── raw/                 # Preserved raw Jira JSON
│   ├── processed/           # Intermediate artefacts
│   ├── output/              # CSV output (jira_issues.csv)
│   └── metadata/            # Run metadata + latest_run.json (watermark)
├── docs/
│   ├── MASTER_BUILD_PROMPT.md   # Verbatim specification (§1–§55)
│   ├── ARCHITECTURE.md
│   └── DATA_DICTIONARY.md
├── logs/                    # Rotating application / error / debug logs
├── scripts/
│   ├── extract_jira.py          # Main CLI (full / incremental)
│   └── incremental_jira_load.py # Scheduled incremental entry point
├── src/
│   ├── clients/         # JiraClient, Google Sheets client
│   ├── handlers/        # JiraHandler (extraction)
│   ├── pipeline/        # JiraPipeline (orchestration)
│   ├── transformers/    # JiraTransformer (normalisation)
│   ├── utils/           # config, logging, dates, DQ, metadata, errors
│   └── writers/         # CSV, Google Sheets, metadata writers
├── tests/
│   ├── conftest.py
│   └── test_*.py
├── .env.example
├── .gitignore
├── pyproject.toml
└── requirements.txt
```

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `ERROR: Required environment variable 'JIRA_BASE_URL' is not set` | Copy `.env.example` to `.env` and fill in your values. |
| `Jira authentication failed (HTTP 401/403)` | Wrong email or API token, or the token was revoked. Create a fresh token at <https://id.atlassian.com/manage-profile/security/api-tokens> and check the account can see the project. Exit code 1; not retried. |
| `Invalid JQL or bad request (HTTP 400)` | The JQL is invalid — check project keys, field names and quoting. Not retried. |
| `Jira rate limit reached (HTTP 429)` | Jira is throttling the caller. The client retries with exponential backoff; if it persists, lower `jira.page_size` or space out scheduled runs. |
| `Could not reach Jira at ...` / timeouts | Network or proxy problem, or a wrong `JIRA_BASE_URL`. Retried, then the run fails with exit code 2. |
| `Configuration file not found` | `--config` points at a missing file; the default is `config/config.yaml` relative to the project root. |
| Empty CSV / 0 records | The JQL matched nothing, or the account cannot see the project. Run with `--log-level DEBUG` and check the JQL in the log. |
| `latest_run.json` not advancing | The last run failed — the watermark only advances on success. Fix the failure and re-run; the next incremental load resumes from the last good watermark. |
