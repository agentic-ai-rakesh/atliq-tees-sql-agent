"""Test doubles: a scripted chat model and plain-Python ground truth (no API key, no network)."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from tshirt_agent.seed_data import load_source


class ScriptedChatModel(BaseChatModel):
    """Returns the scripted AIMessages in order and records what it was sent."""

    responses: list[AIMessage]
    calls: list[dict[str, Any]] = []
    bound_tools: bool = False

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self.model_copy(update={"bound_tools": True})

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs):
        self.calls.append({"messages": messages, "tools": self.bound_tools})
        if not self.responses:
            raise AssertionError("ScriptedChatModel ran out of responses")
        return ChatResult(generations=[ChatGeneration(message=self.responses.pop(0).model_copy())])


def scripted(*responses: AIMessage) -> ScriptedChatModel:
    return ScriptedChatModel(responses=list(responses), calls=[])


def tool_call(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
        usage_metadata={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
    )


def final(text: str) -> AIMessage:
    return AIMessage(content=text, usage_metadata={"input_tokens": 200, "output_tokens": 20, "total_tokens": 220})


# ---------- ground truth computed in plain Python from the seed rows ----------

SOURCE = load_source()
ROWS = SOURCE.t_shirts
DISCOUNT = {t_shirt_id: pct for _id, t_shirt_id, pct in SOURCE.discounts}


def stock(brand=None, color=None, size=None) -> int:
    return sum(
        r[5]
        for r in ROWS
        if (brand is None or r[1] == brand) and (color is None or r[2] == color) and (size is None or r[3] == size)
    )


def inventory_value(brand=None, size=None, discounted=False) -> float:
    total = 0.0
    for t_id, b, _c, s, price, qty in ROWS:
        if (brand is None or b == brand) and (size is None or s == size):
            factor = (100 - DISCOUNT.get(t_id, 0)) / 100 if discounted else 1
            total += price * qty * factor
    return total
