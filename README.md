<div align="center">

# 🗄️ NL-to-SQL Multi-Agent Agentic System

**Ask a question in plain English. Get validated, guarded, PII-safe SQL — with a human in the loop when it matters.**

![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![LangGraph](https://img.shields.io/badge/orchestration-LangGraph-1C3C3C)
![Streamlit](https://img.shields.io/badge/UI-Streamlit-FF4B4B?logo=streamlit&logoColor=white)
![SQLite](https://img.shields.io/badge/database-SQLite-003B57?logo=sqlite&logoColor=white)
![uv](https://img.shields.io/badge/package%20manager-uv-DE5FE9)
![pytest](https://img.shields.io/badge/tests-pytest-0A9EDC?logo=pytest&logoColor=white)

[Demo](#-demo) • [Features](#-features) • [Tech Stack](#-tech-stack) • [Architecture](#-architecture) • [Setup](#-setup--running) • [Auth](#-authentication--authorization)

</div>

<br>

A multi-agent, agentic AI system that turns a natural-language question into
SQL, validates it against a live SQLite schema, auto-regenerates once on
failure, escalates to a human reviewer on a second failure, executes the
approved query, and returns a natural-language answer — with PII masking,
guardrails, per-user authorization, and structured tracing wrapping every hop.

---

## 🎬 Demo

<div align="center">

<video src="demo.mov" controls width="720">
  Your browser/viewer doesn't support inline video playback — download it
  directly: <a href="demo.mov">demo.mov</a>.
</video>

*If the player above doesn't render in your viewer — GitHub plays it inline
once the repo is pushed there, but many local Markdown previewers don't —
open [demo.mov](demo.mov) directly.*

</div>

---

## ✨ Features

<table>
<tr>
<td width="33%" valign="top">

### 🧠 Agentic pipeline
NL → SQL → validate → regenerate once → human review, orchestrated as an
explicit [LangGraph](#-architecture) state machine, not an implicit agent loop.

</td>
<td width="33%" valign="top">

### 🛡️ Guarded by default
Prompt-injection filtering, SQL statement/keyword allow-lists, row limits,
query timeouts, and output leak scanning wrap every hop.

</td>
<td width="33%" valign="top">

### 🕵️ PII-safe
Reversible tokenization masks PII before it ever reaches an LLM; only
rehydrated for the requesting user, never in logs.

</td>
</tr>
<tr>
<td width="33%" valign="top">

### 🙋 Human-in-the-loop
Two failed SQL attempts pause the graph for a real reviewer — approve,
edit, or reject — via CLI or an inline Streamlit widget.

</td>
<td width="33%" valign="top">

### 🔐 Auth & per-user data
Login-gated UI; a table you create is private to you until a superuser
or you decides otherwise.

</td>
<td width="33%" valign="top">

### 📈 Fully traced
Every node emits an OTel span + a structured JSON log line sharing one
`trace_id`, reconstructable end-to-end with `trace_viewer.py`.

</td>
</tr>
</table>

---

## 🧰 Tech Stack

| Layer | Choice |
|---|---|
| Orchestration | **LangGraph** — explicit `StateGraph` with conditional edges, a business-retry loop, and a `MemorySaver` checkpointer with `interrupt_before` for human-in-the-loop |
| LLM (dev) | **Ollama**, local (`qwen2.5:7b` for SQL gen, `llama3.2:latest` for general, `nomic-embed-text:latest` for embeddings — overridable via env) |
| LLM (prod) | **Google Gemini** (`gemini-2.5-flash` generation, `gemini-embedding-001` embeddings) via `langchain-google-genai` |
| Model selection | **Model Factory** ([model_factory.py](model_factory.py)) — the only module allowed to instantiate an LLM/embedding client; provider chosen by `MODEL_PROVIDER` env var, per-request override, or automatic fallback |
| Relational DB | SQLite, file-based (`db/app.db`) |
| SQL parsing | `sqlglot` (statement-type checks, table/column extraction, `LIMIT` injection) |
| Guardrails | Custom validators in [middleware/guardrails.py](middleware/guardrails.py), policy-driven via [config/guardrail_policy.yaml](config/guardrail_policy.yaml) |
| PII masking | Regex-based reversible tokenization in [middleware/pii.py](middleware/pii.py), policy-driven via [config/pii_policy.yaml](config/pii_policy.yaml) |
| Tracing | OpenTelemetry spans → `logs/spans.jsonl`; structured JSON log lines → `logs/app.log` (one line per agent step, filterable by `trace_id`) |
| Resilience | [resilience.py](resilience.py) — per-node retry (3x exponential backoff) + provider fallback, independent of the business-level SQL retry |
| UI | **Streamlit** app ([streamlit_app.py](streamlit_app.py)): Chat tab (live per-node status timeline + human-review widget), SQL Editor tab, Import Schema/Data tab, sidebar table browser |
| Human review | SQLite `review_queue` table ([db/schema.sql](db/schema.sql)) + CLI ([human_review/cli.py](human_review/cli.py)) + Streamlit widget |
| Schema/data admin | [db/admin.py](db/admin.py) — read-write helpers (`execute_script`, `execute_write`, `import_csv`) for the SQL Editor / Import tabs, kept separate from the guarded agent pipeline |
| Auth & authorization | [auth.py](auth.py) — PBKDF2-hashed logins in `app_user`, per-table ownership in `table_ownership`; a table you create is private to you (and superusers) |
| Package/env manager | **uv** |
| Tests | `pytest`, LLM stubbed via `FakeLLM` for deterministic e2e runs |

<details>
<summary><strong>📁 Project layout</strong> (click to expand)</summary>

```
SQL_AGENTS/
├── agents/                  # one module per graph node, invoke(state) -> state
│   ├── input_guard.py        # PII mask + input guardrail (entry point)
│   ├── schema_retriever.py    # SQLite introspection + lexical table ranking
│   ├── sql_generator.py       # NL -> SQL (first attempt)
│   ├── sql_validator.py       # sqlglot + guardrail checks against live schema
│   ├── sql_regenerator.py      # SQL + validator error -> corrected SQL
│   ├── human_review_agent.py   # enqueue_review + await_decision (HITL gate)
│   ├── sql_executor.py         # read-only execution, row-limit, timeout
│   └── response_formatter.py   # rows -> NL answer, output guardrail, rehydrate PII
├── middleware/
│   ├── pii.py                # mask_text/unmask_text, PIIVault, contains_pii
│   ├── guardrails.py           # check_input/check_sql/check_output, apply_row_limit
│   └── tracing.py              # traced_node decorator: OTel span + structlog line
├── human_review/
│   ├── queue.py                # review_queue CRUD
│   └── cli.py                  # list/show/approve/reject CLI
├── db/
│   ├── schema.sql, seed_data.sql, init_db.py
│   ├── admin.py                # read-write helpers: execute_script, execute_write, import_csv
│   └── app.db                  # generated
├── config/
│   ├── pii_policy.yaml
│   └── guardrail_policy.yaml
├── tests/                     # unit tests per module + 3 e2e scenarios + auth/ACL tests
├── auth.py                      # login (PBKDF2), table ownership, visibility rules
├── model_factory.py            # provider-agnostic LLM/embedding client factory
├── resilience.py                # technical retry/self-recovery decorator
├── state.py                     # shared AgentState TypedDict
├── graph.py                     # LangGraph wiring, run_query(), resume_review()
├── trace_viewer.py               # filters logs/app.log by trace_id
└── streamlit_app.py              # Chat / SQL Editor / Import tabs + sidebar table browser
```

</details>

---

## 🤖 Agent Roster

| # | Agent | File | Job |
|---|---|---|---|
| — | Orchestrator | [graph.py](graph.py) | Owns the `StateGraph`, routing functions, and the checkpointer; enforces max 1 regeneration before escalation |
| 1 | Schema Retriever | [agents/schema_retriever.py](agents/schema_retriever.py) | `PRAGMA table_info` / `sqlite_master` introspection, lexical relevance ranking of tables |
| 2 | NL→SQL Generator | [agents/sql_generator.py](agents/sql_generator.py) | First-pass SQL from the masked question + schema context |
| 3 | SQL Validator | [agents/sql_validator.py](agents/sql_validator.py) | Syntax (sqlglot), table/column existence, guardrail compliance, auto row-limit |
| 4 | SQL Regenerator | [agents/sql_regenerator.py](agents/sql_regenerator.py) | Re-prompts with the failed SQL + validator error (one shot only) |
| 5 | Human Review | [agents/human_review_agent.py](agents/human_review_agent.py) | `enqueue_review` writes to `review_queue`; `await_decision` re-checks it after the graph resumes |
| 6 | SQL Executor | [agents/sql_executor.py](agents/sql_executor.py) | Read-only SQLite connection, progress-handler query timeout |
| 7 | Response Formatter | [agents/response_formatter.py](agents/response_formatter.py) | Rows → NL answer; masks rows before the LLM call, rehydrates PII after the output guardrail passes |

**Cross-cutting**, wrapping every node above (not graph nodes themselves):

- 🕵️ **PII middleware** — [middleware/pii.py](middleware/pii.py)
- 🛡️ **Guardrail middleware** — [middleware/guardrails.py](middleware/guardrails.py)
- 📈 **Tracing/logging middleware** — [middleware/tracing.py](middleware/tracing.py) (`traced_node`)
- ♻️ **Resilience wrapper** — [resilience.py](resilience.py) (`resilient_node`), applied outermost on every node

---

## 🏗️ Architecture

<details open>
<summary><strong>System architecture diagram</strong></summary>

```
                              ┌─────────────────────────────┐
                              │        Streamlit UI          │
                              │ 💬 Chat (live step timeline, │
                              │    review approve/reject)    │
                              │ 🧮 SQL Editor  📥 Import     │
                              │ 📚 sidebar table browser      │
                              └───────────────┬───────────────┘
                                              │ run_query() / resume_review()
                                              │ (SQL Editor / Import bypass the graph,
                                              │  hit db/admin.py or agents/sql_executor.py directly)
                                              ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                              graph.py (LangGraph)                          │
│                                                                             │
│   every node below is wrapped:  resilient_node( traced_node( fn ) )        │
│                                                                             │
│   [input_guard] → [schema_retriever] → [sql_generator] → [sql_validator]   │
│         │                                                    │  │          │
│      failed                                             valid│  │invalid   │
│         │                                                    │  │          │
│         ▼                                                    ▼  ▼          │
│        END                              [sql_executor]  [sql_regenerator]  │
│                                                │                │          │
│                                                │                ▼          │
│                                                │          [sql_validator]  │
│                                                │           (loop, once)    │
│                                                │                │invalid   │
│                                                │                ▼          │
│                                                │        [enqueue_review]   │
│                                                │                │          │
│                                                │       ┄┄┄ interrupt ┄┄┄   │
│                                                │                │          │
│                                                │         [await_decision]  │
│                                                │          │approved │rejected/pending
│                                                │◄─────────┘         │      │
│                                                │                    ▼      │
│                                                │                   END     │
│                                                ▼                           │
│                                    [response_formatter] ──► END            │
└───────────────────────────────────────────────────────────────────────────┘
         │                                    │                     │
         ▼                                    ▼                     ▼
┌──────────────────┐              ┌──────────────────────┐  ┌──────────────────┐
│  model_factory.py │              │      db/app.db        │  │ human_review/     │
│  Ollama ↔ Gemini  │              │  (customer, product,  │  │ queue.py + cli.py  │
│  (env / override / │              │   orders, order_item, │  │ review_queue table │
│   auto-fallback)   │              │   review_queue)        │  │                    │
└──────────────────┘              └──────────────────────┘  └──────────────────┘
         ▲
         │
┌──────────────────┐
│  resilience.py    │  3x backoff → fallback provider → structured error
└──────────────────┘
```

</details>

<details>
<summary><strong>Data flow diagram</strong> (click to expand)</summary>

```
User query (raw)
   │
   ▼
┌─────────────────────────────┐
│ input_guard                  │  check_input()  → block prompt-injection / length
│                               │  mask_text()    → user_query_masked, trace_id issued
└──────────────┬───────────────┘
               ▼
┌─────────────────────────────┐
│ schema_retriever              │  introspect SQLite → rank tables by lexical overlap
└──────────────┬───────────────┘  state.schema_context
               ▼
┌─────────────────────────────┐
│ sql_generator                 │  LLM(schema_context, user_query_masked) → SQL
└──────────────┬───────────────┘  state.final_sql
               ▼
┌─────────────────────────────┐
│ sql_validator                  │  sqlglot parse + check_sql() against live schema
└──────────────┬───────────────┘  appends {attempt, sql, valid, error} to sql_attempts
       valid   │   invalid
   ┌───────────┘   └────────────────────┐
   ▼                                     ▼
┌─────────────────┐            attempt 1: sql_regenerator (loop back to validator)
│ sql_executor      │            attempt 2: enqueue_review → [interrupt] → await_decision
│ read-only, LIMIT, │                                              │approved   │other
│ query timeout      │                                              ▼           ▼
└─────────┬─────────┘                                       final_sql=decision  END
          ▼                                                 → sql_executor      (escalated/failed)
┌─────────────────────────────┐
│ response_formatter             │  mask rows → LLM → check_output() → rehydrate PII
└──────────────┬───────────────┘  state.final_answer
               ▼
     Answer + rows → User

Every arrow above is also: OTel span + one JSON line in logs/app.log, carrying
the same trace_id end-to-end.
```

</details>

### Retry & Escalation Semantics

Two independent retry concepts — do not conflate them:

| | Trigger | Behavior |
|---|---|---|
| **Business retry** (semantic) | SQL is *invalid* | Regenerate once → if still invalid, escalate to human review. Hard-coded circuit breaker (`config/guardrail_policy.yaml: escalation.max_regeneration_attempts`), not model-decided. See `route_after_validation` in [graph.py](graph.py). |
| **Technical retry** (exceptions) | A node *throws* (timeout, connection error, malformed response, 5xx/429) | 3 attempts with exponential backoff → one self-recovery attempt on the fallback provider → structured `status: "error"` instead of a crash. See [resilience.py](resilience.py), applied to every node regardless of what it does. |

### Human-in-the-Loop Flow

1. Two failed SQL attempts → `enqueue_review` writes a row to `review_queue`
   (masked query, both attempts + errors, schema context) and the graph is
   compiled with `interrupt_before=["await_decision"]`, so it pauses right
   there.
2. A reviewer inspects the queue via:
   - **CLI**: `uv run python -m human_review.cli list|show|approve|reject`
   - **Streamlit**: the inline approve/edit/reject widget on an escalated chat turn
3. Resuming (`graph.resume_review(trace_id)`) re-enters `await_decision`,
   which re-reads the queue row fresh:
   - ✅ **approved** → `final_sql` = reviewer's SQL → `sql_executor` → answer
   - ❌ **rejected** → ends with a `failed` status and a safe refusal message
   - ⏳ **still pending** → ends `escalated` again; can be resumed later

---

## 🧮 SQL Editor, Import & Table Browser

The Streamlit app has two tabs beyond Chat, plus a sidebar table browser —
none of these go through the LangGraph pipeline; they talk to the database
directly.

- **🧮 SQL Editor tab** — run arbitrary SQL against `db/app.db`.
  - **Guarded mode** (default): the exact same checks the agent pipeline
    uses — `middleware.guardrails.check_sql` (SELECT-only, table/column
    allow-list against the live schema) and `apply_row_limit`, executed
    read-only via `agents.sql_executor._execute_readonly`.
  - **Unguarded mode**: runs any statement (DDL/DML included) on a real
    read-write connection via `db.admin.execute_write` — for schema
    changes or manual fixes. Flagged with an explicit warning since it
    bypasses every guardrail.
- **📥 Import Schema / Data tab**:
  - Upload or paste a `.sql` script and run it as a multi-statement batch
    (`db.admin.execute_script`) — e.g. to load an updated `schema.sql`.
  - Upload a `.csv` and load it into a table (`db.admin.import_csv`, backed
    by `pandas.read_csv` → `to_sql`), with `append` / `replace` / `fail`
    semantics for an existing table.
- **📚 Available Tables (sidebar)** — lists every table and its columns/types
  via live `PRAGMA table_info` introspection (`agents.schema_retriever._introspect_schema`),
  so it reflects the schema immediately after any import.

> `db/admin.py` is intentionally separate from `agents/sql_executor.py`: the
> agent pipeline's executor stays read-only and guardrail-gated no matter what
> the SQL Editor's unguarded mode is used for.

---

## 🔐 Authentication & Authorization

The Streamlit app is gated behind a login. Everything else in this doc —
chat, SQL Editor, Import, the sidebar table browser — runs as the logged-in
user and is scoped by their visibility.

**Model** ([auth.py](auth.py), tables in [db/schema.sql](db/schema.sql)):

- `app_user` — username + PBKDF2-hashed password + `is_superuser` flag.
- `table_ownership` — one row per table *created after seeding*, mapping
  `table_name -> owner`. A table with no row here is **public** (every
  seeded table — `customer`, `product`, `orders`, `order_item`,
  `review_queue` — is public). A table with a row here is **private**:
  visible only to its owner.
- A **superuser** (`is_superuser=1`) bypasses ownership entirely and sees
  every table, public or private — `auth.visible_tables()` returns `None`
  for them, meaning "no restriction."

**Where it's enforced:**

| Layer | Enforcement |
|---|---|
| `agents/schema_retriever.py` | `_introspect_schema(allowed_tables=...)` filters live schema introspection to `auth.visible_tables(username, is_superuser)` before ranking, so the NL agent never even sees another user's private table as candidate context. |
| `agents/sql_validator.py` | Restricts its table/column allow-list to the same visible set, so a user can't get *valid* SQL against a private table they don't own even by guessing its name directly. |
| `db/admin.py` | `_check_access()` extracts referenced tables (via `sqlglot`) from any SQL Editor / import statement and rejects it with `AccessDeniedError` unless every table is visible to the caller. `CREATE TABLE` is exempt (a brand-new name always succeeds), and ownership is recorded immediately afterward via `auth.record_table_owner()`. |
| `graph.run_query(...)` | Threads `username`/`is_superuser` into `AgentState` so every node downstream sees the same user; omitting `username` (e.g. direct CLI/test use) means **unrestricted**, matching the pre-auth behavior. |

**Demo accounts** (seeded by `db/init_db.py` via `auth.ensure_default_users`):

| Username | Password | Role |
|---|---|---|
| `admin` | `admin123` | 👑 superuser — sees every table |
| `alice` | `alice123` | regular user |
| `bob` | `bob123` | regular user |

> ⚠️ Change these before using this anywhere but a local demo.

Log in as `alice`, create a table via the Import tab, and it's invisible to
`bob` but visible to `admin` — that's the whole feature, end to end.

---

## 🚀 Setup & Running

```bash
# 1. Install dependencies (uv reads pyproject.toml)
uv sync

# 2. Seed the demo SQLite database (also seeds admin/alice/bob accounts, see Auth section)
uv run python db/init_db.py

# 3. (dev) make sure Ollama is running with the configured models
ollama pull qwen2.5:7b llama3.2:latest nomic-embed-text:latest
# or override via env: OLLAMA_SQL_MODEL, OLLAMA_GENERAL_MODEL, OLLAMA_EMBED_MODEL

# 4. (prod) set provider + Gemini credentials
export MODEL_PROVIDER=gemini
export GOOGLE_API_KEY=...

# 5. Run the graph directly
uv run python -c "from graph import run_query; print(run_query('How many orders has Asha Rao placed?'))"

# 6. Run the Streamlit UI, then log in (see Auth section for demo accounts)
uv run streamlit run streamlit_app.py

# 7. Run tests
uv run pytest tests/ -v
```

Logs land in `logs/app.log` (structured JSON, one line per agent step,
filterable by `trace_id`) and `logs/spans.jsonl` (OTel spans).

---

## ⚙️ Configuration

| File | Controls |
|---|---|
| [config/pii_policy.yaml](config/pii_policy.yaml) | Which entity types to mask/hash/drop, token prefixes, whether to rehydrate the final answer. |
| [config/guardrail_policy.yaml](config/guardrail_policy.yaml) | Prompt-injection phrases, SQL statement/keyword allow/deny-lists, row-limit and query-timeout caps, output leak checks, max regeneration attempts. |
