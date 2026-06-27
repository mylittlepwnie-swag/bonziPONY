"""Factory: returns the correct LLMProvider based on config."""

from __future__ import annotations

import logging

from llm.base import LLMProvider

logger = logging.getLogger(__name__)

# Providers that send data to the cloud. In read-only mode these are refused
# and the factory falls through to a local/offline provider instead.
_CLOUD_PROVIDERS = {
    "anthropic", "openai", "openrouter", "deepseek", "groq",
    "gemini", "google", "mistral", "cohere", "xai", "grok", "zai",
}


# Well-known provider → default base_url (user can still override via config)
_KNOWN_BASE_URLS = {
    # Cloud providers
    "openrouter": "https://openrouter.ai/api/v1",
    "deepseek":   "https://api.deepseek.com/v1",
    "groq":       "https://api.groq.com/openai/v1",
    # Local model servers
    "ollama":     "http://localhost:11434/v1",
    "lmstudio":   "http://localhost:1234/v1",
    "lm_studio":  "http://localhost:1234/v1",
    "llamacpp":   "http://localhost:8080/v1",
    "llama_cpp":  "http://localhost:8080/v1",
    "gemini":     "https://generativelanguage.googleapis.com/v1beta/openai",
    "koboldcpp":  "http://localhost:5001/v1",
    "textgen":    "http://localhost:5000/v1",
    "oobabooga":  "http://localhost:5000/v1",
    "vllm":       "http://localhost:8000/v1",
    "localai":    "http://localhost:8080/v1",
}


def get_provider(config) -> LLMProvider:
    """
    Instantiate and return an LLMProvider from AppConfig.llm.

    - ``"anthropic"`` → native Anthropic SDK (supports base_url for proxies)
    - Anything else   → OpenAI-compatible client (openai, openrouter, deepseek,
      groq, z.ai, grok, ollama, custom proxies — anything that speaks the
      OpenAI chat completions format)
    """
    from core.config_loader import LLMConfig
    cfg: LLMConfig = config.llm

    provider = cfg.provider.lower()

    # ── Read-only mode: refuse cloud providers ──────────────────────────
    # When safety.read_only_mode is True we must not send conversation
    # content to a third-party API. Fall back to a local Ollama default so
    # the app still starts. The user can flip safety off to reconnect.
    safety = getattr(config, "safety", None)
    read_only = bool(safety and getattr(safety, "read_only_mode", False))
    if read_only and provider in _CLOUD_PROVIDERS:
        logger.warning(
            "Read-only mode: refusing cloud LLM provider %r. "
            "Falling back to local Ollama at %s. "
            "Disable Read-Only in the right-click menu to restore %s.",
            provider, _KNOWN_BASE_URLS["ollama"], provider,
        )
        provider = "ollama"
        # Blank the api_key so the local server isn't handed a cloud secret
        from llm.openai_provider import OpenAIProvider
        return OpenAIProvider(
            api_key="no-key",
            model=cfg.model if "/" in cfg.model or ":" in cfg.model else "llama3.2",
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
            max_history_turns=cfg.max_history_turns,
            base_url=_KNOWN_BASE_URLS["ollama"],
            prefill=cfg.prefill,
        )

    logger.info("Using LLM provider: %s  model: %s", provider, cfg.model)

    if provider == "anthropic":
        from llm.anthropic_provider import AnthropicProvider
        return AnthropicProvider(
            api_key=cfg.api_key,
            model=cfg.model,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
            max_history_turns=cfg.max_history_turns,
            base_url=cfg.base_url or None,
            prefill=cfg.prefill,
        )

    # Everything else is OpenAI-compatible
    if not cfg.api_key:
        logger.warning(
            "No API key configured for provider '%s'. "
            "LLM calls will likely fail. Set 'api_key' in config.yaml.",
            provider,
        )
    base_url = cfg.base_url or _KNOWN_BASE_URLS.get(provider)
    from llm.openai_provider import OpenAIProvider
    return OpenAIProvider(
        api_key=cfg.api_key or "no-key",
        model=cfg.model,
        temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
        max_history_turns=cfg.max_history_turns,
        base_url=base_url,
        prefill=cfg.prefill,
    )
