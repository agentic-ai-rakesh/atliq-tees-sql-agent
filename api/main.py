"""FastAPI backend: ask the AtliQ Tees SQL agent questions about the store's data.

Run with `make backend` (http://127.0.0.1:8000, docs at /docs).
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict
from functools import lru_cache

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field
from sqlalchemy.engine import Engine

from tshirt_agent.agent.examples import SQL_EXAMPLES
from tshirt_agent.agent.sql_agent import ask
from tshirt_agent.config import ConfigError, Settings, load_settings, require_api_key
from tshirt_agent.database import DatabaseNotReadyError, create_readonly_engine, describe_tables, dialect_of
from tshirt_agent.logging_setup import setup_logging
from tshirt_agent.models import create_chat_model

load_dotenv()
logger = logging.getLogger("tshirt_agent.api")

app = FastAPI(
    title="AtliQ Tees SQL Agent API",
    version="2.0.0",
    description=(
        "Ask questions about the t-shirt store's inventory and discounts; a LangGraph agent answers with read-only SQL."
    ),
)

_SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|AIza[0-9A-Za-z_\-]{10,}|://[^:/@\s]+:[^@\s]+@)")


def _safe(error: Exception) -> str:
    """Error text for the client, with API keys and database passwords removed."""
    message = _SECRET.sub("***", str(error)) or type(error).__name__
    return message if len(message) <= 300 else message[:300] + "…"


# ---------- dependencies (overridden in tests) ----------


@lru_cache
def get_settings() -> Settings:
    settings = load_settings()
    setup_logging(settings.log_dir)
    return settings


@lru_cache
def _engine_for(database_url: str) -> Engine:
    return create_readonly_engine(database_url)


def get_engine(settings: Settings = Depends(get_settings)) -> Engine:
    return _engine_for(settings.database_url)


def get_llm(settings: Settings = Depends(get_settings)) -> BaseChatModel:
    return create_chat_model(settings)


class AskRequest(BaseModel):
    question: str = Field(..., description="A question about AtliQ Tees' inventory, prices or discounts.")


# ---------- endpoints ----------


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
def config(settings: Settings = Depends(get_settings)) -> dict:
    """Non-secret settings, so the UI can show what the agent runs with."""
    try:
        require_api_key(settings)
        key_set = True
    except ConfigError:
        key_set = False
    return {
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "database": settings.database_url.split("://")[0],
        "max_result_rows": settings.max_result_rows,
        "max_tool_rounds": settings.max_tool_rounds,
        "api_key_set": key_set,
    }


@app.get("/api/schema")
def schema(engine: Engine = Depends(get_engine)) -> dict:
    return {"dialect": dialect_of(engine), "tables": [asdict(t) for t in describe_tables(engine)]}


@app.get("/api/examples")
def examples() -> list[str]:
    return [question for question, _ in SQL_EXAMPLES] + [
        "How many t-shirts are left in stock in total?",
        "Which t-shirts have the biggest discount, and what do they cost after it?",
        "What is the average price of Adidas t-shirts by size?",
    ]


@app.post("/api/ask")
def ask_question(
    request: AskRequest,
    settings: Settings = Depends(get_settings),
    engine: Engine = Depends(get_engine),
    llm: BaseChatModel = Depends(get_llm),
) -> dict:
    try:
        answer = ask(request.question, llm, engine, settings)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except DatabaseNotReadyError as error:
        raise HTTPException(status_code=503, detail=_safe(error)) from error
    except Exception as error:  # model / network errors
        logger.exception("Agent run failed")
        raise HTTPException(status_code=502, detail=f"The SQL agent failed: {_safe(error)}") from error
    return asdict(answer)


@app.exception_handler(ConfigError)
def config_error_handler(_request, error: ConfigError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(error)})


@app.exception_handler(DatabaseNotReadyError)
def database_error_handler(_request, error: DatabaseNotReadyError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": _safe(error)})
