"""Streamlit UI (spec section 12): chat-style NL->SQL front end with a live,
expandable timeline of every agent step, an inline human-review widget, a
raw SQL editor, and schema/data import -- gated behind a login, with
per-user table visibility and a superuser override.

Run with: uv run streamlit run streamlit_app.py
"""
import time
import uuid
from collections.abc import Iterator

import pandas as pd
import streamlit as st

import auth
from agents.schema_retriever import _introspect_schema
from agents.sql_executor import _execute_readonly
from db import admin as db_admin
from graph import get_graph, resume_review
from human_review import queue
import memory
from middleware.guardrails import apply_row_limit, check_sql
from model_factory import DEFAULT_PROVIDER, MODEL_MAP, ModelRole, Provider

st.set_page_config(page_title="NL -> SQL Agent", page_icon="🗄️", layout="wide")

# Friendly, narrative labels for the (collapsed-by-default) reasoning-steps
# timeline -- translates internal node names into ChatGPT/Perplexity-style
# "what the agent is doing" text. The raw node name still shows as a small
# caption inside each step for anyone who wants the technical detail.
NODE_LABELS = {
    "input_guard": "🛡️ Checking your question",
    "schema_retriever": "🔍 Looking at your data",
    "sql_generator": "🧠 Writing the SQL query",
    "sql_validator": "✅ Validating the query",
    "enqueue_review": "🙋 Escalating for human review",
    "await_decision": "🙋 Waiting on reviewer decision",
    "sql_executor": "🚦 Running the query",
    "response_formatter": "🗣️ Preparing your answer",
    "critic": "🧐 Double-checking the answer",
}

USER_AVATAR = "🧑"
ASSISTANT_AVATAR = "🤖"

# Injected once per script run. Deliberately does NOT animate historical
# chat messages on every rerun (that would look like flicker) -- animations
# are scoped to interactive elements (buttons, inputs) and the one-time
# streamed reveal of a fresh answer (via st.write_stream), not to any CSS
# keyframe tied to message render order.
CUSTOM_CSS = """
<style>
html, body, [class*="css"] {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
html { scroll-behavior: smooth; }

.block-container {
    max-width: 1000px;
    padding-top: 2rem;
    padding-bottom: 7rem;
    margin: 0 auto;
}

/* Chat messages: comfortable spacing, no default card border/shadow */
div[data-testid="stChatMessage"] {
    padding: 0.85rem 1.1rem;
    border-radius: 1.1rem;
    margin-bottom: 0.85rem;
    box-shadow: none;
    border: none;
}

/* User bubble: shaded, rounded, right-aligned -- ChatGPT-style.
   Degrades gracefully (no-op) on Streamlit versions without this testid. */
div[data-testid="stChatMessage"]:has(div[data-testid="stChatMessageAvatarUser"]) {
    background: rgba(120, 120, 130, 0.09);
    flex-direction: row-reverse;
    margin-left: 10%;
}
div[data-testid="stChatMessage"]:has(div[data-testid="stChatMessageAvatarUser"]) div[data-testid="stMarkdownContainer"] {
    text-align: right;
}

/* Assistant message: plain, full width, left-aligned */
div[data-testid="stChatMessage"]:has(div[data-testid="stChatMessageAvatarAssistant"]) {
    background: transparent;
    margin-right: 4%;
}

/* Markdown content: tables, code blocks, lists */
div[data-testid="stMarkdownContainer"] table {
    border-collapse: collapse;
    width: 100%;
    margin: 0.5rem 0;
}
div[data-testid="stMarkdownContainer"] table th,
div[data-testid="stMarkdownContainer"] table td {
    border: 1px solid rgba(120, 120, 130, 0.25);
    padding: 0.35rem 0.7rem;
    text-align: left;
}
div[data-testid="stMarkdownContainer"] pre {
    border-radius: 0.6rem;
    border: 1px solid rgba(120, 120, 130, 0.15);
}
div[data-testid="stDataFrame"] {
    border-radius: 0.6rem;
    overflow: hidden;
}

/* Inputs & buttons: subtle, fast transitions -- not distracting */
div[data-testid="stChatInput"] {
    box-shadow: 0 -2px 14px rgba(0, 0, 0, 0.06);
    border-radius: 1.3rem;
}
button {
    transition: filter 0.12s ease-in-out, transform 0.05s ease-in-out;
    border-radius: 0.6rem !important;
}
button:active {
    transform: scale(0.98);
}

/* Custom scrollbar */
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-thumb { background: rgba(120, 120, 130, 0.35); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: rgba(120, 120, 130, 0.55); }

footer { visibility: hidden; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

if "messages" not in st.session_state:
    st.session_state.messages = []  # each: dict with role, content, trace_id, steps, rows, pending_review

# ------------------------------------------------------------------- Login
if "user" not in st.session_state:
    st.title("🗄️ NL -> SQL Agentic System")
    st.subheader("Sign in")
    with st.form("login_form"):
        username_input = st.text_input("Username")
        password_input = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")
    if submitted:
        user = auth.authenticate(username_input, password_input)
        if user is None:
            st.error("Invalid username or password.")
        else:
            st.session_state.user = user
            st.rerun()
    st.caption(
        "Demo accounts: `admin` / `admin123` (admin, sees every table), "
        "`alice` / `alice123` and `bob` / `bob123` (editors -- each only sees "
        "public tables plus tables they personally created), "
        "`carol` / `carol123` (viewer -- read-only, guarded queries only)."
    )
    st.stop()

current_user = st.session_state.user
USERNAME = current_user["username"]
IS_SUPERUSER = current_user["is_superuser"]
ROLE = current_user.get("role", "editor" if not IS_SUPERUSER else "admin")


def visible_schema() -> list[dict]:
    allowed = auth.visible_tables(USERNAME, IS_SUPERUSER)
    return _introspect_schema(allowed_tables=allowed)


def _replay_as_stream(text: str, delay_seconds: float = 0.02) -> Iterator[str]:
    """Yields `text` word-by-word with a small delay for st.write_stream --
    a typing-effect replay of an already-finished, already-guardrail-checked
    answer, not real token streaming from the LLM (see response_formatter.py
    for why the two can't safely be the same thing here)."""
    words = text.split(" ")
    for i, word in enumerate(words):
        yield word + (" " if i < len(words) - 1 else "")
        time.sleep(delay_seconds)


def _node_label(node_name: str, mode: str | None) -> str:
    if node_name == "sql_generator" and mode == "regenerate":
        return "🔁 Fixing the query"
    return NODE_LABELS.get(node_name, f"⚙️ {node_name}")


def run_new_query(query: str) -> dict:
    trace_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": trace_id}}
    initial_state = {
        "user_query_raw": query,
        "trace_id": trace_id,
        "username": USERNAME,
        "is_superuser": IS_SUPERUSER,
        "_force_provider": st.session_state.get("provider_override"),
    }

    steps = []
    final_state = initial_state
    # Collapsed by default -- keeps the default view clean/ChatGPT-like
    # while still giving anyone who wants it the full agent-step transcript.
    steps_expander = st.expander("🔎 View reasoning steps", expanded=False)

    with st.spinner("🤖 Thinking..."):
        for update in get_graph().stream(initial_state, config, stream_mode="updates"):
            for node_name, node_state in update.items():
                final_state = node_state
                node_status = "error" if node_state.get("status") == "error" else "success"
                attempt = len(node_state.get("sql_attempts", []))
                mode = node_state.get("_generation_mode")
                label = _node_label(node_name, mode)
                with steps_expander:
                    with st.status(label, state="complete" if node_status == "success" else "error"):
                        st.caption(f"step: `{node_name}` · attempt {attempt or 1}")
                        if node_name in ("sql_generator", "sql_validator") and node_state.get("final_sql"):
                            st.code(node_state["final_sql"], language="sql")
                        last_attempt = (node_state.get("sql_attempts") or [None])[-1]
                        if last_attempt and last_attempt.get("error"):
                            st.warning(f"validator error: {last_attempt['error']}")
                        if node_state.get("error_detail"):
                            st.error(node_state["error_detail"])
                steps.append({"node": node_name, "status": node_status, "attempt": attempt})

    if any(s["status"] == "error" for s in steps):
        st.error("⚠️ A technical error occurred while processing this request — see 'View reasoning steps' above for details.")

    return {"trace_id": trace_id, "state": final_state, "steps": steps}


def render_review_widget(msg: dict, idx: int) -> None:
    state = msg["state"]
    review = state.get("human_review", {})
    review_id = review.get("review_id")
    row = queue.get(review_id)
    if row is None or row["status"] != "pending":
        return

    st.warning("This query could not be auto-validated after regeneration "
               "and needs human review before it can run.")
    with st.expander(f"SQL attempts ({len(queue.get_sql_attempts(row))})"):
        for attempt in queue.get_sql_attempts(row):
            st.code(attempt["sql"], language="sql")
            if attempt.get("error"):
                st.caption(f"❌ {attempt['error']}")
    default_sql = queue.latest_failed_sql(row)
    sql_text = st.text_area("SQL to approve (edit if needed)", value=default_sql, key=f"sql_{review_id}")
    reviewer = st.text_input("Reviewer name", value=USERNAME, key=f"reviewer_{review_id}")
    col1, col2 = st.columns([1, 2])
    with col1:
        if st.button("Approve & Run", key=f"approve_{review_id}"):
            queue.decide(review_id, approved=True, reviewer=reviewer, decision_sql=sql_text)
            resumed = resume_review(msg["trace_id"])
            msg["state"] = resumed
            msg["content"] = resumed.get("final_answer", "")
            msg["rows"] = resumed.get("execution_result")
            st.rerun()
    with col2:
        reason = st.text_input("Rejection reason (if rejecting)", key=f"reason_{review_id}")
        if st.button("Reject", key=f"reject_{review_id}"):
            queue.decide(review_id, approved=False, reviewer=reviewer, decision_reason=reason)
            resumed = resume_review(msg["trace_id"])
            msg["state"] = resumed
            msg["content"] = resumed.get("final_answer", "I couldn't safely answer that.")
            st.rerun()


with st.sidebar:
    st.header("👤 Account")
    role_badge = {"admin": "👑 admin", "editor": "✏️ editor", "viewer": "👁️ viewer"}.get(ROLE, ROLE)
    st.write(f"**{USERNAME}**  ·  {role_badge}")

    col_new, col_out = st.columns(2)
    with col_new:
        if st.button("🆕 New chat", use_container_width=True, key="new_chat_btn"):
            st.session_state.messages = []
            st.session_state.pop("conversation_summary", None)
            st.rerun()
    with col_out:
        if st.button("Log out", use_container_width=True, key="logout_btn"):
            del st.session_state["user"]
            st.session_state.messages = []
            st.session_state.pop("conversation_summary", None)
            st.rerun()

    st.divider()
    st.header("⚙️ Settings")
    provider_label = st.selectbox("LLM Provider (this session)", ["Ollama (local)", "Gemini (cloud)"], index=0)
    st.session_state["provider_override"] = Provider.OLLAMA if provider_label.startswith("Ollama") else Provider.GEMINI
    active_provider = st.session_state["provider_override"] or DEFAULT_PROVIDER

    st.caption(f"SQL model: `{MODEL_MAP[active_provider][ModelRole.SQL_GEN]}`")
    st.caption(f"General model: `{MODEL_MAP[active_provider][ModelRole.GENERAL]}`")
    st.caption("DB path: `db/app.db`")

    pending_count = len(queue.list_pending())
    st.metric("🙋 Human Review Queue", pending_count)

    if st.session_state.get("conversation_summary"):
        st.divider()
        with st.expander("🧠 Conversation summary"):
            st.caption(st.session_state["conversation_summary"])

    st.divider()
    st.subheader("📚 Available Tables")
    st.caption("Every table you can see." if IS_SUPERUSER else "Public tables, plus tables you created.")
    # Schema is re-read fresh on every script run already (no caching), so
    # the click itself -- which Streamlit already reruns the script for --
    # is all "refresh" needs. An extra st.rerun() here would just trigger a
    # second, wasted full rerun on top of that.
    st.button("🔄 Refresh", key="refresh_tables_btn")
    for entry in visible_schema():
        owner = auth.get_table_owner(entry["table"])
        label = f"{entry['table']} ({len(entry['columns'])} cols)"
        if owner:
            label += f"  · owner: {owner}"
        with st.expander(label, key=f"table_expander_{entry['table']}"):
            for col in entry["columns"]:
                st.caption(f"`{col['name']}`  —  {col['type']}")

st.title("🗄️ NL -> SQL Agentic System")

tab_chat, tab_sql, tab_import = st.tabs(["💬 Chat", "🧮 SQL Editor", "📥 Import Schema / Data"])

# ---------------------------------------------------------------- Chat tab
with tab_chat:
    st.caption("Ask a question in plain English. Every agent hop is traced, guarded, and PII-masked. "
               "You'll only ever get answers from tables you can see.")

    for i, msg in enumerate(st.session_state.messages):
        avatar = USER_AVATAR if msg["role"] == "user" else ASSISTANT_AVATAR
        with st.chat_message(msg["role"], avatar=avatar):
            st.markdown(msg["content"])
            if msg.get("rows"):
                st.dataframe(pd.DataFrame(msg["rows"]), use_container_width=True)
            if msg.get("state", {}).get("critic_feedback"):
                st.caption(f"🧐 Critic: {msg['state']['critic_feedback']}")
            if msg.get("state", {}).get("status") == "escalated":
                render_review_widget(msg, i)

    user_query = st.chat_input("Ask a question about the data...")
    if user_query:
        st.session_state.messages.append({"role": "user", "content": user_query})
        with st.chat_message("user", avatar=USER_AVATAR):
            st.markdown(user_query)

        with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
            result = run_new_query(user_query)
            state = result["state"]
            answer = state.get("final_answer") or "(no answer produced)"
            # The answer is already complete and guardrail-approved by this
            # point (see agents/response_formatter.py) -- this replays it
            # word-by-word purely for perceived-latency UX, not real
            # token streaming from the LLM.
            st.write_stream(_replay_as_stream(answer))
            rows = state.get("execution_result")
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True)

            assistant_msg = {
                "role": "assistant",
                "content": answer,
                "trace_id": result["trace_id"],
                "state": state,
                "rows": rows,
            }
            if state.get("status") == "escalated":
                render_review_widget(assistant_msg, len(st.session_state.messages))
            st.session_state.messages.append(assistant_msg)

            # Compresses older turns into a running summary once history
            # grows past keep_last -- doesn't trim what's *displayed* (chat
            # scrollback stays intact), just keeps a bounded-size summary
            # available (shown in the sidebar) instead of an ever-growing
            # transcript, ready to feed into follow-up-question context.
            plain_history = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages]
            summary, _ = memory.summarize_if_needed(
                plain_history, st.session_state.get("conversation_summary", "")
            )
            st.session_state["conversation_summary"] = summary

# ------------------------------------------------------------ SQL editor tab
with tab_sql:
    st.caption("Run SQL directly against `db/app.db` — bypasses the NL agent pipeline. "
               "Table access is still restricted to what you can see, superuser aside.")

    can_write = auth.can_write(ROLE)
    if can_write:
        guarded = st.checkbox(
            "Guarded mode (SELECT-only, read-only, auto row-limit — same checks the agent pipeline uses)",
            value=True,
        )
    else:
        guarded = True
        st.info("👁️ Viewer role: always guarded (SELECT-only, read-only). Ask an editor/admin for write access.")
    sql_input = st.text_area("SQL", height=150, placeholder="SELECT * FROM customer LIMIT 10;")

    if st.button("▶ Run SQL", type="primary"):
        sql_text = sql_input.strip().rstrip(";")
        if not sql_text:
            st.warning("Enter a SQL statement first.")
        elif guarded:
            full_schema = visible_schema()
            allowed_tables = {t["table"] for t in full_schema}
            allowed_columns = {c["name"] for t in full_schema for c in t["columns"]}
            ok, reason = check_sql(sql_text, allowed_tables=allowed_tables, allowed_columns=allowed_columns)
            if not ok:
                st.error(f"Blocked by guardrail: {reason}")
            else:
                try:
                    rows = _execute_readonly(apply_row_limit(sql_text))
                    st.success(f"{len(rows)} row(s) returned.")
                    if rows:
                        st.dataframe(pd.DataFrame(rows), use_container_width=True)
                except Exception as e:
                    st.error(f"Execution failed: {e}")
        else:
            st.warning("Unguarded mode: DDL/DML allowed, no row limit, writes are committed. Use with care.")
            try:
                columns, rows = db_admin.execute_write(sql_text, USERNAME, IS_SUPERUSER, role=ROLE)
                st.success(f"Statement executed. {len(rows)} row(s) returned." if columns else "Statement executed.")
                if rows:
                    st.dataframe(pd.DataFrame(rows), use_container_width=True)
            except db_admin.AccessDeniedError as e:
                st.error(f"Access denied: {e}")
            except Exception as e:
                st.error(f"Execution failed: {e}")

# --------------------------------------------------------------- Import tab
with tab_import:
    st.caption("Load a schema/data SQL script, or import a CSV into a table. "
               "New tables you create here are private to you until a superuser or you shares them.")

    if not auth.can_write(ROLE):
        st.info("👁️ Viewer role: read-only. Ask an editor/admin to import schema or data.")
    else:
        st.subheader("SQL script")
        uploaded_sql = st.file_uploader("Upload a .sql file", type=["sql"], key="sql_upload")
        pasted_sql = st.text_area("...or paste a SQL script", height=150, key="sql_paste")
        if st.button("Execute script"):
            script = uploaded_sql.read().decode("utf-8") if uploaded_sql else pasted_sql
            if not script.strip():
                st.warning("Upload a file or paste a script first.")
            else:
                try:
                    db_admin.execute_script(script, USERNAME, IS_SUPERUSER, role=ROLE)
                    st.success("Script executed successfully.")
                except db_admin.AccessDeniedError as e:
                    st.error(f"Access denied: {e}")
                except Exception as e:
                    st.error(f"Failed to execute script: {e}")

        st.divider()
        st.subheader("CSV data")
        uploaded_csv = st.file_uploader("Upload a .csv file", type=["csv"], key="csv_upload")
        col1, col2 = st.columns(2)
        with col1:
            table_name = st.text_input("Target table name")
        with col2:
            if_exists = st.selectbox("If table exists", ["append", "replace", "fail"])
        if st.button("Import CSV"):
            if not uploaded_csv or not table_name:
                st.warning("Provide both a CSV file and a target table name.")
            else:
                try:
                    n = db_admin.import_csv(
                        uploaded_csv, table_name, USERNAME, IS_SUPERUSER, if_exists=if_exists, role=ROLE
                    )
                    st.success(f"Imported {n} row(s) into `{table_name}`.")
                except db_admin.AccessDeniedError as e:
                    st.error(f"Access denied: {e}")
                except Exception as e:
                    st.error(f"Import failed: {e}")
