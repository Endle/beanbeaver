from collections import Counter
from decimal import Decimal

from beanbeaver.receipt.ocr_parser.common import _is_section_header_text
from beanbeaver.receipt.ocr_parser.fields_parser import _extract_price_from_line
from beanbeaver.receipt.ocr_parser.items_text_parser import _extract_items
from beanbeaver.runtime.item_category_rules import load_receipt_structuring_rule_layers


def test_extract_items_supports_trailing_j_tax_marker() -> None:
    lines = [
        "CRLSH ZER0 0 056000010660 $8.28 J",
        "LYSOL BATH P 059631882930 $3.97 J",
        "SUBTOTAL $12.25",
        "TOTAL $12.25",
    ]

    items = _extract_items(
        lines,
        summary_amounts={Decimal("12.25")},
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    assert [item.price for item in items] == [Decimal("8.28"), Decimal("3.97")]
    assert "CRLSH ZER0" in items[0].description
    assert "LYSOL BATH" in items[1].description


def test_extract_items_keeps_priced_meat_label_as_item() -> None:
    lines = [
        "&& 03-Meat",
        "Meat 6.48",
        "&& 06-Frozen",
        "Baifu - Sweetened Soya Mi 2.59",
        "SUB Total 9.07",
        "Total after Tax 9.07",
    ]

    items = _extract_items(
        lines,
        summary_amounts={Decimal("9.07")},
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    assert any(item.description == "Meat" and item.price == Decimal("6.48") for item in items)
    assert all(item.description != "&& 06-Frozen" for item in items)


def test_section_header_with_symbol_prefix_is_detected() -> None:
    assert _is_section_header_text("&& 06-Frozen")


def test_extract_items_skips_malformed_offer_fragments_with_price() -> None:
    lines = [
        "XBL - Spicy Crawfish Past 1.98",
        "(J@6.99(1/$1.98)",
        "1 @ $1.98",
        "XBL - Spicy Crawfish Past 1.98",
        "(@6.99(1/$1.98",
        "1 @ $1.98",
        "SUB Total 3.96",
        "Total after Tax 3.96",
    ]

    items = _extract_items(
        lines,
        summary_amounts={Decimal("3.96")},
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    matching = [item for item in items if item.price == Decimal("1.98")]
    assert len(matching) == 2
    assert all(item.description == "XBL - Spicy Crawfish Past" for item in matching)


def test_extract_items_skips_reg_marker_only_price_lines() -> None:
    lines = [
        "&& Frozen",
        "*Shirakiku Frozen Imitatio 1.99",
        "(9)@REG$3.99",
        "*Frozen Raw Vannanei White",
        "(@REG15.99",
        "3 @ $10.99 32.97",
        "SUB Total 34.96",
        "Total after Tax 34.96",
    ]

    items = _extract_items(
        lines,
        summary_amounts={Decimal("34.96")},
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    assert [item.price for item in items] == [Decimal("1.99"), Decimal("32.97")]
    assert items[0].description == "*Shirakiku Frozen Imitatio"
    assert items[1].description == "*Frozen Raw Vannanei White"


def test_extract_items_skips_reg_marker_without_dollar_or_at_symbol() -> None:
    lines = [
        "*Vita Hongkong Style Milk 2.99",
        "(1REG8.99",
        "*KsF Big Instant Noodles ( 6.99",
        "SUB Total 9.98",
        "Total after Tax 9.98",
    ]

    items = _extract_items(
        lines,
        summary_amounts={Decimal("9.98")},
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    assert [item.price for item in items] == [Decimal("2.99"), Decimal("6.99")]
    assert items[0].description == "*Vita Hongkong Style Milk"
    assert items[1].description == "*KsF Big Instant Noodles ("


def test_extract_items_skips_malformed_parenthesized_price_marker() -> None:
    lines = [
        "*Samyang Buldak Artificial 5.99",
        "(=kx(EG$8.99",
        "Wing Hing Sweet Soy Bever 2.99",
        "SUB Total 8.98",
        "Total after Tax 8.98",
    ]

    items = _extract_items(
        lines,
        summary_amounts={Decimal("8.98")},
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    assert [item.price for item in items] == [Decimal("5.99"), Decimal("2.99")]
    assert items[0].description == "*Samyang Buldak Artificial"
    assert items[1].description == "Wing Hing Sweet Soy Bever"


def test_extract_items_handles_spaced_decimal_quantities_and_prefixed_sku_lines() -> None:
    lines = [
        "(2)05707200195 LUNCH MEAT MRJ",
        "2 @ $1.75 3. 50",
        "06780000102 VEG OIL MRJ 6. 99",
        "(2)4050 CANTALOUPE MRJ",
        "2 @ $1.99 3. 98",
        "SUBTOTAL 14.47",
        "TOTAL 14.47",
    ]

    items = _extract_items(
        lines,
        summary_amounts={Decimal("14.47")},
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    pairs = {(item.description, item.price) for item in items}
    assert ("LUNCH MEAT MRJ", Decimal("3.50")) in pairs
    assert ("VEG OIL MRJ", Decimal("6.99")) in pairs
    assert ("4050 CANTALOUPE MRJ", Decimal("3.98")) in pairs


def test_extract_price_from_line_accepts_spaced_decimals() -> None:
    assert _extract_price_from_line("2 @ $1.75 3. 50") == Decimal("3.50")
    assert _extract_price_from_line("06780000102 VEG OIL MRJ 6. 99") == Decimal("6.99")


def test_extract_items_merges_hyphenated_multiline_description() -> None:
    lines = [
        "&& 01-Grocery  3.59",
        "Foojoy -",
        "Donghei Cold No",
        "(1kg) 16.99",
        "MK - Instant Noodle Pickl 2.98",
        "SUBTOTAL 23.56",
        "TOTAL 23.56",
    ]

    items = _extract_items(
        lines,
        summary_amounts={Decimal("23.56")},
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    assert any(item.description == "Foojoy - Donghei Cold No" and item.price == Decimal("3.59") for item in items)


def test_extract_items_uses_context_for_parenthetical_inline_price() -> None:
    lines = [
        "Foojoy -",
        "Donghei Cold No",
        "(1kg) 16.99",
        "SUBTOTAL 16.99",
        "TOTAL 16.99",
    ]

    items = _extract_items(
        lines,
        summary_amounts={Decimal("16.99")},
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    assert len(items) == 1
    assert items[0].price == Decimal("16.99")
    assert items[0].description == "Foojoy - Donghei Cold No (1kg)"


def test_extract_items_skips_quantity_stub_price_lines() -> None:
    lines = [
        "295619 KS BAGS 60 12.99",
        "2 @ 9.69",
        "430 XL EGGS 19.38",
        "SUBTOTAL 32.37",
        "TOTAL 32.37",
    ]

    items = _extract_items(
        lines,
        summary_amounts={Decimal("32.37")},
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    prices = [item.price for item in items]
    descriptions = [item.description for item in items]
    assert Decimal("9.69") not in prices
    assert all(desc != "2 @" for desc in descriptions)
    assert Decimal("12.99") in prices
    assert Decimal("19.38") in prices


def test_extract_items_skips_unit_price_fragment_ghost_lines() -> None:
    lines = [
        "HLY - Fish Cracker Tomato 2.59H",
        "@2.592/$3.50",
        "1 @ $2.59",
        "LZJ - Ice Cream 0.79H",
        "62g)@0.794/$1.99",
        "1 @ $0.79",
        "SUB Total 3.38",
        "Total after Tax 3.38",
    ]

    items = _extract_items(
        lines,
        summary_amounts={Decimal("3.38")},
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    assert any(item.description == "HLY - Fish Cracker Tomato" and item.price == Decimal("2.59") for item in items)
    assert any(item.description == "LZJ - Ice Cream" and item.price == Decimal("0.79") for item in items)
    assert all("@" not in item.description for item in items)
    assert all("/$" not in item.description for item in items)


def test_extract_items_keeps_priced_bakery_generic_label() -> None:
    lines = [
        "&&14-Bakery 1",
        "BAKERY 6.99",
        "SUB Total 6.99",
        "Total after Tax 6.99",
    ]

    items = _extract_items(
        lines,
        summary_amounts={Decimal("6.99")},
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    assert len(items) == 1
    assert items[0].description == "BAKERY"
    assert items[0].price == Decimal("6.99")


def test_extract_items_recovers_item_from_split_multibuy_price_marker() -> None:
    lines = [
        "SunriseTofu 700g",
        "() 5.99",
        "*Kam Yen Jan Chinese Sausa",
        "*Yo Yan Soya Drink Sweet x2",
        "($2F 3.99",
        "(2 /for $3.99) 2 /for",
        "&& Taxed Grocery",
        '"Orion Potato Chips-Orig x1',
        "(2 /for $5.00) 2 /for 5.00H",
        "SUB Total 14.98",
        "Total after Tax 14.98",
    ]

    items = _extract_items(
        lines,
        summary_amounts={Decimal("14.98")},
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    assert any(item.description == "SunriseTofu 700g" and item.price == Decimal("5.99") for item in items)
    assert any(item.description == "*Yo Yan Soya Drink Sweet x2" and item.price == Decimal("3.99") for item in items)
    assert any(item.description == '"Orion Potato Chips-Orig x1' and item.price == Decimal("5.00") for item in items)


def test_extract_items_skips_compact_promo_marker_ghost_price_line() -> None:
    lines = [
        "*Asahi Rich Calpis Drink 1.99",
        "EG2.99",
        "JHL. Fried Red Onion 227g 6.99",
        "SUB Total 8.98",
        "Total after Tax 8.98",
    ]

    items = _extract_items(
        lines,
        summary_amounts={Decimal("8.98")},
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    assert len(items) == 2
    assert items[0].description == "*Asahi Rich Calpis Drink"
    assert items[0].price == Decimal("1.99")
    assert items[1].description == "JHL. Fried Red Onion 227g"
    assert items[1].price == Decimal("6.99")


def test_extract_items_prefers_forward_item_for_reg_marker_price_lines() -> None:
    lines = [
        "*Chuan Qi Hot Pot Sauce 10 0.99",
        "(|@REG$1.29 1.99",
        "La Pian (Spicy Gluten Sli",
        "*Yuan Qi Sen Lin Iced Tea 1.99",
        "(REG$299 3.99",
        "*Or:ion Double Choco Pie 12",
        "&& Meat 13.88",
        "SUB Total 22.84",
        "Total after Tax 22.84",
    ]

    items = _extract_items(
        lines,
        summary_amounts={Decimal("22.84")},
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    assert any(item.description == "*Chuan Qi Hot Pot Sauce 10" and item.price == Decimal("0.99") for item in items)
    assert any(item.description == "La Pian (Spicy Gluten Sli" and item.price == Decimal("1.99") for item in items)
    assert any(item.description == "*Yuan Qi Sen Lin Iced Tea" and item.price == Decimal("1.99") for item in items)
    assert any(item.description == "*Or:ion Double Choco Pie 12" and item.price == Decimal("3.99") for item in items)
    assert not any(
        item.description == "*Yuan Qi Sen Lin Iced Tea 1.99" and item.price == Decimal("3.99") for item in items
    )


def test_extract_items_uses_neighboring_items_for_onsale_markers() -> None:
    lines = [
        "*Udon Noodles With Tonkots 3.99",
        "ONSAL 3.99",
        "*Lucky Pearl Shanghai Dry",
        "ONSALE 3.99",
        "*Lucky Henan Noodles",
        "(ONSAL",
        "SUB Total 11.97",
        "Total after Tax 11.97",
    ]

    items = _extract_items(
        lines,
        summary_amounts={Decimal("11.97")},
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    pairs = {(item.description, item.price) for item in items}
    assert ("*Udon Noodles With Tonkots", Decimal("3.99")) in pairs
    assert ("*Lucky Pearl Shanghai Dry", Decimal("3.99")) in pairs
    assert ("*Lucky Henan Noodles", Decimal("3.99")) in pairs
    assert not any(item.description in {"ONSAL", "ONSALE"} for item in items)


def test_extract_items_keeps_cash_prefix_product_names() -> None:
    lines = [
        "CASHMERE BATHROOM TISSUE 9.99",
        "TOTAL 9.99",
    ]

    items = _extract_items(
        lines,
        summary_amounts={Decimal("9.99")},
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    assert len(items) == 1
    assert items[0].description == "CASHMERE BATHROOM TISSUE"
    assert items[0].price == Decimal("9.99")


def test_extract_items_handles_weight_quantity_previous_description() -> None:
    lines = [
        "Al-Premium Food Mart",
        "Batry Strawbarries",
        "3 @ $1.99 5.97",
        "Cherries",
        "(H7)",
        "1.22 1k @ $2.99/1b 3.65",
        "Sub Total 55.29",
    ]

    items = _extract_items(
        lines,
        summary_amounts={Decimal("55.29")},
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    observed = {(item.description, item.price) for item in items}
    assert ("Batry Strawbarries", Decimal("5.97")) in observed
    assert ("Cherries", Decimal("3.65")) in observed


def test_extract_items_handles_loblaw_multi_buy_and_priced_section_headers() -> None:
    first_lines = [
        "LOBLAW",
        "DICED TOMATO",
        "2 @ 2/$5.00 5.00",
        "TOTAL 5.00",
    ]

    first_items = _extract_items(
        first_lines,
        summary_amounts={Decimal("5.00")},
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    assert len(first_items) == 1
    assert first_items[0].description == "DICED TOMATO"
    assert first_items[0].price == Decimal("5.00")

    second_lines = [
        "LOBLAW",
        "22-DAIRY 5.49",
        "06038304842 PC ORNG JUICE NP MRJ 5.49",
        "TOTAL 5.49",
    ]

    second_items = _extract_items(
        second_lines,
        summary_amounts={Decimal("5.49")},
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    assert len(second_items) == 1
    assert second_items[0].description == "PC ORNG JUICE NP MRJ"
    assert second_items[0].price == Decimal("5.49")


def test_extract_items_keeps_costco_prescanned_duplicates() -> None:
    lines = [
        "COSTCO",
        "EWHOLESALE",
        "Markham #151",
        "1 Yorktech Dr",
        "Markham, ON L6G 1A6",
        "OL Member 111942685019",
        "***START OF PRE-SCANNED ITEMS*********",
        "232952 COKE ZERO 16.99",
        "1075424 50/70 SHRIMP 16.99",
        "430 XL EGGS 9.69",
        "430 XL EGGS 9.69",
        "435259 2% FINE-FILT 5.95",
        "435259 2% FINE-FILT 5.95",
        "*X*END OF PRE-SCANNED ITEMSX*X****XX**",
        "TOTAL NUMBER OF PRE-SCANNED ITEMS= 6",
        "SUBTOTAL 65.26",
        "TAX 2.21",
        "TOTAL 67.47",
    ]

    items = _extract_items(
        lines,
        summary_amounts={Decimal("67.47"), Decimal("65.26"), Decimal("2.21")},
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    assert len(items) == 6
    assert sum(item.price for item in items) == Decimal("65.26")

    observed = Counter((item.description, item.price) for item in items)
    assert observed[("232952 COKE ZERO", Decimal("16.99"))] == 1
    assert observed[("1075424 50/70 SHRIMP", Decimal("16.99"))] == 1
    assert observed[("430 XL EGGS", Decimal("9.69"))] == 2
    assert observed[("435259 2% FINE-FILT", Decimal("5.95"))] == 2
