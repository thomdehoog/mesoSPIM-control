"""Every Remote Control call leaves one line in the log: who, what, how it ended."""
import logging

from mesoSPIM.src.remote_control import call_log
from mesoSPIM.src.remote_control.servers import Acceptor
from mesoSPIM.test.remote_control.support.fakes import RecordingCore


def _lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("Remote Control |")]


def test_a_read_a_change_and_a_refusal_are_each_one_line(caplog, monkeypatch):
    monkeypatch.setattr(call_log, "_user", "anna")
    acceptor = Acceptor(RecordingCore())
    acceptor.client = "AI Assistant (some-model)"
    with caplog.at_level(logging.INFO):
        acceptor.dispatch("get_position", {})
        acceptor.dispatch("get_state", {}, client="TCP 10.0.0.5")
        try:
            acceptor.dispatch("no_such_call", {})
        except Exception:
            pass
    lines = _lines(caplog)
    assert len(lines) == 3
    assert lines[0].startswith("Remote Control | anna | AI Assistant (some-model) | get_position {} | ok | ")
    assert " | TCP 10.0.0.5 | get_state {} | ok | " in lines[1]
    assert " | no_such_call {} | refused: " in lines[2]


def test_long_values_are_replaced_by_their_size():
    text = call_log.as_text({"image": {"base64": "A" * 5000}, "bin": 2})
    assert "<5000 characters>" in text and "AAAA" not in text and '"bin": 2' in text


def test_a_failure_to_write_never_fails_the_call(monkeypatch):
    monkeypatch.setattr(call_log, "as_text", lambda value: 1 / 0)
    call_log.record("TCP", "get_state", {}, 0.1, result={})     # does not raise
