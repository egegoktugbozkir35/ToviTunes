"""Offline checks of the temporary ComfyUI adapter and saved API graph."""

import json
from pathlib import Path

import httpx
import pytest

from tovitunes.benchmark.providers import ProviderFailure, QwenComfyUIImageProvider
from tovitunes.config import load_config
from tovitunes.render.lesson_objects import _spec

WORKFLOW = Path("workflows/qwen_image_2_1_t2i_api.json").resolve()


def test_workflow_patch_is_explicit_deterministic_and_does_not_mutate_file() -> None:
    original = WORKFLOW.read_bytes()
    provider = QwenComfyUIImageProvider(WORKFLOW, width=1024, height=1024)
    assert provider.provider == "qwen_comfyui"
    assert provider.model == "qwen-image-2.1-q8"
    assert provider.capabilities.maximum_reference_images == 0
    spec = _spec("red_apple", "brand-test", white_background=True)
    first = provider.translate(spec)
    second = provider.translate(spec)
    assert first.body == second.body
    assert first.endpoint == "http://127.0.0.1:8188/prompt"
    graph = first.body["prompt"]
    assert spec.prompt() in graph["459:452"]["inputs"]["prompt"]
    assert graph["459:456"]["inputs"]["width"] == 1024
    assert graph["459:456"]["inputs"]["height"] == 1024
    sampler = graph["459:458"]["inputs"]
    assert (sampler["steps"], sampler["cfg"], sampler["sampler_name"], sampler["scheduler"]) == (
        20,
        1.0,
        "euler",
        "simple",
    )
    assert isinstance(sampler["seed"], int)
    other = provider.translate(spec.model_copy(update={"attempt": 2}))
    assert other.body["prompt"]["459:458"]["inputs"]["seed"] != sampler["seed"]
    exported = Path("review/QWEN_EXPORTED_API_WORKFLOW.json").resolve()
    assert QwenComfyUIImageProvider(exported).translate(spec).body == first.body
    assert WORKFLOW.read_bytes() == original


def test_prompt_history_view_and_submission_boundary() -> None:
    events = []
    histories = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal histories
        events.append(request.url.path)
        if request.url.path == "/prompt":
            body = json.loads(request.content)
            assert body["prompt"]["459:458"]["inputs"]["steps"] == 20
            assert body["prompt"]["459:452"]["inputs"]["prompt"]
            return httpx.Response(200, json={"prompt_id": "local-123"})
        if request.url.path == "/history/local-123":
            histories += 1
            if histories == 1:
                return httpx.Response(200, json={})
            return httpx.Response(
                200,
                json={
                    "local-123": {
                        "status": {"status_str": "success", "completed": True},
                        "outputs": {
                            "461": {
                                "images": [
                                    {"filename": "apple.png", "subfolder": "", "type": "output"}
                                ]
                            }
                        },
                    }
                },
            )
        assert request.url.path == "/view"
        assert request.url.params["filename"] == "apple.png"
        return httpx.Response(200, content=b"exact-image-bytes")

    provider = QwenComfyUIImageProvider(
        WORKFLOW, transport=httpx.MockTransport(handle), sleep=lambda _seconds: None
    )
    result = provider.generate(
        _spec("red_apple", "brand-test", white_background=True),
        (),
        on_remote_start=lambda: events.append("boundary"),
    )
    assert events == ["boundary", "/prompt", "/history/local-123", "/history/local-123", "/view"]
    assert result.provider_request_id == "local-123"
    assert result.image_bytes == b"exact-image-bytes"
    assert result.mime_type == "image/png"
    assert result.response_metadata["output_filename"] == "apple.png"
    assert result.response_metadata["workflow_sha256"]
    assert result.response_metadata["seed"]
    assert events.count("/prompt") == 1


def test_execution_error_is_terminal_and_timeout_is_ambiguous() -> None:
    spec = _spec("red_ball", "brand-test", white_background=True)

    def error(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "failed-1"})
        return httpx.Response(
            200,
            json={
                "failed-1": {
                    "status": {
                        "status_str": "error",
                        "completed": False,
                        "messages": [["execution_error", {"exception_message": "bad node"}]],
                    }
                }
            },
        )

    with pytest.raises(ProviderFailure) as failed:
        QwenComfyUIImageProvider(WORKFLOW, transport=httpx.MockTransport(error)).generate(spec, ())
    assert failed.value.outcome == "terminal_failure"
    assert failed.value.provider_request_id == "failed-1"

    calls = []
    ticks = iter([0.0, 0.0, 2.0])

    def pending(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"prompt_id": "pending-1"})

    with pytest.raises(ProviderFailure) as uncertain:
        QwenComfyUIImageProvider(
            WORKFLOW,
            transport=httpx.MockTransport(pending),
            timeout_seconds=1,
            clock=lambda: next(ticks),
        ).generate(spec, ())
    assert uncertain.value.outcome == "ambiguous"
    assert uncertain.value.provider_request_id == "pending-1"
    assert calls.count("/prompt") == 1


def test_missing_or_invalid_workflow_fails_before_submission(tmp_path: Path) -> None:
    called = []
    provider = QwenComfyUIImageProvider(tmp_path / "missing.json")
    with pytest.raises(ProviderFailure, match="workflow unavailable or invalid") as missing:
        provider.generate(
            _spec("red_apple", "brand-test"), (), on_remote_start=lambda: called.append(1)
        )
    assert missing.value.outcome == "terminal_failure"
    assert called == []
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"1": {"class_type": "wrong", "inputs": {}}}', encoding="utf-8")
    with pytest.raises(ProviderFailure) as bad:
        QwenComfyUIImageProvider(invalid).translate(_spec("red_apple", "brand-test"))
    assert bad.value.outcome == "terminal_failure"


def test_connection_loss_is_classified_without_resubmission() -> None:
    spec = _spec("red_apple", "brand-test", white_background=True)
    calls = []

    def refused(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise httpx.ConnectError("offline")

    with pytest.raises(ProviderFailure) as before:
        QwenComfyUIImageProvider(WORKFLOW, transport=httpx.MockTransport(refused)).generate(
            spec, ()
        )
    assert before.value.outcome == "terminal_failure"
    assert calls == ["/prompt"]

    calls.clear()

    def disappeared(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "accepted-1"})
        raise httpx.ConnectError("offline")

    with pytest.raises(ProviderFailure) as after:
        QwenComfyUIImageProvider(WORKFLOW, transport=httpx.MockTransport(disappeared)).generate(
            spec, ()
        )
    assert after.value.outcome == "ambiguous"
    assert after.value.provider_request_id == "accepted-1"
    assert calls == ["/prompt", "/history/accepted-1"]


def test_workflow_path_is_relative_to_config_file(tmp_path: Path) -> None:
    config_file = tmp_path / "local.yaml"
    config_file.write_text(
        "schema_version: 1\n"
        "database_path: data/test.db\n"
        "data_root: data\n"
        "brand_root: brands/tovitunes\n"
        "lesson_object_generation:\n"
        "  provider: qwen_comfyui\n"
        "  model: qwen-image-2.1-q8\n"
        "  workflow_path: workflows/proven.json\n",
        encoding="utf-8",
    )
    assert load_config(config_file).lesson_object_generation.workflow_path == (
        tmp_path / "workflows" / "proven.json"
    )
