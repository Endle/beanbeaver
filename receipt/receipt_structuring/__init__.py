"""Step 2 Receipt Structuring Stage public API."""

from beanbeaver.receipt.ocr_result_parser import parse_receipt
from beanbeaver.receipt.staged_json import (
    build_parsed_receipt_stage,
    clone_stage_document,
    get_receipt_id,
    get_stage_index,
    get_stage_summary,
    load_stage_document,
    receipt_from_stage_document,
    save_stage_document,
)

__all__ = [
    "build_parsed_receipt_stage",
    "clone_stage_document",
    "get_receipt_id",
    "get_stage_index",
    "get_stage_summary",
    "load_stage_document",
    "parse_receipt",
    "receipt_from_stage_document",
    "save_stage_document",
]
