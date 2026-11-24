import dataclasses
import json

import pytest

from tests.fakes import final, inventory_value, scripted, stock, tool_call
from tshirt_agent.agent.graph import unverified_numbers
from tshirt_agent.agent.sql_agent import ask
from tshirt_agent.agent.tools import make_tools

NIKE_SQL = "SELECT SUM(stock_quantity) FROM t_shirts WHERE brand = 'Nike' AND color = 'White' AND size = 'XS'"


def test_agent_inspects_schema_runs_sql_and_answers_from_results(engine, settings):
    nike = stock("Nike", "White", "XS")
    llm = scripted(
        tool_call("describe_table", {"table_name": "t_shirts"}, "c1"),
        tool_call("run_sql", {"query": NIKE_SQL}, "c2"),
        final(f"There are {nike} white Nike t-shirts in XS left."),
    )
    result = ask("How many white Nike XS t-shirts are left?", llm, engine, settings)

    assert result.answer == f"There are {nike} white Nike t-shirts in XS left."
    assert result.grounded and not result.self_corrected and result.unverified_numbers == []
    assert result.queries[0]["rows"] == [[nike]] and result.queries[0]["ok"]
    assert [s["tool"] for s in result.steps] == ["describe_table", "run_sql"]
    assert result.steps[1]["rows"] == 1
    assert result.usage == {"input_tokens": 400, "output_tokens": 40, "total_tokens": 440}
    # the schema with exact brand names reached the model
    assert "Van Heusen" in llm.calls[1]["messages"][-1].content
    assert "SQLite" in llm.calls[0]["messages"][0].content


def test_agent_fixes_its_own_sql_error(engine, settings):
    total = inventory_value(size="S")
    llm = scripted(
        tool_call("run_sql", {"query": "SELECT SUM(price * qty) FROM t_shirts WHERE size = 'S'"}, "c1"),
        tool_call("run_sql", {"query": "SELECT SUM(price * stock_quantity) FROM t_shirts WHERE size = 'S'"}, "c2"),
        final(f"The S-size inventory is worth {total:,.0f}."),
    )
    result = ask("What is the S-size inventory worth?", llm, engine, settings)
    assert [q["ok"] for q in result.queries] == [False, True]
    assert "no such column: qty" in result.queries[0]["error"]
    assert "ERROR: no such column" in llm.calls[1]["messages"][-1].content  # error fed back
    assert result.self_corrected and result.grounded and result.unverified_numbers == []


def test_write_requests_are_refused_by_the_tool(engine, settings):
    llm = scripted(
        tool_call("run_sql", {"query": "DELETE FROM discounts"}, "c1"),
        final("I can't delete data: the database is read-only."),
    )
    result = ask("Delete all discounts", llm, engine, settings)
    assert result.queries[0]["ok"] is False and "Only SELECT" in result.queries[0]["error"]
    assert not result.grounded
    assert stock() > 0 and result.unverified_numbers == []


def test_numbers_not_in_the_results_are_flagged(engine, settings):
    nike = stock("Nike", "White", "XS")
    llm = scripted(tool_call("run_sql", {"query": NIKE_SQL}, "c1"), final(f"There are {nike + 7} left."))
    result = ask("How many white Nike XS t-shirts are left?", llm, engine, settings)
    assert result.unverified_numbers == [str(nike + 7)]


def test_tool_rounds_are_capped(engine, settings):
    capped = dataclasses.replace(settings, max_tool_rounds=2)
    llm = scripted(
        tool_call("list_tables", {}, "c1"),
        tool_call("list_tables", {}, "c2"),
        final("I could not get the data."),
    )
    ask("Anything?", llm, engine, capped)
    assert [c["tools"] for c in llm.calls] == [True, True, False]
    assert "You have used all your tool calls" in llm.calls[-1]["messages"][1].content


@pytest.mark.parametrize(("question", "message"), [("  ", "Type a question"), ("x" * 501, "too long")])
def test_invalid_questions_never_reach_the_model(engine, settings, question, message):
    llm = scripted()
    with pytest.raises(ValueError, match=message):
        ask(question, llm, engine, settings)
    assert llm.calls == []


def test_tools_directly(engine):
    list_tables, describe_table, run_sql = make_tools(engine, max_rows=3, timeout_seconds=5)
    assert list_tables.invoke({}) == "discounts: 10 rows\nt_shirts: 80 rows"
    assert json.loads(describe_table.invoke({"table_name": "discounts"}))["rows"] == 10
    assert "Unknown table" in describe_table.invoke({"table_name": "users"})
    content, artifact = run_sql.func("SELECT t_shirt_id FROM t_shirts")
    assert artifact["truncated"] and len(artifact["rows"]) == 3 and "first 3 rows" in content


def test_unverified_number_matching_allows_rounding():
    queries = [{"ok": True, "rows": [[16725.4567, 80]]}]
    assert unverified_numbers("Revenue is 16,725.46 across 80 shirts.", "", queries) == []
    assert unverified_numbers("Revenue is 16,725 (about 16725.5).", "", queries) == []
    assert unverified_numbers("1. Revenue is 99.", "", queries) == ["99"]
    assert unverified_numbers("Size XS has 3.", "top 3 sizes", queries) == []  # number from the question


def test_zero_and_code_spans_are_not_claims():
    queries = [{"ok": True, "rows": [[33112.0]]}]
    answer = "Revenue is $33,112.00, treating missing discounts as 0% (`price × stock_quantity × 0.9`)."
    assert unverified_numbers(answer, "", queries) == []
