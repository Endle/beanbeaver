from __future__ import annotations

import asyncio
from pathlib import Path

import beanbeaver.runtime.receipt_pipeline as rp
import beanbeaver.runtime.receipt_server as srv
from _pytest.monkeypatch import MonkeyPatch
from beanbeaver.runtime.receipt_pipeline import OCRServiceUnavailable

# --- select_ocr_backend -----------------------------------------------------


def test_explicit_backend_wins_over_models(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(rp, "resolve_models_dir", lambda: Path("/models"))  # models present
    monkeypatch.setenv("OCR_BACKEND", "Container")  # explicit, mixed case
    assert rp.select_ocr_backend() == "container"
    monkeypatch.setenv("OCR_BACKEND", "native")
    assert rp.select_ocr_backend() == "native"


def test_default_native_when_models_present(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OCR_BACKEND", raising=False)
    monkeypatch.setattr(rp, "resolve_models_dir", lambda: tmp_path)
    assert rp.select_ocr_backend() == "native"


def test_default_container_when_no_models(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("OCR_BACKEND", raising=False)
    monkeypatch.setattr(rp, "resolve_models_dir", lambda: None)
    assert rp.select_ocr_backend() == "container"


def test_blank_env_is_treated_as_unset(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("OCR_BACKEND", "   ")
    monkeypatch.setattr(rp, "resolve_models_dir", lambda: None)
    assert rp.select_ocr_backend() == "container"


# --- server _acquire_ocr_result dispatch ------------------------------------

_RAW = {"image_width": 1, "image_height": 2, "detections": []}


def test_acquire_native_success(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(srv, "select_ocr_backend", lambda: "native")
    monkeypatch.setattr(srv, "call_ocr_native", lambda path: _RAW)
    result, code, message = asyncio.run(_acquire(tmp_path))
    assert result == _RAW
    assert (code, message) == ("", "")


def test_acquire_native_failure_surfaces_instructions(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    def boom(path: Path) -> dict[str, object]:
        raise OCRServiceUnavailable("no models — run bb fetch-models")

    monkeypatch.setattr(srv, "select_ocr_backend", lambda: "native")
    monkeypatch.setattr(srv, "call_ocr_native", boom)
    result, code, message = asyncio.run(_acquire(tmp_path))
    assert result is None
    assert code == "ocr_unreachable"
    assert "bb fetch-models" in message


def test_acquire_container_success(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(srv, "select_ocr_backend", lambda: "container")
    monkeypatch.setattr(srv.httpx, "AsyncClient", _FakeClient)
    result, code, message = asyncio.run(_acquire(tmp_path))
    assert result == {"image_width": 3, "image_height": 4, "detections": []}
    assert (code, message) == ("", "")


def test_acquire_container_http_error(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(srv, "select_ocr_backend", lambda: "container")
    monkeypatch.setattr(srv.httpx, "AsyncClient", _FakeClient500)
    result, code, message = asyncio.run(_acquire(tmp_path))
    assert result is None
    assert code == "ocr_error"
    assert "HTTP 500" in message


async def _acquire(tmp_path: Path) -> tuple[dict[str, object] | None, str, str]:
    return await srv._acquire_ocr_result(tmp_path / "r.jpg", b"resized", "r.jpg")


class _FakeResp:
    status_code = 200
    text = ""

    def json(self) -> dict[str, object]:
        return {"image_width": 3, "image_height": 4, "detections": []}


class _FakeClient:
    def __init__(self, *args: object, **kwargs: object) -> None: ...

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def post(self, *args: object, **kwargs: object) -> _FakeResp:
        return _FakeResp()


class _FakeResp500:
    status_code = 500
    text = "boom"

    def json(self) -> dict[str, object]:
        raise AssertionError("should not parse a non-200 response")


class _FakeClient500(_FakeClient):
    async def post(self, *args: object, **kwargs: object) -> _FakeResp500:  # type: ignore[override]
        return _FakeResp500()
