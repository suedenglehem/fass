from fass.nlu.base import AdapterError, NLUAdapter  # noqa: F401
from fass.nlu.rules import RulesAdapter, SUPPORTED_SUMMARY  # noqa: F401
from fass.nlu.llm import LLMAdapter  # noqa: F401


def get_adapter(name: str = "auto") -> NLUAdapter:
    """Resolve the parser: 'rules', 'llm', or 'auto' (LLM when a provider
    key is configured, rules otherwise)."""
    if name in ("rules",):
        return RulesAdapter()
    llm = LLMAdapter()
    if name in ("llm", "auto") and llm.available():
        return llm
    if name == "llm":
        raise AdapterError(
            "LLM adapter requested but no ANTHROPIC_API_KEY is configured; "
            "use --adapter rules for the deterministic parser"
        )
    return RulesAdapter()
