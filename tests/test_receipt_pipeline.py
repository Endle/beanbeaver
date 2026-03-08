from __future__ import annotations

import importlib
import json
from pathlib import Path

import beanbeaver.runtime.receipt_pipeline as receipt_pipeline
from _pytest.monkeypatch import MonkeyPatch
from beanbeaver.runtime.paths import ProjectPaths


def test_save_stage1_ocr_json_writes_named_artifact(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("BEANBEAVER_ROOT", str(tmp_path))
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

    expected_path = ProjectPaths(root=tmp_path).receipts_ocr_json / "sample.stage1.json"
    assert output_path == expected_path
    assert json.loads(output_path.read_text()) == stage1_document
