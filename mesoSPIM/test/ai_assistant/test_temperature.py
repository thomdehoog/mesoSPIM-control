"""Which models are sent a temperature. A model that refuses one and is sent one cannot be used."""
from mesoSPIM.src.ai_assistant import assistant as ai
from mesoSPIM.src.ai_assistant import config


def _settings(model, kind="anthropic"):
    return ai.model_settings(ai.Endpoint(provider="any", kind=kind, model=model))


def test_the_claude_models_that_refuse_a_temperature_are_sent_none():
    """Haiku 5.5 and Opus 5.5 answer a temperature with a 400 ("`temperature` is deprecated for
    this model"), so the first message of a chat would fail. Sonnet 5.5 accepts one."""
    for refusing in ("claude-haiku-5-5", "claude-opus-5-5"):
        assert "temperature" not in _settings(refusing)
    assert _settings("claude-sonnet-5-5")["temperature"] == config.MODEL_TEMPERATURE


def test_every_other_model_keeps_the_most_likely_answer():
    assert _settings("gemini-3.5-flash-lite", kind="google") == {"temperature": config.MODEL_TEMPERATURE}
    assert _settings("gemma4:26b", kind="openai-compatible") == {"temperature": config.MODEL_TEMPERATURE}


def test_an_anthropic_model_is_still_cached_whether_or_not_it_takes_a_temperature():
    for model in ("claude-opus-5-5", "claude-sonnet-5-5"):
        assert _settings(model)["anthropic_cache_instructions"] == "1h"
