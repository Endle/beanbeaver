"""Tests for the Wealthsimple chequing activity-export importer."""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from beanbeaver.application.imports.chequing import CHEQUING_TYPES, detect_chequing_type
from beanbeaver.application.imports.csv_routing import route_csv
from beanbeaver.domain.chequing_import import clean_wealthsimple_description, parse_wealthsimple_rows
from beanbeaver.importers.wealthsimple_chequing import WealthsimpleChequingImporter

HEADER = (
    "transaction_date,settlement_date,account_id,account_type,activity_type,activity_sub_type,"
    "description,direction,symbol,name,currency,quantity,unit_price,commission,net_cash_amount\n"
)

CHEQUING_ROWS = (
    "2026-07-03,,WK20P9D33CAD,Chequing,BonusPayment,GIVEAWAY,Giveaway received,,,,CAD,56.96,,,56.96\n"
    "2026-07-23,,WK20P9D33CAD,Chequing,MoneyMovement,AFT_IN,Direct deposit received,,,,CAD,2610.91,,,2610.91\n"
    "2026-07-28,,WK20P9D33CAD,Chequing,MoneyMovement,OBP_OUT,"
    "Online bill payment (executed at 2026-07-28),,,,CAD,-497.86,,,-497.86\n"
)

FOOTER = '\n"As of 2026-08-01 23:10 GMT-04:00"\n'


def _write_export(path: Path, body: str = CHEQUING_ROWS, *, footer: str = FOOTER) -> Path:
    path.write_text(HEADER + body + footer, encoding="utf-8")
    return path


class FileMemo:
    def __init__(self, name: str) -> None:
        self.name = name


def test_route_csv_detects_wealthsimple_activities_export(tmp_path: Path) -> None:
    csv_path = _write_export(tmp_path / "activities-export-2026-08-01.csv")

    routes = route_csv(csv_path)

    assert len(routes) == 1
    assert routes[0].import_type == "chequing"
    assert routes[0].importer_id == "wealthsimple_chequing"
    assert routes[0].rule_id == "chequing-wealthsimple"
    assert routes[0].stage == 2


def test_route_csv_accepts_numbered_wealthsimple_export(tmp_path: Path) -> None:
    csv_path = _write_export(tmp_path / "activities-export-2026-08-01 (1).csv")

    routes = route_csv(csv_path)

    assert [route.importer_id for route in routes] == ["wealthsimple_chequing"]


def test_route_csv_rejects_wealthsimple_export_without_chequing_rows(tmp_path: Path) -> None:
    """Wealthsimple reuses the filename for Cash/TFSA exports; those must not route here."""
    body = "2026-07-03,,WK20P9D33CAD,Cash,BonusPayment,GIVEAWAY,Giveaway received,,,,CAD,56.96,,,56.96\n"
    csv_path = _write_export(tmp_path / "activities-export-2026-08-01.csv", body)

    assert route_csv(csv_path) == []


def test_detect_chequing_type_recognizes_wealthsimple(tmp_path: Path) -> None:
    csv_path = _write_export(tmp_path / "activities-export-2026-08-01.csv")

    assert detect_chequing_type(csv_path) == "wealthsimple"


def test_clean_description_strips_executed_at_suffix() -> None:
    assert clean_wealthsimple_description("Online bill payment (executed at 2026-07-28)") == "Online bill payment"
    assert clean_wealthsimple_description(" Interest received ") == "Interest received"
    assert clean_wealthsimple_description("Cash back - Credit card") == "Cash back - Credit card"


def test_parse_rows_skips_footer_and_foreign_accounts(tmp_path: Path) -> None:
    body = CHEQUING_ROWS + (
        "2026-07-15,,WK20P9D33USD,Cash,MoneyMovement,AFT_IN,Ignored cash row,,,,CAD,10.00,,,10.00\n"
    )
    csv_path = _write_export(tmp_path / "activities-export-2026-08-01.csv", body)

    with open(csv_path, encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    parsed = parse_wealthsimple_rows(rows)

    assert [(row[1], row[2]) for row in parsed] == [
        ("Giveaway received", Decimal("56.96")),
        ("Direct deposit received", Decimal("2610.91")),
        ("Online bill payment", Decimal("-497.86")),
    ]
    # Wealthsimple publishes no running balance.
    assert all(row[3] is None for row in parsed)


def test_importer_emits_signed_postings_and_no_balances(tmp_path: Path) -> None:
    csv_path = _write_export(tmp_path / "activities-export-2026-08-01.csv")
    importer = WealthsimpleChequingImporter(
        account="Assets:Bank:Chequing:Wealthsimple",
        categorization_patterns=[("DIRECT DEPOSIT", "Income:Salary")],
    )

    transactions, balances = importer.extract_with_balances(FileMemo(str(csv_path)))

    assert balances == []
    assert len(transactions) == 3

    deposit = transactions[1]
    assert deposit.payee == "Direct deposit received"
    assert deposit.postings[0].account == "Assets:Bank:Chequing:Wealthsimple"
    assert deposit.postings[0].units.number == Decimal("2610.91")
    assert deposit.postings[1].account == "Income:Salary"
    assert deposit.postings[1].units.number == Decimal("-2610.91")

    payment = transactions[2]
    assert payment.postings[0].units.number == Decimal("-497.86")
    assert payment.postings[1].account == "Expenses:Uncategorized"


def test_wealthsimple_registered_in_chequing_types() -> None:
    spec = CHEQUING_TYPES["wealthsimple"]

    assert spec.label == "Wealthsimple chequing"
    assert spec.account_patterns == [
        "Assets:Bank:Chequing:Wealthsimple*",
        "Assets:Bank:Chequing:*Wealthsimple*",
    ]
    assert spec.build_importer("Assets:Bank:Chequing:Wealthsimple").account == ("Assets:Bank:Chequing:Wealthsimple")
