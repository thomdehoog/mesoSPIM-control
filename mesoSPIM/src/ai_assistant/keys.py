"""The API key: checking it with the provider, and remembering it safely when asked to.

An API key is what a provider (Anthropic, Google, OpenAI) uses to know who is asking and whom to
bill. Each person uses their own. This file does two things with it.

**Checking.** Before a chat starts, mesoSPIM asks the provider for its list of models. That
request costs nothing, and it shows at once whether the key is accepted. A wrong key then gives
a plain message in the setup window, and not an error in the middle of the first question.

**Remembering.** By default a key is held only while mesoSPIM runs, and has to be typed again
next time. When the operator ticks "Remember this key on this computer", the key is handed to the
password store of the operating system (on Windows, the Credential Manager, where Windows also
keeps saved network passwords). It is encrypted there and belongs to the account of whoever is
logged in. mesoSPIM never writes a key to a file of its own, to the config file, or to a log.

Remembering needs the small `keyring` library. Without it everything else still works, and the
window says that keys cannot be remembered on this computer.

Maintainer (2026):
    Thom de Hoog
    Center for Microscopy and Image Analysis
    thom.dehoog@zmb.uzh.ch
    thomdehoog@gmail.com
"""

import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

SERVICE = "mesoSPIM AI Assistant"       # the name the keys are kept under in the password store
CHECK_TIMEOUT_S = 10

# Where each kind of provider lists its models, and how it wants the key sent. Asking for this
# list is the cheapest request there is: it uses no tokens.
_LISTS = {
    "anthropic": ("https://api.anthropic.com/v1/models?limit=1",
                  lambda key: {"x-api-key": key, "anthropic-version": "2023-06-01"}),
    "google": ("https://generativelanguage.googleapis.com/v1beta/models?pageSize=1",
               lambda key: {"x-goog-api-key": key}),
    "openai": ("https://api.openai.com/v1/models",
               lambda key: {"Authorization": f"Bearer {key}"}),
}


def _keyring():
    """The keyring library, or None when it is not installed or has nowhere to keep a password
    (a computer without a password store)."""
    try:
        import keyring
        from keyring.backends import fail
    except Exception:                               # not installed, or broken on this system
        return None
    try:
        if isinstance(keyring.get_keyring(), fail.Keyring):
            return None
    except Exception:
        return None
    return keyring


def can_remember():
    """True when this computer has a password store that mesoSPIM can use."""
    return _keyring() is not None


def remembered(provider):
    """The key remembered for a provider by the person logged in, or "" when there is none."""
    store = _keyring()
    if store is None:
        return ""
    try:
        return store.get_password(SERVICE, provider) or ""
    except Exception as error:
        logger.warning("AI Assistant: the remembered key for %s could not be read: %s", provider, error)
        return ""


def remember(provider, key):
    """Hand the key to the password store. Returns True when it is kept there. The key itself is
    never written to the log, only whether it worked."""
    store = _keyring()
    if store is None or not key:
        return False
    try:
        store.set_password(SERVICE, provider, key)
        return True
    except Exception as error:
        logger.warning("AI Assistant: the key for %s could not be remembered: %s", provider, error)
        return False


def forget(provider):
    """Remove the remembered key for a provider. True when none is left afterwards."""
    store = _keyring()
    if store is None:
        return True
    try:
        if store.get_password(SERVICE, provider):
            store.delete_password(SERVICE, provider)
        return True
    except Exception as error:
        logger.warning("AI Assistant: the remembered key for %s could not be removed: %s", provider, error)
        return False


def check(endpoint, opener=urllib.request.urlopen, timeout=CHECK_TIMEOUT_S):
    """Ask the provider whether it accepts the key, with a request that costs nothing.

    Returns (accepted, sentence). `accepted` is True only when the provider answered and took the
    key. The sentence is for the setup window: it says what is wrong and what to do about it.
    `opener` is replaced in the tests, so that no test talks to a real provider."""
    if endpoint.kind == "openai-compatible":
        if not endpoint.base_url:
            return False, "No address is set for this model. Add it to the config file's ai_assistant block."
        url, headers = endpoint.base_url.rstrip("/") + "/models", {}
        if endpoint.api_key:
            headers["Authorization"] = f"Bearer {endpoint.api_key}"
    else:
        if not endpoint.api_key:
            return False, f"Enter your {endpoint.provider} API key."
        url, make_headers = _LISTS[endpoint.kind]
        headers = make_headers(endpoint.api_key)
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with opener(request, timeout=timeout) as reply:
            status = getattr(reply, "status", 200)
    except urllib.error.HTTPError as error:
        status = error.code
    except Exception as error:                      # no network, a firewall, a wrong address
        reason = getattr(error, "reason", None) or error
        return False, (f"{endpoint.provider} could not be reached ({reason}). Check the network connection; "
                       "some networks block these addresses.")
    if 200 <= status < 300:
        return True, f"{endpoint.provider} accepted the key."
    if status in (401, 403):
        return False, f"{endpoint.provider} did not accept this key. Check that it was copied in full."
    return False, f"{endpoint.provider} answered with an error ({status}). Try again in a moment."
