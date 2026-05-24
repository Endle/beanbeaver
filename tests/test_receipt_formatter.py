from datetime import date
from decimal import Decimal

from beanbeaver.domain.receipt import Receipt, ReceiptItem, ReceiptWarning, Tender
from beanbeaver.ledger_access import LedgerAmount, LedgerPosting, LedgerTransaction
from beanbeaver.receipt.beancount_rendering import (
    format_draft_beancount,
    format_enriched_transaction,
    format_parsed_receipt,
    generate_filename,
)
from beanbeaver.receipt.matcher import MatchResult


def test_format_parsed_receipt_renders_expected_metadata_and_warning_anchor() -> None:
    receipt = Receipt(
        merchant='Fresh "Mart"',
        date=date(2026, 3, 5),
        total=Decimal("12.75"),
        tax=Decimal("0.75"),
        raw_text="CARD ********1234\nTOTAL 12.75",
        image_filename="fresh.jpg",
        items=[
            ReceiptItem(
                description='Milk "2%"',
                price=Decimal("4.00"),
                category="Expenses:Food:Grocery:Dairy",
            ),
            ReceiptItem(
                description="Bread",
                price=Decimal("8.00"),
                quantity=2,
                category=None,
            ),
        ],
        warnings=[ReceiptWarning(message="check quantity", after_item_index=1)],
    )

    output = format_parsed_receipt(receipt, image_sha256="abc123")

    assert '; @merchant: Fresh "Mart"' in output
    assert "; @image_sha256: abc123" in output
    assert '2026-03-05 * "Fresh \'Mart\'" "Receipt scan"' in output
    assert "Liabilities:CreditCard:PENDING" in output
    assert "card ****1234" in output
    assert "Expenses:Food:Grocery:Dairy" in output
    assert "; Milk '2%'" in output
    assert "Expenses:FIXME" in output
    assert "; Bread (qty 2)" in output
    assert "; WARN:PARSER check quantity" in output
    assert "; TOTAL 12.75" in output


def test_format_draft_beancount_warns_when_items_exceed_total() -> None:
    receipt = Receipt(
        merchant="Corner Store",
        date=date(2026, 3, 5),
        total=Decimal("5.00"),
        raw_text="TOTAL 5.00",
        items=[
            ReceiptItem(
                description="Item A",
                price=Decimal("3.00"),
                category="Expenses:Food:Grocery:Staple",
            ),
            ReceiptItem(
                description="Item B",
                price=Decimal("3.50"),
                category="Expenses:Food:Grocery:Staple",
            ),
        ],
    )

    output = format_draft_beancount(receipt)

    assert "; === DRAFT - REVIEW NEEDED ===" in output
    assert "  ; WARNING: items total (6.50) exceeds receipt total (5.00)" in output
    assert "; --- Raw OCR Text (for reference) ---" in output


def test_generate_filename_uses_placeholder_and_normalizes_merchant() -> None:
    receipt = Receipt(
        merchant="T&T Super-Market!!",
        date=date(2026, 3, 5),
        total=Decimal("1.00"),
        date_is_placeholder=True,
    )

    assert generate_filename(receipt) == "unknown-date-t-t-super-market.beancount"


def test_format_enriched_transaction_keeps_original_card_posting_and_reference() -> None:
    receipt = Receipt(
        merchant="Fresh Mart",
        date=date(2026, 3, 5),
        total=Decimal("12.75"),
        tax=Decimal("0.75"),
        image_filename="fresh.jpg",
        items=[
            ReceiptItem(
                description="Milk",
                price=Decimal("4.00"),
                category="Expenses:Food:Grocery:Dairy",
            ),
            ReceiptItem(
                description="Bread",
                price=Decimal("8.00"),
                category=None,
            ),
        ],
    )
    txn = LedgerTransaction(
        date=date(2026, 3, 6),
        payee='Fresh "Mart"',
        narration='Weekly "groceries"',
        postings=(
            LedgerPosting(
                account="Expenses:Food:Grocery",
                units=LedgerAmount(number=Decimal("12.75"), currency="CAD"),
            ),
            LedgerPosting(
                account="Liabilities:CreditCard:Visa",
                units=LedgerAmount(number=Decimal("-12.75"), currency="CAD"),
            ),
        ),
        file_path="/tmp/ledger.beancount",
        line_number=42,
    )
    match = MatchResult(
        transaction=txn,
        file_path=txn.file_path,
        line_number=txn.line_number,
        confidence=0.68,
        match_details="amount: exact match",
    )

    output = format_enriched_transaction(receipt, match)

    assert "; === ENRICHED TRANSACTION - REVIEW NEEDED ===" in output
    assert "; Matched: /tmp/ledger.beancount:42" in output
    assert "; Confidence: 68% (amount: exact match)" in output
    assert "2026-03-06 * \"Fresh 'Mart'\" \"Weekly 'groceries'\"" in output
    assert "Liabilities:CreditCard:Visa" in output
    assert "-12.75 CAD" in output
    assert "Expenses:Food:Grocery:Dairy" in output
    assert "; --- Original Transaction (to be replaced) ---" in output
    assert ";   Expenses:Food:Grocery  12.75 CAD" in output


def test_format_parsed_receipt_emits_one_posting_per_tender() -> None:
    receipt = Receipt(
        merchant="COSTCO",
        date=date(2026, 3, 7),
        total=Decimal("466.68"),
        items=[ReceiptItem(description="CHICKEN", price=Decimal("7.99"))],
        tax=Decimal("5.72"),
        raw_text="",
        image_filename="costco.jpg",
        tenders=[
            Tender(amount=Decimal("441.68"), kind="card", raw_label="MasterCard"),
            Tender(amount=Decimal("25.00"), kind="gift_card", raw_label="Shop Card"),
        ],
    )

    output = format_parsed_receipt(receipt, credit_card_account="Liabilities:CreditCard:Costco:MC")

    # Both payment postings present.
    assert "Liabilities:CreditCard:PENDING" in output
    assert "-441.68 CAD" in output
    assert "Assets:GiftCards:PENDING" in output
    assert "-25.00 CAD" in output
    assert "; gift card" in output


def test_format_parsed_receipt_uses_assigned_tender_account() -> None:
    receipt = Receipt(
        merchant="COSTCO",
        date=date(2026, 3, 7),
        total=Decimal("466.68"),
        items=[ReceiptItem(description="CHICKEN", price=Decimal("7.99"))],
        tax=Decimal("5.72"),
        raw_text="",
        image_filename="costco.jpg",
        tenders=[
            Tender(
                amount=Decimal("441.68"),
                account="Liabilities:CreditCard:Costco:Mastercard",
                kind="card",
                raw_label="MasterCard",
            ),
            Tender(
                amount=Decimal("25.00"),
                account="Assets:GiftCards:Costco",
                kind="gift_card",
                raw_label="Shop Card",
            ),
        ],
    )

    output = format_parsed_receipt(receipt)
    assert "Liabilities:CreditCard:Costco:Mastercard  -441.68 CAD" in output
    assert "Assets:GiftCards:Costco                    -25.00 CAD" in output
    # No PENDING placeholder when the user has assigned accounts.
    assert "PENDING" not in output


def test_format_enriched_transaction_splits_card_and_gift_card() -> None:
    receipt = Receipt(
        merchant="COSTCO",
        date=date(2026, 3, 7),
        total=Decimal("466.68"),
        items=[ReceiptItem(description="CHICKEN", price=Decimal("466.68"))],
        raw_text="",
        image_filename="costco.jpg",
        tenders=[
            Tender(amount=Decimal("441.68"), kind="card", raw_label="MasterCard"),
            Tender(amount=Decimal("25.00"), kind="gift_card", raw_label="Shop Card"),
        ],
    )

    txn = LedgerTransaction(
        date=date(2026, 3, 7),
        payee="COSTCO MARKHAM",
        narration="",
        postings=[
            LedgerPosting(
                account="Liabilities:CreditCard:Costco:Mastercard",
                units=LedgerAmount(number=Decimal("-441.68"), currency="CAD"),
            ),
            LedgerPosting(
                account="Expenses:Uncategorized",
                units=LedgerAmount(number=Decimal("441.68"), currency="CAD"),
            ),
        ],
        file_path="/tmp/ledger.beancount",
        line_number=10,
    )
    match = MatchResult(
        transaction=txn,
        file_path=txn.file_path,
        line_number=txn.line_number,
        confidence=0.95,
        match_details="amount: exact match (after $25.00 non-card tender)",
    )

    output = format_enriched_transaction(receipt, match)

    # Card posting reuses the matched txn's CC account; gift card uses PENDING.
    assert "Liabilities:CreditCard:Costco:Mastercard  -441.68 CAD" in output
    assert "Assets:GiftCards:PENDING                   -25.00 CAD" in output
