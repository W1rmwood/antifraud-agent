"""API для телефона в ui/server.py (docs/ios/architecture.md): /api/info, remote-звонок, приём звука,
код доступа, сторож тишины.

Модели не нужны: вместо agent.run --source remote запускается tests/fakes/fake_remote_agent.py —
он читает stdin так же и сохраняет всё принятое, чтобы сверить звук байт в байт.
"""

import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent import ui_api
from ui import server as ui_server

TOKEN = "123456"
EXAMPLE = "data/mock_events/example.jsonl"


def read_stream(url: str) -> list[tuple[str, dict]]:
    """Весь поток событий до `end`: [(тип SSE-события, данные)] — как в test_ui_server.py."""
    out, event = [], None
    with urllib.request.urlopen(url, timeout=20) as resp:
        for raw in resp:
            line = raw.decode().rstrip("\n")
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                out.append((event, json.loads(line[6:])))
                if event == "end":
                    break
    return out


def fake_start_call(source="file", path=None, phone=None, call_id=None, realtime=True) -> ui_api.CallProcess:
    """ui_api.start_call для remote, но с подставным агентом вместо настоящего."""
    assert source == "remote"
    call_id = call_id or ui_api.new_call_id()
    args = ["tests.fakes.fake_remote_agent", "--call-id", call_id] + (["--phone", phone] if phone else [])
    return ui_api._spawn(args, call_id, audio_in=True)


@pytest.fixture
def base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setattr(ui_api, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(ui_api, "start_call", fake_start_call)
    monkeypatch.setattr(ui_server, "WEB_DIST", tmp_path / "no-dist")
    srv = ui_server.make_server(port=0, token=TOKEN)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    if (call := srv.RequestHandlerClass.calls.running()) is not None:
        call.stop(timeout_s=5)
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def remote_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Запросы будто бы не с этого компьютера: к ним сервер требует код доступа."""
    monkeypatch.setattr(ui_server, "_is_loopback", lambda address: False)


def call(url: str, body: bytes | dict | None = None, token: str | None = None,
         method: str | None = None) -> tuple[int, bytes]:
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data is not None else "GET"))
    if token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def audio(base: str, call_id: str, seq: int | str | None, data: bytes, token: str | None = None) -> tuple[int, bytes]:
    query = "" if seq is None else f"?seq={seq}"
    return call(f"{base}/api/calls/{call_id}/audio{query}", data, token, method="POST")


def start_remote(base: str, token: str | None = None, phone: str | None = None) -> dict:
    status, body = call(f"{base}/api/calls", {"source": "remote", **({"phone": phone} if phone else {})}, token)
    assert status == 201, body
    return json.loads(body)


def wait_until(predicate, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_info_says_this_is_our_server(base: str, remote_client: None) -> None:
    status, body = call(f"{base}/api/info")  # без кода, даже не с этого компьютера
    assert status == 200 and json.loads(body) == {"app": "antifraud", "api": 1, "auth": True}


def test_remote_call_takes_audio_once_per_seq_and_stops_cleanly(base: str, tmp_path: Path) -> None:
    started = start_remote(base, phone="8 999 000 00 42")
    assert started["source"] == "remote" and started["running"]
    cid = started["call_id"]
    a, b, c = b"\x01\x00" * 4000, b"\x02\x00" * 4000, b"\x03\x00" * 10
    assert audio(base, cid, 0, a)[0] == 204
    assert audio(base, cid, 1, b)[0] == 204
    assert audio(base, cid, 1, b"\x09\x00" * 4000)[0] == 204  # повтор после обрыва — не пишется
    assert audio(base, cid, 0, a)[0] == 204
    assert audio(base, cid, 3, c)[0] == 204  # seq 2 потерян — допустимо
    status, body = call(f"{base}/api/calls/{cid}/stop", {})
    assert status == 202 and json.loads(body)["stopping"] in (True, False)
    stream = read_stream(f"{base}/api/calls/{cid}/events")
    agent = [d for e, d in stream if e == "agent"]
    assert agent[0]["type"] == "call_started" and agent[-1]["type"] == "call_ended"
    assert agent[0]["payload"] == {"phone": "+79990000042", "source": {"kind": "remote", "path": None}}
    assert (tmp_path / f"{cid}.pcm").read_bytes() == a + b + c  # «Стоп» закрыл stdin, всё дописано
    assert wait_until(lambda: json.loads(call(f"{base}/api/calls/{cid}")[1])["returncode"] == 0)
    assert audio(base, cid, 4, a)[0] == 409  # звонок закончился


def test_audio_errors(base: str, monkeypatch: pytest.MonkeyPatch) -> None:
    assert audio(base, "nope", 0, b"\x00\x00")[0] == 404
    cid = start_remote(base)["call_id"]
    assert audio(base, cid, None, b"\x00\x00")[0] == 400  # нет seq
    assert audio(base, cid, "abc", b"\x00\x00")[0] == 400
    assert audio(base, cid, -1, b"\x00\x00")[0] == 400
    assert audio(base, cid, 0, b"")[0] == 400  # пустое тело
    status, body = audio(base, cid, 0, b"\x00\x00\x00")  # нечётное число байт
    assert status == 400 and "error" in json.loads(body)
    status, body = audio(base, cid, 0, b"\x00" * (ui_server.AUDIO_MAX_BYTES + 2))
    assert status == 413 and "error" in json.loads(body)
    assert audio(base, cid, 0, b"\x00" * ui_server.AUDIO_MAX_BYTES)[0] == 204  # ровно 1 МБ — можно
    call(f"{base}/api/calls/{cid}/stop", {})
    assert wait_until(lambda: not json.loads(call(f"{base}/api/calls/{cid}")[1])["running"])


def test_audio_to_a_call_that_is_not_remote_is_409(base: str) -> None:
    status, body = call(f"{base}/api/calls", {"source": "replay", "path": EXAMPLE, "speed": 0.01})
    assert status == 201
    status, body = audio(base, json.loads(body)["call_id"], 0, b"\x00\x00")
    assert status == 409 and "remote" in json.loads(body)["error"]


def test_this_computer_needs_no_code(base: str) -> None:
    assert call(f"{base}/api/sources")[0] == 200
    assert call(f"{base}/api/sources", token="000000")[0] == 200  # неверный код с loopback не мешает
    assert call(f"{base}/")[0] == 503  # статика (фронт не собран) — без 401


def test_other_computers_need_the_code(base: str, remote_client: None) -> None:
    for token in (None, "000000", "", "1234567"):
        status, body = call(f"{base}/api/sources", token=token)
        assert status == 401 and "error" in json.loads(body)
    assert call(f"{base}/api/sources", token=TOKEN)[0] == 200
    # Статика фронта для внешних адресов тоже закрыта.
    assert call(f"{base}/history")[0] == 401
    assert call(f"{base}/history", token=TOKEN)[0] == 503
    # POST тоже: без кода звонок не запустить и звук не прислать.
    assert call(f"{base}/api/calls", {"source": "remote"})[0] == 401
    assert audio(base, "nope", 0, b"\x00\x00")[0] == 401
    cid = start_remote(base, token=TOKEN)["call_id"]
    assert audio(base, cid, 0, b"\x00\x00", token=TOKEN)[0] == 204
    assert audio(base, cid, 1, b"\x00\x00")[0] == 401
    assert call(f"{base}/api/calls/{cid}/stop", {}, TOKEN)[0] == 202
    # Поток событий — тоже с кодом (звонок остановлен, поток дочитывается до конца).
    assert call(f"{base}/api/calls/{cid}/events")[0] == 401
    status, raw = call(f"{base}/api/calls/{cid}/events", token=TOKEN)
    assert status == 200 and b"call_ended" in raw and raw.rstrip().endswith(b"data: {}")


def test_silence_watchdog_ends_a_lost_phone_call(base: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ui_server, "SILENCE_TIMEOUT_S", 0.6)
    monkeypatch.setattr(ui_server, "WATCHDOG_POLL_S", 0.1)
    cid = start_remote(base)["call_id"]
    for seq in range(4):  # пока звук идёт, сторож молчит
        assert audio(base, cid, seq, b"\x05\x00" * 100)[0] == 204
        time.sleep(0.3)
    # Телефон пропал: через SILENCE_TIMEOUT_S звонок заканчивается штатно, как по «Стоп».
    stream = read_stream(f"{base}/api/calls/{cid}/events")
    assert [d for e, d in stream if e == "agent"][-1]["type"] == "call_ended"
    assert (tmp_path / f"{cid}.pcm").read_bytes() == b"\x05\x00" * 400
    assert audio(base, cid, 4, b"\x00\x00")[0] == 409


def test_feed_after_the_process_ended_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui_api, "RUNS_DIR", tmp_path)
    proc = fake_start_call(source="remote")
    proc.feed(b"\x00\x00")
    proc.close_audio()
    proc.process.wait(timeout=10)
    with pytest.raises(RuntimeError):
        proc.feed(b"\x00\x00")
    proc.stop(timeout_s=1)  # уже закончился — ничего не делает
    # Остальным источникам stdin не нужен: звук им не подать.
    replay = ui_api.start_replay(EXAMPLE, speed=1000)
    assert replay.process.stdin is None and not replay.takes_audio
    with pytest.raises(ValueError):
        replay.feed(b"\x00\x00")
    replay.stop(timeout_s=5)


def test_loopback_addresses() -> None:
    assert all(ui_server._is_loopback(a) for a in ("127.0.0.1", "::1", "::ffff:127.0.0.1"))
    assert not any(ui_server._is_loopback(a) for a in ("192.168.1.5", "10.0.0.2", "::ffff:192.168.1.5", "fe80::1"))


def test_bonjour_is_skipped_without_dns_sd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui_server.shutil, "which", lambda name: None)
    assert ui_server._bonjour(8642) is None


def test_token_comes_from_env_or_is_six_random_digits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ui_server.TOKEN_ENV, "424242")
    assert ui_server.new_token() == "424242"
    monkeypatch.delenv(ui_server.TOKEN_ENV)
    token = ui_server.new_token()
    assert len(token) == 6 and token.isdigit()
