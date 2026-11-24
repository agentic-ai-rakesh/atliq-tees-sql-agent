import pytest

from tshirt_agent.config import ConfigError, Settings, load_settings, require_api_key

VARS = ["LLM_PROVIDER", "LLM_MODEL", "DATABASE_URL", "MAX_RESULT_ROWS", "LLM_TEMPERATURE"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults():
    s = load_settings()
    assert (s.llm_provider, s.llm_model, s.temperature) == ("google", "gemini-3.5-flash", None)
    assert s.database_url.startswith("sqlite:///") and s.database_url.endswith("data/atliq_tshirts.db")
    assert s.api_key_env == "GOOGLE_API_KEY"


def test_switching_to_openai_picks_its_default_model_and_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "OpenAI")
    s = load_settings()
    assert (s.llm_provider, s.llm_model, s.api_key_env) == ("openai", "gpt-5.5", "OPENAI_API_KEY")


def test_mysql_url_and_limits_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://reader:pw@localhost/atliq_tshirts")
    monkeypatch.setenv("MAX_RESULT_ROWS", "20")
    s = load_settings()
    assert s.database_url.startswith("mysql+pymysql://") and s.max_result_rows == 20


@pytest.mark.parametrize(
    ("name", "value"), [("LLM_PROVIDER", "palm"), ("MAX_RESULT_ROWS", "lots"), ("LLM_TEMPERATURE", "5")]
)
def test_invalid_values_name_the_variable(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError, match=name):
        load_settings()


@pytest.mark.parametrize("value", ["", "your_google_api_key_here"])
def test_missing_or_placeholder_key_is_refused(monkeypatch, value):
    monkeypatch.setenv("GOOGLE_API_KEY", value)
    with pytest.raises(ConfigError, match="GOOGLE_API_KEY"):
        require_api_key(Settings())
