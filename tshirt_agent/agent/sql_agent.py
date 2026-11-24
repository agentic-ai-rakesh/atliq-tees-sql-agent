"""Entry point for one question: validate, build tools on a read-only engine, run the graph."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from sqlalchemy.engine import Engine

from tshirt_agent.agent.graph import build_sql_graph, timed_invoke
from tshirt_agent.agent.prompts import PROMPT_VERSION, system_prompt
from tshirt_agent.agent.tools import make_tools
from tshirt_agent.config import Settings
from tshirt_agent.database import dialect_of

logger = logging.getLogger(__name__)


@dataclass
class AgentAnswer:
    question: str
    answer: str
    queries: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    grounded: bool
    self_corrected: bool
    unverified_numbers: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    latency_seconds: float = 0.0
    model: str = ""
    prompt_version: str = PROMPT_VERSION


def validate_question(question: str, settings: Settings) -> str:
    question = (question or "").strip()
    if not question:
        raise ValueError("Type a question first.")
    if len(question) > settings.max_question_chars:
        raise ValueError(f"The question is too long (max {settings.max_question_chars} characters).")
    return question


def ask(question: str, llm: BaseChatModel, engine: Engine, settings: Settings) -> AgentAnswer:
    question = validate_question(question, settings)
    tools = make_tools(engine, settings.max_result_rows, settings.query_timeout_seconds)
    prompt = system_prompt(dialect_of(engine), settings.max_result_rows)
    graph = build_sql_graph(llm, tools, prompt, settings.max_tool_rounds)
    final_state, latency = timed_invoke(graph, {"messages": [HumanMessage(question)], "tool_rounds": 0})
    result = final_state["result"]
    logger.info(
        "Answered in %.2fs: %d steps, %d queries, unverified=%s, tokens=%s",
        latency,
        len(result["steps"]),
        len(result["queries"]),
        result["unverified_numbers"],
        result["usage"].get("total_tokens"),
    )
    model = getattr(llm, "model_name", None) or getattr(llm, "model", None) or type(llm).__name__
    return AgentAnswer(question=question, latency_seconds=latency, model=str(model), **result)
