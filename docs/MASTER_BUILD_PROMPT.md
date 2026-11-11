# Master Build Prompt — Enterprise Python Jira Data Ingestion Framework

_Stored verbatim from the user's request (2026-11-10). This is the specification the framework was built against._

Build an Enterprise Python Jira Data Ingestion Framework

Objective

Build a reusable, production-quality Python framework that connects to Jira using the Jira REST API, extracts Jira data, transforms it into a standardized tabular format, and persists the data to CSV by default and optionally to Google Sheets.

The framework must be modular, configurable, reusable, testable, and designed to support incremental/append-based ingestion.

The primary use case is:

Jira REST API → Python Jira Handler → Standardized DataFrame → CSV → Google Sheets

The framework should be designed so that additional Jira endpoints, output formats, and data destinations can be added later without redesigning the core architecture.

⸻

1. Core Requirements

Build the following capabilities:

1. Connect to Jira REST API.
2. Authenticate securely.
3. Extract Jira issues using JQL.
4. Support pagination.
5. Extract all relevant Jira issue fields.
6. Normalize nested Jira JSON into tabular data.
7. Convert extracted data into a standardized Pandas DataFrame.
8. Save data as CSV by default.
9. Append new data to an existing CSV.
10. Prevent duplicate Jira issues when appending.
11. Optionally write/append data to Google Sheets.
12. Support incremental extraction.
13. Support configurable Jira projects.
14. Support configurable JQL.
15. Support configurable fields.
16. Provide robust logging and error handling.
17. Provide retry logic for Jira API failures/rate limits.
18. Provide unit tests.
19. Provide example configuration files.
20. Provide documentation and usage examples.

⸻

2. Recommended Project Structure

Create the following repository structure:

jira-data-platform/
│
├── README.md
├── requirements.txt
├── pyproject.toml
├── .gitignore
├── .env.example
│
├── config/
│   ├── config.yaml
│   └── jira_fields.yaml
│
├── src/
│   ├── __init__.py
│   │
│   ├── handlers/
│   │   ├── __init__.py
│   │   └── jira_handler.py
│   │
│   ├── clients/
│   │   ├── __init__.py
│   │   ├── jira_client.py
│   │   └── google_sheets_client.py
│   │
│   ├── transformers/
│   │   ├── __init__.py
│   │   └── jira_transformer.py
│   │
│   ├── writers/
│   │   ├── __init__.py
│   │   ├── csv_writer.py
│   │   └── google_sheet_writer.py
│   │
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── config_loader.py
│   │   ├── logger.py
│   │   └── date_utils.py
│   │
│   └── pipeline/
│       ├── __init__.py
│       └── jira_pipeline.py
│
├── scripts/
│   ├── extract_jira.py
│   └── incremental_jira_load.py
│
├── data/
│   ├── raw/
│   ├── processed/
│   └── output/
│
├── logs/
│
└── tests/
    ├── __init__.py
    ├── test_jira_handler.py
    ├── test_jira_transformer.py
    ├── test_csv_writer.py
    └── test_pipeline.py

⸻

3. Main Jira Handler

The most important component must be:

src/handlers/jira_handler.py

Create a class:

class JiraHandler:
    ...

This class should provide all major Jira extraction functions.

Example interface:

class JiraHandler:
    def __init__(
        self,
        base_url: str,
        username: str | None = None,
        api_token: str | None = None,
        auth_type: str = "basic",
        timeout: int = 30
    ):
        pass
    def test_connection(self):
        pass
    def get_projects(self):
        pass
    def get_project(self, project_key):
        pass
    def search_issues(
        self,
        jql: str,
        fields: list[str] | None = None,
        start_at: int = 0,
        max_results: int = 100
    ):
        pass
    def get_all_issues(
        self,
        jql: str,
        fields: list[str] | None = None
    ):
        pass
    def get_issue(self, issue_key):
        pass
    def get_issue_comments(self, issue_key):
        pass
    def get_issue_worklogs(self, issue_key):
        pass
    def get_issue_changelog(self, issue_key):
        pass
    def get_sprints(self, board_id):
        pass
    def get_users(self):
        pass
    def extract_issues(
        self,
        jql: str,
        fields: list[str] | None = None
    ):
        pass
    def extract_to_dataframe(
        self,
        jql: str,
        fields: list[str] | None = None
    ):
        pass

The handler should hide Jira API complexity from the rest of the application.

⸻

4. Standard Jira Data Model

Create a standardized issue-level schema.

At minimum include:

issue_id
issue_key
project_id
project_key
project_name
summary
description
issue_type
issue_type_id
status
status_id
status_category
priority
priority_id
resolution
resolution_date
reporter
reporter_account_id
assignee
assignee_account_id
creator
creator_account_id
created
updated
due_date
labels
components
fix_versions
affected_versions
parent_issue_key
epic_key
sprint
story_points
environment
comment_count
attachment_count
url
ingestion_timestamp

The implementation should gracefully handle Jira fields that don’t exist.

Do NOT fail because a custom Jira field is missing.

⸻

5. Custom Jira Fields

Support custom fields.

The configuration should allow mappings such as:

custom_fields:
  customfield_10001:
    name: story_points
  customfield_10002:
    name: business_value
  customfield_10003:
    name: product_owner

The transformer should convert:

customfield_10001

into:

story_points

where configured.

Unknown custom fields should optionally be retained.

⸻

6. Jira REST API

Use the Jira REST API rather than browser automation.

Use a clean HTTP client abstraction.

Preferred libraries:

requests
pandas
python-dotenv
PyYAML
tenacity
gspread
google-auth

If appropriate, use the official Jira Python client, but keep the architecture abstract enough that the framework can work directly with REST APIs.

⸻

7. Authentication

Support environment variables.

Example:

JIRA_BASE_URL=https://company.atlassian.net
JIRA_EMAIL=user@company.com
JIRA_API_TOKEN=xxxxxxxx

Never hard-code credentials.

Never store credentials in Git.

Provide:

.env.example

with placeholders.

⸻

8. JQL Support

Allow JQL to be supplied from configuration.

Example:

jira:
  base_url: ${JIRA_BASE_URL}
  jql:
    issues: >
      project = DATA
      ORDER BY updated DESC
  fields:
    - summary
    - description
    - issuetype
    - status
    - priority
    - assignee
    - reporter
    - created
    - updated
    - resolution
    - labels

Also allow runtime JQL:

python scripts/extract_jira.py \
    --jql "project = DATA ORDER BY updated DESC"

⸻

9. Pagination

Jira API responses are paginated.

Implement robust pagination.

The framework should:

1. Start at startAt = 0.
2. Request a configurable page size.
3. Continue until all issues are retrieved.
4. Detect total result count.
5. Handle Jira maximum page sizes.
6. Log progress.

Example log:

Retrieving Jira issues...
Page: 1
Records retrieved: 100
Total retrieved: 100
Page: 2
Records retrieved: 100
Total retrieved: 200
...
Completed.
Total Jira issues retrieved: 2,438

⸻

10. Data Transformation

Create:

src/transformers/jira_transformer.py

with:

class JiraTransformer:
    def transform_issues(self, issues):
        pass
    def flatten_issue(self, issue):
        pass
    def normalize_dates(self, dataframe):
        pass
    def normalize_users(self, dataframe):
        pass
    def normalize_lists(self, dataframe):
        pass
    def normalize_custom_fields(self, dataframe):
        pass

Use Pandas.

The final output should be a clean DataFrame.

Example:

df = jira_handler.extract_to_dataframe(
    jql="project = DATA"
)

⸻

11. CSV Output — DEFAULT

CSV must be the default output format.

Example:

data/output/jira_issues.csv

Create:

src/writers/csv_writer.py

with:

class CSVWriter:
    def write(self, dataframe, filepath):
        pass
    def append(self, dataframe, filepath, key_column="issue_id"):
        pass
    def upsert(self, dataframe, filepath, key_column="issue_id"):
        pass

⸻

12. Append / Upsert Logic

This is critical.

The pipeline must support:

NEW Jira issues
        ↓
Existing CSV
        ↓
Compare issue_id
        ↓
NEW → append
EXISTING → update
        ↓
Final CSV

Do NOT simply append rows blindly.

Default behavior should be:

UPSERT

using:

issue_id

as the primary key.

If issue_id isn’t available, use:

issue_key

as fallback.

Example:

writer.upsert(
    dataframe=df,
    filepath="data/output/jira_issues.csv",
    key_column="issue_id"
)

⸻

13. Incremental Extraction

Support incremental Jira extraction.

Example:

updated >= last_successful_run

Store pipeline metadata:

data/output/metadata.json

Example:

{
    "last_successful_run": "2026-11-10T18:30:00",
    "records_processed": 2450,
    "status": "success"
}

On the next run, dynamically generate JQL:

project = DATA
AND updated >= "2026-11-10 18:30"
ORDER BY updated ASC

Allow configuration:

incremental_load:
  enabled: true
  timestamp_field: updated
  lookback_minutes: 10

The lookback window should protect against records updated around the previous extraction boundary.

⸻

14. Google Sheets Integration

Google Sheets should be an optional destination.

Create:

src/clients/google_sheets_client.py
src/writers/google_sheet_writer.py

Support:

class GoogleSheetWriter:
    def create_sheet(self):
        pass
    def write(self, dataframe):
        pass
    def append(self, dataframe):
        pass
    def upsert(self, dataframe, key_column="issue_id"):
        pass

Configuration:

google_sheets:
  enabled: false
  spreadsheet_id: ""
  worksheet_name: "jira_issues"
  credentials_file: "credentials/service_account.json"

The Google Sheets implementation should:

1. Connect securely.
2. Create worksheet if necessary.
3. Create headers.
4. Append new records.
5. Update existing records.
6. Avoid duplicates.
7. Preserve the standardized schema.

⸻

15. Output Formats

Design the framework around an output abstraction.

Supported destinations:

CSV          ← DEFAULT
Google Sheets
Excel        ← optional future extension
Parquet      ← optional future extension
BigQuery     ← optional future extension

Create an abstract writer interface where appropriate.

For example:

class DataWriter:
    def write(self, dataframe):
        raise NotImplementedError
    def append(self, dataframe):
        raise NotImplementedError
    def upsert(self, dataframe):
        raise NotImplementedError

⸻

16. CLI

Create:

scripts/extract_jira.py

Example:

python scripts/extract_jira.py

Default behavior:

Jira
 ↓
Extract
 ↓
Transform
 ↓
CSV

Support:

python scripts/extract_jira.py \
    --jql "project = DATA"

and:

python scripts/extract_jira.py \
    --output csv

and:

python scripts/extract_jira.py \
    --output google_sheets

and:

python scripts/extract_jira.py \
    --mode incremental

and:

python scripts/extract_jira.py \
    --mode full

⸻

17. Logging

Create centralized logging.

Example:

logs/jira_pipeline.log

Log:

Connection
Authentication
JQL
API requests
Pagination
Records retrieved
Transformation
Duplicates
New records
Updated records
Output location
Execution time
Errors

Example:

2026-11-10 18:30:00 INFO Jira connection successful
2026-11-10 18:30:01 INFO JQL: project = DATA
2026-11-10 18:30:02 INFO Retrieved 100 issues
2026-11-10 18:30:03 INFO Retrieved 100 issues
2026-11-10 18:30:15 INFO Total issues retrieved: 2,450
2026-11-10 18:30:17 INFO Existing CSV records: 2,400
2026-11-10 18:30:17 INFO New records: 35
2026-11-10 18:30:17 INFO Updated records: 15
2026-11-10 18:30:18 INFO CSV successfully updated

⸻

18. Error Handling

Implement robust error handling for:

401 Unauthorized
403 Forbidden
404 Not Found
429 Rate Limit
500 Server Error
502 Bad Gateway
503 Service Unavailable
Network timeout
Connection failure
Invalid JQL
Malformed Jira response
Missing fields
Google authentication failure
Google Sheets API failure
CSV corruption

Use exponential backoff for retryable failures.

Do not retry authentication or invalid JQL errors unnecessarily.

⸻

19. Data Quality Checks

Before writing output, validate:

issue_id is not null
issue_key is not null
created is valid
updated is valid
duplicate issue IDs
duplicate issue keys
unexpected columns
missing required columns

Generate a simple quality summary:

Total records: 2,450
Duplicate issue IDs: 0
Missing issue IDs: 0
Missing issue keys: 0
Invalid dates: 0

⸻

20. Pipeline Orchestration

Create:

src/pipeline/jira_pipeline.py

with:

class JiraPipeline:
    def run_full_load(self):
        pass
    def run_incremental_load(self):
        pass
    def extract(self):
        pass
    def transform(self):
        pass
    def validate(self):
        pass
    def write(self):
        pass
    def run(self):
        pass

The pipeline should follow:

CONFIG
  ↓
AUTHENTICATE
  ↓
JIRA API
  ↓
PAGINATION
  ↓
RAW JSON
  ↓
TRANSFORM
  ↓
DATA QUALITY
  ↓
DEDUPLICATION
  ↓
UPSERT
  ↓
CSV
  ↓
OPTIONAL GOOGLE SHEETS
  ↓
METADATA
  ↓
LOGGING

⸻

21. Configuration-Driven Architecture

Do not hard-code Jira project names, fields, URLs, output locations, or Google Sheet IDs.

Use:

jira:
  base_url: ${JIRA_BASE_URL}
  page_size: 100
  projects:
    - DATA
    - TECH
    - OPS
  fields:
    - summary
    - description
    - status
    - priority
    - assignee
    - reporter
    - created
    - updated
output:
  default_format: csv
  csv:
    path: data/output/jira_issues.csv
google_sheets:
  enabled: false

⸻

22. Multiple Jira Data Products

Design the framework so it can eventually support separate datasets:

jira_issues
jira_comments
jira_worklogs
jira_sprints
jira_projects
jira_users
jira_changelog
jira_attachments

Each should be capable of having its own:

JQL
schema
output
incremental strategy
primary key
destination

For example:

datasets:
  issues:
    enabled: true
    endpoint: issues
    jql: "project = DATA"
    output: jira_issues.csv
    key: issue_id
  worklogs:
    enabled: false
    endpoint: worklogs
    output: jira_worklogs.csv
    key: worklog_id

⸻

23. API Response Preservation

Provide an option to preserve raw Jira API responses.

Example:

data/raw/jira/

Store raw JSON using date-based partitions:

data/raw/jira/
    2026/
        11/
            10/
                jira_issues_20261110_183000.json

Configuration:

raw_data:
  enabled: true

This will make troubleshooting and reprocessing possible without repeatedly calling Jira.

⸻

24. Performance

The framework should be designed for potentially large Jira datasets.

Consider:

Pagination
Batch processing
Streaming where appropriate
Memory-efficient transformations
Incremental extraction
API throttling
Retry/backoff

Avoid loading unnecessarily large datasets into memory when possible.

⸻

25. Testing

Create unit tests using:

pytest

Mock Jira API calls.

Tests should cover:

Successful authentication
Failed authentication
Pagination
JQL extraction
Empty result
Nested Jira fields
Custom fields
Missing fields
Date conversion
Duplicate issues
CSV creation
CSV append
CSV upsert
Incremental extraction
API retry
Rate limiting
Google Sheets writer

No tests should require a live Jira environment.

⸻

26. README

Create comprehensive documentation covering:

Installation

pip install -r requirements.txt

Configuration

Explain .env.

Jira authentication

Explain API token configuration.

Running full extraction

python scripts/extract_jira.py --mode full

Running incremental extraction

python scripts/extract_jira.py --mode incremental

Running custom JQL

python scripts/extract_jira.py \
    --jql "project = DATA AND status = 'In Progress'"

CSV output

Explain the default CSV behavior.

Google Sheets

Explain how to enable it.

Adding custom Jira fields

Explain jira_fields.yaml.

Adding another Jira endpoint

Explain how to extend JiraHandler.

⸻

27. Coding Standards

Use:

Python 3.11+
PEP8
Type hints
Docstrings
Dataclasses where useful
Clear separation of concerns
Dependency injection where appropriate
Environment variables for secrets
Structured logging
Exception classes
pytest

Avoid:

Hard-coded credentials
Hard-coded project IDs
Hard-coded file paths
Large monolithic scripts
Duplicated API logic
Silent failures

⸻

28. Deliverables

Generate the complete working repository.

The final implementation must include:

jira_handler.py
jira_client.py
jira_transformer.py
csv_writer.py
google_sheets_client.py
google_sheet_writer.py
jira_pipeline.py
configuration
CLI
logging
tests
README
requirements.txt
.env.example
sample YAML configuration

The framework must work out of the box in CSV mode without requiring Google Sheets.

Google Sheets should be an optional extension.

⸻

29. Example Usage

The preferred developer experience should be as simple as:

from src.handlers.jira_handler import JiraHandler
jira = JiraHandler()
df = jira.extract_to_dataframe(
    jql="project = DATA ORDER BY updated DESC"
)
print(df.head())

Then:

from src.writers.csv_writer import CSVWriter
writer = CSVWriter()
writer.upsert(
    df,
    "data/output/jira_issues.csv",
    key_column="issue_id"
)

Or through the pipeline:

from src.pipeline.jira_pipeline import JiraPipeline
pipeline = JiraPipeline()
pipeline.run_incremental_load()

⸻

30. Important Architectural Principle

Keep the responsibilities separate:

JiraHandler
    ↓
Jira API interaction
JiraTransformer
    ↓
JSON → standardized DataFrame
CSVWriter
    ↓
CSV persistence
GoogleSheetWriter
    ↓
Google Sheets persistence
JiraPipeline
    ↓
Orchestration
Configuration
    ↓
Runtime behavior

Do not put CSV or Google Sheets logic inside jira_handler.py.

jira_handler.py should focus on Jira.

⸻

31. Future Extensibility

Design the framework so the same architecture can eventually support:

Jira
 ↓
Data Lake
 ↓
BigQuery
 ↓
Power BI
 ↓
Data Products
 ↓
AI / ML

Potential future capabilities:

Jira → BigQuery
Jira → Snowflake
Jira → Databricks
Jira → Google Cloud Storage
Jira → Power BI
Jira → Data Quality Dashboard
Jira → AI Agent

The current implementation should NOT require these features, but the architecture should make them easy to add later.

⸻

Final Acceptance Criteria

The project is complete only when the following works:

Test 1 — Full CSV extraction

Jira REST API
→ extract issues
→ transform
→ CSV

Test 2 — Second execution

Jira REST API
→ extract updated issues
→ compare existing CSV
→ append new records
→ update changed records
→ no duplicates

Test 3 — Incremental execution

Previous successful timestamp
→ generate incremental JQL
→ extract changed issues
→ upsert CSV
→ update metadata

Test 4 — Google Sheets

Jira
→ DataFrame
→ Google Sheets
→ append/upsert

Test 5 — Failure recovery

Simulate:

API timeout
429
500
invalid JQL
authentication failure

and verify that the framework handles each appropriately.

The final result should be a clean, reusable Jira data ingestion library, not simply a script that downloads Jira data.

32. Pipeline Metadata and Enterprise Logging

Extend the Jira ingestion framework with a robust pipeline metadata and logging framework.

The objective is to provide complete operational visibility into every pipeline execution.

The framework must answer:

* When did the pipeline start?
* When did it finish?
* What Jira source was queried?
* What JQL was executed?
* How many records were extracted?
* How many records were inserted?
* How many records were updated?
* How many duplicates were detected?
* How many records failed validation?
* Where was the output written?
* Did Google Sheets succeed?
* Did the pipeline succeed or fail?
* What errors or warnings occurred?
* How long did each stage take?
* Which pipeline version/code version executed?

⸻

33. Logging Architecture

Create a centralized logging module:

src/utils/logger.py

Implement a reusable logger:

class PipelineLogger:
    ...

The logging framework must support the following severity levels:

DEBUG
INFO
WARNING
ERROR
CRITICAL

The user specifically wants operational logging at:

DEBUG
WARNING
NORMAL / INFO
ERROR

Map “normal” operational messages to the standard Python:

INFO

⸻

34. Log Levels

DEBUG

Use DEBUG for detailed technical information useful during development and troubleshooting.

Examples:

DEBUG - Jira API request URL
DEBUG - Jira API request parameters
DEBUG - Request page size: 100
DEBUG - Pagination start_at=200
DEBUG - Fields requested
DEBUG - Processing issue ABC-123
DEBUG - Transforming customfield_10001
DEBUG - Existing CSV loaded with 2,450 rows
DEBUG - Comparing issue IDs

DEBUG should be configurable and should normally be disabled in production.

⸻

INFO

Use INFO for normal pipeline operations.

Examples:

INFO - Pipeline started
INFO - Jira authentication successful
INFO - Jira connection successful
INFO - Executing JQL
INFO - Retrieved 100 Jira issues
INFO - Total issues retrieved: 2,450
INFO - Transformation completed
INFO - Data quality validation completed
INFO - New records: 35
INFO - Updated records: 15
INFO - CSV successfully written
INFO - Google Sheets successfully updated
INFO - Pipeline completed successfully

⸻

WARNING

Use WARNING when the pipeline can continue but something requires attention.

Examples:

WARNING - Jira field 'story_points' not found
WARNING - 12 issues have missing assignees
WARNING - 5 duplicate issue keys detected
WARNING - Google Sheets destination disabled
WARNING - API rate limit approaching
WARNING - 3 records failed optional field validation
WARNING - Incremental metadata unavailable; falling back to full extraction

Warnings must not automatically cause the pipeline to fail.

⸻

ERROR

Use ERROR for failures affecting a component or operation.

Examples:

ERROR - Jira API request failed
ERROR - Authentication failed
ERROR - Invalid JQL
ERROR - CSV write failed
ERROR - Google Sheets authentication failed
ERROR - Data transformation failed
ERROR - Pipeline metadata could not be written

Errors should be captured in pipeline metadata.

⸻

CRITICAL

Use CRITICAL for failures that terminate the entire pipeline.

Examples:

CRITICAL - Unable to connect to Jira
CRITICAL - Required configuration missing
CRITICAL - Unable to initialize pipeline
CRITICAL - Pipeline terminated unexpectedly

⸻

35. Log File Structure

Create:

logs/

with separate operational logs.

Recommended structure:

logs/
├── jira_pipeline.log
├── jira_pipeline_error.log
├── jira_pipeline_debug.log
└── archive/

However, prefer a configurable approach.

Example:

logging:
  enabled: true
  level: INFO
  directory: logs
  files:
    application: jira_pipeline.log
    error: jira_pipeline_error.log
    debug: jira_pipeline_debug.log
  rotation:
    enabled: true
    max_bytes: 10485760
    backup_count: 10

Use Python’s logging module and RotatingFileHandler or TimedRotatingFileHandler.

Do not allow log files to grow indefinitely.

⸻

36. Console Logging

Also support console output.

Example:

2026-11-10 19:30:01 INFO     Pipeline started
2026-11-10 19:30:02 INFO     Jira connection successful
2026-11-10 19:30:03 INFO     Extracting Jira issues
2026-11-10 19:30:08 INFO     Retrieved 2,450 records
2026-11-10 19:30:10 INFO     Transformation completed
2026-11-10 19:30:12 INFO     CSV upsert completed
2026-11-10 19:30:13 INFO     Pipeline completed successfully

Allow console logging level to be configured independently from file logging.

⸻

37. Structured Logging

Where practical, use structured logging.

Each log event should contain:

timestamp
log_level
pipeline_name
pipeline_run_id
module
function
message
duration
record_count

Example JSON log:

{
  "timestamp": "2026-11-10T19:30:10Z",
  "level": "INFO",
  "pipeline_name": "jira_issue_ingestion",
  "pipeline_run_id": "20261110_193001_a82f",
  "module": "jira_handler",
  "function": "get_all_issues",
  "message": "Jira extraction completed",
  "record_count": 2450
}

Provide an option:

logging:
  format: text

or:

logging:
  format: json

Default:

text

⸻

38. Pipeline Run ID

Every execution must receive a unique:

pipeline_run_id

Example:

20261110_193001_a82f

This ID must appear in:

1. Log entries
2. Metadata
3. Error records
4. Audit information

This allows a complete execution to be traced across all components.

⸻

39. Pipeline Metadata

Create a dedicated metadata module:

src/utils/pipeline_metadata.py

Create:

class PipelineMetadata:
    ...

The metadata system should capture one record per pipeline execution.

⸻

40. Metadata Storage

Store metadata separately from the business data.

Recommended:

data/
├── output/
│   └── jira_issues.csv
│
└── metadata/
    ├── pipeline_runs.csv
    ├── pipeline_runs.json
    └── latest_run.json

The primary metadata file should be append-only:

data/metadata/pipeline_runs.csv

Every pipeline execution adds one record.

Do NOT overwrite historical pipeline metadata.

⸻

41. Pipeline Metadata Schema

At minimum capture:

pipeline_run_id
pipeline_name
pipeline_version
execution_mode
full_or_incremental
start_timestamp
end_timestamp
duration_seconds
status
jira_base_url
jira_project
jql
records_extracted
records_transformed
records_validated
records_inserted
records_updated
records_skipped
records_failed
records_duplicates
source_system
source_endpoint
output_format
output_location
google_sheets_enabled
google_sheets_status
last_successful_run
current_watermark
warnings_count
errors_count
retry_count
data_quality_status
configuration_version
python_version
host
environment
error_message
created_timestamp

⸻

42. Example Metadata Record

Example:

{
  "pipeline_run_id": "20261110_193001_a82f",
  "pipeline_name": "jira_issue_ingestion",
  "pipeline_version": "1.0.0",
  "execution_mode": "incremental",
  "start_timestamp": "2026-11-10T19:30:01Z",
  "end_timestamp": "2026-11-10T19:30:13Z",
  "duration_seconds": 12.4,
  "status": "SUCCESS",
  "source_system": "JIRA",
  "source_endpoint": "/rest/api/3/search",
  "jira_project": "DATA",
  "records_extracted": 125,
  "records_transformed": 125,
  "records_validated": 125,
  "records_inserted": 35,
  "records_updated": 15,
  "records_skipped": 75,
  "records_failed": 0,
  "records_duplicates": 0,
  "output_format": "CSV",
  "output_location": "data/output/jira_issues.csv",
  "google_sheets_enabled": true,
  "google_sheets_status": "SUCCESS",
  "warnings_count": 2,
  "errors_count": 0,
  "retry_count": 1,
  "data_quality_status": "PASS",
  "last_successful_run": "2026-11-10T19:20:00Z",
  "current_watermark": "2026-11-10T19:29:58Z",
  "environment": "DEV",
  "status_message": "Pipeline completed successfully"
}

⸻

43. Pipeline Execution Lifecycle

Every pipeline execution must follow:

START
  ↓
Generate pipeline_run_id
  ↓
Initialize logging
  ↓
Create metadata record
  ↓
Validate configuration
  ↓
Connect to Jira
  ↓
Extract
  ↓
Transform
  ↓
Validate
  ↓
Deduplicate
  ↓
Upsert
  ↓
Google Sheets
  ↓
Update metadata
  ↓
Mark SUCCESS
  ↓
END

If an error occurs:

ERROR
  ↓
Capture exception
  ↓
Write ERROR log
  ↓
Update metadata
  ↓
Mark FAILED
  ↓
END

⸻

44. Stage-Level Metadata

Capture execution timing for each stage.

Example:

{
  "extraction": {
    "status": "SUCCESS",
    "duration_seconds": 8.2,
    "records": 2450
  },
  "transformation": {
    "status": "SUCCESS",
    "duration_seconds": 1.4,
    "records": 2450
  },
  "validation": {
    "status": "SUCCESS",
    "duration_seconds": 0.7,
    "records": 2450
  },
  "csv_upsert": {
    "status": "SUCCESS",
    "duration_seconds": 1.1,
    "inserted": 35,
    "updated": 15
  },
  "google_sheets": {
    "status": "SUCCESS",
    "duration_seconds": 1.8
  }
}

This will make it possible to identify pipeline bottlenecks.

⸻

45. Data Quality Metadata

Capture data-quality metrics for every execution.

Example:

total_records
valid_records
invalid_records
missing_issue_id
missing_issue_key
missing_summary
duplicate_issue_id
duplicate_issue_key
invalid_created_dates
invalid_updated_dates
missing_assignee
missing_reporter
missing_status

Example:

{
  "data_quality": {
    "status": "PASS",
    "total_records": 2450,
    "valid_records": 2447,
    "invalid_records": 3,
    "duplicate_issue_id": 0,
    "missing_issue_key": 0,
    "missing_assignee": 125
  }
}

⸻

46. Metadata Writer

Create:

src/writers/metadata_writer.py

Implement:

class MetadataWriter:
    def create_run(self):
        pass
    def update_run(self):
        pass
    def complete_run(self):
        pass
    def fail_run(self):
        pass
    def append_run(self):
        pass
    def get_last_successful_run(self):
        pass

The metadata writer must be resilient.

A failure to write metadata should be logged, but should not hide the original pipeline error.

⸻

47. Context-Aware Logging

Pass the pipeline_run_id through all major components.

Example:

logger.info(
    "Jira extraction completed",
    extra={
        "pipeline_run_id": pipeline_run_id,
        "records": len(df)
    }
)

All modules should be able to associate their logs with the current pipeline execution.

⸻

48. Exception Logging

Never silently catch exceptions.

Bad:

try:
    ...
except Exception:
    pass

Instead:

try:
    ...
except Exception as exc:
    logger.exception(
        "Jira extraction failed",
        extra={
            "pipeline_run_id": pipeline_run_id
        }
    )
    raise

Capture:

exception type
exception message
stack trace
module
function
pipeline_run_id
timestamp

⸻

49. Error Metadata

When a pipeline fails, record:

{
  "status": "FAILED",
  "error": {
    "type": "JiraAPIError",
    "message": "Unable to retrieve Jira issues",
    "module": "jira_handler",
    "function": "get_all_issues"
  }
}

Do not expose API tokens, passwords, OAuth secrets, or other credentials in logs or metadata.

⸻

50. Log Redaction

Implement a security-aware logging filter.

Never log:

API tokens
Passwords
Authorization headers
OAuth tokens
Service account private keys
Client secrets
Cookies

If these appear accidentally in an exception or response, redact them.

Example:

JIRA_API_TOKEN=********
Authorization=********

⸻

51. Log Retention

Make retention configurable.

Example:

logging:
  retention:
    enabled: true
    days: 30
  rotation:
    enabled: true
    max_bytes: 10485760
    backup_count: 10

Pipeline metadata should have a longer retention period than operational logs.

Example:

metadata:
  retention_days: 365

⸻

52. Pipeline Summary

At the end of every execution, output a concise summary.

Example:

==================================================
JIRA PIPELINE SUMMARY
==================================================
Run ID:              20261110_193001_a82f
Pipeline:            jira_issue_ingestion
Mode:                Incremental
Status:              SUCCESS
Start:               19:30:01
End:                 19:30:13
Duration:            12.4 seconds
Records Extracted:   125
Records Inserted:    35
Records Updated:     15
Records Skipped:     75
Records Failed:      0
Duplicates:          0
Data Quality:        PASS
CSV:                 SUCCESS
Google Sheets:       SUCCESS
Warnings:             2
Errors:               0
Retries:              1
==================================================

⸻

53. Metadata as an Analytics Dataset

Design the metadata so it can eventually be loaded into:

BigQuery
Power BI
Looker
Tableau

This will allow creation of a Pipeline Operations Dashboard.

Potential dashboard KPIs:

Pipeline Success Rate
Pipeline Failure Rate
Average Runtime
Records Processed
Records Inserted
Records Updated
Records Failed
Data Quality Score
API Retry Count
API Error Count
Warning Count
Daily Pipeline Runs

Potential visuals:

Pipeline execution trend
Runtime trend
Records processed trend
Success/failure trend
Error distribution
Warnings by category
Pipeline stage duration
Data quality trend

⸻

54. Final Architecture

The final architecture should be:

                    ┌──────────────────────┐
                    │       CONFIG         │
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │   PIPELINE RUN ID    │
                    └──────────┬───────────┘
                               │
                ┌──────────────┴──────────────┐
                │                             │
                ▼                             ▼
        ┌───────────────┐             ┌──────────────┐
        │    LOGGER     │             │   METADATA   │
        │ DEBUG/INFO/   │             │   TRACKER    │
        │ WARN/ERROR    │             │              │
        └───────┬───────┘             └──────┬───────┘
                │                            │
                └──────────────┬─────────────┘
                               ▼
                    ┌──────────────────────┐
                    │    JIRA HANDLER      │
                    │      REST API        │
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │    TRANSFORMER       │
                    │ JSON → DataFrame     │
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │    DATA QUALITY       │
                    │     VALIDATION       │
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │    DEDUP / UPSERT    │
                    └──────────┬───────────┘
                               │
                    ┌──────────┴───────────┐
                    ▼                      ▼
             ┌─────────────┐       ┌──────────────┐
             │    CSV      │       │ GOOGLE SHEETS│
             │   DEFAULT   │       │   OPTIONAL   │
             └─────────────┘       └──────────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │   PIPELINE METADATA  │
                    │   + RUN AUDIT TRAIL   │
                    └──────────────────────┘

⸻

55. Acceptance Criteria for Logging and Metadata

The implementation is complete only when:

Logging

* DEBUG logging works.
* INFO/normal logging works.
* WARNING logging works.
* ERROR logging works.
* CRITICAL logging works.
* Console logging works.
* File logging works.
* Log rotation works.
* Log retention is configurable.
* Exceptions include stack traces.
* Secrets are never logged.

Metadata

* Every pipeline run receives a unique run ID.
* Every execution creates metadata.
* Metadata is append-only.
* Full and incremental runs are identified.
* Start/end times are captured.
* Runtime is calculated.
* Record counts are captured.
* Insert/update counts are captured.
* Errors and warnings are captured.
* Data-quality results are captured.
* Output status is captured.
* Google Sheets status is captured.
* Last successful watermark is maintained.
* Failed runs remain available for historical analysis.

Recovery

If the pipeline fails:

Pipeline
   ↓
ERROR
   ↓
Log exception
   ↓
Update metadata = FAILED
   ↓
Preserve last successful watermark

The pipeline must never advance the incremental watermark when the pipeline execution fails.

This is critical for reliable incremental ingestion.

Also save the prompt and everything
