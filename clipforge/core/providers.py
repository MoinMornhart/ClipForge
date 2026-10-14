"""Alle unterstützten KI-Anbieter an einer Stelle.

Anthropic läuft über das offizielle SDK, alle anderen über ihre OpenAI-kompatible
Schnittstelle. Lokale Server (Ollama, LM Studio) brauchen keinen Key.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    kind: str  # "anthropic" | "openai_compat"
    base_url: str = ""
    needs_key: bool = True
    key_url: str = ""
    models: tuple[str, ...] = ()  # Vorschläge; die echte Liste wird beim Anbieter abgefragt
    default_model: str = ""
    local: bool = False


LLM_PROVIDERS: dict[str, Provider] = {p.id: p for p in [
    Provider("anthropic", "Anthropic Claude", "anthropic", key_url="https://console.anthropic.com/settings/keys",
             models=("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"),
             default_model="claude-opus-5-5"),
    Provider("openai", "OpenAI", "openai_compat", "https://api.openai.com/v1",
             key_url="https://platform.openai.com/api-keys"),
    Provider("gemini", "Google Gemini", "openai_compat", "https://generativelanguage.googleapis.com/v1beta/openai/",
             key_url="https://aistudio.google.com/apikey"),
    Provider("mistral", "Mistral", "openai_compat", "https://api.mistral.ai/v1",
             key_url="https://console.mistral.ai/api-keys"),
    Provider("groq", "Groq", "openai_compat", "https://api.groq.com/openai/v1",
             key_url="https://console.groq.com/keys"),
    Provider("deepseek", "DeepSeek", "openai_compat", "https://api.deepseek.com/v1",
             key_url="https://platform.deepseek.com/api_keys"),
    Provider("xai", "xAI Grok", "openai_compat", "https://api.x.ai/v1", key_url="https://console.x.ai"),
    Provider("openrouter", "OpenRouter (viele Modelle)", "openai_compat", "https://openrouter.ai/api/v1",
             key_url="https://openrouter.ai/keys"),
    Provider("ollama", "Ollama (lokal)", "openai_compat", "http://localhost:11434/v1", needs_key=False, local=True),
    Provider("lmstudio", "LM Studio (lokal)", "openai_compat", "http://localhost:1234/v1", needs_key=False,
             local=True),
    Provider("custom", "Eigener Server (OpenAI-kompatibel)", "openai_compat", "", needs_key=False),
]}

# Transkriptions-Anbieter (Wortzeitstempel nötig)
STT_PROVIDERS: dict[str, Provider] = {p.id: p for p in [
    Provider("local", "Lokal (faster-whisper, GPU)", "local", needs_key=False, local=True,
             models=("large-v3-turbo", "large-v3", "medium", "small"), default_model="large-v3-turbo"),
    Provider("openai", "OpenAI Whisper", "openai_compat", "https://api.openai.com/v1",
             models=("whisper-1",), default_model="whisper-1"),
    Provider("groq", "Groq Whisper", "openai_compat", "https://api.groq.com/openai/v1",
             models=("whisper-large-v3-turbo", "whisper-large-v3"), default_model="whisper-large-v3-turbo"),
]}

# Alle Anbieter, für die ein Key in der Anmeldeinformationsverwaltung liegen kann
KEY_PROVIDERS = tuple(p.id for p in LLM_PROVIDERS.values() if p.needs_key or p.id == "custom")


@dataclass
class Price:
    input_per_m: float  # US-$ pro 1 Mio. Eingabe-Tokens
    output_per_m: float  # US-$ pro 1 Mio. Ausgabe-Tokens


# Nur Preise, die verlässlich bekannt sind. Alles andere trägt der Nutzer in den Einstellungen ein.
LLM_PRICES: dict[str, Price] = {
    "claude-fable-5-1": Price(10.0, 50.0),
    "claude-opus-5-5": Price(4.0, 20.0),
    "claude-sonnet-5-5": Price(2.0, 10.0),
    "claude-haiku-5-5": Price(0.10, 0.50),
}

# US-$ pro Audiominute
STT_PRICES_PER_MIN: dict[str, float] = {
    "local": 0.0,
    "openai:whisper-1": 0.006,
    "groq:whisper-large-v3-turbo": 0.04 / 60,
    "groq:whisper-large-v3": 0.111 / 60,
}


def llm_price(model: str, overrides: dict[str, list[float]] | None = None, provider: str = "") -> Price | None:
    if overrides and model in overrides:
        i, o = overrides[model]
        return Price(float(i), float(o))
    if provider in ("ollama", "lmstudio"):
        return Price(0.0, 0.0)
    return LLM_PRICES.get(model)
