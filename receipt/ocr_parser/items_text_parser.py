"""Text-line based receipt item extraction."""

import re
from decimal import Decimal

from beanbeaver.domain.receipt import ReceiptItem, ReceiptWarning

from ..item_categories import ItemCategoryRuleLayers, categorize_item
from .common import (
    _is_section_header_text,
    _looks_like_quantity_expression,
    _looks_like_summary_line,
    _parse_quantity_modifier,
    _strip_leading_receipt_codes,
    _validate_quantity_price,
)


def _extract_items(
    lines: list[str],
    summary_amounts: set[Decimal] | None = None,
    warning_sink: list[ReceiptWarning] | None = None,
    *,
    item_category_rule_layers: ItemCategoryRuleLayers,
) -> list[ReceiptItem]:
    """
    Extract line items from receipt.

    This is heuristic-based and will likely need manual correction.
    Handles multi-line item formats where description and price are on separate lines.

    Args:
        lines: List of text lines from the receipt
        summary_amounts: Set of Decimal amounts (total, tax, subtotal) to exclude from items
    """
    items: list[ReceiptItem] = []
    if summary_amounts is None:
        summary_amounts = set()

    # Skip header/footer sections
    skip_patterns = [
        # Total/subtotal patterns
        r"TOTAL",
        r"SUBTOTAL",
        r"SUB\s+TOTAL",
        r"TOTALS?\s+ON",
        # Tax patterns
        r"^TAX$",
        r"^HST",
        r"^GST",
        r"^PST",
        r"AFTER\s+TAX",
        r"\d+%$",  # Lines ending with percentage like "nst5%"
        # Payment patterns
        r"CASH",
        r"CREDIT",
        r"DEBIT",
        r"CHANGE",
        r"^BALANCE",
        r"VISA",
        r"MASTERCARD",
        r"AMEX",
        r"APPROVED",
        r"ACTIVATED",
        r"^PC\s+\d",  # Gift card / payment card lines like "PC 339918..."
        r"^ACCT:",
        r"^REFERENCE",
        # Footer patterns
        r"THANK YOU",
        r"WELCOME",
        r"RECEIPT",
        r"TRANSACTION",
        r"POINTS",
        r"REWARDS",
        r"EARNED",
        r"^SAVED$",
        r"^YOU SAVED",
        r"^CARD",
        r"AUTH",
        r"REF\s*#",
        r"SLIP\s*#",
        r"^TILL",
        r"CASHIER",
        r"\bSTORE\b",
        r"^PHONE",
        r"ADDRESS",
        r"SIGNATURE",
        r"Merchant",
        r"^QTY$",
        r"^UNIT$",
        r"^SAV$",
        r"ITEM\s+COUNT",
        r"NUMBER\s+OF\s+ITEMS",
        r"XXXX+",  # Masked card numbers
        r"^CAD",  # Payment amount lines like "CAD$ 5.00"
        r"VERIFIED",  # PIN verification
        r"^PIN$",
        r"CUSTOMER\s+COPY",  # Receipt copy marker
        r"COPY$",
        r"Optimum",  # PC Optimum loyalty program
        r"Redeemed",
    ]
    skip_regex = re.compile("|".join(skip_patterns), re.IGNORECASE)

    # Find where the items section ends (at TOTAL line) to avoid processing payment section
    total_line_idx = None
    for i, line in enumerate(lines):
        if re.search(r"\bTOTAL\b", line, re.IGNORECASE) and "SUBTOTAL" not in line.upper():
            total_line_idx = i
            break

    # First pass: identify item lines with prices (format: "DESCRIPTION ... PRICE H")
    # Common receipt format: "ITEM NAME    8.99 H" where H indicates taxable
    for i, line in enumerate(lines):
        # Stop processing after TOTAL line (rest is payment/footer section)
        if total_line_idx is not None and i > total_line_idx:
            break

        if skip_regex.search(line):
            continue

        # Skip very short lines or lines that are just numbers (item codes)
        if len(line) < 3:
            continue
        if re.match(r"^\d+$", line):
            continue

        # Skip quantity expressions - they'll be captured with their item in backward search
        # e.g., "3 @ $1.99", "2 /for $3.00", "1.22 lb @ $2.99/lb"
        # EXCEPT: Loblaw format "2 @ 2/$5.00 5.00" has trailing total price on same line
        is_qty_line = _looks_like_quantity_expression(line)
        has_trailing_total = re.search(r"\s+\d+\.\d{2}\s*[HhTtJj]?\s*$", line)
        if is_qty_line and not has_trailing_total:
            if warning_sink is not None and "/for" in line.lower():
                tail_token_match = re.search(r"([0-9A-Za-z]\.[0-9A-Za-z]{2,3}[HhTtJj]?)\s*$", line)
                tail_token = tail_token_match.group(1) if tail_token_match else ""
                if tail_token and any(c.isalpha() for c in tail_token):
                    context = line.strip()
                    if len(context) > 80:
                        context = context[:80]
                    warning_sink.append(
                        ReceiptWarning(
                            message=(
                                f'maybe missed item near malformed multi-buy total "{tail_token}"'
                                f' (context: "{context}")'
                            ),
                            after_item_index=(len(items) - 1) if items else None,
                        )
                    )
            continue

        # Skip lines that are just parenthetical codes like "( nel #44)", "(HHIT)".
        # Keep parenthetical promo lines that still carry a trailing item total.
        if re.match(r"^\([^)]*\)?$", line) and not re.search(r"\d+\.\d{2}\s*[HhTtJj]?\s*$", line):
            continue

        # Pattern 1: Price at end of line with optional H/tax marker
        # e.g., "SKITTLES GUMM 8.00 H" or "8.00 H" or "24.84"
        # Also handle discounts: "9.00- H" or "9.00-"
        match = re.search(r"(\d+\.\d{2})(-?)\s*[HhTtJj]?\s*$", line)
        if match:
            price = Decimal(match.group(1))
            is_discount = match.group(2) == "-"
            if is_discount:
                price = -price

            line_upper = line.upper()
            # Handle @REG$/REG$ promo lines.
            # If line is just a reg-price marker (single price), skip it.
            # If line includes both reg and sale prices, treat as price line for the item above.
            if "REG$" in line_upper or "@REG" in line_upper:
                prices = re.findall(r"(\d+\.\d{2})", line)
                # If previous line already contains a price, this is just promo info; skip it.
                if len(prices) > 1 and i > 0 and re.search(r"\d+\.\d{2}\s*[HhTtJj]?\s*$", lines[i - 1]):
                    continue

            # Skip if this is a summary line (contains TOTAL/SUBTOTAL keywords)
            # Don't skip just because the price matches - single-item receipts have item = total
            if "TOTAL" in line_upper or "SUBTOTAL" in line_upper or "SUB TOTAL" in line_upper:
                continue

            # Skip if previous line is a summary keyword and this is just the price
            if i > 0 and abs(price) in summary_amounts:
                prev_upper = lines[i - 1].upper()
                if "TOTAL" in prev_upper or "SUBTOTAL" in prev_upper or "SUB TOTAL" in prev_upper:
                    continue

            # Get description from same line (before the price)
            desc_part = line[: match.start()].strip()
            # Promo lines like "REG$8.99 5.99" should use the previous line as description
            force_backward = "REG$" in line_upper or "@REG" in line_upper

            # Clean up description - remove item codes at start
            if desc_part:
                desc_part = re.sub(r"^\d{8,}\s*", "", desc_part)

            # Priced aisle/section headers (e.g., "33-BAKERY INSTORE 12.00") should
            # use a nearby SKU-led item line, not the header text itself.
            is_priced_section_header = bool(desc_part) and _is_section_header_text(desc_part)
            skip_section_header_price = False
            if is_priced_section_header:
                desc_part = ""
                # If the next content line already has the same trailing price,
                # treat this header row as metadata and let the priced line parse itself.
                for j in range(i + 1, min(i + 4, len(lines))):
                    next_line = lines[j].strip()
                    if not next_line:
                        continue
                    if _looks_like_summary_line(next_line):
                        break
                    next_price_match = re.search(r"(\d+\.\d{2})(-?)\s*[HhTtJj]?\s*$", next_line)
                    if next_price_match:
                        next_price = Decimal(next_price_match.group(1))
                        if next_price_match.group(2) == "-":
                            next_price = -next_price
                        if next_price == price:
                            skip_section_header_price = True
                    break
            if skip_section_header_price:
                continue

            # Check if desc_part is valid: not empty, not too short, not a quantity expression
            # Quantity expressions like "2 @ 2/$5.00" should trigger backward search instead
            # Also handle promotional patterns like "(1 /for $2.99) 1 /for" from C&C receipts
            is_qty_expr = (
                (
                    _looks_like_quantity_expression(desc_part)
                    # Promotional pattern like "(#)<ON SALE)"
                    or re.match(r"^\([#\w]*\)\s*<?\s*ON\s*SALE", desc_part, re.IGNORECASE)
                )
                if desc_part
                else False
            )

            if desc_part and len(desc_part) > 2 and not is_qty_expr and not force_backward:
                items.append(
                    ReceiptItem(
                        description=desc_part,
                        price=price,
                        category=categorize_item(desc_part, rule_layers=item_category_rule_layers),
                    )
                )
            else:
                # Price on its own line - look backwards for description
                # Take the first valid candidate (closest to price line)
                qty_info = []
                qty_modifiers = []  # Store parsed quantity modifier data
                found_desc = None
                # For priced section headers, description usually follows on the next line
                # as a SKU-led item line (e.g., "62843020000 DOUGHNUTS MRJ").
                if is_priced_section_header:
                    for j in range(i + 1, min(i + 5, len(lines))):
                        next_line = lines[j].strip()
                        if not next_line:
                            continue
                        if skip_regex.search(next_line):
                            continue
                        if _looks_like_summary_line(next_line):
                            continue
                        if _looks_like_quantity_expression(next_line):
                            continue
                        if re.search(r"(\d+\.\d{2})(-?)\s*[HhTtJj]?\s*$", next_line):
                            # This line is a standalone priced item; do not borrow it.
                            continue
                        if re.match(r"^\$?\d+\.\d{2}\s*[HhTtJj]?\s*$", next_line):
                            continue
                        if re.match(r"^\d{8,}\s*$", next_line):
                            continue
                        cleaned_next = _strip_leading_receipt_codes(next_line)
                        if not cleaned_next:
                            continue
                        if _is_section_header_text(cleaned_next):
                            continue
                        alpha_count = sum(1 for c in cleaned_next if c.isalpha())
                        alpha_ratio = alpha_count / len(cleaned_next) if cleaned_next else 0
                        if alpha_ratio < 0.5:
                            continue
                        found_desc = cleaned_next
                        break
                if is_priced_section_header and found_desc is None:
                    # No safe lookahead description for this header price row.
                    continue
                if found_desc is None:
                    for j in range(i - 1, max(i - 6, -1), -1):
                        prev_line = lines[j].strip()
                        # Skip if it's a price line, skip line, or item code
                        if re.match(r"^[\d.]+\s*[HhTtJj]?\s*$", prev_line):
                            continue
                        if re.match(r"^\d{8,}$", prev_line):
                            continue
                        if skip_regex.search(prev_line):
                            continue
                        # Check for quantity/weight modifier patterns first
                        # This extracts structured data from lines like "3 @ $1.99", "1.22 lb @"
                        modifier = _parse_quantity_modifier(prev_line)
                        if modifier:
                            qty_modifiers.append(modifier)
                            qty_info.append(prev_line)  # Keep raw text for fallback
                            continue
                        # Capture other quantity expressions that don't match our structured patterns
                        if _looks_like_quantity_expression(prev_line):
                            qty_info.append(prev_line)
                            continue
                        # Skip price-info lines: "$2.99 ea or 2/$5.00 KB", "$8.80/kg"
                        # These start with $ and contain unit prices or multi-buy offers
                        if re.match(r"^\$\d+\.\d{2}", prev_line):
                            continue
                        # Skip lines that are just parenthetical codes like "( nel #44)"
                        if re.match(r"^\([^)]*\)$", prev_line):
                            continue
                        # Skip incomplete parentheticals - start with ( but don't end with )
                        # These are often garbled OCR of Chinese text, e.g., "(Hi N" from "青蔥"
                        if prev_line.startswith("(") and not prev_line.endswith(")"):
                            continue
                        # Skip promotional/sale lines like "(#)<ON SALE)", "(KAE)<ON SALE)"
                        if re.match(r"^\([^)]*\)\s*<?\s*ON\s*SALE", prev_line, re.IGNORECASE):
                            continue
                        # Skip quantity expressions: "(1 /for $2.99) 1 /for", "(2 /for $4.50) 2 /for"
                        if re.match(r"^\(\d+\s*/\s*for\s+\$[\d.]+\)", prev_line):
                            continue
                        # Skip very short codes like "MRJ", "KB", "plo" (likely tax/sale markers or OCR noise)
                        if len(prev_line) <= 3:
                            continue
                        # Strip leading item code (digits) before calculating alpha ratio
                        # This handles Costco format: "1214759 GARLIC 3 LB"
                        desc_for_ratio = re.sub(r"^\d+\s*", "", prev_line)
                        # Calculate alpha ratio to filter garbled OCR lines
                        alpha_count = sum(1 for c in desc_for_ratio if c.isalpha())
                        alpha_ratio = alpha_count / len(desc_for_ratio) if desc_for_ratio else 0
                        # Skip garbled OCR lines (low alphabetic ratio, e.g., unrecognized Chinese)
                        if alpha_ratio < 0.5:
                            continue
                        if len(prev_line) > 2 and not re.match(r"^[\d.]+$", prev_line):
                            # Found a valid description - use it (proximity wins)
                            found_desc = prev_line
                            break

                if found_desc:
                    quantity = 1
                    description_suffix = ""

                    # Extract quantity from validated modifiers
                    if qty_modifiers:
                        # Use first modifier (closest to price line)
                        mod = qty_modifiers[0]
                        if _validate_quantity_price(price, mod):
                            quantity = mod.get("quantity", 1)
                            # Add weight info to description if present
                            if "weight" in mod:
                                description_suffix = f" ({mod['weight']} lb)"
                        else:
                            # Validation failed - append raw text as fallback
                            description_suffix = f" ({', '.join(reversed(qty_info))})"
                    elif qty_info:
                        # No structured modifiers but have raw qty text
                        description_suffix = f" ({', '.join(reversed(qty_info))})"

                    items.append(
                        ReceiptItem(
                            description=found_desc + description_suffix,
                            price=price,
                            quantity=quantity,
                            category=categorize_item(
                                found_desc,
                                rule_layers=item_category_rule_layers,
                            ),  # Categorize on item name only
                        )
                    )
                elif warning_sink is not None and price > Decimal("0"):
                    context = line.strip()
                    if len(context) > 80:
                        context = context[:80]
                    message = f"maybe missed item near price {price:.2f}"
                    if context:
                        message += f' (context: "{context}")'
                    warning_sink.append(
                        ReceiptWarning(
                            message=message,
                            after_item_index=(len(items) - 1) if items else None,
                        )
                    )
        elif warning_sink is not None:
            # OCR can corrupt trailing prices (e.g., "8l.99", "1I.50"), causing
            # otherwise valid item lines to be skipped. Emit a review hint.
            malformed_price = re.search(r"(\d+[Il]\.\d{2}|\d+\.[Il]\d|\d+\.\d[Il])\s*[HhTtJj]?\s*$", line)
            if malformed_price:
                token = malformed_price.group(1)
                context = line.strip()
                if len(context) > 80:
                    context = context[:80]
                warning_sink.append(
                    ReceiptWarning(
                        message=(f'maybe missed item with malformed OCR price "{token}" (context: "{context}")'),
                        after_item_index=(len(items) - 1) if items else None,
                    )
                )
            # Multi-buy rows can also carry malformed totals like "2 /for S.OOH".
            # These indicate a likely missed item when no parseable trailing total exists.
            elif "/for" in line.lower() and re.search(r"\b[0-9A-Za-z]\.[0-9A-Za-z]{2,3}[HhTtJj]?\s*$", line):
                tail_token_match = re.search(r"([0-9A-Za-z]\.[0-9A-Za-z]{2,3}[HhTtJj]?)\s*$", line)
                tail_token = tail_token_match.group(1) if tail_token_match else ""
                if any(c.isalpha() for c in tail_token):
                    context = line.strip()
                    if len(context) > 80:
                        context = context[:80]
                    warning_sink.append(
                        ReceiptWarning(
                            message=(
                                f'maybe missed item near malformed multi-buy total "{tail_token}"'
                                f' (context: "{context}")'
                            ),
                            after_item_index=(len(items) - 1) if items else None,
                        )
                    )

    # Keep duplicates: repeated identical lines are common (e.g., two cartons
    # of the same milk/eggs with same price) and should remain separate items.
    return items
