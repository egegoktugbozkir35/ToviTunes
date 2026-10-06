"""All model and device loaders are mocked; CI never obtains model assets."""

import json
import os
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tovitunes.music import analysis_runtime as runtime
from tovitunes.music.analysis import AnalysisConfig, transcribe_and_align
from tovitunes.music.benchmark import MusicBenchmark, plan
from tovitunes.music.models import load_brief, load_lyrics
from tovitunes.music.providers import FakeMusicProvider


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("no external calls in runtime tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(FakeMusicProvider, "generate", forbidden)
    monkeypatch.setattr(MusicBenchmark, "provider_resume", forbidden)
    monkeypatch.setattr("tovitunes.music.vertex_lyria.VertexLyriaProvider.generate", forbidden)
    monkeypatch.setattr(
        "tovitunes.music.vertex_lyria.VertexLyriaProvider.generate_with_identity", forbidden
    )
    monkeypatch.setattr(runtime, "vad_asset", lambda: Path(__file__))
    monkeypatch.setitem(sys.modules, "nltk", SimpleNamespace(data=SimpleNamespace(path=[])))
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            __version__="2.8.0+cpu",
            version=SimpleNamespace(cuda=None),
            backends=SimpleNamespace(cudnn=SimpleNamespace(version=lambda: None)),
            cuda=SimpleNamespace(is_available=lambda: False, device_count=lambda: 0),
        ),
    )
    monkeypatch.setitem(sys.modules, "whisperx.asr", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "whisperx.alignment", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "whisperx", SimpleNamespace())


def fill_cache(root, model="small.en"):
    snapshot = root / f"asr/models--Systran--faster-whisper-{model}/snapshots" / ("a" * 40)
    snapshot.mkdir(parents=True)
    reference = snapshot.parent.parent / "refs/main"
    reference.parent.mkdir()
    reference.write_text("a" * 40)
    for name in runtime.asr_files(model):
        (snapshot / name).write_bytes(b"fixture")
    alignment = root / "alignment" / runtime.ALIGNMENT_FILE
    alignment.parent.mkdir()
    alignment.write_bytes(b"fixture")
    tokenizer = root / "nltk/tokenizers/punkt_tab/english"
    tokenizer.mkdir(parents=True)
    for name in runtime.TOKENIZER_FILES:
        (tokenizer / name).write_bytes(b"fixture")
    return snapshot


@pytest.mark.parametrize("status", ["found", "not_found", "failed"])
@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("cuda", [False, True])
def test_doctor(tmp_path, monkeypatch, status, cached, cuda):
    if cached:
        fill_cache(tmp_path)
    monkeypatch.setattr(runtime, "ffmpeg_status", lambda: {"status": status})
    monkeypatch.setattr(runtime, "package_versions", lambda: {"whisperx": "fixture"})
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            __version__="2.8.0+cu128" if cuda else "2.8.0+cpu",
            version=SimpleNamespace(cuda="12.8" if cuda else None),
            backends=SimpleNamespace(
                cudnn=SimpleNamespace(version=lambda: 91002 if cuda else None)
            ),
            cuda=SimpleNamespace(
                is_available=lambda: cuda,
                device_count=lambda: int(cuda),
                get_device_name=lambda index: "fixture GPU",
                get_device_properties=lambda index: SimpleNamespace(total_memory=8 * 1024**3),
            ),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "ctranslate2",
        SimpleNamespace(
            get_cuda_device_count=lambda: int(cuda),
            get_supported_compute_types=lambda device, index: {"float16", "float32"},
        ),
    )
    files_before = list(tmp_path.rglob("*"))
    report = runtime.runtime_doctor(tmp_path, device="auto")
    assert report["selected_device"] == ("cuda" if cuda else "cpu")
    assert report["offline_ready"] == (cached and status == "found")
    assert report["cuda_device_count"] == int(cuda)
    assert report["gpu_name"] == ("fixture GPU" if cuda else None)
    assert report["torch_cuda_build"] == ("12.8" if cuda else None)
    assert report["cudnn_version"] == (91002 if cuda else None)
    assert report["gpu_total_memory_bytes"] == (8 * 1024**3 if cuda else None)
    assert report["cuda_device_queries_ready"] == cuda
    assert list(tmp_path.rglob("*")) == files_before


def test_doctor_absent_whisperx(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "whisperx.asr", None)
    monkeypatch.setattr(runtime, "package_versions", lambda: {"whisperx": None})
    report = runtime.runtime_doctor(tmp_path)
    assert not report["whisperx_installed"] and not report["offline_ready"]
    assert "optional_dependency_missing_or_broken" in report["failures"][0]


@pytest.mark.parametrize("failure", ["torch_build", "cudnn", "ct2_count", "fp16", "ct2_dll"])
def test_cuda_doctor_rejects_incomplete_stack(tmp_path, monkeypatch, failure):
    fill_cache(tmp_path, "large-v3")
    monkeypatch.setattr(runtime, "ffmpeg_status", lambda: {"status": "found"})
    monkeypatch.setattr(runtime, "package_versions", lambda: {"whisperx": "fixture"})
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            __version__="2.8.0+cu128",
            version=SimpleNamespace(cuda=None if failure == "torch_build" else "12.8"),
            backends=SimpleNamespace(
                cudnn=SimpleNamespace(
                    version=lambda: None if failure == "cudnn" else 91002,
                )
            ),
            cuda=SimpleNamespace(
                is_available=lambda: True,
                device_count=lambda: 1,
                get_device_name=lambda index: "fixture GPU",
                get_device_properties=lambda index: SimpleNamespace(total_memory=8 * 1024**3),
            ),
        ),
    )

    def compute_types(*args):
        if failure == "ct2_dll":
            raise RuntimeError("private DLL path; token=secret")
        return {"float32"} if failure == "fp16" else {"float16"}

    monkeypatch.setitem(
        sys.modules,
        "ctranslate2",
        SimpleNamespace(
            get_cuda_device_count=lambda: 0 if failure == "ct2_count" else 1,
            get_supported_compute_types=compute_types,
        ),
    )
    report = runtime.runtime_doctor(tmp_path, "large-v3", "cuda")
    assert not report["offline_ready"] and not report["cuda_device_queries_ready"]
    assert "secret" not in json.dumps(report)
    assert report["selected_device"] == "cuda"
    # CPU diagnostics remain independent of the CUDA runtime.
    assert runtime.runtime_doctor(tmp_path, "large-v3", "cpu")["offline_ready"]


@pytest.mark.parametrize("found,code", [(False, 0), (True, 0), (True, 1)])
def test_ffmpeg_preflight(monkeypatch, found, code):
    monkeypatch.setattr(runtime.shutil, "which", lambda name: "ffmpeg.exe" if found else None)
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=code, stdout="ffmpeg fixture\nrest"),
    )
    result = runtime.ffmpeg_status()
    assert result["status"] == ("not_found" if not found else "found" if code == 0 else "failed")


@pytest.mark.parametrize("asset", ["asr", "alignment", "nltk"])
def test_offline_absent_asset_fails_before_loader(tmp_path, monkeypatch, asset):
    snapshot = fill_cache(tmp_path)
    paths = {
        "asr": snapshot / "tokenizer.json",
        "alignment": tmp_path / "alignment" / runtime.ALIGNMENT_FILE,
        "nltk": tmp_path / "nltk/tokenizers/punkt_tab/english" / runtime.TOKENIZER_FILES[0],
    }
    paths[asset].unlink()
    monkeypatch.setattr("tovitunes.music.analysis.ffmpeg_status", lambda: {"status": "found"})

    def forbidden(*args, **kwargs):
        raise AssertionError("loader should not be reached")

    monkeypatch.setitem(sys.modules, "whisperx", SimpleNamespace(load_model=forbidden))
    brief_root = Path(__file__).resolve().parents[1] / "benchmarks/music"
    spec = plan(
        load_brief(brief_root / "colors_red_v1.yaml"),
        load_lyrics(brief_root / "colors_red_lyrics_v1.yaml"),
        [FakeMusicProvider()],
        attempt=1,
    )[0].canonical_spec
    # Audio parent determines the deterministic cache root.
    monkeypatch.setattr(
        "tovitunes.music.analysis.require_cache",
        lambda root, model: runtime.require_cache(tmp_path, model),
    )
    transcript, alignment, _, _ = transcribe_and_align(
        tmp_path / "fixture.mp3", spec, 30, AnalysisConfig(device="cpu")
    )
    assert transcript.status == "incomplete" and not transcript.recognized_text
    assert asset in transcript.failure_reason
    assert alignment.status == "unavailable"


def test_offline_guard_restores_environment_and_blocks_network(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "fixture")
    connect = socket.socket.connect
    for name in ("create_connection", "connect", "connect_ex"):
        with runtime.model_environment(tmp_path, False):
            assert os.environ["HF_HUB_OFFLINE"] == os.environ["TRANSFORMERS_OFFLINE"] == "1"
            with pytest.raises(runtime.RuntimeFailure, match="offline_network_blocked"):
                # Look up patched method instead of using the saved original.
                if name == "create_connection":
                    socket.create_connection(("example.invalid", 443))
                else:
                    with socket.socket() as client:
                        getattr(client, name)(("example.invalid", 443))
        assert socket.socket.connect is connect
        assert os.environ["HF_HUB_OFFLINE"] == "fixture"


def test_permission_required_before_any_provisioning(tmp_path):
    with pytest.raises(runtime.RuntimeFailure, match="explicit_download_permission_required"):
        runtime.prepare_models(tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("model", ["small.en", "medium.en", "large-v3"])
@pytest.mark.parametrize("cached", [True, False])
def test_preparation_and_reuse(tmp_path, monkeypatch, cached, model):
    if cached:
        fill_cache(tmp_path, model)
    calls = []

    def download(*args, **kwargs):
        assert not cached
        assert args == (model,)
        calls.append("download")
        fill_cache(tmp_path, model)

    def load_model(*args, **kwargs):
        assert kwargs["local_files_only"] is True
        assert Path(args[0]).is_dir()
        assert os.environ["HF_HUB_OFFLINE"] == "1"
        calls.append("validate")

    monkeypatch.setitem(
        sys.modules, "faster_whisper.utils", SimpleNamespace(download_model=download)
    )
    monkeypatch.setitem(
        sys.modules,
        "whisperx",
        SimpleNamespace(
            load_model=load_model, load_align_model=lambda **kwargs: calls.append("align")
        ),
    )
    result = runtime.prepare_models(tmp_path, model, allow_download=True)
    assert result["status"] == "prepared", result
    assert all(
        a["action"] == ("reused" if cached else "downloaded")
        for a in result["assets"]
        if a["asset"] != "vad"
    )
    assert result["assets"][0]["revision"] == "a" * 40
    assert result["assets"][1]["revision"] is None
    assert calls == (["align", "validate"] if cached else ["download", "align", "validate"])
    monkeypatch.setattr(runtime, "_hash_file", lambda *args: pytest.fail("repeat hashing"))
    assert result["assets"][0]["logical_name"] == model
    assert runtime.prepare_models(tmp_path, model, allow_download=True)["status"] == "prepared"


def test_failed_preparation_does_not_expose_exception(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("Authorization: secret https://signed.invalid/?token=secret")

    monkeypatch.setitem(sys.modules, "faster_whisper.utils", SimpleNamespace(download_model=fail))
    result = runtime.prepare_models(tmp_path, allow_download=True)
    assert result["status"] == "failed"
    assert "asr_model_download_failed" in result["failure_reason"]
    assert "secret" not in json.dumps(result) and "https" not in json.dumps(result)


@pytest.mark.parametrize("missing", ["vocabulary.json", "preprocessor_config.json"])
def test_large_v3_cache_requires_multilingual_assets(tmp_path, missing):
    snapshot = fill_cache(tmp_path, "large-v3")
    assert runtime.asr_snapshot(tmp_path, "large-v3") == snapshot
    (snapshot / missing).unlink()
    assert runtime.asr_snapshot(tmp_path, "large-v3") is None


@pytest.mark.parametrize("old_path_exists", [True, False])
def test_cached_large_v3_inventory_does_not_reuse_other_model_hashes(
    tmp_path,
    monkeypatch,
    old_path_exists,
):
    snapshot = fill_cache(tmp_path, "large-v3")
    old_path = Path(__file__) if old_path_exists else tmp_path / "deleted-medium-model.bin"
    (tmp_path / "inventory.json").write_text(
        json.dumps(
            {
                "assets": [
                    {
                        "asset": "asr",
                        "logical_name": "medium.en",
                        "files": [
                            {
                                "path": str(old_path),
                                "size": Path(__file__).stat().st_size,
                                "sha256": "incorrect-other-model-hash",
                            }
                        ],
                    }
                ]
            }
        )
    )
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper.utils",
        SimpleNamespace(
            download_model=lambda *a, **k: pytest.fail("cached model download"),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "whisperx",
        SimpleNamespace(
            load_model=lambda *a, **k: None,
            load_align_model=lambda **k: None,
        ),
    )
    result = runtime.prepare_models(tmp_path, "large-v3", allow_download=True)
    assert result["status"] == "prepared", result
    asset = result["assets"][0]
    assert asset["logical_name"] == "large-v3" and asset["action"] == "reused"
    assert {record["path"] for record in asset["files"]} == {
        str(snapshot / name) for name in runtime.asr_files("large-v3")
    }
    assert all(record["sha256"] != "incorrect-other-model-hash" for record in asset["files"])


def test_nltk_never_downloads_when_offline(tmp_path, monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "nltk",
        SimpleNamespace(download=lambda *a, **k: pytest.fail("offline download")),
    )
    with pytest.raises(runtime.RuntimeFailure, match="nltk_resource_missing"):
        runtime.prepare_tokenizer(tmp_path, False)


def test_cli_doctor_and_prepare_bypass_database(tmp_path, monkeypatch, capsys):
    from tovitunes.cli import main

    config = tmp_path / "config.yaml"
    config.write_text("database_path: absent.db\ndata_root: data\nbrand_root: brands\n")
    monkeypatch.setattr("tovitunes.cli.Database", lambda *a: pytest.fail("database access"))
    monkeypatch.setattr("tovitunes.cli.runtime_doctor", lambda *a: {"offline_ready": False})
    monkeypatch.setattr("tovitunes.cli.prepare_models", lambda *a, **k: {"status": "prepared"})
    for command in (["analysis-doctor"], ["analysis-models", "prepare", "--allow-model-download"]):
        assert main(["--config", str(config), "music-benchmark", *command]) == 0
        json.loads(capsys.readouterr().out)
    assert not (tmp_path / "absent.db").exists()


@pytest.mark.parametrize("model", ["large-v2", "distil-large-v3", "turbo", "medium", "arbitrary"])
def test_preparation_rejects_other_models_before_download(tmp_path, model):
    with pytest.raises(runtime.RuntimeFailure, match="unsupported_preparation_model"):
        runtime.prepare_models(tmp_path, model, allow_download=True)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("model", ["small.en", "medium.en", "large-v3"])
def test_cli_preparation_explicit_allowlist(tmp_path, monkeypatch, model):
    from tovitunes.cli import main

    config = tmp_path / "config.yaml"
    config.write_text("database_path: absent.db\ndata_root: data\nbrand_root: brands\n")
    calls = []
    monkeypatch.setattr("tovitunes.cli.Database", lambda *a: pytest.fail("database access"))
    monkeypatch.setattr(
        "tovitunes.cli.prepare_models",
        lambda root, selected, device, **kw: calls.append((selected, device, kw)) or {},
    )
    args = [
        "--config",
        str(config),
        "music-benchmark",
        "analysis-models",
        "prepare",
        "--asr-model",
        model,
        "--device",
        "cpu",
        "--allow-model-download",
    ]
    assert main(args) == 0
    assert calls == [(model, "cpu", {"allow_download": True})]
    with pytest.raises(SystemExit) as exc:
        main([*args[:7], "arbitrary", *args[8:]])
    assert exc.value.code == 2
    assert len(calls) == 1
