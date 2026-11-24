import pytest
from fastapi.testclient import TestClient

import api.main as api_main
from tests.fakes import final, scripted, stock, tool_call
from tshirt_agent.config import Settings


@pytest.fixture
def client(settings, engine, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-real")
    state = {"llm": scripted()}
    app = api_main.app
    app.dependency_overrides[api_main.get_settings] = lambda: settings
    app.dependency_overrides[api_main.get_engine] = lambda: engine
    app.dependency_overrides[api_main.get_llm] = lambda: state["llm"]
    test_client = TestClient(app)
    test_client.state = state
    yield test_client
    app.dependency_overrides.clear()


def test_health_config_schema_examples(client):
    assert client.get("/health").json() == {"status": "ok"}
    config = client.get("/api/config").json()
    assert config["api_key_set"] and config["llm_provider"] == "google" and config["database"] == "sqlite"
    schema = client.get("/api/schema").json()
    assert schema["dialect"] == "sqlite" and [t["name"] for t in schema["tables"]] == ["discounts", "t_shirts"]
    assert len(client.get("/api/examples").json()) == 8


def test_ask_returns_answer_queries_and_steps(client):
    levi = stock(brand="Levi", color="White")
    client.state["llm"] = scripted(
        tool_call(
            "run_sql",
            {"query": "SELECT SUM(stock_quantity) FROM t_shirts WHERE brand = 'Levi' AND color = 'White'"},
            "c1",
        ),
        final(f"You have {levi} white Levi's t-shirts."),
    )
    response = client.post("/api/ask", json={"question": "How many white Levi's t-shirts do I have?"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["queries"][0]["rows"] == [[levi]] and body["grounded"]
    assert body["prompt_version"] == "1.0" and body["latency_seconds"] >= 0


def test_empty_question_is_422(client):
    assert client.post("/api/ask", json={"question": "  "}).status_code == 422


def test_model_errors_are_502_with_secrets_removed(client):
    class Broken:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            raise RuntimeError("API key AIzaSyABCDEFGHIJKLMNOP invalid; db mysql://root:secret@host/x")

    client.state["llm"] = Broken()
    response = client.post("/api/ask", json={"question": "Stock?"})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "AIzaSyABCDEFGHIJKLMNOP" not in detail and "secret" not in detail


def test_missing_database_is_503_with_the_fix(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-real")
    api_main._engine_for.cache_clear()
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'none.db'}", log_dir=tmp_path)
    api_main.app.dependency_overrides[api_main.get_settings] = lambda: settings
    try:
        response = TestClient(api_main.app).get("/api/schema")
    finally:
        api_main.app.dependency_overrides.clear()
    assert response.status_code == 503 and "make db" in response.json()["detail"]


def test_missing_api_key_is_503_with_the_fix(settings, engine, monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    api_main.app.dependency_overrides[api_main.get_settings] = lambda: settings
    api_main.app.dependency_overrides[api_main.get_engine] = lambda: engine
    try:
        response = TestClient(api_main.app).post("/api/ask", json={"question": "Stock?"})
    finally:
        api_main.app.dependency_overrides.clear()
    assert response.status_code == 503 and "GOOGLE_API_KEY" in response.json()["detail"]
