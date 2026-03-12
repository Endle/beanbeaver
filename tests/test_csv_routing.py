from __future__ import annotations

from pathlib import Path

from beanbeaver.application.imports.csv_routing import detect_credit_card_importer_id, route_csv


def test_route_csv_detects_mbna_monthly_named_export(tmp_path: Path) -> None:
    csv_path = tmp_path / "February2026_0464.csv"
    csv_path.write_text(
        'Posted Date,Payee,Address,Amount\n01/19/2026,"UBER CANADA/UBEREATS TORONTO ON","TORONTO ",-48.66\n',
        encoding="utf-8",
    )

    routes = route_csv(csv_path)

    assert len(routes) == 1
    assert routes[0].import_type == "cc"
    assert routes[0].importer_id == "mbna"
    assert routes[0].rule_id == "cc-mbna-monthly"
    assert routes[0].stage == 2


def test_detect_credit_card_importer_id_accepts_mbna_monthly_named_export(tmp_path: Path) -> None:
    csv_path = tmp_path / "February2026_0464.csv"
    csv_path.write_text(
        'Posted Date,Payee,Address,Amount\n01/19/2026,"UBER CANADA/UBEREATS TORONTO ON","TORONTO ",-48.66\n',
        encoding="utf-8",
    )

    assert detect_credit_card_importer_id(csv_path) == "mbna"


def test_route_csv_accepts_numbered_transactions_download_for_rogers(tmp_path: Path) -> None:
    csv_path = tmp_path / "transactions (1).csv"
    csv_path.write_text(
        "Date,Merchant Name,Amount\n2026-03-10,Coffee Shop,12.34\n",
        encoding="utf-8",
    )

    routes = route_csv(csv_path)

    assert len(routes) == 1
    assert routes[0].import_type == "cc"
    assert routes[0].importer_id == "rogers"
    assert routes[0].rule_id == "cc-transactions-rogers"
    assert routes[0].stage == 2


def test_route_csv_accepts_numbered_transactions_download_for_ctfs(tmp_path: Path) -> None:
    csv_path = tmp_path / "transactions (2).csv"
    csv_path.write_text(
        "\n\n\nTransaction Date,Amount,Description,Type\n2026-03-10,12.34,Gas Purchase,Purchase\n",
        encoding="utf-8",
    )

    routes = route_csv(csv_path)

    assert len(routes) == 1
    assert routes[0].import_type == "cc"
    assert routes[0].importer_id == "ctfs"
    assert routes[0].rule_id == "cc-transactions-ctfs"
    assert routes[0].stage == 2
