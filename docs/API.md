# StarIndex HTTP API

Base URL `http://127.0.0.1:8765/api`. All bodies are JSON; models are defined in
`backend/starindex/api/schemas.py` (authoritative) and exposed at `/api/openapi.json`.
Errors are always `{"error": {"kind": str, "message": str, "detail": {...}}}` with a 4xx/5xx status.
State-changing requests from a browser must be same-origin (the server rejects foreign `Origin`s).

Streaming endpoints (marked SSE) respond with `text/event-stream`; each message is
`event: <type>\ndata: <json>\n\n`. Event types: `status`, `plan`, `step`, `evidence`, `delta`, `done`, `error`
(payloads documented beside `ProviderErrorInfo` in `schemas.py`).

## System
| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/health` | | `{status, version}` |
| GET | `/system/status` | | `SystemStatus` |
| GET | `/system/diagnostics` | | `Diagnostics` |
| GET | `/system/hardware` | | `HardwareInfo` |
| GET | `/settings` | | `AppSettings` |
| PATCH | `/settings` | `SettingsPatch` | `AppSettings` |
| GET | `/storage` | | `StorageInfo` |
| GET | `/storage/backups` | | `BackupInfo[]` |
| POST | `/storage/backups` | | `Job` (backup job) |
| POST | `/storage/restore` | `RestoreRequest` | `Ok` |
| GET | `/export?format=json\|csv` | | file download of the library + annotations |

## GitHub
| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/github/auth` | | `GitHubAuthStatus` |
| PUT | `/github/auth` | `GitHubAuthUpdate` | `GitHubAuthStatus` |
| DELETE | `/github/auth/token` | | `GitHubAuthStatus` |

## Library
| Method | Path | Request | Response |
|---|---|---|---|
| POST | `/repos/list` | `RepoListRequest` | `RepoPage` |
| POST | `/repos/facets` | `RepoListRequest` (filters+text used) | `Facets` |
| GET | `/repos/by-name/{owner}/{name}` | | `RepoDetail` |
| GET | `/repos/{id}` | | `RepoDetail` |
| GET | `/repos/{id}/readme` | | `DocumentContent` (404 if not indexed) |
| GET | `/repos/{id}/documents` | | `DocumentInfo[]` |
| GET | `/repos/{id}/documents/{doc_id}` | | `DocumentContent` |
| GET | `/repos/{id}/dependencies` | | `Dependency[]` |
| GET | `/repos/{id}/related?limit=12` | | `RelatedRepo[]` |
| PATCH | `/repos/{id}/annotation` | `AnnotationPatch` | `RepoDetail` |
| PUT | `/repos/{id}/tags` | `TagsUpdate` | `RepoDetail` |
| PUT | `/repos/{id}/categories` | `CategoriesUpdate` | `RepoDetail` |
| POST | `/repos/{id}/opened` | | `Ok` |
| POST | `/repos/{id}/index` | `IndexRequest` | `Job` |
| POST | `/repos/index-batch` | `BatchIndexRequest` | `BatchIndexResponse` |
| GET | `/repos/{id}/summary?kind=summary\|usage_ideas` | | `RepoSummaryText` (content null if never generated) |
| POST | `/repos/{id}/summary?kind=…` | `AskRequest`-like `{route?, confirm_cloud}` | SSE → `done {summary: RepoSummaryText}` |
| GET | `/library/overview` | | `LibraryOverview` |
| GET | `/library/duplicates` | | `DuplicateGroup[]` |

## Search
| Method | Path | Request | Response |
|---|---|---|---|
| POST | `/search` | `SearchRequest` | `SearchResponse` |
| GET | `/search/suggest?q=` | | `Suggestion[]` |
| GET | `/search/history?limit=50` | | `SearchHistoryEntry[]` |
| DELETE | `/search/history` | | `Ok` |
| GET | `/saved-searches` | | `SavedSearch[]` |
| POST | `/saved-searches` | `SavedSearchCreate` | `SavedSearch` |
| DELETE | `/saved-searches/{id}` | | `Ok` |

Search query operators (parsed server-side, reported in `SearchResponse.parsed.operators`):
`lang:python` `topic:mcp` `license:mit` `owner:vercel` `stars:>1000` `stars:100..5000` `pushed:>2025-01-01`
`starred:<2024-06-01` `is:archived|fork|favorite|unstarred` `tag:x` `category:slug` `collection:"Name"`
`in:name|description|readme|docs|code` `"exact phrase"` `-excluded` `OR`.

## Ask, Research, Compare, Reports
| Method | Path | Request | Response |
|---|---|---|---|
| POST | `/ask` | `AskRequest` | SSE (`status`,`evidence`,`delta`,`done`,`error`) |
| POST | `/research` | `ResearchRequest` | SSE (`status`,`plan`,`step`,`evidence`,`delta`,`done`,`error`) |
| POST | `/research/{report_id}/cancel` | | `Ok` |
| POST | `/compare` | `CompareRequest` | `Comparison` (deterministic, no model needed) |
| POST | `/compare/save` | `CompareRequest` | `Report` (kind `compare`, saved) |
| POST | `/compare/narrative` | `CompareNarrativeRequest` | SSE → `done {report: Report}` |
| GET | `/reports?kind=&saved=&limit=` | | `ReportSummary[]` |
| GET | `/reports/{id}` | | `Report` |
| PATCH | `/reports/{id}` | `ReportPatch` | `Report` |
| DELETE | `/reports/{id}` | | `Ok` |
| GET | `/reports/{id}/export` | | Markdown download |
| GET | `/evidence/chunk/{chunk_id}` | | `EvidenceDetail` |
| GET | `/model-options` | | `{active: LlmStatus, options: ProviderOption[], cloud_allowed, confirm_before_cloud}` |

## Collections, tags, categories
| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/collections` | | `Collection[]` |
| POST | `/collections` | `CollectionCreate` | `Collection` |
| GET | `/collections/{id}` | | `CollectionDetail` |
| PATCH | `/collections/{id}` | `CollectionPatch` | `Collection` |
| DELETE | `/collections/{id}` | | `Ok` |
| POST | `/collections/{id}/repos` | `RepoIds` | `Collection` |
| POST | `/collections/{id}/repos/remove` | `RepoIds` | `Collection` |
| GET | `/tags` | | `Tag[]` |
| PATCH | `/tags/{name}` | `TagRename` | `Tag[]` (renames; merges into an existing tag of that name) |
| DELETE | `/tags/{name}` | | `Tag[]` |
| GET | `/categories` | | `Category[]` |
| POST | `/categories` | `CategoryCreate` | `Category` |
| PATCH | `/categories/{id}` | `CategoryPatch` | `Category` |
| DELETE | `/categories/{id}` | | `Ok` |

## Sync and jobs
| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/sync/status` | | `SyncStatus` (poll every ~2 s while active) |
| POST | `/sync/start` | `SyncStartRequest` | `Job` |
| POST | `/sync/cancel` | | `Ok` |
| GET | `/sync/runs?limit=20` | | `SyncRun[]` |
| GET | `/jobs?status=&type=&limit=&offset=` | | `JobPage` |
| POST | `/jobs` | `JobEnqueue` | `Job` |
| POST | `/jobs/{id}/retry` | | `Job` |
| POST | `/jobs/{id}/cancel` | | `Job` |
| POST | `/jobs/retry-failed` | | `Ok` |
| POST | `/jobs/clear-finished` | | `Ok` |
| GET | `/schedules` | | `Schedule[]` |
| PATCH | `/schedules/{name}` | `SchedulePatch` | `Schedule` |

## Model providers
| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/providers/templates` | | `ProviderTemplate[]` |
| GET | `/providers` | | `ProviderInfo[]` |
| POST | `/providers` | `ProviderCreate` | `ProviderInfo` |
| PATCH | `/providers/{id}` | `ProviderPatch` | `ProviderInfo` |
| DELETE | `/providers/{id}` | | `Ok` |
| PUT | `/providers/{id}/secret` | `SecretUpdate` | `ProviderInfo` |
| DELETE | `/providers/{id}/secret` | | `ProviderInfo` |
| POST | `/providers/{id}/test` | `ProviderTestRequest` | `ProviderTestResult` |
| GET | `/providers/{id}/models` | | `ProviderModels` |
| GET | `/routing` | | `RoutingInfo` |
| PUT | `/routing` | `RoutingUpdate` | `RoutingInfo` |
| GET | `/usage?days=30` | | `UsageSummary` |

## ChatGPT plan (Sign in with ChatGPT)
| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/chatgpt/status` | | `ChatGPTStatus` |
| POST | `/chatgpt/login` | `ChatGPTLoginStart` | `ChatGPTLoginAttempt` (open `auth_url` in the system browser) |
| GET | `/chatgpt/login/{attempt_id}` | | `ChatGPTLoginAttempt` (poll) |
| POST | `/chatgpt/accounts/{id}/activate` | | `ChatGPTStatus` |
| POST | `/chatgpt/accounts/{id}/sign-out` | | `ChatGPTStatus` |
| DELETE | `/chatgpt/accounts/{id}` | | `ChatGPTStatus` |
| POST | `/chatgpt/accounts/{id}/welcome-shown` | | `Ok` |

## Relevance feedback and personal evaluation
| Method | Path | Request | Response |
|---|---|---|---|
| POST | `/search/feedback` | `SearchFeedbackCreate` | `SearchFeedback` (upsert per query + repository; verdicts `relevant`, `not_relevant`, `missing`) |
| GET | `/search/feedback?query=` | | `SearchFeedback[]` (verdicts for that query; without `query`, the most recent) |
| GET | `/search/feedback/queries` | | `FeedbackQuery[]` (the personal evaluation set, grouped by query) |
| DELETE | `/search/feedback/{id}` | | `Ok` |
| GET | `/search/feedback/export` | | JSON download in the shape of `eval/queries.yaml` |
| GET | `/eval/status` | | `EvalStatus` (counts, whether a run is in progress, latest `EvalRun`) |
| POST | `/eval/run` | | `Job` (`evaluate_search`); poll `/eval/status` until `running` is false |
| POST | `/eval/apply` | | `AppSettings` (applies `latest.suggested_settings`; 409 when there is no suggestion) |

## Tools for other agents (MCP)
| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/tools` | | `ToolInfo[]` |
| POST | `/tools/{name}` | `ToolCall` | `ToolCallResult` |
| GET | `/mcp/info` | | `McpInfo` (connection commands and config to copy) |
| POST | `/mcp` (not under `/api`) | JSON-RPC 2.0 | MCP Streamable HTTP endpoint (JSON responses) |
