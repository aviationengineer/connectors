# LinkedIn Company Pages — test use cases

All use cases run against fictional fixtures (`tests/fixtures/`, Acme Corp /
Globex Corporation) with the HTTP layer mocked. No test touches the network
or needs a LinkedIn account.

| ID | Use case | What is verified | Tests |
|----|----------|------------------|-------|
| UC-01 | Discover administered Pages | `organizationAcls` filtered to admin roles; MEMBER role excluded | `test_linkedin_handler.py::test_test_connection`, `test_get_administered_pages_filters_roles` |
| UC-02 | Fetch a Page's posts with pagination | 3 posts across 2 pages; `max_posts` cap respected | `test_linkedin_handler.py::test_list_all_posts_paginates`, `test_list_all_posts_respects_max_posts` |
| UC-03 | Transform posts to canonical DataFrame | URNs, epoch-ms dates, commentary text, media/article extraction, org name join | `test_linkedin_transformer.py` |
| UC-04 | Enrich engagement | Social Actions likes/comments merged per post | `test_linkedin_transformer.py::test_engagement_enrichment` |
| UC-05 | First run writes CSV | 3 inserted, raw payload preserved, metadata SUCCESS + watermark | `test_pipeline.py::test_full_run_success` |
| UC-06 | Second run upserts | Identical posts skipped (0/0/3); edited post updated in place | `test_pipeline.py::test_second_run_upsert_skips_identical`, `test_changed_post_is_updated` |
| UC-07 | Incremental run | Posts older than the watermark (minus lookback) are filtered out | `test_pipeline.py::test_incremental_run_filters_old_posts` |
| UC-08 | Auth failure | 401 raises, metadata FAILED, watermark untouched, no false SUCCESS | `test_pipeline.py::test_auth_failure_marks_run_failed`, `test_linkedin_client.py::test_permanent_errors_not_retried` |
| UC-09 | Transient failures retry | 429 then success; 500 retried 3x then raises | `test_linkedin_client.py::test_rate_limit_retried_then_succeeds`, `test_server_error_retried_then_raises` |
| UC-10 | Data quality gate | Missing business key warns; all-invalid fails the run | `test_data_quality.py`, `test_pipeline.py::test_data_quality_failure_fails_run` |
| UC-11 | Corrupt CSV recovery | Corrupt file moved aside, fresh write starts clean | `test_csv_writer.py::test_corrupt_csv_moved_aside` |
| UC-12 | Metadata audit | Run history (CSV + JSONL), latest-run watermark, failed runs never move it | `test_metadata.py` |
| UC-13 | OAuth token exchange/refresh | Authorization-code and refresh-token grants | `test_linkedin_client.py::test_exchange_code_for_tokens`, `test_refresh_access_token` |

Run the suite:

```bash
python -m pytest -q
```
