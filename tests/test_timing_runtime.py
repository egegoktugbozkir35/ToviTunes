"""Optional timing runtime tests: no GPU, weights, provider or network required."""

import json
import socket
import sys
import tomllib
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from tovitunes.music import timing_runtime as runtime
from tovitunes.music.analysis_models import RhythmEvidence
from tovitunes.music.analysis_runtime import RuntimeFailure

BRIEF = SimpleNamespace(target_bpm=112, bpm_range=(100, 124))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Network is forbidden")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(runtime, "_download", blocked)


@pytest.fixture
def cached(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "installed_version", lambda: "1.1.0")
    monkeypatch.setattr(runtime, "_load_detector", lambda *args: object())
    path = runtime.checkpoint_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"fixture checkpoint")
    assert runtime.prepare_timing(tmp_path, allow_download=True)["status"] == "prepared"
    return tmp_path


def test_optional_dependency_isolation():
    project = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    assert not any("torch" in p or "beat-this" in p for p in project["project"]["dependencies"])
    assert project["project"]["optional-dependencies"]["audio-timing"] == ["beat-this==1.1.0"]


def test_permission_download_then_reuse(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(runtime, "installed_version", lambda: "1.1.0")
    monkeypatch.setattr(runtime, "_load_detector", lambda *args: object())

    def download(path):
        calls.append(path)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"checkpoint fixture")

    monkeypatch.setattr(runtime, "_download", download)
    with pytest.raises(RuntimeFailure, match="permission"):
        runtime.prepare_timing(tmp_path)
    first = runtime.prepare_timing(tmp_path, allow_download=True)
    second = runtime.prepare_timing(tmp_path, allow_download=True)
    assert len(calls) == 1
    assert first["action"] == "downloaded" and second["action"] == "reused"
    assert first["sha256"] == second["sha256"] == sha256(b"checkpoint fixture").hexdigest()
    assert first["byte_size"] == len(b"checkpoint fixture")
    assert second["offline_load_validated"]


def test_missing_checkpoint_fails_before_loading(tmp_path, monkeypatch):
    def forbidden(*args):
        raise AssertionError("missing checkpoint must not reach loader")

    monkeypatch.setattr(runtime, "_load_detector", forbidden)
    report = runtime.analyze_rhythm(SimpleNamespace(), BRIEF, tmp_path, "cpu", "a" * 64)
    assert report.status == "unavailable" and report.downbeat_seconds == ()
    assert "checkpoint_missing" in report.failure_reason
    assert not runtime.timing_doctor(tmp_path, "cpu")["offline_timing_ready"]


def test_wrong_hash_and_missing_package_fail_closed(cached, monkeypatch):
    runtime.checkpoint_path(cached).write_bytes(b"altered fixture")
    with pytest.raises(RuntimeFailure, match="unverified"):
        runtime.require_checkpoint(cached)
    assert runtime.prepare_timing(cached, allow_download=True)["status"] == "failed"


def test_missing_package_records_unavailable_rhythm(cached, monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(runtime, "installed_version", lambda: None)
    report = runtime.analyze_rhythm(SimpleNamespace(), BRIEF, cached, "cpu", "a" * 64)
    assert report.status == "unavailable" and report.beat_seconds == report.downbeat_seconds == ()
    assert "package_missing_or_wrong_version" in report.failure_reason
    monkeypatch.setattr(runtime, "installed_version", lambda: None)
    assert runtime.prepare_timing(cached, allow_download=True)["status"] == "failed"


def test_local_path_dbn_disabled_and_fp16_device(cached, monkeypatch):
    # Replace the fixture loader to exercise the public-API constructor adapter itself.
    monkeypatch.undo()
    monkeypatch.setattr(runtime, "installed_version", lambda: "1.1.0")
    calls = []
    hub = SimpleNamespace(load_state_dict_from_url=lambda *a, **k: pytest.fail("download"))
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(hub=hub, cuda=SimpleNamespace(is_available=lambda: True)),
    )
    monkeypatch.setitem(sys.modules, "beat_this", SimpleNamespace())
    monkeypatch.setitem(
        sys.modules,
        "beat_this.inference",
        SimpleNamespace(Audio2Beats=lambda **kw: calls.append(kw)),
    )
    from tovitunes.music.timing_runtime import _load_detector

    for device in ("cpu", "cuda"):
        _load_detector(runtime.checkpoint_path(cached), device)
    assert calls == [
        dict(
            checkpoint_path=str(runtime.checkpoint_path(cached)),
            device=d,
            float16=d == "cuda",
            dbn=False,
        )
        for d in ("cpu", "cuda")
    ]


def test_adapter_tuple_and_offline_guard(cached, monkeypatch):
    signal = object()
    monkeypatch.setattr(runtime, "_pcm_signal", lambda decoded: signal)

    def loader(path, device):
        assert path.is_absolute() and path.name == "beat_this-final0.ckpt" and device == "cpu"

        def detector(pcm, rate):
            assert pcm is signal and rate == 8000
            with pytest.raises(RuntimeFailure, match="offline_network_blocked"):
                socket.create_connection(("example.com", 443))
            return [0.5, 1.0, 1.5, 2.0, 2.5], [1.0, 2.5]

        return detector

    monkeypatch.setattr(runtime, "_load_detector", loader)
    report = runtime.analyze_rhythm(
        SimpleNamespace(sample_rate=8000, duration=3), BRIEF, cached, "cpu", "a" * 64
    )
    assert report.status == "complete"
    assert report.beat_seconds == (0.5, 1, 1.5, 2, 2.5)
    assert report.downbeat_seconds == (1, 2.5)
    assert report.estimated_bpm == 120 and report.interval_cv == 0
    assert report.provenance.model_revision == "sha256:" + runtime.require_checkpoint(cached)[1]
    assert report.provenance.configuration == {"dbn": False, "float16": False}


@pytest.mark.parametrize(
    "beats,downbeats",
    [
        ([0.5, 1], []),
        ([1, 0.5], [1]),
        ([-1, 1], [1]),
        ([0.5, 4], [0.5]),
        ([0.5, float("nan")], [0.5]),
        ([0.5, 1], [0.75]),
        ([0.5, 1], [1, 0.5]),
        ([0.5, 1], [1, 1]),
    ],
)
def test_invalid_detector_output_rejected(beats, downbeats):
    with pytest.raises(RuntimeFailure):
        runtime.measured_rhythm(beats, downbeats, 3, BRIEF)


def test_old_rhythm_without_downbeats_loads():
    old = dict(
        status="complete",
        estimated_bpm=120,
        beat_seconds=[0, 0.5],
        beat_count=2,
        target_bpm=112,
        allowed_bpm_range=[100, 124],
    )
    assert RhythmEvidence.model_validate_json(json.dumps(old)).downbeat_seconds == ()


def test_authoritative_loader_blocks_upstream_retry(cached, monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(runtime, "installed_version", lambda: "1.1.0")
    hub = SimpleNamespace(load_state_dict_from_url=lambda *a, **k: pytest.fail("download"))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(hub=hub))
    monkeypatch.setitem(sys.modules, "beat_this", SimpleNamespace())

    def retry(**kwargs):
        return hub.load_state_dict_from_url("https://example.com/should-not-download")

    monkeypatch.setitem(sys.modules, "beat_this.inference", SimpleNamespace(Audio2Beats=retry))
    with pytest.raises(RuntimeFailure, match="implicit_download_blocked"):
        runtime._load_detector(runtime.checkpoint_path(cached), "cpu")
