"""Native-OCR model resolution, per-user cache, and download (`bb fetch-models`).

The ONNX Runtime engine is statically linked into the ``_rust_matcher`` extension,
so the only redistributable artifact for native OCR is the **model weights**
(~90 MB of ``.onnx``). These are NOT committed to git; they are fetched on demand
into a per-user cache and verified by SHA-256.

Two model sets are published as GitHub Release assets (see ``MODELS_RELEASE_TAG``):

* ``server`` (default) — PP-OCRv5 **server** detection (88 MB): matching-grade
  header accuracy, the faithful "same as the container" set.
* ``mobile`` — PP-OCRv5 **mobile** detection (4.8 MB): lighter, slightly lower
  header accuracy.

Both share the English 436-class recognizer and the textline-orientation model.
"""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from beanbeaver.runtime import get_logger

logger = get_logger(__name__)

# Env override pointing at a directory that already holds a complete model set.
OCR_MODELS_DIR_ENV = "BEANBEAVER_OCR_MODELS_DIR"

# GitHub Release that hosts the model assets. To publish: create a release with
# this exact tag on the repo and upload the four ``.onnx`` files below as assets.
# The pinned SHA-256s mean the bytes are verified regardless of the release.
MODELS_RELEASE_TAG = "ocr-models-v1"
MODELS_BASE_URL = f"https://github.com/Endle/beanbeaver/releases/download/{MODELS_RELEASE_TAG}"


@dataclass(frozen=True)
class ModelFile:
    """One downloadable model weight, pinned by SHA-256 and exact size."""

    filename: str
    sha256: str
    size: int

    @property
    def url(self) -> str:
        return f"{MODELS_BASE_URL}/{self.filename}"


# One catalog entry per distinct file. The recognizer + textline-orientation
# models are shared across both sets, so only the detector differs.
_SERVER_DET = ModelFile(
    "PP-OCRv5_server_det.onnx",
    "ce29b6081118e0bffacd1ba48286dbe51373ad2b3fc6aba9f17df6baeebf4620",
    88118705,
)
_MOBILE_DET = ModelFile(
    "PP-OCRv5_mobile_det.onnx",
    "d5de5df358366210d16419b9636a2fc1efa5d7a20688f38a7869ec7b1a4f4f7d",
    4819576,
)
_EN_REC = ModelFile(
    "PP-OCRv5_mobile_rec.onnx",
    "20b6945179e9aa1dabe8770a0e73dba9a05350431549e58d7e88837f033c2890",
    7870939,
)
_TEXTLINE_ORI = ModelFile(
    "PP-LCNet_x1_0_textline_ori.onnx",
    "b209584534e174a8d3054b08e2d66b874836b6234dc752c0e249c3d44fd7dde6",
    6772806,
)

# Each set needs exactly one detector + one recognizer + one orientation model
# (the Rust engine resolves them by ``_det/_rec/_ori`` suffix).
MODEL_SETS: dict[str, tuple[ModelFile, ...]] = {
    "server": (_SERVER_DET, _EN_REC, _TEXTLINE_ORI),
    "mobile": (_MOBILE_DET, _EN_REC, _TEXTLINE_ORI),
}
DEFAULT_SET = "server"

_REQUIRED_SUFFIXES = ("_det.onnx", "_rec.onnx", "_ori.onnx")


class MissingModelsError(RuntimeError):
    """Raised when native OCR is selected but the model weights aren't present."""


def models_cache_dir() -> Path:
    """Per-user cache dir for downloaded OCR models (cross-platform, XDG-style)."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    elif os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
    else:
        xdg = os.environ.get("XDG_CACHE_HOME")
        base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "beanbeaver" / "models"


def set_cache_dir(set_name: str) -> Path:
    """Cache directory holding the weights for one model set (e.g. ``server``)."""
    return models_cache_dir() / set_name


def _repo_models_dir() -> Path:
    """Developer convenience: the gitignored ``models-desktop/`` at the repo root."""
    return Path(__file__).resolve().parents[1] / "models-desktop"


def has_complete_set(directory: Path) -> bool:
    """True when ``directory`` holds at least one each of det/rec/ori ``.onnx``."""
    if not directory.is_dir():
        return False
    present = {suffix: False for suffix in _REQUIRED_SUFFIXES}
    for onnx in directory.glob("*.onnx"):
        for suffix in _REQUIRED_SUFFIXES:
            if onnx.name.endswith(suffix):
                present[suffix] = True
    return all(present.values())


def _candidate_dirs() -> list[Path]:
    """Resolution precedence: env override → repo dev dir → cache (default set first)."""
    candidates: list[Path] = []
    override = os.environ.get(OCR_MODELS_DIR_ENV)
    if override:
        candidates.append(Path(override))
    candidates.append(_repo_models_dir())
    candidates.append(set_cache_dir(DEFAULT_SET))
    candidates.extend(set_cache_dir(name) for name in MODEL_SETS if name != DEFAULT_SET)
    return candidates


def resolve_models_dir() -> Path | None:
    """Return the first directory holding a complete model set, or ``None``."""
    for candidate in _candidate_dirs():
        if has_complete_set(candidate):
            return candidate
    return None


def require_models_dir() -> Path:
    """Resolve the models directory or raise :class:`MissingModelsError`."""
    resolved = resolve_models_dir()
    if resolved is not None:
        return resolved
    searched = "\n  ".join(str(c) for c in _candidate_dirs())
    raise MissingModelsError(
        "Native OCR is selected (OCR_BACKEND=native) but no model weights were found.\n"
        f"Searched:\n  {searched}\n"
        "Download them with:  bb fetch-models\n"
        "or set OCR_BACKEND=container to use the PaddleOCR container instead."
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(path: Path, model: ModelFile) -> bool:
    """True when ``path`` exists with the model's exact size and SHA-256."""
    try:
        if path.stat().st_size != model.size:
            return False
    except OSError:
        return False
    return _sha256_file(path) == model.sha256


def _download(url: str, dest: Path) -> None:
    """Stream ``url`` to ``dest`` (follows GitHub's redirect to the asset CDN)."""
    import httpx

    with httpx.stream("GET", url, follow_redirects=True, timeout=60.0) as response:
        response.raise_for_status()
        with dest.open("wb") as handle:
            for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                handle.write(chunk)


def fetch_model(model: ModelFile, dest_dir: Path, *, force: bool = False) -> Path:
    """Download one model into ``dest_dir`` (idempotent, SHA-256 verified, atomic).

    Skips the download when a valid copy is already present (unless ``force``).
    Raises :class:`MissingModelsError` if the downloaded bytes fail verification.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / model.filename

    if not force and verify_file(final, model):
        logger.info("%s already present and verified, skipping", model.filename)
        return final

    tmp = final.with_suffix(final.suffix + ".part")
    logger.info("Downloading %s (%.1f MB) from %s", model.filename, model.size / 1e6, model.url)
    try:
        _download(model.url, tmp)
        if not verify_file(tmp, model):
            actual = _sha256_file(tmp) if tmp.exists() else "missing"
            raise MissingModelsError(
                f"Downloaded {model.filename} failed verification "
                f"(expected sha256 {model.sha256}, got {actual}). "
                "The release asset may be wrong or corrupted."
            )
        tmp.replace(final)
    finally:
        tmp.unlink(missing_ok=True)
    logger.info("Installed %s -> %s", model.filename, final)
    return final


def fetch_set(set_name: str, *, force: bool = False) -> Path:
    """Download every weight for ``set_name`` into its cache dir; return that dir."""
    if set_name not in MODEL_SETS:
        raise ValueError(f"unknown model set {set_name!r} (expected one of {sorted(MODEL_SETS)})")
    dest_dir = set_cache_dir(set_name)
    for model in MODEL_SETS[set_name]:
        fetch_model(model, dest_dir, force=force)
    return dest_dir
