"""Shared pytest fixtures/options for public beanbeaver tests."""

from __future__ import annotations


def pytest_addoption(parser):
    """Custom pytest option for public receipt e2e tests."""
    parser.addoption(
        "--beanbeaver-e2e-mode",
        action="store",
        default="cached",
        choices=["cached", "live", "both"],
        help=(
            "Receipt E2E mode for vendor/beanbeaver/tests/test_e2e_receipts.py: "
            "cached (.ocr.json), live (.jpg -> OCR service), or both."
        ),
    )
