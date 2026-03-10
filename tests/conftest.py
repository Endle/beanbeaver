"""Shared pytest fixtures/options for public beanbeaver tests."""

from __future__ import annotations

import os

import pytest
from _pytest.config.argparsing import Parser

from beanbeaver.receipt._rust import require_rust_matcher


def pytest_addoption(parser: Parser) -> None:
    """Custom pytest option for public receipt e2e tests."""
    parser.addoption(
        "--beanbeaver-e2e-mode",
        action="store",
        default="cached",
        choices=["cached", "live", "both"],
        help=(
            "Receipt E2E mode for tests/test_e2e_receipts.py: cached (.ocr.json), live (.jpg -> OCR service), or both."
        ),
    )


def pytest_sessionstart(session: pytest.Session) -> None:
    """Fail fast when the native PyO3 extension is unavailable."""
    os.environ["BEANBEAVER_REQUIRE_RUST_MATCHER"] = "1"
    try:
        require_rust_matcher()
    except ImportError as exc:
        raise pytest.UsageError(
            "beanbeaver._rust_matcher must be built before running pytest"
        ) from exc
