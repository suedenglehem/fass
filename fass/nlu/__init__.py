from fass.nlu.base import AdapterError, NLUAdapter  # noqa: F401
from fass.nlu.rules import RulesAdapter, SUPPORTED_SUMMARY  # noqa: F401
from fass.nlu.llm import LLMAdapter  # noqa: F401


def _llm_from_config() -> LLMAdapter:
    """Build the LLM adapter from the deployment config (M6); env vars
    remain the fallback for a bare development checkout."""
    from fass.config import load_config

    llm = load_config().llm
    if llm.provider == "openai":
        return LLMAdapter(
            api_key=llm.api_key or None, model=llm.model or None,
            provider="openai", host=llm.host or None, port=llm.port or None,
            timeout=llm.timeout,
        )
    return LLMAdapter(api_key=llm.api_key or None, model=llm.model or None,
                      timeout=llm.timeout)


def get_adapter(name: str = "auto") -> NLUAdapter:
    """Resolve the parser: 'rules', 'llm', or 'auto' (LLM when a provider
    is configured — config tab or env — rules otherwise)."""
    if name in ("rules",):
        return RulesAdapter()
    llm = _llm_from_config()
    if name in ("llm", "auto") and llm.available():
        return llm
    if name == "llm":
        what = (
            "no OpenAI-compatible LLM configured (set host/port in the config tab)"
            if llm.provider == "openai"
            else "no ANTHROPIC_API_KEY configured"
        )
        raise AdapterError(
            f"LLM adapter requested but {what}; use --adapter rules for the deterministic parser"
        )
    return RulesAdapter()
