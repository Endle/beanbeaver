from __future__ import annotations

import importlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import beanbeaver.runtime.paths as runtime_paths
import beanbeaver.runtime.receipt_pipeline as receipt_pipeline
import beanbeaver.runtime.receipt_storage as receipt_storage
from _pytest.monkeypatch import MonkeyPatch
from beanbeaver.domain.receipt import Receipt


def test_save_stage1_ocr_json_writes_named_artifact(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("BEANBEAVER_ROOT", str(tmp_path))
    runtime_paths.reset_paths()
    importlib.reload(receipt_pipeline)

    receipt_path = tmp_path / "receipts" / "sample.jpg"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_bytes(b"fake-image")

    stage1_document = {
        "schema_version": "ocr.v1",
        "engine": {"name": "paddleocr", "version": None},
        "source": {"image_width": 100, "image_height": 200},
        "pages": [],
        "full_text": "",
        "status": "success",
    }

    output_path = receipt_pipeline.save_stage1_ocr_json(stage1_document, receipt_path)

    expected_path = receipt_path.with_name("sample.stage1.json")
    assert output_path == expected_path
    assert json.loads(output_path.read_text()) == stage1_document


def test_save_stage1_ocr_json_supports_canonical_output_path(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("BEANBEAVER_ROOT", str(tmp_path))
    runtime_paths.reset_paths()
    importlib.reload(receipt_pipeline)
    importlib.reload(receipt_storage)

    receipt_dir = tmp_path / "receipts" / "2026-03-18_store_12_34_abcd"
    output_path = receipt_storage.receipt_ocr_stage1_path(receipt_dir)
    receipt_path = receipt_storage.receipt_source_original_path(receipt_dir)
    stage1_document = {
        "schema_version": "ocr.v1",
        "engine": {"name": "paddleocr", "version": None},
        "pages": [],
        "full_text": "",
        "status": "success",
    }

    saved_path = receipt_pipeline.save_stage1_ocr_json(stage1_document, receipt_path, output_path=output_path)

    assert saved_path == output_path
    assert json.loads(output_path.read_text()) == stage1_document


def test_save_scanned_receipt_writes_canonical_source_and_ocr_artifacts(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("BEANBEAVER_ROOT", str(tmp_path))
    runtime_paths.reset_paths()
    importlib.reload(receipt_storage)

    source_image = tmp_path / "input.jpg"
    source_image.write_bytes(b"original-image")
    raw_ocr_payload = {"detections": [], "status": "success"}
    stage1_payload = {"schema_version": "ocr.v1", "pages": [], "full_text": "", "status": "success"}

    stage_path = receipt_storage.save_scanned_receipt(
        Receipt(
            merchant="Store",
            date=date(2026, 3, 18),
            total=Decimal("12.34"),
            raw_text="TOTAL 12.34",
            image_filename="input.jpg",
        ),
        raw_ocr_payload=raw_ocr_payload,
        stage1_ocr_payload=stage1_payload,
        image_sha256="abc123",
        source_image_path=source_image,
        resized_image_bytes=b"resized-image",
    )

    receipt_dir = receipt_storage.receipt_dir_from_stage_path(stage_path)
    assert stage_path.parent == receipt_dir / "stages"
    assert (receipt_dir / "current.receipt.json").exists()
    assert (receipt_dir / "meta.json").exists()
    assert receipt_storage.receipt_source_original_path(receipt_dir, suffix=".jpg").read_bytes() == b"original-image"
    assert receipt_storage.receipt_source_resized_path(receipt_dir).read_bytes() == b"resized-image"
    assert json.loads(receipt_storage.receipt_ocr_raw_path(receipt_dir).read_text()) == raw_ocr_payload
    assert json.loads(receipt_storage.receipt_ocr_stage1_path(receipt_dir).read_text()) == stage1_payload
