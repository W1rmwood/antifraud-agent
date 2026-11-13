"""Звук от телефона (source="remote"): PCM s16le из stdin → блоки → реплики → звонок агента.

Сеть, микрофон и модели не нужны: распознавание подменяется (как в test_audio_live.py),
агент работает на фейках (как в test_agent.py).
"""
import asyncio
import io
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from agent import run as agent_run
from agent.config import Settings
from agent.graph import AgentDeps
from audio import asr
from audio.capture import BLOCK_MS, SAMPLE_RATE, load_channels, open_remote
from audio.stream import listen
from contracts import AudioSource, EventType, Speaker
from mcp_server.server import build_server
from rag.chunks import load_schemes, scheme_chunks
from rag.retriever import Retriever
from tests.fakes import InMemoryStorage
from tests.fakes.fake_llm import FakeLLM, no_fraud
from tests.test_audio_live import fake_asr  # noqa: F401 — фикстура

BLOCK = int(SAMPLE_RATE * BLOCK_MS / 1000)


def pcm(samples: np.ndarray) -> bytes:
    """float32 [-1, 1] → PCM s16le, как шлёт телефон."""
    return (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()


def wav_pcm(path: str = "data/calls/011.wav", tail_s: float = 1.5) -> bytes:
    audio = load_channels(path)[0]
    return pcm(np.concatenate([audio, np.zeros(int(tail_s * SAMPLE_RATE), np.float32)]))


class Chunks(io.RawIOBase):
    """Поток, который отдаёт байты заданными порциями — как труба, в которую пишут кусками."""

    def __init__(self, parts: list[bytes]) -> None:
        self.parts = list(parts)

    def readable(self) -> bool:
        return True

    def read1(self, n: int = -1) -> bytes:
        return self.parts.pop(0) if self.parts else b""


async def collect(stream) -> list[np.ndarray]:
    return [block async for block in open_remote(AudioSource(kind="remote"), stream=stream)]


# --- open_remote ---


def test_open_remote_cuts_30ms_blocks_and_ends_with_the_stream() -> None:
    samples = np.full(BLOCK * 2 + BLOCK // 2, 16384, dtype="<i2")  # 2,5 блока; 16384 → 0.5
    blocks = asyncio.run(collect(io.BytesIO(samples.tobytes())))
    assert [len(b) for b in blocks] == [BLOCK, BLOCK, BLOCK // 2]  # хвост короче блока — тоже звук
    assert all(b.dtype == np.float32 for b in blocks)
    assert np.allclose(np.concatenate(blocks), 0.5)


def test_open_remote_glues_an_odd_tail_to_the_next_read() -> None:
    values = np.array([1000, -2000, 3000, -4000, 5000], dtype="<i2")
    raw = values.tobytes()
    # Порции режут отсчёты пополам: 3 + 1 + 5 + 1 байт; последний непарный байт — обрывок, его нет.
    blocks = asyncio.run(collect(Chunks([raw[:3], raw[3:4], raw[4:9], raw[9:10], b"\x7f"])))
    assert np.allclose(np.concatenate(blocks) * 32768, values)


def test_open_remote_empty_stream_gives_nothing() -> None:
    assert asyncio.run(collect(io.BytesIO(b""))) == []


def test_open_remote_read_error_reaches_the_consumer() -> None:
    class Broken(Chunks):
        def read1(self, n: int = -1) -> bytes:
            raise OSError("труба сломалась")

    with pytest.raises(OSError, match="труба сломалась"):
        asyncio.run(collect(Broken([])))


def test_open_remote_reads_a_pipe_without_blocking_the_loop() -> None:
    """Настоящая труба, как stdin агента: блокирующее чтение не останавливает event loop."""
    read_fd, write_fd = os.pipe()
    data = np.arange(BLOCK * 4, dtype="<i2").tobytes()

    def writer() -> None:
        with os.fdopen(write_fd, "wb") as w:
            for i in range(0, len(data), 1000):
                w.write(data[i:i + 1000])
                w.flush()
                time.sleep(0.02)

    async def go() -> tuple[list[np.ndarray], int]:
        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.005)
                ticks += 1

        tick = asyncio.create_task(ticker())
        with os.fdopen(read_fd, "rb") as r:
            blocks = await collect(r)
        tick.cancel()
        return blocks, ticks

    threading.Thread(target=writer, daemon=True).start()
    blocks, ticks = asyncio.run(go())
    assert np.concatenate(blocks).size == BLOCK * 4
    assert ticks > 10  # пока поток ждал данных, loop крутил другие задачи


# --- listen(kind="remote") ---


def test_listen_remote_gives_unknown_phrases_and_finishes_the_last_one(fake_asr) -> None:  # noqa: F811
    # Звук обрывается посреди речи (без тишины в конце): последнюю фразу дораспознаёт flush().
    audio = wav_pcm(tail_s=0)

    async def go():
        stream = io.BytesIO(audio)
        return [u async for u in listen(AudioSource(kind="remote"), "r1",
                                        blocks=lambda src: open_remote(src, stream=stream))]

    utts = asyncio.run(go())
    assert 3 <= len(utts) <= 8
    assert [u.seq for u in utts] == list(range(len(utts)))
    assert {u.speaker for u in utts} == {Speaker.unknown}
    assert utts[-1].t_end > len(audio) / 2 / SAMPLE_RATE - 1.5  # хвост звонка не потерян


def test_listen_remote_reads_stdin_by_default(fake_asr, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BufferedReader(io.BytesIO(wav_pcm()))))

    async def go():
        return [u async for u in listen(AudioSource(kind="remote"), "r2")]

    assert len(asyncio.run(go())) >= 3


# --- agent.run --source remote ---


def test_cli_accepts_remote_source() -> None:
    source, args = agent_run.parse_args(["--call-id", "c1", "--source", "remote", "--json"])
    assert source == AudioSource(kind="remote") and args.call_id == "c1"
    with pytest.raises(SystemExit):
        agent_run.parse_args(["--source", "remote", "--path", "data/calls/001.wav"])


def test_cli_remote_call_ends_normally_when_stdin_ends(
    fake_asr, monkeypatch, tmp_path: Path, capsys  # noqa: F811
) -> None:
    """Конец stdin — штатный конец звонка: отчёт, save_case, call_ended (без SIGTERM)."""
    schemes = load_schemes("data/schemes")
    retriever = Retriever(scheme_chunks(schemes), mode="bm25")
    storage = InMemoryStorage()
    settings = Settings(runs_dir=str(tmp_path / "runs"), retrieval_mode="bm25")
    deps = AgentDeps(retriever=retriever, llm=FakeLLM(no_fraud), settings=settings)
    monkeypatch.setattr(agent_run, "default_deps", lambda _settings: deps)
    server = build_server(retriever, storage, schemes)
    monkeypatch.setattr(agent_run, "default_mcp_target", lambda _settings, _retriever: server)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BufferedReader(io.BytesIO(wav_pcm()))))

    asyncio.run(agent_run._main(AudioSource(kind="remote"), "+79990000042", raw_json=True, call_id="remote-cli"))

    log = (tmp_path / "runs" / "remote-cli.jsonl").read_text(encoding="utf-8").splitlines()
    from contracts import AgentEvent  # noqa: PLC0415

    events = [AgentEvent.model_validate_json(line) for line in log]
    types = [e.type for e in events]
    assert types[0] == EventType.call_started and types[-1] == EventType.call_ended
    assert events[0].payload["source"] == {"kind": "remote", "path": None}
    assert types.count(EventType.transcript) >= 3 and EventType.summary in types
    assert [c.call_id for c in storage.list_recent_cases()] == ["remote-cli"]
    assert '"call_ended"' in capsys.readouterr().out
