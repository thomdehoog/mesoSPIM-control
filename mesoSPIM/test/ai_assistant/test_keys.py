"""The API key: the check with the provider, and remembering it in the password store."""
import sys
import types
import urllib.error

from mesoSPIM.src.ai_assistant import keys
from mesoSPIM.src.ai_assistant.assistant import Endpoint


class _Reply:
    def __init__(self, status):
        self.status = status
    def __enter__(self): return self
    def __exit__(self, *_): return False


def _opener(status=200, error=None, seen=None):
    def opened(request, timeout=None):
        if seen is not None:
            seen.append((request.full_url, dict(request.header_items())))
        if error is not None:
            raise error
        return _Reply(status)
    return opened


def test_an_accepted_key_turns_the_light_green():
    seen = []
    ok, sentence = keys.check(Endpoint.from_preset("Anthropic", api_key="secret"), opener=_opener(seen=seen))
    assert ok and "accepted" in sentence
    url, headers = seen[0]
    assert url.startswith("https://api.anthropic.com/v1/models") and headers["X-api-key"] == "secret"
    assert "secret" not in url and "secret" not in sentence


def test_a_refused_key_and_a_dead_network_say_what_to_do():
    endpoint = Endpoint.from_preset("Gemini", api_key="wrong")
    refused = urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)
    ok, sentence = keys.check(endpoint, opener=_opener(error=refused))
    assert not ok and "did not accept this key" in sentence
    ok, sentence = keys.check(endpoint, opener=_opener(error=urllib.error.URLError("no route to host")))
    assert not ok and "could not be reached" in sentence and "no route to host" in sentence
    ok, sentence = keys.check(endpoint, opener=_opener(status=500))
    assert not ok and "500" in sentence


def test_no_key_is_never_sent(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    seen = []
    ok, sentence = keys.check(Endpoint.from_preset("Anthropic", api_key=""), opener=_opener(seen=seen))
    assert not ok and "Enter your Anthropic API key" in sentence and seen == []


def test_a_model_at_your_own_address_is_asked_there():
    seen = []
    own = Endpoint.from_preset("OpenAI-style", base_url="http://models.example.org/v1/")
    assert keys.check(own, opener=_opener(seen=seen))[0]
    assert seen[0][0] == "http://models.example.org/v1/models" and "Authorization" not in seen[0][1]


def test_a_key_is_remembered_per_provider_and_forgotten(monkeypatch):
    kept = {}
    fake = types.ModuleType("keyring")
    fake.get_keyring = lambda: object()
    fake.get_password = lambda service, name: kept.get((service, name))
    fake.set_password = lambda service, name, value: kept.__setitem__((service, name), value)
    fake.delete_password = lambda service, name: kept.pop((service, name))
    backends, fail = types.ModuleType("keyring.backends"), types.ModuleType("keyring.backends.fail")
    fail.Keyring = type("Keyring", (), {})
    backends.fail = fail
    fake.backends = backends
    for name, module in (("keyring", fake), ("keyring.backends", backends), ("keyring.backends.fail", fail)):
        monkeypatch.setitem(sys.modules, name, module)
    assert keys.can_remember() and keys.remembered("Anthropic") == ""
    assert keys.remember("Anthropic", "secret-a") and keys.remember("Gemini", "secret-g")
    assert keys.remembered("Anthropic") == "secret-a" and keys.remembered("Gemini") == "secret-g"
    assert kept == {(keys.SERVICE, "Anthropic"): "secret-a", (keys.SERVICE, "Gemini"): "secret-g"}
    assert keys.forget("Anthropic") and keys.remembered("Anthropic") == "" and keys.remembered("Gemini") == "secret-g"


def test_without_a_password_store_nothing_is_remembered(monkeypatch):
    monkeypatch.setitem(sys.modules, "keyring", None)            # as if the library were not installed
    assert not keys.can_remember() and not keys.remember("Anthropic", "secret")
    assert keys.remembered("Anthropic") == "" and keys.forget("Anthropic")
