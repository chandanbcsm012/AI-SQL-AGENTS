"""The ONLY module in this codebase allowed to instantiate an LLM or embedding
client. Every agent must go through get_chat_model() / get_embedding_model()
so provider switching (dev=Ollama, prod=Gemini) and fallback stay centralized.
"""
import os
from enum import Enum
from functools import lru_cache


class Provider(str, Enum):
    OLLAMA = "ollama"
    GEMINI = "gemini"


class ModelRole(str, Enum):
    SQL_GEN = "sql_gen"
    GENERAL = "general"
    EMBEDDING = "embedding"


DEFAULT_PROVIDER = Provider(os.getenv("MODEL_PROVIDER", "ollama"))
FALLBACK_PROVIDER = Provider(os.getenv("MODEL_FALLBACK_PROVIDER", "ollama"))

MODEL_MAP = {
    Provider.OLLAMA: {
        # Defaults match `ollama list` on this machine; override via env if
        # your local Ollama has different tags pulled (spec recommends
        # qwen2.5-coder for SQL_GEN and llama3.1:8b for GENERAL).
        ModelRole.SQL_GEN: os.getenv("OLLAMA_SQL_MODEL", "qwen2.5:7b"),
        ModelRole.GENERAL: os.getenv("OLLAMA_GENERAL_MODEL", "llama3.2:latest"),
        ModelRole.EMBEDDING: os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text:latest"),
    },
    Provider.GEMINI: {
        ModelRole.SQL_GEN: "gemini-2.5-flash",
        ModelRole.GENERAL: "gemini-2.5-flash",
        ModelRole.EMBEDDING: "gemini-embedding-001",
    },
}


@lru_cache(maxsize=16)
def _build_chat_client(provider: Provider, model_name: str, temperature: float):
    if provider == Provider.OLLAMA:
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=model_name,
            temperature=temperature,
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        )
    if provider == Provider.GEMINI:
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=model_name,
            temperature=temperature,
            google_api_key=os.getenv("GOOGLE_API_KEY"),
        )
    raise ValueError(f"Unknown provider: {provider}")


def resolve_model_name(role: ModelRole, provider: "Provider | None" = None) -> str:
    """The model name that get_chat_model(role, provider) would build --
    useful for cache keys / logging without instantiating a client."""
    return MODEL_MAP[provider or DEFAULT_PROVIDER][role]


def get_chat_model(
    role: ModelRole = ModelRole.GENERAL,
    provider: "Provider | None" = None,
    temperature: float = 0.0,
):
    """The only entry point agents use to get an LLM.

    Supports a runtime override, e.g.
    get_chat_model(role=ModelRole.SQL_GEN, provider=Provider.GEMINI).
    """
    provider = provider or DEFAULT_PROVIDER
    model_name = MODEL_MAP[provider][role]
    return _build_chat_client(provider, model_name, temperature)


def get_embedding_model(provider: "Provider | None" = None):
    provider = provider or DEFAULT_PROVIDER
    model_name = MODEL_MAP[provider][ModelRole.EMBEDDING]
    if provider == Provider.OLLAMA:
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(
            model=model_name,
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        )
    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    return GoogleGenerativeAIEmbeddings(
        model=model_name, google_api_key=os.getenv("GOOGLE_API_KEY")
    )
