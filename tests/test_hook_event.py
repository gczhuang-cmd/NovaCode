"""hook.event 单测：Event 枚举、is_blocking、parse_event（ch12 T6/T13）。"""

from novacode.hook import BLOCKING_EVENTS, Event, is_blocking, parse_event


def test_event_count_and_values():
    assert len(Event) == 11
    assert Event.PRE_TOOL_USE.value == "PreToolUse"
    assert Event.SESSION_START.value == "SessionStart"


def test_is_blocking():
    assert is_blocking(Event.PRE_TOOL_USE)
    assert is_blocking(Event.USER_PROMPT_SUBMIT)
    assert not is_blocking(Event.STOP)
    assert not is_blocking(Event.SESSION_START)
    assert not is_blocking(Event.POST_TOOL_USE)
    assert not is_blocking(Event.PRE_USER_MESSAGE)


def test_blocking_events_set():
    assert BLOCKING_EVENTS == frozenset({Event.PRE_TOOL_USE, Event.USER_PROMPT_SUBMIT})


def test_parse_event():
    assert parse_event("SessionStart") is Event.SESSION_START
    assert parse_event("UnknownEvent") is None
    assert parse_event("") is None
    assert parse_event(42) is None
    assert parse_event(None) is None
