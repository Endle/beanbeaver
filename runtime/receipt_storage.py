"""Storage and retrieval of scanned/approved receipts.

This module handles saving/loading/managing receipts that move through:
    scanned/  ->  approved/  ->  matched/

Directory structure:
    receipts/
    ├── approved/   - Human-reviewed receipts awaiting CC match
    ├── matched/    - Receipts successfully merged into CC imports
    ├── images/     - Receipt photos
    ├── scanned/    - OCR+parser succeeded, not yet reviewed
    └── ocr_json/   - Raw OCR results
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from beanbeaver.runtime import get_logger, get_paths

from beanbeaver.domain.receipt import Receipt, ReceiptItem
from beanbeaver.receipt.date_utils import placeholder_receipt_date

logger = get_logger(__name__)

# Get paths from centralized module
_paths = get_paths()
RECEIPTS_DIR = _paths.receipts
APPROVED_DIR = _paths.receipts_approved
MATCHED_DIR = _paths.receipts_matched
IMAGES_DIR = _paths.receipts_images
SCANNED_DIR = _paths.receipts_scanned


def ensure_directories() -> None:
    """Create required directories if they don't exist."""
    _paths.ensure_receipt_directories()


def generate_receipt_filename(receipt: Receipt) -> str:
    """
    Generate filename for receipt files.

    Format: YYYY-MM-DD_merchant_amount.beancount

    This format enables quick pre-filtering by parsing filename
    before loading file content.
    """
    if receipt.date_is_placeholder:
        date_str = "unknown-date"
    else:
        date_str = receipt.date.strftime("%Y-%m-%d")

    # Clean merchant name for filename
    merchant_clean = receipt.merchant.lower()
    merchant_clean = "".join(c if c.isalnum() else "_" for c in merchant_clean)
    merchant_clean = "_".join(filter(None, merchant_clean.split("_")))
    if not merchant_clean:
        merchant_clean = "unknown"
    # Limit merchant name length
    if len(merchant_clean) > 30:
        merchant_clean = merchant_clean[:30]

    # Format amount (replace . with _ for filename safety)
    amount_str = f"{receipt.total:.2f}".replace(".", "_")

    return f"{date_str}_{merchant_clean}_{amount_str}.beancount"


def save_approved_receipt(receipt: Receipt, beancount_content: str) -> Path:
    """
    Save an approved receipt to the approved/ directory.

    Args:
        receipt: The Receipt object
        beancount_content: Formatted beancount content with metadata header

    Returns:
        Path to the saved file
    """
    ensure_directories()

    filename = generate_receipt_filename(receipt)
    filepath = APPROVED_DIR / filename

    # Handle filename collisions by appending a counter
    counter = 1
    base_name = filename.rsplit(".", 1)[0]
    while filepath.exists():
        filepath = APPROVED_DIR / f"{base_name}_{counter}.beancount"
        counter += 1

    filepath.write_text(beancount_content)
    logger.info("Saved approved receipt to %s", filepath)

    return filepath


def save_scanned_receipt(receipt: Receipt, beancount_content: str) -> Path:
    """
    Save a scanned receipt to the scanned/ directory for manual editing.

    Args:
        receipt: The Receipt object
        beancount_content: Formatted beancount content with metadata header

    Returns:
        Path to the saved file
    """
    ensure_directories()

    filename = generate_receipt_filename(receipt)
    filepath = SCANNED_DIR / filename

    # Handle filename collisions by appending a counter
    counter = 1
    base_name = filename.rsplit(".", 1)[0]
    while filepath.exists():
        filepath = SCANNED_DIR / f"{base_name}_{counter}.beancount"
        counter += 1

    filepath.write_text(beancount_content)
    logger.info("Saved scanned receipt to %s", filepath)

    return filepath


def move_scanned_to_approved(receipt_path: Path) -> Path:
    """
    Move a receipt file from scanned/ to approved/ after manual review.

    Args:
        receipt_path: Path to the receipt in scanned/

    Returns:
        New path in approved/
    """
    ensure_directories()

    if not receipt_path.exists():
        raise FileNotFoundError(f"Receipt not found: {receipt_path}")

    # Prefer a clean filename based on edited content
    try:
        receipt = parse_receipt_from_beancount(receipt_path)
        new_filename = generate_receipt_filename(receipt)
        new_path = APPROVED_DIR / new_filename
    except Exception:
        new_path = APPROVED_DIR / receipt_path.name

    # Handle filename collisions
    counter = 1
    base_name = new_path.stem
    while new_path.exists():
        new_path = APPROVED_DIR / f"{base_name}_{counter}.beancount"
        counter += 1

    receipt_path.rename(new_path)
    logger.info("Moved %s to %s", receipt_path, new_path)

    return new_path


def parse_filename_info(filepath: Path) -> tuple[date | None, str | None, Decimal | None]:
    """
    Extract date, merchant, and amount from filename.

    Args:
        filepath: Path to the receipt file

    Returns:
        Tuple of (date, merchant, amount) - any may be None if parsing fails
    """
    filename = filepath.stem  # Remove .beancount extension

    # Expected format: YYYY-MM-DD_merchant_amount
    # amount is like 51_61 for $51.61
    pattern = r"^(\d{4}-\d{2}-\d{2})_(.+)_(\d+_\d{2})(?:_\d+)?$"
    match = re.match(pattern, filename)

    if not match:
        return None, None, None

    try:
        parsed_date = date.fromisoformat(match.group(1))
        merchant = match.group(2).replace("_", " ").title()
        amount_str = match.group(3).replace("_", ".")
        amount = Decimal(amount_str)
        return parsed_date, merchant, amount
    except (ValueError, InvalidOperation):
        return None, None, None


def load_approved_receipts(
    date_filter: date | None = None,
    amount_filter: Decimal | None = None,
    tolerance_days: int = 3,
    amount_tolerance: Decimal = Decimal("0.10"),
) -> list[tuple[Path, Receipt]]:
    """
    Load receipts from approved/ directory, optionally pre-filtered.

    Pre-filtering by filename allows efficient scanning without
    parsing every file's content.

    Args:
        date_filter: If provided, only load receipts within tolerance_days
        amount_filter: If provided, only load receipts within amount_tolerance
        tolerance_days: Days tolerance for date filtering
        amount_tolerance: Amount tolerance for amount filtering

    Returns:
        List of (filepath, Receipt) tuples
    """
    ensure_directories()

    results = []

    for filepath in APPROVED_DIR.glob("*.beancount"):
        # Quick pre-filter by filename
        file_date, _, file_amount = parse_filename_info(filepath)

        if date_filter and file_date:
            if abs((file_date - date_filter).days) > tolerance_days:
                continue

        if amount_filter and file_amount:
            if abs(file_amount - amount_filter) > amount_tolerance:
                continue

        # Parse the full file
        try:
            receipt = parse_receipt_from_beancount(filepath)
            results.append((filepath, receipt))
        except Exception as e:
            logger.warning("Failed to parse %s: %s", filepath, e)

    return results


def parse_receipt_from_beancount(filepath: Path) -> Receipt:
    """
    Reconstruct a Receipt from a saved beancount file.

    Parses the metadata header and transaction content to rebuild
    the Receipt object.
    """
    content = filepath.read_text()
    lines = content.split("\n")

    # Initialize defaults
    merchant = "Unknown"
    receipt_date: date | None = None
    date_is_unknown = False
    total = Decimal("0")
    items: list[ReceiptItem] = []
    tax: Decimal | None = None
    image_filename = ""

    # Parse metadata header (lines starting with ; @)
    for line in lines:
        line = line.strip()
        if line.startswith("; @merchant:"):
            merchant = line.split(":", 1)[1].strip()
        elif line.startswith("; @date:"):
            date_value = line.split(":", 1)[1].strip()
            if date_value.upper() == "UNKNOWN":
                date_is_unknown = True
                receipt_date = None
            else:
                try:
                    receipt_date = date.fromisoformat(date_value)
                except ValueError:
                    receipt_date = None
        elif line.startswith("; @total:"):
            try:
                total = Decimal(line.split(":", 1)[1].strip())
            except InvalidOperation:
                pass
        elif line.startswith("; @tax:"):
            try:
                tax = Decimal(line.split(":", 1)[1].strip())
            except InvalidOperation:
                pass
        elif line.startswith("; @image_filename:"):
            image_filename = line.split(":", 1)[1].strip()
        elif line.startswith("; @image:"):
            image_filename = line.split(":", 1)[1].strip()

    # Parse transaction line for date if not found in metadata
    for line in lines:
        line = line.strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}\s+\*", line):
            # Transaction line: YYYY-MM-DD * "Payee" "Narration"
            try:
                if receipt_date is None or date_is_unknown:
                    receipt_date = date.fromisoformat(line[:10])
                    date_is_unknown = False
            except ValueError:
                pass
            # Extract payee
            payee_match = re.search(r'\*\s+"([^"]*)"', line)
            if payee_match and merchant == "Unknown":
                merchant = payee_match.group(1)
            break

    # Parse posting lines for items
    expense_pattern = re.compile(r"^\s+(Expenses:\S+)\s+(\d+\.?\d*)\s+\w+\s*;?\s*(.*)$")

    for line in lines:
        match = expense_pattern.match(line)
        if match:
            category = match.group(1)
            try:
                price = Decimal(match.group(2))
            except InvalidOperation:
                continue

            description = match.group(3).strip()

            # Skip tax line (handled separately)
            if "Tax:HST" in category or "Tax:GST" in category:
                tax = price
                continue

            # Skip FIXME placeholders for unaccounted amounts
            if "FIXME: unaccounted" in description:
                continue

            # Parse quantity from description if present
            quantity = 1
            qty_match = re.search(r"\(qty\s+(\d+)\)", description)
            if qty_match:
                quantity = int(qty_match.group(1))
                description = re.sub(r"\s*\(qty\s+\d+\)", "", description)

            items.append(
                ReceiptItem(
                    description=description,
                    price=price,
                    quantity=quantity,
                    category=category,
                )
            )

    # Calculate total from items if not found in metadata
    if total == Decimal("0") and items:
        total = sum((item.total for item in items), Decimal("0"))
        if tax:
            total += tax

    date_is_placeholder = date_is_unknown
    if receipt_date is None:
        receipt_date = placeholder_receipt_date()
        date_is_placeholder = True
    return Receipt(
        merchant=merchant,
        date=receipt_date,
        date_is_placeholder=date_is_placeholder,
        total=total,
        items=items,
        tax=tax,
        image_filename=image_filename,
    )


def move_to_matched(receipt_path: Path) -> Path:
    """
    Move a receipt file from approved/ to matched/ after successful matching.

    Args:
        receipt_path: Path to the receipt in approved/

    Returns:
        New path in matched/
    """
    ensure_directories()

    if not receipt_path.exists():
        raise FileNotFoundError(f"Receipt not found: {receipt_path}")

    new_path = MATCHED_DIR / receipt_path.name

    # Handle filename collisions
    counter = 1
    base_name = new_path.stem
    while new_path.exists():
        new_path = MATCHED_DIR / f"{base_name}_{counter}.beancount"
        counter += 1

    receipt_path.rename(new_path)
    logger.info("Moved %s to %s", receipt_path, new_path)

    return new_path


def list_approved_receipts() -> list[tuple[Path, str, date, Decimal]]:
    """
    List all receipts in approved/ with summary info.

    Returns:
        List of (path, merchant, date, amount) tuples (date is placeholder if unknown)
    """
    ensure_directories()

    results = []

    for filepath in sorted(APPROVED_DIR.glob("*.beancount")):
        file_date, merchant, amount = parse_filename_info(filepath)

        if file_date and merchant and amount:
            results.append((filepath, merchant, file_date, amount))
        else:
            # Fallback: parse the file
            try:
                receipt = parse_receipt_from_beancount(filepath)
                results.append((filepath, receipt.merchant, receipt.date, receipt.total))
            except Exception as e:
                logger.warning("Failed to parse %s: %s", filepath, e)

    return results


def list_scanned_receipts() -> list[Path]:
    """
    List all receipts in scanned/ directory.

    Returns:
        List of file paths
    """
    ensure_directories()
    return sorted(SCANNED_DIR.glob("*.beancount"))


def delete_receipt(receipt_path: Path) -> bool:
    """
    Delete a receipt file.

    Args:
        receipt_path: Path to the receipt file

    Returns:
        True if deleted, False if not found
    """
    if receipt_path.exists():
        receipt_path.unlink()
        logger.info("Deleted %s", receipt_path)
        return True
    return False
