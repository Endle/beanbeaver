"""Centralized two-stage CSV routing for imports.

Stage 1: filename/string matching
Stage 2: optional Python validators (headers/content)
"""

from __future__ import annotations

import csv
import datetime
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from beanbeaver.runtime import get_logger, get_paths

logger = get_logger(__name__)
_paths = get_paths()

ImportType = Literal["cc", "chequing"]

CardImporterId = Literal[
    "cibc",
    "bmo",
    "scotia",
    "rogers",
    "mbna",
    "pcf",
    "ctfs",
    "amex",
]

ChequingImporterId = Literal["eqbank", "scotia_chequing"]
ImporterId = CardImporterId | ChequingImporterId


@dataclass(frozen=True)
class Stage1Rule:
    rule_id: str
    import_type: ImportType
    importer_id: ImporterId
    label: str
    has_validator: bool

    def matches_name(self, file_name: str) -> bool:
        lower = file_name.lower()
        if self.rule_id == "cc-cibc":
            return lower == "cibc.csv"
        if self.rule_id == "cc-simplii":
            return "simplii" in lower and lower.endswith(".csv")
        if self.rule_id == "cc-bmo-statement":
            return lower == "statement.csv"
        if self.rule_id == "cc-bmo-porter":
            return lower == "porter.csv"
        if self.rule_id == "cc-pcf":
            return lower == "report.csv"
        if self.rule_id == "cc-transactions-rogers":
            return lower == "transactions.csv"
        if self.rule_id == "cc-transactions-ctfs":
            return lower == "transactions.csv"
        if self.rule_id == "cc-rogers-history":
            return lower.startswith("transaction history_") and lower.endswith(".csv")
        if self.rule_id == "cc-scotia":
            return "scotiabank" in lower and lower.endswith(".csv")
        if self.rule_id == "cc-mbna":
            return "mbna" in lower and lower.endswith(".csv")
        if self.rule_id == "cc-amex-activity":
            return lower == "activity.csv"
        if self.rule_id == "cc-amex-named":
            return "amex" in lower and lower.endswith(".csv")
        if self.rule_id == "cc-amex-plat":
            return lower == "plat.csv"
        if self.rule_id == "chequing-eqbank":
            return lower.endswith("details.csv")
        if self.rule_id == "chequing-scotia":
            return file_name.startswith("Preferred_Package_") and lower.endswith(".csv")
        return False


@dataclass(frozen=True)
class CsvRoute:
    file_name: str
    import_type: ImportType
    importer_id: ImporterId
    rule_id: str
    stage: int

    @property
    def label(self) -> str:
        if self.import_type == "cc":
            return f"Credit card ({self.importer_id}): {self.file_name}"
        return f"Chequing ({self.importer_id}): {self.file_name}"


STAGE1_RULES: tuple[Stage1Rule, ...] = (
    Stage1Rule("cc-cibc", "cc", "cibc", "CIBC.csv", False),
    Stage1Rule("cc-simplii", "cc", "cibc", "SIMPLII*.csv", False),
    Stage1Rule("cc-bmo-statement", "cc", "bmo", "statement.csv", False),
    Stage1Rule("cc-bmo-porter", "cc", "bmo", "porter.csv", False),
    Stage1Rule("cc-pcf", "cc", "pcf", "report.csv", False),
    Stage1Rule("cc-transactions-rogers", "cc", "rogers", "Transactions.csv (Rogers)", True),
    Stage1Rule("cc-transactions-ctfs", "cc", "ctfs", "Transactions.csv (CTFS)", True),
    Stage1Rule("cc-rogers-history", "cc", "rogers", "Transaction History_*.csv", False),
    Stage1Rule("cc-scotia", "cc", "scotia", "*Scotiabank*.csv", False),
    Stage1Rule("cc-mbna", "cc", "mbna", "*MBNA*.csv", True),
    Stage1Rule("cc-amex-activity", "cc", "amex", "activity.csv", False),
    Stage1Rule("cc-amex-named", "cc", "amex", "*AMEX*.csv", True),
    Stage1Rule("cc-amex-plat", "cc", "amex", "plat.csv", False),
    Stage1Rule("chequing-eqbank", "chequing", "eqbank", "*Details.csv", True),
    Stage1Rule("chequing-scotia", "chequing", "scotia_chequing", "Preferred_Package_*.csv", True),
)


def _read_header(path: Path, *, skip_rows: int = 0, encoding: str = "utf-8-sig") -> list[str]:
    try:
        with open(path, encoding=encoding) as handle:
            reader = csv.reader(handle)
            for _ in range(skip_rows):
                next(reader, None)
            row = next(reader, [])
        return [col.strip().lower() for col in row]
    except Exception:
        return []


def _validate_rule(rule_id: str, path: Path) -> bool:
    if rule_id == "cc-transactions-rogers":
        header = _read_header(path)
        required = {"date", "merchant name", "amount"}
        return required.issubset(set(header))
    if rule_id == "cc-transactions-ctfs":
        header = _read_header(path, skip_rows=3, encoding="utf-8")
        required = {"transaction date", "amount", "description", "type"}
        return required.issubset(set(header))
    if rule_id == "cc-mbna":
        try:
            with open(path, encoding="iso-8859-1") as handle:
                reader = csv.reader(handle)
                first_row = next(reader, [])
        except Exception:
            return False
        if len(first_row) < 4:
            return False
        try:
            datetime.datetime.strptime(first_row[0].strip(), "%m/%d/%Y")
        except Exception:
            return False
        amount_col = first_row[3].strip().replace(",", "")
        if amount_col.startswith("$"):
            amount_col = amount_col[1:]
        try:
            float(amount_col)
        except Exception:
            return False
        return True
    if rule_id == "cc-amex-named":
        header = _read_header(path)
        required = {"date", "description", "amount"}
        return required.issubset(set(header))
    if rule_id == "chequing-eqbank":
        header = _read_header(path)
        required = {"transfer date", "amount", "balance"}
        return required.issubset(set(header))
    if rule_id == "chequing-scotia":
        header = _read_header(path)
        required = {"type of transaction", "sub-description"}
        return required.issubset(set(header))
    return True


def route_csv(path: Path) -> list[CsvRoute]:
    stage1 = [rule for rule in STAGE1_RULES if rule.matches_name(path.name)]
    if not stage1:
        return []

    if len(stage1) == 1 and not stage1[0].has_validator:
        rule = stage1[0]
        return [CsvRoute(path.name, rule.import_type, rule.importer_id, rule.rule_id, 1)]

    stage2: list[CsvRoute] = []
    for rule in stage1:
        if _validate_rule(rule.rule_id, path):
            stage = 2 if rule.has_validator else 1
            stage2.append(CsvRoute(path.name, rule.import_type, rule.importer_id, rule.rule_id, stage))

    if stage2:
        return stage2

    if len(stage1) == 1 and stage1[0].has_validator:
        # Strict mode: validator-backed single-rule candidates must pass stage 2.
        return []

    # Keep stage-1 candidates only when multiple competing rules remain unresolved.
    return [CsvRoute(path.name, rule.import_type, rule.importer_id, rule.rule_id, 1) for rule in stage1]


def find_download_routes(downloads_dir: Path | None = None) -> list[CsvRoute]:
    downloads = downloads_dir or _paths.downloads
    if not downloads.exists():
        return []

    routes: list[CsvRoute] = []
    for entry in downloads.iterdir():
        if not entry.is_file():
            continue
        routes.extend(route_csv(entry))

    routes.sort(key=lambda r: (r.file_name.lower(), r.import_type, r.importer_id))
    return routes


def detect_download_route(import_type: ImportType | None = None, downloads_dir: Path | None = None) -> CsvRoute | None:
    routes = find_download_routes(downloads_dir)
    if import_type is not None:
        routes = [route for route in routes if route.import_type == import_type]

    if not routes:
        return None

    if len(routes) == 1:
        selected = routes[0]
        logger.info("Auto-detected %s route via %s: %s", selected.import_type, selected.rule_id, selected.file_name)
        return selected

    if not sys.stdin.isatty():
        labels = ", ".join(route.label for route in routes)
        raise RuntimeError(
            "Multiple matching CSV files found in ~/Downloads. "
            f"Run interactively to choose one, or pass an explicit file. Candidates: {labels}"
        )

    print("Select file to import:")
    for idx, route in enumerate(routes, 1):
        stage = "stage2" if route.stage == 2 else "stage1"
        print(f"{idx}. {route.label} [{stage}:{route.rule_id}]")

    choice = input("Enter choice (number): ").strip()
    try:
        return routes[int(choice) - 1]
    except (ValueError, IndexError):
        raise RuntimeError("Invalid choice.") from None


def detect_credit_card_csv(downloads_dir: Path | None = None) -> str | None:
    route = detect_download_route(import_type="cc", downloads_dir=downloads_dir)
    return None if route is None else route.file_name


def detect_chequing_csv(downloads_dir: Path | None = None) -> str | None:
    route = detect_download_route(import_type="chequing", downloads_dir=downloads_dir)
    return None if route is None else route.file_name


def detect_credit_card_importer_id(path: Path) -> CardImporterId:
    routes = [route for route in route_csv(path) if route.import_type == "cc"]
    if not routes:
        raise RuntimeError("Could not determine credit card importer for this CSV.")

    importer_ids = sorted({route.importer_id for route in routes})
    if len(importer_ids) == 1:
        return importer_ids[0]  # type: ignore[return-value]

    if not sys.stdin.isatty():
        raise RuntimeError(
            f"Ambiguous credit card importer for CSV. Run interactively to choose: {', '.join(importer_ids)}"
        )

    print(f"Ambiguous credit card importer for file: {path.name}")
    for idx, importer_id in enumerate(importer_ids, 1):
        print(f"  {idx}. {importer_id}")
    choice = input("Select importer (number): ").strip()
    try:
        return importer_ids[int(choice) - 1]  # type: ignore[return-value]
    except (ValueError, IndexError):
        raise RuntimeError("Invalid importer selection") from None
