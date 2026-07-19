"""Streamlit UI (spec section 12): chat-style NL->SQL front end with a live,
expandable timeline of every agent step, an inline human-review widget, a
raw SQL editor, and schema/data import -- gated behind a login, with
per-user table visibility and a superuser override.

Run with: uv run streamlit run streamlit_app.py
"""
import uuid

import pandas as pd
import streamlit as st

import auth
from agents.schema_retriever import _introspect_schema
from agents.sql_executor import _execute_readonly
from db import admin as db_admin
from graph import get_graph, resume_review
from human_review import queue
from middleware.guardrails import apply_row_limit, check_sql
from model_factory import DEFAULT_PROVIDER, MODEL_MAP, ModelRole, Provider

st.set_page_config(page_title="NL -> SQL Agent", page_icon="🗄️", layout="wide")

NODE_ICONS = {
    "input_guard": "🛡️",
    "schema_retriever": "🔍",
    "sql_generator": "🧠",
    "sql_validator": "✅",
    "sql_regenerator": "🔁",
    "enqueue_review": "🙋",
    "await_decision": "🙋",
    "sql_executor": "🚦",
    "response_formatter": "🗣️",
}

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
        "Demo accounts: `admin` / `admin123` (superuser, sees every table), "
        "`alice` / `alice123` and `bob` / `bob123` (regular users -- each only "
        "sees public tables plus tables they personally created)."
    )
    st.stop()

current_user = st.session_state.user
USERNAME = current_user["username"]
IS_SUPERUSER = current_user["is_superuser"]


def visible_schema() -> list[dict]:
    allowed = auth.visible_tables(USERNAME, IS_SUPERUSER)
    return _introspect_schema(allowed_tables=allowed)


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
    status_ph = st.container()
    final_state = initial_state
    for update in get_graph().stream(initial_state, config, stream_mode="updates"):
        for node_name, node_state in update.items():
            final_state = node_state
            icon = NODE_ICONS.get(node_name, "⚙️")
            node_status = "error" if node_state.get("status") == "error" else "success"
            attempt = len(node_state.get("sql_attempts", []))
            with status_ph.status(f"{icon} {node_name}", state="complete" if node_status == "success" else "error"):
                st.caption(f"attempt: {attempt or 1}")
                if node_name in ("sql_generator", "sql_regenerator", "sql_validator") and node_state.get("final_sql"):
                    st.code(node_state["final_sql"], language="sql")
                last_attempt = (node_state.get("sql_attempts") or [None])[-1]
                if last_attempt and last_attempt.get("error"):
                    st.warning(f"validator error: {last_attempt['error']}")
                if node_state.get("error_detail"):
                    st.error(node_state["error_detail"])
            steps.append({"node": node_name, "status": node_status, "attempt": attempt})

    return {"trace_id": trace_id, "state": final_state, "steps": steps}


def render_review_widget(msg: dict, idx: int) -> None:
    state = msg["state"]
    review = state.get("human_review", {})
    review_id = review.get("review_id")
    row = queue.get(review_id)
    if row is None or row["status"] != "pending":
        return

    st.warning("This query could not be auto-validated after one regeneration attempt "
               "and needs human review before it can run.")
    default_sql = row["sql_attempt_2"] or row["sql_attempt_1"] or ""
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
    st.write(f"**{USERNAME}**" + (" 👑 superuser" if IS_SUPERUSER else ""))
    if st.button("Log out"):
        del st.session_state["user"]
        st.session_state.messages = []
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

    st.divider()
    st.subheader("📚 Available Tables")
    st.caption("Every table you can see." if IS_SUPERUSER else "Public tables, plus tables you created.")
    if st.button("🔄 Refresh"):
        st.rerun()
    for entry in visible_schema():
        owner = auth.get_table_owner(entry["table"])
        label = f"{entry['table']} ({len(entry['columns'])} cols)"
        if owner:
            label += f"  · owner: {owner}"
        with st.expander(label):
            for col in entry["columns"]:
                st.caption(f"`{col['name']}`  —  {col['type']}")

st.title("🗄️ NL -> SQL Agentic System")

tab_chat, tab_sql, tab_import = st.tabs(["💬 Chat", "🧮 SQL Editor", "📥 Import Schema / Data"])

# ---------------------------------------------------------------- Chat tab
with tab_chat:
    st.caption("Ask a question in plain English. Every agent hop is traced, guarded, and PII-masked. "
               "You'll only ever get answers from tables you can see.")

    for i, msg in enumerate(st.session_state.messages):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("rows"):
                st.dataframe(pd.DataFrame(msg["rows"]))
            if msg.get("state", {}).get("status") == "escalated":
                render_review_widget(msg, i)

    user_query = st.chat_input("Ask a question about the data...")
    if user_query:
        st.session_state.messages.append({"role": "user", "content": user_query})
        with st.chat_message("user"):
            st.markdown(user_query)

        with st.chat_message("assistant"):
            result = run_new_query(user_query)
            state = result["state"]
            answer = state.get("final_answer") or "(no answer produced)"
            st.markdown(answer)
            rows = state.get("execution_result")
            if rows:
                st.dataframe(pd.DataFrame(rows))

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

# ------------------------------------------------------------ SQL editor tab
with tab_sql:
    st.caption("Run SQL directly against `db/app.db` — bypasses the NL agent pipeline. "
               "Table access is still restricted to what you can see, superuser aside.")

    guarded = st.checkbox(
        "Guarded mode (SELECT-only, read-only, auto row-limit — same checks the agent pipeline uses)",
        value=True,
    )
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
                        st.dataframe(pd.DataFrame(rows))
                except Exception as e:
                    st.error(f"Execution failed: {e}")
        else:
            st.warning("Unguarded mode: DDL/DML allowed, no row limit, writes are committed. Use with care.")
            try:
                columns, rows = db_admin.execute_write(sql_text, USERNAME, IS_SUPERUSER)
                st.success(f"Statement executed. {len(rows)} row(s) returned." if columns else "Statement executed.")
                if rows:
                    st.dataframe(pd.DataFrame(rows))
            except db_admin.AccessDeniedError as e:
                st.error(f"Access denied: {e}")
            except Exception as e:
                st.error(f"Execution failed: {e}")

# --------------------------------------------------------------- Import tab
with tab_import:
    st.caption("Load a schema/data SQL script, or import a CSV into a table. "
               "New tables you create here are private to you until a superuser or you shares them.")

    st.subheader("SQL script")
    uploaded_sql = st.file_uploader("Upload a .sql file", type=["sql"], key="sql_upload")
    pasted_sql = st.text_area("...or paste a SQL script", height=150, key="sql_paste")
    if st.button("Execute script"):
        script = uploaded_sql.read().decode("utf-8") if uploaded_sql else pasted_sql
        if not script.strip():
            st.warning("Upload a file or paste a script first.")
        else:
            try:
                db_admin.execute_script(script, USERNAME, IS_SUPERUSER)
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
                n = db_admin.import_csv(uploaded_csv, table_name, USERNAME, IS_SUPERUSER, if_exists=if_exists)
                st.success(f"Imported {n} row(s) into `{table_name}`.")
            except db_admin.AccessDeniedError as e:
                st.error(f"Access denied: {e}")
            except Exception as e:
                st.error(f"Import failed: {e}")
