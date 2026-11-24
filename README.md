# Relay — phase 1

React/TypeScript/Vite frontend and Django/DRF backend. Session login, signup, logout, text chat upload/preview/confirmation, owner-scoped chat lists, message viewing and deletion are implemented. Local Qwen3 answers with source messages are implemented. QR linking, exports, password recovery, email verification and ZIP ingestion are not implemented yet.

## Local setup

Run backend commands from `C:\Onesmarter\Wattsapp Relay\backend`. The root contains only `backend` and `frontend`; configuration, local PostgreSQL data, Python environment, samples, and verification scripts live inside `backend`.


1. Python 3.12+: create `.venv` and install `requirements.in`.
2. With PostgreSQL 18 installed on Windows, run `.venv/Scripts/python setup_local.py` to provision an isolated loopback cluster on port 55432 and generate ignored local credentials. Alternatively provision PostgreSQL and set `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` in an ignored backend `.env`.
3. Generate a random `DJANGO_SECRET_KEY` (at least 50 characters). Set `RELAY_LOCAL_DEV=1` for loopback development only. Never deploy with it enabled.
4. Run `.venv/Scripts/python manage.py migrate`, then `manage.py runserver 127.0.0.1:8000` with the same Python executable.
5. Run `npm ci` and `npm run dev -- --host 127.0.0.1 --port 5173` in `frontend`. Open http://127.0.0.1:5173/. Vite proxies `/api` to Django; browser credentials stay in HTTP-only session cookies. Local setup configures CSRF trusted origins for port 5173; keep CSRF enabled.
6. For isolated tests run `manage.py test relay --settings=config.test_settings`. These use SQLite, not a production database; PostgreSQL concurrency/TLS still need validation.

Use explicit DMY/MDY and source timezone when importing. Supports common Android/iOS timestamped UTF-8 text exports and multiline messages. Review preview before confirmation. Select an existing chat when reimporting to deduplicate. Exact identical timestamp/sender/text messages within the same chat collapse; this can merge genuinely identical repeated messages. Unsupported locales need parser fixtures before support is claimed.

## Deployment and security status

- PASS (local, 2026-11-21): frontend TypeScript/Vite production build, Django configuration checks and four isolated security tests. Live Vite-to-Django-to-PostgreSQL verification passed signup, logout, login, upload preview, confirm, saved messages, duplicate reimport and anonymous denial (`verify_live.py`). Browser signup/logout/login and four-message TXT preview/confirmation/message viewing also passed. Synthetic test accounts remain in the local database.
- NOT VERIFIED: PostgreSQL concurrent locking behavior, production HTTPS/proxy, restoration, dependency and secret scans, deployment configuration.
- NOT IMPLEMENTED: breached-password provider, password reset, email verification, privileged MFA. No admin UI exposed. Do not release publicly before these are addressed.
- Private sessions, CSRF, owner-scoped endpoints, bounded TXT imports and no retained raw upload files. Staged parsed content expires in 24 hours: run Celery worker and beat for cleanup (`config` app); configure private Redis. Without the scheduler expired staging data remains stored until cleanup runs.
- Production needs private PostgreSQL/Redis, restrictive security groups, TLS with DB server verification, restricted runtime DB privileges, separate migration credentials, backups, HTTPS and validated HSTS configuration.
- Enforce upload size, API rate/concurrency limits at the reverse proxy. Import requests are synchronous in this first build; large imports need queue integration before scale-up. S3 is not wired into this flow. Local AI uses the separate Ask endpoint.
- Never expose raw chats in logs or serve the model directly to browsers. Source authorization and citation-validation tests cover local Ask; broader adversarial evaluation is still needed before public release.

## Future mobile app

React Native can reuse Django endpoints and the PostgreSQL data model; web DOM components are not directly reusable native screens. Share TypeScript API types and design tokens. Add separately reviewed mobile OAuth/OIDC PKCE or supported token authentication, platform secure storage, token rotation/revocation and mobile upload handling. Keep web cookie/CSRF protection intact. QR sync remains a separate later integration.

## Discover, Explore and Ask

The signed-in workspace uses account-scoped PostgreSQL data. Discover shows actual chat/message/sender-name totals and recent messages. Explore searches text or sender, filters by chat and pages through 30 results at a time. Ask uses local Qwen3 1.7B to answer from retrieved source excerpts. Retrieval uses multilingual semantic embeddings; see the semantic search section below. Chat collections are capped at 100 displayed chats. Search is authenticated, CSRF-protected, bounded and rate-limited; raw message text is rendered as escaped React text. No private chat data is saved in browser local storage.

Validation: production frontend build and five Django tests pass, including cross-user workspace/search isolation, invalid pagination and anonymous denial. Browser checks against local PostgreSQL verified the existing four-message import in Discover, Explore substring search and Ask source lookup. Mobile layout checked at 375px. Production security, concurrency and deployment checks remain pending as above.

## Local Qwen3 answers

Ask now calls `POST /api/ask/` through authenticated Django sessions and CSRF protection. Django retrieves only the caller's messages, optionally constrained to an owned chat, and calls `qwen3:1.7b` at the fixed loopback Ollama endpoint (`127.0.0.1:11434/api/chat`). Start Ollama and ensure `ollama list` contains `qwen3:1.7b`; install with `ollama pull qwen3:1.7b` if needed. No paid AI API or external chat transfer is used by this integration.

Retrieval: multilingual local semantic embeddings, with original message excerpts supplied to Qwen. Citations are assigned by the server after exact source-quote matching against a constrained set of retrieved excerpts; citation existence does not prove every claim is true. Lack of evidence returns an explicit abstention. No tools are given to the answer model; runtime failures are not disguised as answers.

Local limits: 30 generated answers/hour/user and 240 semantic preparation/search requests/hour/user, one active inference per Django process, 120-second socket timeout, 1,000 output tokens, bounded response size, no proxy/redirects to other AI hosts. Keep Ollama on loopback. Multi-worker/AWS deployment needs a shared inference queue and concurrency control before scale-up. No chat history or AI answers are persisted. Model stays warm for five minutes. Current request is a single-question answer, not a multi-turn conversation.

Validation: nine Django tests cover isolation, CSRF/anonymous denial, empty library, invalid citation rejection and outage handling alongside prior import checks. Real local model smoke test returned a supported answer with a source. Frontend build passes. Prompt injection defenses reduce risk but do not guarantee semantic correctness of model output.


## Multilingual semantic search

Ask uses `embeddinggemma:300m-qat-q4_0` locally through Ollama for Hindi, Hinglish and English retrieval, with Qwen3 1.7B still generating answers. Install with `ollama pull embeddinggemma:300m-qat-q4_0`, then apply migrations. Both models must be present; an unavailable embedding model produces an explicit error, never a silent keyword fallback. Explore retains its deliberate literal-text filter.

Messages are split into overlapping chunks of up to 800 characters / 1,200 UTF-8 bytes (120-character overlap). All chunks, including long-message tails, receive normalized 768-dimensional embeddings with the model's documented query/document prompts. The versioned index is stored in PostgreSQL JSON, keyed by message with cascading deletion. It rebuilds when the model digest or chunk/prompt version changes. New imports are indexed on the next Ask request. Existing batches are resumable and never expose a partial-search answer. The UI continues up to 20 preparation batches and then lets you resume; bulk preparation is available through `python manage.py index_semantic --user-id ID`.

Candidates are filtered to the authenticated account and optional owned chat BEFORE ranking. Exact cosine search streams the authorized index and selects the best eight excerpts and fits them into Qwen's bounded prompt budget, without a keyword prefilter. A relative relevance band retains candidates scoring at least 75% of the best match to reduce unrelated context; it is a tunable heuristic, not a calibrated confidence threshold. Excerpt offsets identify the original text span. Similarity is not a probability of correctness. Romanized Hindi spelling and small-model reasoning remain imperfect; verify the sources. For larger deployments replace the bounded exact scan (25,000 chunks per search) with an indexed vector store such as pgvector, preserving authorization filters. Indexing currently runs synchronously in small resumable batches, not a background queue; the first question can be slower. Embeddings and cached excerpts are private data subject to the same backup/retention controls as chats.

Model/prompt references: https://huggingface.co/google/embeddinggemma-300m and https://docs.ollama.com/api/embed . Live multilingual evaluation uses only disposable synthetic records via `verify_semantic.py`; results are written to ignored `.local/semantic-evaluation.json`.


Hindi/Hinglish questions are interpreted locally by Qwen into English; retrieval ranks both original and translated query embeddings. Answers now follow the detected question language: Hindi, Hinglish or English. Localized quantitative summaries copy facts from the cited source text; other replies use protected local translation. Its source-quote output is constrained to actual retrieved text and matched server-side. If a generated answer introduces unsupported numeric values, weekdays or month names, or omits numeric details from its cited evidence, Relay displays exact source excerpts instead. This guard is deliberately conservative and does not verify every possible factual or logical claim; original messages remain the authority. Query translation can also be imperfect.

Local model installation on this machine used the official Ollama QAT Q4 weights, downloaded over IPv4 after an IPv6 redirect failure. Weight SHA-256: `98e0321ff4472646a1f3a1301a051f80388fdb0e8d2e3dc7b814cd734c90b717`. The official license is retained in the imported Ollama model. No model-training or cloud inference is used.

Semantic validation (local, 2026-11-21): frontend production build and 24 backend tests pass. Final live evaluation passed all five synthetic multilingual retrieval/citation/fact cases plus a no-evidence abstention case. One answer used the exact-source fallback; this is not a broad accuracy benchmark. Browser verification on the existing imported chat answered the Hindi presentation-day question with `Friday [1]` and Rahul's original message. The live report is `.local/semantic-evaluation.json`. Earlier evaluation found small-model mistranslation, unsupported dates and omitted details; these motivated local query interpretation, constrained quote citations and the conservative excerpt fallback. Further domain-specific evaluation is still needed for production.


## Same-language answers

Question script and common Romanized-Hindi terms select `hi`, `hinglish` or `en`. Hindi/Hinglish query translation is still used only to improve retrieval. The response includes `answer_language` and a mode (`generated`, `localized`, `localized_facts`, or `source_excerpt`). Source messages, sender names, timestamps, excerpt offsets and citation IDs are never translated or rewritten.

For quantitative questions, same-language summaries extract source values directly: exact numeric date format, decimal amount, currency symbol, time suffix and weekday. Hindi weekday names use a fixed mapping; Hinglish keeps familiar Roman weekday names. These summaries describe what the selected messages contain rather than inferring that an event is confirmed. Narrative translations protect recognized dates/amounts/times with placeholders and reject missing, duplicated or invented tokens and changed facts. Failed translations use a localized notice with unchanged source cards. Simple Hinglish state sentences have conservative grammar templates that preserve negation and do not convert uncertain statements into confirmed ones.

Ambiguous short questions, uncommon Romanized spellings, written-out quantities and complex wording remain limitations of heuristic language detection and the small model. Fact checks do not prove every semantic claim correct. Original source context stays visible and should be reviewed.

Validation: production frontend build and 35 backend tests pass. `verify_languages.py` passed eight local-model examples covering Hindi/Hinglish/English, weekdays, times, decimal currency, numeric dates, narrative status and missing evidence; report: `.local/language-evaluation.json`. Tests also reject swapped AM/PM, changed precision/date format, invented translation values and fabricated citations. These are targeted checks, not a universal accuracy guarantee.

## Automatic conversation topics
Discover and Explore discover actual project names and specific subjects from imported message excerpts using local Qwen3 1.7B. Labels must occur in their own source text; there is no fixed category list. Names such as MySelfPay are combined across chats using stable case/spacing-normalized IDs. Clear continuations can join an existing subject only with model agreement and a high embedding similarity margin. Ambiguous messages and acknowledgements remain ungrouped and remain searchable in Explore.

Opening either screen resumes indexing, bounded discovery batches (up to 12 messages per request), and source-backed summaries (one per request). Existing fixed-category assignments are automatically replaced. English summaries preserve original-language source messages; each summary covers up to eight representative excerpts. All topic messages are available chronologically, with pagination. Owner isolation, authentication, CSRF for session requests, rate limits and the 25,000-message account limit remain enforced.

Discovery uses one bounded excerpt per message; a new project mentioned only later in a long message may be missed. Project aliases are not automatically merged, and one main topic is assigned per message. Rename/merge/split tools and unattended background processing are future work. Model failures return a retryable error instead of fabricated categories.

Validation: `manage.py test relay --settings=config.test_settings` runs isolated tests; `verify_topics.py` exercises actual local models and PostgreSQL with a disposable multilingual, cross-chat dataset and removes the test account afterwards. This small evaluation is not a general accuracy guarantee.

### Topic summary languages
Topic summaries now use the majority language of non-system messages in that topic: Hindi, Hinglish, or English (ties prefer English, then Hinglish). This is a heuristic; very short and mixed-language messages can be ambiguous. Cached summaries are versioned so existing English cards regenerate automatically.

Local Qwen translations preserve protected numeric/calendar facts. Numeric source details are displayed per citation rather than merging amounts or dates across messages. Ambiguous kal/parso is never guessed into an absolute date. Failed translation uses a localized notice and/or original excerpts, explicitly labelled separately from AI summaries. Full source text, sender, timestamp, and source IDs remain unchanged. Numeric details describe what appears in a message, not whether a payment was made or a plan confirmed; consult its original source for that context.

Validation: 49 backend tests passed, production frontend build passed, and `verify_topic_languages.py` checked four real local-model Hindi/Hinglish scenarios, including exact amounts and dates. Results are saved locally in `.local/topic-language-evaluation.json`.


## PowerShell startup after folder cleanup

Backend terminal:
```powershell
cd "C:\Onesmarter\Wattsapp Relay\backend"
.\.venv\Scripts\python.exe setup_local.py
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Frontend terminal:
```powershell
cd "C:\Onesmarter\Wattsapp Relay\frontend"
npm.cmd run dev -- --port 5173
```

Keep Ollama running (`ollama serve` if it is not already running). The existing models and imported database have been retained. Use `python -m pip` through the backend virtual environment when installing Python dependencies. All verification scripts may be run using the backend virtual environment; their report paths resolve inside `backend/.local`.

## Current response language preference
AI responses in Ask, Discover and Explore now use English regardless of question or imported-chat language. Hindi/Hinglish query interpretation and semantic retrieval remain supported. Source messages are unchanged. The topic-summary cache version has been bumped so older localized summaries regenerate on page load or Refresh topics. Existing translation utilities remain available but topic language auto-selection is no longer the active product behavior. Validation: 51 backend tests passed, including English answers to Hindi/Hinglish questions and unchanged Hindi source text.

## React Native mobile client
The active frontend now lives at `frontend/mobile` (Expo SDK 58 + Expo Router). The previous Vite app is under `frontend/web`; see `frontend/README.md` for updated commands. Run `python -m pip install -r requirements.in` and `python manage.py migrate` for the Knox token tables. Existing accounts and imports are reused.

Mobile endpoints: POST `/api/mobile/signup/`, POST `/api/mobile/login/`, GET `/api/mobile/me/`, POST `/api/mobile/logout/`. Other DRF endpoints accept Knox `Authorization: Token ...` as well as existing CSRF-protected browser sessions. Native tokens are hashed server-side, expire in seven days, and are revoked on logout; clients store them in Expo SecureStore. No automatic refresh tokens are issued. Production requires HTTPS and device/release verification; web session CSRF is not disabled. Existing production-readiness limitations still apply.


### Project-first grouping revision
Discovery v2 uses up to three preceding messages from the same chat within 30 minutes to interpret substantive followups. It prioritizes named projects, then concrete workstreams/tasks, and rejects acknowledgements, filenames, clock-time labels and generic fragments. Cards display PROJECT, WORKSTREAM, TASK or TOPIC. Changing the discovery version automatically replaces the old phrase-based assignments on the next preparation run. Context is bounded: ambiguous projects, aliases and long-range replies can still require manual correction; there is no task completion/owner extraction or nested project-task board yet.

Start the backend with `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run_backend.ps1` from the backend directory to replace stale development servers. The assistant does not run this command on your behalf.


### Grouping accuracy revision (v4)
Project IDs normalize case, spaces, hyphens and underscores. Explicit declarations such as `MySelfPay (MSP)` or `MySelfPay aka MSP` allow the abbreviation across the same owner's chats; conflicting abbreviations are not guessed. Alias-registry changes invalidate old assignments, including when the defining message is deleted. This does not infer that unrelated names such as `selfpay` and `MySelfPay` are aliases.

Messages explicitly naming multiple projects appear under each relevant project. Ungrouped coverage counts unique messages. Mixed-project summaries use isolated original clauses; inseparable mixed statements fall back to original sources. Task/work titles may describe source subject words (for example collected college records), but invented project names are rejected. Placeholder metadata, invitations and pronoun-heavy sentence fragments cannot become topic titles.

`verify_grouping_readonly.py --chat-name osi` audits the existing chat with local AI without changing imported data or saved topic assignments. The 52-message audit recovered selfpay, whatsapp project overlay, record collection, Excel-sheet work and a mobile-app task. It assigned 7 messages and left 45 ungrouped; many are acknowledgements, while incomplete status updates remain a known recall limitation. The audit is not a claim of perfect accuracy. Source-backed projects may still be missed when names or links are implicit, and manual correction tools are now available.


### Project thread timeline
Discover and Explore cards now open `/thread?topic=<topic-id>` in the React Native frontend. The screen combines assigned original messages from all imported chats in chronological order, with original chat name, sender, full message text and device-local date/time. It loads 30 messages at a time and supports refresh, retry, direct links and navigation back to topics.

`GET /api/topics/<topic>/messages/` includes thread metadata. `GET /api/topics/<topic>/messages/<message-id>/context/` returns up to three original messages before and after the selected message, strictly from its source chat. The selected message and other topic members are marked; surrounding messages are not automatically treated as project members. Both endpoints require authentication and enforce account ownership and current topic membership. Deleted/stale topics return not-found. Context may span time gaps; actual timestamps are always displayed. Viewing a thread does not call the AI or change grouping.

Original messages are read-only: sending replies and editing source messages are not included. Topic corrections are available separately. Validation covers chronological pagination across chats, bounded context, metadata, deleted records and foreign-account access. No application server is launched by the verification workflow.


### Manual topic corrections
Apply migration `0004_topiccorrections` before running the updated backend. Open a thread and choose **Rename, merge, split or move messages**. Rename changes the title while keeping its stable ID. Merge moves the entire source topic into an existing destination and redirects future AI assignments. Split creates a new topic from selected messages and requires at least one message to remain in the source. Move transfers selected messages to an existing topic. Other topic memberships are preserved; choose up to 500 messages per operation. Destination search shows the first 20 matches, so narrow the search if necessary. Every action has an explicit review/confirmation step.

Corrections are stored separately from AI labels and original chat messages. AI refreshes cannot overwrite corrected names or selected memberships. Updates are owner-scoped, transactional, rate limited and CSRF-protected for browser sessions. Old summaries are invalidated on correction; queue background processing to regenerate summaries. Original messages are not edited or sent to WhatsApp. Correction history/undo and ungrouped-message assignment are available in Workspace tools.

Run from backend: `.\.venv\Scripts\python.exe manage.py migrate`, then `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run_backend.ps1`. Build the frontend with `npm.cmd run build` from frontend and use the existing preview or run `npm.cmd run preview` if it is stopped.


### Project → workstream → task hierarchy
Discover/Explore render root project cards with nested workstreams and tasks. Open a thread and choose **Organize workstreams and tasks** to add a workstream under a project, add a task under a workstream, or change an existing item's type/parent. Parent relationships are owner-scoped, validated by level and cycle checks, and stored in the existing TopicCorrections state. There is no additional schema migration beyond 0004.

Project and workstream timelines include descendant messages, deduplicated by original message ID. Breadcrumbs and child links open each level. Direct memberships remain distinct from inherited messages: open a child thread to correct an inherited message. Moving/splitting a direct message out of a parent removes its memberships within that parent's subtree; unrelated project memberships remain. Merging parents reparents their children. Empty user-created workstreams/tasks are retained so users can move messages into them.

Automatic hierarchy links require unique source evidence. When local AI proposes a supported work/task subject alongside an identified project, it is retained as a scoped child. Tasks without a named workstream use the organizational container **General work**; this name is not presented as a discovered source phrase. Ambiguous standalone work remains unassigned rather than being forced into a project. Creating empty work/task items does not generate fictional messages, owners, due dates or completion statuses. Parent counts cover descendants, while summary evidence remains explicitly sampled from direct messages.


### Background processing and workspace tools (migrations 0005–0006)

Apply migrations before starting the updated backend. Run a separate worker terminal:

```powershell
cd "C:\Onesmarter\Wattsapp Relay\backend"
.\.venv\Scripts\python.exe manage.py relay_worker
```

Confirmed imports queue processing automatically. For existing imports, select **Refresh topics** or **Queue background processing**. The database retains the queue and completed batches; closing Discover or the browser does not stop the worker. A stopped/crashed worker can resume an abandoned claim after its ten-minute lease expires. Failed jobs retain progress and require Retry. Keep PostgreSQL, Ollama, and the worker running. The worker is a separate process, not a browser timer or automatically installed Windows service. `--once` processes one batch for operations/debugging.

**Tasks & decisions:** Workspace tools extracts task, decision and commitment candidates from each non-system source message. Each candidate displays an exact original quote and the original chat/sender/date. Owners require explicit assignment wording; deadline text is preserved without converting relative dates. Unknown fields stay unknown. Status describes that source only: there is no automatic cross-message task identity/completion reconciliation yet. Extraction reads the first 3,500 characters of each message and returns up to five items. Model errors pause the job instead of storing invented content. Candidate classification remains probabilistic and should be checked against sources.

**Scoped Ask:** Open a project/workstream thread and choose Ask this scope. Retrieval includes that node and all descendants, deduplicated, within the authenticated account. Corrections take effect on the next request. An optional chat filter intersects the scope.

**Coverage:** Discovery v6 can validate work/task subjects against up to three preceding messages within 30 minutes of the same chat. Saved context IDs/hashes are checked before accepting the title. This improves support for fragmented updates without automatically assigning acknowledgements. The prior 7/52 real-chat coverage audit has not been rerun for this update; no new coverage percentage is claimed.

**History:** Rename/merge/split/move/hierarchy edits and ungrouped assignments record transactional before/after snapshots. Undo is newest-first and invalidates summaries. History begins with this update; older edits have no undo snapshot. Original messages remain unchanged. The History screen displays the latest 50 events; undoing recent events exposes earlier eligible events. Ungrouped assignment shows 30 messages per page and validates current ownership and membership again when submitted.

**Map:** The React Native relationship map shows project/workstream/task/topic hierarchy and original sender contributions. Select a node for its connections and thread link. The API caps topics/people at 100 each and contributors per topic at 30. The canvas displays six nodes per column; narrow to a project to inspect more focused results. Sender labels are not verified people identities or task owners.

Security: all endpoints require existing authentication, account-scoped queries, browser CSRF protection for writes, and rate limits on mutations. No new cloud AI provider is used. SQLite unit tests mock local-model responses; these do not establish accuracy on the user's real chat data or constitute an Android device test.


### Context, task histories, and truthful progress (migration 0007)

Apply `manage.py migrate` before restarting backend/worker. Discovery v8 invalidates automatic v7 labels; Refresh topics schedules reprocessing. Manual corrections remain separate. Generic library names (such as TensorFlow/OpenCV), greetings and administrative announcements cannot become automatic projects/workstreams. Context uses at most eight earlier messages within four hours in the same chat. A project follow-up needs a unique, source-supported match. Website feedback can use an explicit shared hostname; a named project may use a domain only when the association appears in a source and is not ambiguous. Unnamed sites use their literal hostname rather than an invented brand. No links are fetched. Saved context references are checked against original message hashes.

Task histories are derived from validated extraction evidence within the selected scope. Matching requires the same unique project and exact normalized subject with at least two meaningful words; conflicting explicit owners and incompatible actions stay separate. Later explicit statuses update the displayed task, retaining all linked evidence. Ambiguous short replies such as “done” are not used to close an unspecified task. Unknown fields do not overwrite supported owners/deadlines. This conservative linker is not a general semantic paraphrase resolver.

Progress reports distinct semantic-indexing, project-grouping, summary, and task-extraction stages, each with its own completed/total counts. A worker heartbeat is updated every 15 seconds, with last saved checkpoint shown separately. A heartbeat means the process is alive, not that AI has completed new work. No heartbeat for 45 seconds displays unresponsive. Live heartbeats protect long batches from duplicate claims. Clean Ctrl+C releases the claim; a crashed process is reclaimed after its lease and heartbeat expire. Batch starts/checkpoints log counts and phases without chat text.

Read-only post-run audit: `.\.venv\Scripts\python.exe manage.py audit_topics`. It prints saved coverage and group names per account without running AI or modifying messages. Coverage is not accuracy: greetings, birthdays, administrative notices and ambiguous updates may appropriately remain ungrouped.


### Ask evidence-first answers

Ask now uses strict source-excerpt output: the local model selects supporting original quotes, but its unchecked free-form conclusion is not displayed. The server verifies each quote against the exact bounded excerpt sent to the model. The English wrapper preserves original source language, sender, chat and linked message timestamps; it does not translate or invent missing facts. Empty supporting evidence produces a not-found answer. This intentionally favors traceable evidence over fluent paraphrases; source selection can still be imperfect and is not a guarantee of complete coverage.

Multilingual embeddings remain the recall mechanism. Exact question terms provide a small ranking boost, and each message contributes at most two of eight selected chunks. Ask adds at most two adjacent messages within five minutes, constrained to the same authorized chat/project query scope, to surface corrections and context. Requests/questions are explicitly distinguished from confirmed outcomes. Truncated source text, quote-validation schema and returned excerpt offsets now agree.

This change requires a backend restart only, with no additional schema migration or frontend build. Regression tests cover invented conclusions attached to real citations, absent evidence, source truncation, project-name ranking and context scope isolation. Model selection quality on live questions still needs evaluation; no perfect-accuracy claim is made.
# Runtime configuration

All backend runtime settings are loaded from `backend/.env`. Use `.env.example`
as a reference; never overwrite an existing `.env` with the example because it
does not contain your database password or Django secret. Existing process
environment variables take precedence over the file.

Model selection uses `AI_ANSWER_MODEL`, `AI_TOPIC_MODEL`, `AI_TASK_MODEL`,
`AI_TRANSLATION_MODEL`, and `AI_EMBEDDING_MODEL`. Set the exact installed Ollama
tag. Download a new model with `ollama pull <model-tag>` before selecting it.
Chat models must support structured JSON; embedding models must match
`SEARCH_EMBEDDING_DIMENSIONS`. Changing the model does not guarantee equal answer
quality. `OLLAMA_BASE_URL` chooses the Ollama server; imported content is sent to
that server.

The example also documents generation limits, timeouts, indexing/chunk limits,
worker intervals, PostgreSQL connections, import limits, and Django security
settings. Invalid numeric settings and incompatible heartbeat/chunk settings
fail at startup with a configuration error. Authentication and source-validation
protections remain enforced in code.

After editing `.env`, restart both Django and `relay_worker`. Use Refresh topics
to queue processing again. Model and chunk configuration changes invalidate
the relevant cached outputs; embedding changes require reindexing. No database
migration or frontend rebuild is needed solely to change these settings.

`.gitignore` excludes private environment files, credentials and database backups;
the secret-free `.env.example` can be committed. Backend secrets must never be
placed in Expo public environment variables.

