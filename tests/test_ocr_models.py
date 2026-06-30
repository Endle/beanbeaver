from __future__ import annotations

import hashlib
from pathlib import Path

import beanbeaver.runtime.ocr_models as ocr_models
import pytest
from _pytest.monkeypatch import MonkeyPatch
from beanbeaver.runtime.ocr_models import (
    DEFAULT_SET,
    MODEL_SETS,
    MissingModelsError,
    ModelFile,
)


def _make_complete_set(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "a_det.onnx").write_bytes(b"d")
    (directory / "a_rec.onnx").write_bytes(b"r")
    (directory / "a_ori.onnx").write_bytes(b"o")
    return directory


def test_model_sets_have_det_rec_ori() -> None:
    for name, files in MODEL_SETS.items():
        assert len(files) == 3, name
        kinds = {
            suffix
            for model in files
            for suffix in ("_det.onnx", "_rec.onnx", "_ori.onnx")
            if model.filename.endswith(suffix)
        }
        assert kinds == {"_det.onnx", "_rec.onnx", "_ori.onnx"}, name
    assert DEFAULT_SET in MODEL_SETS


def test_manifest_sha256_are_hex64_and_consistent() -> None:
    seen: dict[str, str] = {}
    for files in MODEL_SETS.values():
        for model in files:
            assert len(model.sha256) == 64
            int(model.sha256, 16)  # must be valid hex
            assert model.size > 0
            # A shared file (rec/ori) must carry the same checksum everywhere.
            assert seen.setdefault(model.filename, model.sha256) == model.sha256


def test_models_cache_dir_layout() -> None:
    cache = ocr_models.models_cache_dir()
    assert cache.name == "models"
    assert cache.parent.name == "beanbeaver"


def test_has_complete_set(tmp_path: Path) -> None:
    assert not ocr_models.has_complete_set(tmp_path / "empty")
    assert ocr_models.has_complete_set(_make_complete_set(tmp_path / "full"))

    partial = tmp_path / "partial"
    partial.mkdir()
    (partial / "a_det.onnx").write_bytes(b"d")
    (partial / "a_rec.onnx").write_bytes(b"r")  # no _ori.onnx
    assert not ocr_models.has_complete_set(partial)


def test_resolve_prefers_env_override(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    override = _make_complete_set(tmp_path / "override")
    monkeypatch.setenv(ocr_models.OCR_MODELS_DIR_ENV, str(override))
    monkeypatch.setattr(ocr_models, "_repo_models_dir", lambda: _make_complete_set(tmp_path / "repo"))
    monkeypatch.setattr(ocr_models, "models_cache_dir", lambda: tmp_path / "cache")
    assert ocr_models.resolve_models_dir() == override


def test_resolve_falls_back_to_cache_default_set(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv(ocr_models.OCR_MODELS_DIR_ENV, raising=False)
    monkeypatch.setattr(ocr_models, "_repo_models_dir", lambda: tmp_path / "norepo")
    cache = tmp_path / "cache"
    monkeypatch.setattr(ocr_models, "models_cache_dir", lambda: cache)
    _make_complete_set(cache / DEFAULT_SET)
    assert ocr_models.resolve_models_dir() == cache / DEFAULT_SET


def test_require_models_dir_raises_with_instructions(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv(ocr_models.OCR_MODELS_DIR_ENV, raising=False)
    monkeypatch.setattr(ocr_models, "_repo_models_dir", lambda: tmp_path / "norepo")
    monkeypatch.setattr(ocr_models, "models_cache_dir", lambda: tmp_path / "nocache")
    with pytest.raises(MissingModelsError) as excinfo:
        ocr_models.require_models_dir()
    assert "bb fetch-models" in str(excinfo.value)


def test_verify_file(tmp_path: Path) -> None:
    content = b"weights-bytes"
    model = ModelFile("a_det.onnx", hashlib.sha256(content).hexdigest(), len(content))
    path = tmp_path / "a_det.onnx"

    path.write_bytes(content)
    assert ocr_models.verify_file(path, model)

    path.write_bytes(content + b"x")  # wrong size and digest
    assert not ocr_models.verify_file(path, model)
    assert not ocr_models.verify_file(tmp_path / "missing.onnx", model)


def test_fetch_model_downloads_verifies_and_is_idempotent(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    content = b"the-model-weights"
    model = ModelFile("a_det.onnx", hashlib.sha256(content).hexdigest(), len(content))
    calls = {"n": 0}

    def fake_download(url: str, dest: Path) -> None:
        calls["n"] += 1
        dest.write_bytes(content)

    monkeypatch.setattr(ocr_models, "_download", fake_download)

    out = ocr_models.fetch_model(model, tmp_path / "server")
    assert out.read_bytes() == content
    assert calls["n"] == 1

    # A present, verified file is not re-downloaded.
    ocr_models.fetch_model(model, tmp_path / "server")
    assert calls["n"] == 1


def test_fetch_model_rejects_bad_bytes(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    model = ModelFile("a_det.onnx", "ab" * 32, 5)  # checksum/size won't match download

    def fake_download(url: str, dest: Path) -> None:
        dest.write_bytes(b"corrupted-bytes")

    monkeypatch.setattr(ocr_models, "_download", fake_download)

    with pytest.raises(MissingModelsError):
        ocr_models.fetch_model(model, tmp_path / "server")

    # Neither the final file nor the temp part survives a failed verification.
    assert not (tmp_path / "server" / "a_det.onnx").exists()
    assert not (tmp_path / "server" / "a_det.onnx.part").exists()


def test_fetch_set_unknown_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown model set"):
        ocr_models.fetch_set("does-not-exist")
