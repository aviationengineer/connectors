# LinkedIn Company Pages Ingestion Framework

A reusable, class-based Python framework that reads **Company Page posts**
(news) through **LinkedIn's official API** and lands them as standardised
Pandas DataFrames — CSV by default, Google Sheets optional.

```
LinkedIn API → Handler → Posts → Transformer → Data Quality → Dedup/UPSERT → CSV → (Google Sheets)
                                                              ↓
                                                      Metadata + Logging
```

## What it does — and what it does not do

- **It reads only Company Pages the authenticated member administers**,
  via LinkedIn's Community Management API (Marketing Developer Platform).
- **It does not scrape LinkedIn.** No automation of linkedin.com, no
  session cookies, no bypass of authentication, access controls or
  CAPTCHA. Scraping LinkedIn violates its User Agreement — LinkedIn
  sued Proxycurl over it in January 2025 and Proxycurl shut down that
  July. This framework is the compliant alternative.
- **It does not read job postings.** LinkedIn has no public API to search
  or read the job index (its public Jobs API shut down in 2018; the
  Talent Solutions Job Posting API is partner-only and built for *pushing*
  jobs in, not reading them). If you need jobs, that is a partnership
  conversation with LinkedIn, not a pipeline.
- **It does not read personal profiles or personal posts.** The
  `r_member_social` permission is closed to new requests.

## Layout

```
linkedin-pages-platform/
├── config/
│   ├── config.yaml            # API version, polling, logging, paths, DQ, compliance
│   └── linkedin_pages.yaml    # organisations to ingest (your Page ids)
├── src/
│   ├── handlers/linkedin_handler.py     # LinkedInPagesHandler
│   ├── clients/linkedin_client.py       # versioned REST client + OAuth helpers
│   ├── clients/google_sheets_client.py  # gspread client (Sheets optional)
│   ├── transformers/linkedin_transformer.py  # posts -> canonical DataFrame
│   ├── writers/                 # csv_writer / metadata_writer / google_sheet_writer
│   ├── pipeline/linkedin_pages_pipeline.py
│   └── utils/                   # logger / config_loader / pipeline_metadata /
│                                # data_quality / date_utils / exceptions
├── scripts/
│   ├── linkedin_auth.py         # OAuth browser flow + token refresh
│   └── run_linkedin_pages.py    # pipeline CLI
├── tests/                       # mocked pytest suite (no live account needed)
└── docs/TEST_USE_CASES.md
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### 1. Create a LinkedIn developer app

1. Go to the [LinkedIn Developer Portal](https://www.linkedin.com/developers)
   and create an app.
2. On the **Products** tab, request the **Community Management API**.
   Approval is manual: LinkedIn only grants it to registered legal
   organisations with a commercial use case. You will need a verified
   business email, and the app starts on a **Development tier** (500 app
   calls / 100 member calls per day). Production use needs a separate
   Standard tier review, including a screen recording of your use cases.
3. Request these scopes: `r_organization_social` (read Page posts),
   `rw_organization_admin` (discover Pages, read analytics),
   `r_organization_social_feed` (read comments/reactions).
4. Add a redirect URI (e.g. `http://localhost:8080`) and copy the
   **Client ID** and **Client Secret** into `.env`.

### 2. Authorise (OAuth)

```bash
python scripts/linkedin_auth.py
```

Open the printed URL, authorise as a member who administers the Page(s),
paste the `code` back. Tokens are written to `.env` (mode 0600,
git-ignored) — never printed in full, never committed.

> **Refresh tokens:** LinkedIn issues programmatic refresh tokens only
> to eligible approved partners. Without one, re-run the browser flow
> when the access token expires (60 days). `linkedin_auth.py --refresh`
> works when a refresh token exists.

### 3. Point it at your Pages

```bash
python scripts/run_linkedin_pages.py --discover
```

Copy the organisation ids into `config/linkedin_pages.yaml`.

## Usage

```bash
# Full run for every configured Page
python scripts/run_linkedin_pages.py

# Incremental: only posts newer than the last successful watermark
python scripts/run_linkedin_pages.py --mode incremental

# One Page, capped at 100 posts, debug logging
python scripts/run_linkedin_pages.py --org 12345678 --max-posts 100 --debug
```

Output: `data/output/linkedin/linkedin_posts.csv` (upsert on `post_urn`),
raw payloads under `data/raw/linkedin/<org>/<YYYY>/<MM>/<DD>/`,
run history in `data/metadata/`.

### Python API

```python
from src.handlers.linkedin_handler import LinkedInPagesHandler

handler = LinkedInPagesHandler(access_token="...")  # or from .env via config
df = handler.organization_posts_to_dataframe("urn:li:organization:12345678")
print(df.head())
```

```python
from src.writers.csv_writer import CSVWriter

CSVWriter().upsert(df, "data/output/linkedin/linkedin_posts.csv", key_column="post_urn")
```

```python
from src.pipeline.linkedin_pages_pipeline import LinkedInPagesPipeline
from src.utils.config_loader import load_config

pipeline = LinkedInPagesPipeline(load_config("config/config.yaml"))
pipeline.run(mode="incremental")
```

## Canonical post schema

| Column | Meaning |
|---|---|
| `post_urn` | Business key, e.g. `urn:li:share:111111111` |
| `post_url` | `https://www.linkedin.com/feed/update/<urn>` |
| `organization_urn` / `organization_id` / `organization_name` | Owning Page |
| `author_type` | `organization` / `person` |
| `text` | Post body |
| `created_at` | ISO 8601 UTC (from epoch-millis `publishedAt`) |
| `visibility`, `lifecycle_state` | As returned by the API |
| `likes_count`, `comments_count`, `shares_count` | Engagement (Social Actions enrichment when enabled) |
| `media_type`, `media_title`, `media_url`, `article_url` | Attached content |
| `source` | Always `LINKEDIN` |
| `extraction_timestamp`, `ingestion_timestamp` | Pipeline stamps |

## Incremental processing

Posts are upserted on `post_urn`: new key → INSERT, same key with
changed content → UPDATE, same key with identical content → SKIP.
`ingestion_timestamp` is excluded from change detection. The incremental
watermark is the newest `created_at` of the last successful run, minus a
10-minute lookback.

## Data quality

Every frame is validated before writing: record counts, required fields,
business-key presence, duplicate keys, URL and date validity, null
percentages. Status `PASS` / `WARN` / `FAIL` — a fully invalid frame
fails the run and no output is written.

## Error handling

Retries with exponential backoff: 429, 500/502/503, network timeouts.
Never retried: 401/403 (bad token, missing scope, missing Community
Management product), 400, 404. A failed run writes FAILED metadata with
the run id preserved and never touches the success watermark — no false
SUCCESS.

## Google Sheets (optional, off by default)

Set `google_sheets.enabled: true` in `config/config.yaml` and place a
service-account key at `credentials/service_account.json`. The writer
upserts (never replaces) the `linkedin_posts` worksheet.

## API version

The client sends `LinkedIn-Version: 202610` and
`X-Restli-Protocol-Version: 2.0.0`. LinkedIn versions its Marketing APIs
monthly and sunsets old versions after about a year — bump
`linkedin.api_version` in `config/config.yaml` (or `LINKEDIN_API_VERSION`)
when the configured version approaches sunset.

## Compliance

- Only Pages the authenticated member administers, through the official
  API, under the Community Management product. No scraping.
- Only data the app and account are authorised to access is collected.
- Post text and engagement counts are business content from Pages you
  administer. Retain only what the pipeline needs, restrict access to
  output files, and delete raw payloads when no longer required
  (`compliance.retention_days: 365` in config).
- Secrets live in `.env` (git-ignored) or the secure vault — never in
  code, logs, metadata or the repo. The logger redacts tokens, secrets
  and authorisation headers.

## Tests

Mocked end-to-end (no live LinkedIn account needed):

```bash
python -m pytest -q
```

See `docs/TEST_USE_CASES.md` for the UC-01…UC-13 mapping.
