"""Public end-to-end tests for receipt processing in cached/live modes.

Each test case consists of files in tests/receipts_e2e/:
  - Optional JPG image: <name>.jpg
  - Optional OCR JSON: <name>.ocr.json
  - Required expected results: <name>.expected.json
"""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
from importlib.util import find_spec
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from beanbeaver.domain.receipt import Receipt
from beanbeaver.receipt.formatter import format_parsed_receipt
from beanbeaver.receipt.ocr_helpers import resize_image_bytes, transform_paddleocr_result
from beanbeaver.receipt.ocr_result_parser import parse_receipt
from beanbeaver.runtime.item_category_rules import load_item_category_rule_layers

RECEIPTS_DIR = Path(__file__).parent / "receipts_e2e"

HAS_PIL = find_spec("PIL") is not None


@dataclass(frozen=True)
class E2ECase:
    name: str
    expected_path: Path
    jpg_path: Path | None
    ocr_path: Path | None


def find_e2e_test_cases() -> list[E2ECase]:
    """Find test cases by <name>.expected.json and optional JPG/OCR artifacts."""
    test_cases: list[E2ECase] = []
    for expected_path in RECEIPTS_DIR.glob("*.expected.json"):
        name = expected_path.name.removesuffix(".expected.json")
        jpg_path = RECEIPTS_DIR / f"{name}.jpg"
        ocr_path = RECEIPTS_DIR / f"{name}.ocr.json"
        test_cases.append(
            E2ECase(
                name=name,
                expected_path=expected_path,
                jpg_path=jpg_path if jpg_path.exists() else None,
                ocr_path=ocr_path if ocr_path.exists() else None,
            )
        )
    return sorted(test_cases, key=lambda c: c.name)


def load_expected(expected_path: Path) -> dict[str, Any]:
    with open(expected_path) as f:
        return cast(dict[str, Any], json.load(f))


class TestE2EReceiptProcessing:
    @pytest.fixture
    def ocr_service_url(self) -> str:
        return "http://localhost:8001"

    @staticmethod
    def _is_ocr_service_available(ocr_service_url: str) -> bool:
        try:
            response = httpx.get(f"{ocr_service_url}/health", timeout=5.0)
            return response.status_code == 200
        except httpx.RequestError:
            return False

    @pytest.fixture
    def e2e_mode(self, request: pytest.FixtureRequest) -> str:
        return request.config.getoption("--beanbeaver-e2e-mode")

    @pytest.mark.parametrize(
        "test_case",
        find_e2e_test_cases(),
        ids=lambda c: c.name if isinstance(c, E2ECase) else str(c),
    )
    def test_receipt_extraction(self, ocr_service_url: str, e2e_mode: str, test_case: E2ECase) -> None:
        expected = load_expected(test_case.expected_path)
        ran_cached = False
        ran_live = False

        if e2e_mode in {"cached", "both"} and test_case.ocr_path is not None:
            raw_ocr_result = json.loads(test_case.ocr_path.read_text())
            ocr_result = transform_paddleocr_result(raw_ocr_result)
            image_name = test_case.jpg_path.name if test_case.jpg_path else f"{test_case.name}.jpg"
            receipt = parse_receipt(
                ocr_result,
                image_filename=image_name,
                item_category_rule_layers=load_item_category_rule_layers(),
            )
            self._verify_expected(receipt, expected)
            ran_cached = True
        elif e2e_mode == "cached":
            pytest.skip(f"cached mode requires {test_case.name}.ocr.json")

        if e2e_mode in {"live", "both"}:
            if test_case.jpg_path is None:
                if e2e_mode == "live":
                    pytest.skip(f"live mode requires {test_case.name}.jpg")
            elif not HAS_PIL:
                pytest.skip("PIL/Pillow not installed (required for live mode)")
            elif not self._is_ocr_service_available(ocr_service_url):
                pytest.skip("OCR service not available for live mode")
            else:
                image_bytes = resize_image_bytes(test_case.jpg_path.read_bytes())
                response = httpx.post(
                    f"{ocr_service_url}/ocr",
                    files={"file": (test_case.jpg_path.name, image_bytes, "image/jpeg")},
                    timeout=60.0,
                )
                assert response.status_code == 200, f"OCR failed: {response.text}"
                raw_ocr_result = response.json()
                ocr_result = transform_paddleocr_result(raw_ocr_result)
                receipt = parse_receipt(
                    ocr_result,
                    image_filename=test_case.jpg_path.name,
                    item_category_rule_layers=load_item_category_rule_layers(),
                )
                self._verify_expected(receipt, expected)
                ran_live = True

        assert ran_cached or ran_live, (
            f"No mode executed for case {test_case.name} with --beanbeaver-e2e-mode {e2e_mode}. "
            "Check available artifacts (.jpg/.ocr.json)."
        )

    def _verify_expected(self, receipt: Receipt, expected: dict[str, Any]) -> None:
        if "merchant" in expected:
            expected_merchant = expected["merchant"]
            actual_merchant = receipt.merchant or ""
            merchant_optional = expected.get("merchant_optional", False)
            merchant_any_of = expected.get("merchant_any_of", [])

            if not self._merchant_matches(expected_merchant, actual_merchant, merchant_any_of):
                assert merchant_optional, f"Merchant mismatch: expected '{expected_merchant}', got '{actual_merchant}'"

        if "date" in expected:
            expected_date = expected["date"]
            actual_date = receipt.date.isoformat() if receipt.date else None
            assert actual_date == expected_date, f"Date mismatch: expected '{expected_date}', got '{actual_date}'"

        expected_total = Decimal(expected["total"])
        assert receipt.total == expected_total, f"Total mismatch: expected {expected_total}, got {receipt.total}"

        if "critical_items" in expected:
            self._verify_critical_items(receipt, cast(list[dict[str, str]], expected["critical_items"]))

        beancount_output = format_parsed_receipt(receipt)
        assert len(beancount_output) > 0, "Beancount output should not be empty"
        assert receipt.date.isoformat() in beancount_output, "Date should appear in output"

    def _verify_critical_items(self, receipt: Receipt, critical_items: list[dict[str, str]]) -> None:
        extracted_items: dict[str, list[tuple[Decimal, str | None]]] = {}
        for item in receipt.items:
            desc_upper = item.description.upper()
            if desc_upper not in extracted_items:
                extracted_items[desc_upper] = []
            extracted_items[desc_upper].append((item.price, item.category))

        for critical in critical_items:
            desc_pattern = critical["description"].upper()
            expected_price = Decimal(critical["price"])
            expected_category = critical.get("category")

            matching_items: list[tuple[Decimal, str | None]] = []
            for desc, items in extracted_items.items():
                if desc_pattern in desc or desc in desc_pattern:
                    matching_items.extend(items)

            assert matching_items, (
                f"Critical item '{critical['description']}' not found in receipt. "
                f"Extracted items: {list(extracted_items.keys())}"
            )

            matching_prices = [price for price, _ in matching_items]
            assert expected_price in matching_prices, (
                f"Critical item '{critical['description']}' has wrong price. "
                f"Expected {expected_price}, found {matching_prices}"
            )

            if expected_category:
                matching_categories = [category for price, category in matching_items if price == expected_price]
                assert any(expected_category in (cat or "") for cat in matching_categories), (
                    f"Critical item '{critical['description']}' has wrong category. "
                    f"Expected '{expected_category}', found {matching_categories}"
                )

    @staticmethod
    def _normalize_merchant(value: str) -> str:
        return "".join(ch for ch in value.upper() if ch.isalnum())

    def _merchant_matches(self, expected: str, actual: str, any_of: list[str]) -> bool:
        expected_norm = self._normalize_merchant(expected)
        actual_norm = self._normalize_merchant(actual)
        if not expected_norm or not actual_norm:
            return False

        if expected_norm in actual_norm or actual_norm in expected_norm:
            return True

        for alt in any_of:
            alt_norm = self._normalize_merchant(alt)
            if alt_norm and (alt_norm in actual_norm or actual_norm in alt_norm):
                return True

        similarity = SequenceMatcher(None, expected_norm, actual_norm).ratio()
        return similarity >= 0.85


_test_cases = find_e2e_test_cases()
if not _test_cases:
    warnings.warn(
        f"No e2e test cases found. Add .expected.json files to {RECEIPTS_DIR} "
        "and optional matching .jpg/.ocr.json files.",
        stacklevel=2,
    )
