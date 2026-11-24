import pytest

from models.exceptions import RequestException
from services import whatapi
from services.whatapi import WhatAPI


class Clock:
    def __init__(self, now: float = 1_000.0):
        self.now = now
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payloads: list[dict]):
        self.payloads = list(payloads)
        self.calls: list[tuple] = []

    def get(self, url, params=None):
        self.calls.append(("GET", url, params))
        return FakeResponse(self.payloads.pop(0))

    def post(self, url, params=None, data=None, files=None):
        self.calls.append(("POST", url, params, data))
        return FakeResponse(self.payloads.pop(0))


def success(response=None):
    return {"status": "success", "response": response if response is not None else {"ok": True}}


def rate_limited():
    return {"status": "failure", "error": "Rate limit exceeded"}


def make_api(payloads: list[dict]) -> tuple[WhatAPI, FakeSession]:
    api = WhatAPI.__new__(WhatAPI)
    api.base_url = "https://orpheus.network/"
    api.authkey = "auth"
    api._request_times = []
    api._torrentgroup_times = []
    session = FakeSession(payloads)
    api.session = session
    return api, session


def install_clock(monkeypatch, clock: Clock) -> None:
    monkeypatch.setattr(whatapi.time, "time", clock.time)
    monkeypatch.setattr(whatapi.time, "sleep", clock.sleep)


def test_calls_closer_than_five_seconds_are_held_apart(monkeypatch):
    clock = Clock()
    install_clock(monkeypatch, clock)
    api, session = make_api([success(), success()])

    api.request_ajax("index", method="GET")
    assert clock.sleeps == []

    api.request_ajax("index", method="GET")
    assert clock.sleeps == [whatapi.MIN_REQUEST_GAP_SEC]
    assert len(session.calls) == 2


def test_torrentgroup_waits_until_sixty_second_window_has_room(monkeypatch):
    clock = Clock()
    install_clock(monkeypatch, clock)
    api, session = make_api([success(), success()])
    # Nine group fetches sit inside the 60s window and outside the 10s window,
    # so the next one still fits and the one after has to wait on the minute.
    older = [clock.now - 50 + i for i in range(9)]
    api._request_times = list(older)
    api._torrentgroup_times = list(older)

    api.request_ajax("torrentgroup", id=1)
    assert clock.sleeps == []

    api.request_ajax("torrentgroup", id=2)
    assert clock.sleeps == [10.0]
    assert len(session.calls) == 2


def test_index_rate_limit_pauses_for_sixty_seconds(monkeypatch):
    clock = Clock()
    install_clock(monkeypatch, clock)
    api, session = make_api([
        rate_limited(),
        success({"id": 7}),
    ])

    assert api.request_ajax("index", method="GET") == {"id": 7}
    assert clock.sleeps == [whatapi.RATE_LIMIT_PAUSE_SEC]
    assert len(session.calls) == 2


def test_torrentgroup_rate_limit_pauses_for_the_minute_window(monkeypatch):
    clock = Clock()
    install_clock(monkeypatch, clock)
    api, session = make_api([
        rate_limited(),
        success({"group": {"id": 1}}),
    ])

    assert api.request_ajax("torrentgroup", id=418678) == {"group": {"id": 1}}
    assert clock.sleeps == [whatapi.RATE_LIMIT_PAUSE_SEC]
    assert len(session.calls) == 2


def test_rate_limit_gives_up_after_three_attempts(monkeypatch):
    clock = Clock()
    install_clock(monkeypatch, clock)
    api, session = make_api([rate_limited(), rate_limited(), rate_limited()])

    with pytest.raises(RequestException, match="Rate limit exceeded"):
        api.request_ajax("user", method="GET")

    assert clock.sleeps == [
        whatapi.RATE_LIMIT_PAUSE_SEC,
        whatapi.RATE_LIMIT_PAUSE_SEC,
    ]
    assert len(session.calls) == 3
