"""The SQL agent as an explicit LangGraph loop.

    START -> agent --(tool calls)--> tools --> agent ... --(no tool calls)--> finalize -> END

- agent: the chat model plans, inspects the schema, writes SQL or answers. After
  max_tool_rounds the tools are taken away, so the loop always ends.
- tools: list_tables / describe_table / run_sql. A failing query comes back as an error
  message, so the agent can fix it and try again (self-correction).
- finalize: the answer, every query with its rows, the steps, token usage, and a check that
  the numbers in the answer appear in the query results.
"""

from __future__ import annotations

import re
import time
from typing import Annotated, Any, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from tshirt_agent.agent.prompts import FINAL_ROUND_NOTE

NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    tool_rounds: int
    result: dict[str, Any]


def build_sql_graph(llm: BaseChatModel, tools: list[BaseTool], system_prompt: str, max_tool_rounds: int):
    llm_with_tools = llm.bind_tools(tools)
    tool_node = ToolNode(tools)

    def agent(state: AgentState) -> dict:
        rounds = state.get("tool_rounds", 0)
        system = [SystemMessage(system_prompt)]
        if rounds >= max_tool_rounds:
            system.append(SystemMessage(FINAL_ROUND_NOTE))
            model = llm
        else:
            model = llm_with_tools
        return {"messages": [model.invoke(system + state["messages"])]}

    def tools_step(state: AgentState) -> dict:
        update = tool_node.invoke(state)
        return {"messages": update["messages"], "tool_rounds": state.get("tool_rounds", 0) + 1}

    def route(state: AgentState) -> str:
        last = state["messages"][-1]
        return "tools" if isinstance(last, AIMessage) and last.tool_calls else "finalize"

    def finalize(state: AgentState) -> dict:
        return {"result": summarize_run(state["messages"])}

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent)
    graph.add_node("tools", tools_step)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route, ["tools", "finalize"])
    graph.add_edge("tools", "agent")
    graph.add_edge("finalize", END)
    return graph.compile()


def _numbers(text: str) -> list[float]:
    values = []
    for token in NUMBER.findall(text):
        try:
            values.append(float(token.replace(",", "")))
        except ValueError:
            continue
    return values


def _matches(value: float, candidates: list[float]) -> bool:
    for candidate in candidates:
        if any(abs(round(candidate, digits) - value) < 1e-6 for digits in (0, 1, 2)) or abs(candidate - value) < 1e-6:
            return True
    return False


def unverified_numbers(answer: str, question: str, queries: list[dict[str, Any]]) -> list[str]:
    """Numbers in the answer that are not in any query result (nor in the question)."""
    found: list[float] = []
    for query in queries:
        if query.get("ok"):
            for row in query["rows"]:
                found.extend(float(v) for v in row if isinstance(v, (int, float)) and not isinstance(v, bool))
            found.append(float(len(query["rows"])))
    found.extend(_numbers(question))
    # not claims: list markers like "1." at a line start, and `code` such as `price × 2`
    body = re.sub(r"(?m)^\s*\d+[.)]\s", "", answer)
    body = re.sub(r"`[^`]*`", "", body)
    # 0 is never a data claim ("missing discounts count as 0%")
    return [f"{n:g}" for n in dict.fromkeys(_numbers(body)) if n != 0 and not _matches(n, found)]


def summarize_run(messages: list[AnyMessage]) -> dict[str, Any]:
    answer = messages[-1].text if messages else ""
    question = next((m.text for m in messages if isinstance(m, HumanMessage)), "")
    tool_results = {m.tool_call_id: m for m in messages if isinstance(m, ToolMessage)}
    steps: list[dict[str, Any]] = []
    queries: list[dict[str, Any]] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for key in usage:
            usage[key] += (message.usage_metadata or {}).get(key, 0)
        for call in message.tool_calls:
            result = tool_results.get(call["id"])
            step = {"tool": call["name"], "args": call["args"]}
            if call["name"] == "run_sql" and result is not None and result.artifact:
                queries.append(result.artifact)
                step["ok"] = result.artifact["ok"]
                step["rows"] = len(result.artifact.get("rows", [])) if result.artifact["ok"] else None
                step["error"] = result.artifact.get("error")
            steps.append(step)
    successful = [q for q in queries if q.get("ok")]
    return {
        "answer": answer,
        "queries": queries,
        "steps": steps,
        "usage": usage,
        "grounded": bool(successful),
        "self_corrected": any(not q["ok"] for q in queries) and bool(successful),
        "unverified_numbers": unverified_numbers(answer, question, queries) if successful else [],
    }


def timed_invoke(graph, state: AgentState) -> tuple[AgentState, float]:
    started = time.perf_counter()
    final_state = graph.invoke(state)
    return final_state, round(time.perf_counter() - started, 3)
