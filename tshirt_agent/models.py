"""Creates the chat model for the configured provider (Google Gemini or OpenAI)."""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from tshirt_agent.config import Settings, require_api_key


def create_chat_model(settings: Settings) -> BaseChatModel:
    require_api_key(settings)
    common = {"model": settings.llm_model, "timeout": settings.request_timeout, "max_retries": settings.max_retries}
    if settings.temperature is not None:  # empty = the model's default (GPT-5.x needs that)
        common["temperature"] = settings.temperature
    if settings.llm_provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(max_output_tokens=settings.max_answer_tokens, **common)
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(max_tokens=settings.max_answer_tokens, **common)
