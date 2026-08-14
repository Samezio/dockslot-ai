"""Provider-agnostic chat-model factory.

Swapping providers is a config change (LLM_PROVIDER in .env), not a code
change: every provider goes through langchain's common BaseChatModel
interface (same .invoke() / .with_structured_output() regardless of which
provider is behind it), per CLAUDE.md section 8 (AI Stack) and section 12
(use LangChain where it genuinely simplifies -- here it's exactly what
buys us "any provider" for free).

Nothing outside this file should import a provider-specific SDK
(google.generativeai, openai, ...) directly.
"""
import os
from typing import Optional

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

# Loads .env once, on first import, without overriding variables already
# set in the real environment (e.g. in CI or a container).
load_dotenv()

# One entry per supported provider. "google_genai" and "openai" are
# first-class langchain providers and pick up their API key from the
# standard env var (GOOGLE_API_KEY / OPENAI_API_KEY) automatically.
# "openrouter" isn't a distinct langchain provider -- it's OpenAI-wire-
# compatible, so it rides the "openai" provider with a different
# base_url and its own API key passed explicitly.
_PROVIDERS = {
    "google_genai": {
        "model": "gemini-2.5-flash",
        "model_provider": "google_genai",
    },
    "openai": {
        "model": "gpt-4o-mini",
        "model_provider": "openai",
    },
    "openrouter": {
        "model": "openai/gpt-4o-mini",
        "model_provider": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPEN_ROUNTER_API_KEY",  # matches the name already in .env
        # Dev-safety cap: this key has limited credits. Our structured
        # output is a small JSON object, so this is generous for the
        # real response and just prevents a runaway/looping call from
        # burning credits. Raise or drop once this is more than a dev key.
        "max_tokens": 300,
    },
}

DEFAULT_PROVIDER = "google_genai"


def get_chat_model(provider: Optional[str] = None, **overrides):
    """Return a ready-to-use chat model for the given provider.

    provider defaults to the LLM_PROVIDER env var, then DEFAULT_PROVIDER.
    Extra keyword args (e.g. temperature) are forwarded to init_chat_model.
    """
    provider = provider or os.environ.get("LLM_PROVIDER", DEFAULT_PROVIDER)
    if provider not in _PROVIDERS:
        raise ValueError(
            "Unknown LLM provider '{}'. Known providers: {}".format(
                provider, ", ".join(_PROVIDERS)
            )
        )

    config = dict(_PROVIDERS[provider])
    api_key_env = config.pop("api_key_env", None)
    if api_key_env:
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise RuntimeError(
                "Provider '{}' needs {} set in the environment (.env).".format(provider, api_key_env)
            )
        config["api_key"] = api_key

    config.update(overrides)
    return init_chat_model(**config)
