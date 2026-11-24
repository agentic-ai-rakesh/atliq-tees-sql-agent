"""Streamlit UI for the AtliQ Tees SQL agent. It only talks to the FastAPI backend.

Run with `make frontend` (http://localhost:8501).
"""

from __future__ import annotations

import os
from pathlib import Path

import requests
import streamlit as st

API_URL = os.getenv("TSHIRT_AGENT_API_URL", "http://127.0.0.1:8000").rstrip("/")
LOGO = Path(__file__).resolve().parent.parent / "atliq_tees.png"


def api(method: str, path: str, timeout: int = 180, **kwargs):
    """Call the backend; show a readable error and return None on failure."""
    try:
        response = requests.request(method, f"{API_URL}{path}", timeout=timeout, **kwargs)
    except requests.ConnectionError:
        st.error(f"Cannot reach the API at {API_URL}. Start it with `make backend`.")
        return None
    except requests.Timeout:
        st.error("The API took too long to answer. Try again.")
        return None
    if not response.ok:
        try:
            st.error(response.json().get("detail", response.text))
        except ValueError:
            st.error(response.text or f"HTTP {response.status_code}")
        return None
    return response.json()


st.set_page_config(page_title="AtliQ Tees: SQL Agent", page_icon="👕", layout="wide")
st.title("AtliQ Tees: Talk to the Database 👕")
st.caption(
    "Ask about inventory, prices and discounts. A LangGraph agent reads the schema, writes "
    "read-only SQL, fixes its own errors and answers from the query results."
)

if "chat" not in st.session_state:
    st.session_state.chat = []
if "pending" not in st.session_state:
    st.session_state.pending = None

config = api("GET", "/api/config", timeout=10)
schema = api("GET", "/api/schema", timeout=10)
examples = api("GET", "/api/examples", timeout=10) or []

with st.sidebar:
    if LOGO.exists():
        st.image(str(LOGO), width="stretch")
    if config:
        if not config["api_key_set"]:
            st.warning("The LLM API key is not set in .env, so questions will fail.")
        st.caption(
            f"Model: {config['llm_provider']} / {config['llm_model']} · DB: {config['database']} · "
            f"max {config['max_tool_rounds']} tool rounds · {config['max_result_rows']} rows per query"
        )
    st.subheader("Try a question")
    for example in examples:
        if st.button(example, width="stretch"):
            st.session_state.pending = example
    if schema:
        st.subheader("Database")
        for table in schema["tables"]:
            with st.expander(f"{table['name']} ({table['row_count']} rows)"):
                for column in table["columns"]:
                    values = f": {', '.join(map(str, column['values']))}" if column.get("values") else ""
                    key = " 🔑" if column["primary_key"] else ""
                    st.markdown(f"- `{column['name']}` {column['type']}{key}{values}")
                for fk in table["foreign_keys"]:
                    st.caption(f"FK: {fk}")
    if st.session_state.chat and st.button("Clear chat", width="stretch"):
        st.session_state.chat = []
        st.rerun()


def show_answer(result: dict) -> None:
    st.markdown(result["answer"])
    if result.get("unverified_numbers"):
        st.warning(
            "These numbers were not found in the query results, check them: " + ", ".join(result["unverified_numbers"])
        )
    if not result["grounded"]:
        st.caption("No query succeeded for this answer.")
    for number, query in enumerate(result["queries"], start=1):
        label = "✅" if query["ok"] else "❌"
        with st.expander(f"{label} SQL query {number}", expanded=query["ok"] and number == len(result["queries"])):
            st.code(query["sql"], language="sql")
            if query["ok"]:
                rows = [dict(zip(query["columns"], row, strict=False)) for row in query["rows"]]
                st.dataframe(rows, width="stretch")
                if query.get("truncated"):
                    st.caption("Only the first rows are shown.")
            else:
                st.error(query["error"])
    usage = result.get("usage", {})
    with st.expander("How the agent worked"):
        st.caption(
            f"{result['model']} · {result['latency_seconds']}s · {usage.get('total_tokens', 0)} tokens "
            f"(in {usage.get('input_tokens', 0)}, out {usage.get('output_tokens', 0)}) · "
            f"prompt v{result['prompt_version']}" + (" · fixed its own SQL error" if result["self_corrected"] else "")
        )
        for number, step in enumerate(result["steps"], start=1):
            detail = ""
            if step["tool"] == "run_sql":
                detail = f" → {step['rows']} rows" if step.get("ok") else f" → error: {step.get('error')}"
                st.markdown(f"{number}. `run_sql`{detail}")
            else:
                st.markdown(f"{number}. `{step['tool']}` {step['args'] or ''}")


for turn in st.session_state.chat:
    with st.chat_message("user"):
        st.markdown(turn["question"])
    with st.chat_message("assistant"):
        show_answer(turn)

question = st.chat_input("Ask about the store's inventory, prices or discounts") or st.session_state.pending
st.session_state.pending = None
if question:
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.spinner("Querying the database…"):
            result = api("POST", "/api/ask", json={"question": question})
        if result:
            show_answer(result)
            st.session_state.chat.append(result)
