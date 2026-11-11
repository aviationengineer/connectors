# Data Dictionary

Standardised output schema produced by `JiraTransformer` (38 columns).
Column order in CSV / Sheets output follows this table; mapped custom
fields are appended after the standard columns.

Missing fields in Jira become empty values — never errors. Dates are
normalised to UTC (`YYYY-MM-DDTHH:MM:SSZ`); list values are joined with
`;`.

## Standard columns

| Column | Type | Source Jira field | Notes |
| --- | --- | --- | --- |
| `issue_id` | string | `id` | Primary key for upserts. |
| `issue_key` | string | `key` | e.g. `DATA-123`. Fallback upsert key. |
| `project_id` | string | `fields.project.id` | |
| `project_key` | string | `fields.project.key` | e.g. `DATA`. |
| `project_name` | string | `fields.project.name` | |
| `summary` | string | `fields.summary` | Required; missing counts against data quality. |
| `description` | string | `fields.description` | ADF (Atlassian Document Format) flattened to plain text; plain strings passed through. |
| `issue_type` | string | `fields.issuetype.name` | e.g. Task, Story, Bug, Epic. |
| `issue_type_id` | string | `fields.issuetype.id` | |
| `status` | string | `fields.status.name` | e.g. To Do, In Progress, Done. |
| `status_id` | string | `fields.status.id` | |
| `status_category` | string | `fields.status.statusCategory.name` | e.g. To Do, In Progress, Done. |
| `priority` | string | `fields.priority.name` | e.g. Highest, High, Medium, Low. Empty when unset. |
| `priority_id` | string | `fields.priority.id` | |
| `resolution` | string | `fields.resolution.name` | Empty for unresolved issues. |
| `resolution_date` | datetime (UTC) | `fields.resolutiondate` | |
| `reporter` | string | `fields.reporter.displayName` | |
| `reporter_account_id` | string | `fields.reporter.accountId` | |
| `assignee` | string | `fields.assignee.displayName` | Empty when unassigned. |
| `assignee_account_id` | string | `fields.assignee.accountId` | |
| `creator` | string | `fields.creator.displayName` | |
| `creator_account_id` | string | `fields.creator.accountId` | |
| `created` | datetime (UTC) | `fields.created` | |
| `updated` | datetime (UTC) | `fields.updated` | Timestamp field used by the incremental watermark. |
| `due_date` | datetime (UTC) | `fields.duedate` | |
| `labels` | string | `fields.labels` | Joined with `;`. |
| `components` | string | `fields.components[].name` | Joined with `;`. |
| `fix_versions` | string | `fields.fixVersions[].name` | Joined with `;`. |
| `affected_versions` | string | `fields.versions[].name` | Joined with `;`. |
| `parent_issue_key` | string | `fields.parent.key` | Parent issue for sub-tasks. |
| `epic_key` | string | `fields.epic.key`, else epic-link custom field | Epic this issue belongs to. |
| `sprint` | string | `customfield_10020` (or `customfield_10007`) | Sprint name; last sprint when several apply. |
| `story_points` | number | `customfield_10001` | Mapped custom field (see below). |
| `environment` | string | `fields.environment` | |
| `comment_count` | integer | `fields.comment.total` | Number of comments on the issue. |
| `attachment_count` | integer | `fields.attachment` (length) | Number of attachments. |
| `url` | string | built from `base_url` + `key` | e.g. `https://your-company.atlassian.net/browse/DATA-123`. |
| `ingestion_timestamp` | datetime (UTC) | generated at transform time | When this row was ingested. |

## Custom-field mapping

Configured in `config/jira_fields.yaml`. These columns are appended
after the standard columns (except `story_points` and `sprint`, which
are already standard columns).

| Jira field id | Output column | Notes |
| --- | --- | --- |
| `customfield_10001` | `story_points` | Numeric estimate. |
| `customfield_10002` | `business_value` | |
| `customfield_10003` | `product_owner` | |
| `customfield_10020` | `sprint` | Sprint name (standard column). |
| `customfield_10014` | `epic_link` | Epic key for the issue. |

With `retain_unknown_custom_fields: true`, any other `customfield_*`
present in a payload is kept as an extra `custom_<field_id>` column;
otherwise unmapped custom fields are dropped.

## Notes

* A field that does not exist on an issue (or on the Jira site at all)
  becomes an empty value in that row — the pipeline never fails because
  a field is missing.
* `story_points` and other custom-field values are reduced to plain
  scalars where possible (a `{"value": ...}` / `{"name": ...}` object
  becomes that value; a list becomes a `;`-joined string).
* `created`, `updated`, `resolution_date` and `due_date` are parsed to
  UTC datetimes; unparseable values become empty (and are counted by
  the data-quality check).
