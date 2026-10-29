"""HTTP-мост веб-интерфейса (ui/server.py) — без модели: звонки идут проигрыванием записи."""

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent import ui_api
from ui import server as ui_server

EXAMPLE = "data/mock_events/example.jsonl"


@pytest.fixture
def base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Сервер на свободном порту; логи звонков — во временной папке."""
    monkeypatch.setattr(ui_api, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(ui_server, "WEB_DIST", tmp_path / "no-dist")
    srv = ui_server.make_server(port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    if (call := srv.RequestHandlerClass.calls.running()) is not None:
        call.stop(timeout_s=5)
    srv.shutdown()
    srv.server_close()


def request(url: str, body: dict | None = None) -> tuple[int, dict | list]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def read_stream(url: str) -> list[tuple[str, dict]]:
    """Весь поток событий до `end`: [(тип SSE-события, данные)]."""
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


def test_sources_list_test_calls_and_records(base: str) -> None:
    status, body = request(f"{base}/api/sources")
    assert status == 200 and body["busy"] is None
    assert len(body["calls"]) == len(ui_api.list_test_calls())
    assert {"path", "title", "phone", "scheme_id", "kind"} <= body["calls"][0].keys()
    assert EXAMPLE in [r["path"] for r in body["records"]]


def test_schemes_catalog_and_card(base: str) -> None:
    status, schemes = request(f"{base}/api/schemes")
    assert status == 200 and len(schemes) == len(list(ui_api.SCHEMES_DIR.glob("*.json")))
    assert schemes[0]["stages"][0] == {"n": 1, "name": schemes[0]["stages"][0]["name"]}
    status, scheme = request(f"{base}/api/schemes/relative-accident")
    assert status == 200 and scheme["stall_tactics"]
    assert request(f"{base}/api/schemes/no-such")[0] == 404
    assert request(f"{base}/api/schemes/..%2Fsecret")[0] == 404


def test_replay_streams_the_whole_call_then_ends(base: str) -> None:
    status, call = request(f"{base}/api/calls", {"source": "replay", "path": EXAMPLE, "speed": 1000})
    assert status == 201 and call["running"]
    stream = read_stream(f"{base}/api/calls/{call['call_id']}/events")
    agent = [data for event, data in stream if event == "agent"]
    assert agent[0]["type"] == "call_started" and agent[-1]["type"] == "call_ended"
    assert len(agent) == len(ui_api.read_events(EXAMPLE))
    assert stream[-1][0] == "end"
    # Законченный звонок можно открыть ещё раз — поток отдаёт записанное.
    again = [d for e, d in read_stream(f"{base}/api/calls/{call['call_id']}/events") if e == "agent"]
    assert [e["seq"] for e in again] == [e["seq"] for e in agent]
    # И он есть в истории звонков этого компьютера — как запись, а не как настоящий звонок.
    status, runs = request(f"{base}/api/runs")
    run = next(r for r in runs if r["call_id"] == call["call_id"])
    assert run["ended"] and run["replay"] and run["summary"]["summary"]


def test_only_one_call_at_a_time_and_stop(base: str) -> None:
    status, call = request(f"{base}/api/calls", {"source": "replay", "path": EXAMPLE, "speed": 0.01})
    assert status == 201
    assert request(f"{base}/api/calls", {"source": "replay", "path": EXAMPLE})[0] == 409
    status, stopping = request(f"{base}/api/calls/{call['call_id']}/stop", {})
    assert status == 202 and stopping["call_id"] == call["call_id"]
    stream = read_stream(f"{base}/api/calls/{call['call_id']}/events")
    assert stream[-1][0] == "end"
    assert request(f"{base}/api/calls/{call['call_id']}")[1]["running"] is False


def test_bad_requests(base: str) -> None:
    assert request(f"{base}/api/calls", {"source": "replay", "path": "/etc/passwd"})[0] == 400
    assert request(f"{base}/api/calls", {"source": "file", "path": "data/calls/nope.wav"})[0] == 400
    assert request(f"{base}/api/calls", {"source": "telepathy"})[0] == 400
    assert request(f"{base}/api/calls/nope/stop", {})[0] == 404
    assert request(f"{base}/api/calls/bad%20id/events")[0] == 400
    assert request(f"{base}/api/calls/never-started/events")[0] == 404
    assert request(f"{base}/api/numbers/123")[0] == 400


def test_frontend_not_built_says_how_to_build(base: str) -> None:
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(f"{base}/history", timeout=5)
    assert err.value.code == 503 and "npm run build" in err.value.read().decode()


def test_health_lists_what_a_model_call_needs(base: str) -> None:
    status, health = request(f"{base}/api/health")
    assert status == 200 and isinstance(health["model_ready"], bool)
    keys = [c["key"] for c in health["checks"]]
    assert keys == ["deps", "llm", "embed", "index", "db", "audio", "asr"]
    assert all({"label", "ok", "detail", "fix"} <= c.keys() for c in health["checks"])
    # Без зависимостей, модели или индекса звонок через модель не готов.
    if not all(c["ok"] for c in health["checks"] if c["key"] in ("deps", "llm", "embed", "index")):
        assert health["model_ready"] is False


def test_runs_show_a_call_while_it_is_going(base: str) -> None:
    status, call = request(f"{base}/api/calls", {"source": "replay", "path": EXAMPLE, "speed": 0.05})
    assert status == 201
    for _ in range(50):  # процесс пишет первое событие не мгновенно
        runs = request(f"{base}/api/runs")[1]
        run = next((r for r in runs if r["call_id"] == call["call_id"]), None)
        if run:
            break
        threading.Event().wait(0.1)
    assert run and run["running"] and run["replay"] and not run["ended"]


def upload(base: str, name: str, data: bytes) -> tuple[int, dict]:
    req = urllib.request.Request(f"{base}/api/upload?name={urllib.request.quote(name)}", data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_upload_saves_a_call_file_for_the_agent(base: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui_server, "UPLOADS_DIR", ui_api.ROOT / "data" / "uploads" / "pytest")
    status, saved = upload(base, "звонок бабушке.txt", b"caller: \u0410\u043b\u043b\u043e\n")
    try:
        assert status == 201 and saved["path"].startswith("data/uploads/pytest/") and saved["path"].endswith(".txt")
        assert ui_server._is_upload(saved["path"])
        assert upload(base, "virus.exe", b"MZ")[0] == 400
        assert upload(base, "empty.wav", b"")[0] == 400
        # Чужой путь вместо загрузки агент не получит; неверный номер — понятная ошибка, а не упавший агент.
        assert request(f"{base}/api/calls", {"source": "file", "path": "agent/run.py"})[0] == 400
        assert request(f"{base}/api/calls", {"source": "file", "path": saved["path"], "phone": "12"})[0] == 400
    finally:
        for f in (ui_api.ROOT / "data" / "uploads" / "pytest").glob("*"):
            f.unlink()
        (ui_api.ROOT / "data" / "uploads" / "pytest").rmdir()
