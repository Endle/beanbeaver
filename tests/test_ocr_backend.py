from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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
    response = MagicMock(status_code=200)
    response.json.return_value = {"image_width": 3, "image_height": 4, "detections": []}
    monkeypatch.setattr(srv, "select_ocr_backend", lambda: "container")
    monkeypatch.setattr(srv.httpx, "AsyncClient", _fake_async_client(response))
    result, code, message = asyncio.run(_acquire(tmp_path))
    assert result == {"image_width": 3, "image_height": 4, "detections": []}
    assert (code, message) == ("", "")


def test_acquire_container_http_error(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    response = MagicMock(status_code=500, text="boom")
    monkeypatch.setattr(srv, "select_ocr_backend", lambda: "container")
    monkeypatch.setattr(srv.httpx, "AsyncClient", _fake_async_client(response))
    result, code, message = asyncio.run(_acquire(tmp_path))
    assert result is None
    assert code == "ocr_error"
    assert "HTTP 500" in message


async def _acquire(tmp_path: Path) -> tuple[dict[str, object] | None, str, str]:
    """Invoke the server's OCR-acquisition helper for one fake upload."""
    return await srv._acquire_ocr_result(tmp_path / "r.jpg", b"resized", "r.jpg")


def _fake_async_client(response: object) -> object:
    """Return a drop-in for httpx.AsyncClient whose async `post` yields `response`."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=response)
    return lambda *args, **kwargs: client
