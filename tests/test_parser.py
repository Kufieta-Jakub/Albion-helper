from albion_tracker import parser

CITIES = ["Caerleon", "Bridgewatch", "Fort Sterling", "Lymhurst", "Martlock", "Thetford", "Brecilien"]


def box(text, x0, cy, h=20, w=80):
    return {"text": text, "score": 0.9, "x0": x0, "x1": x0 + w, "y0": cy - h / 2, "y1": cy + h / 2,
            "cx": x0 + w / 2, "cy": cy}


def test_detect_city_single_box():
    assert parser.detect_city([box("Fort Sterling Rynek", 0, 0)], CITIES) == "Fort Sterling"


def test_detect_city_split_boxes_and_ocr_noise():
    assert parser.detect_city([box("Lymhurst", 0, 0), box("Rynek", 90, 0)], CITIES) == "Lymhurst"
    assert parser.detect_city([box("Lymhurst Rynelc", 0, 0)], CITIES) == "Lymhurst"


def test_no_market_no_city():
    assert parser.detect_city([box("Lymhurst", 0, 0), box("Ekwipunek", 90, 0)], CITIES) is None
    assert parser.detect_city([], CITIES) is None


def test_parse_number_formats():
    assert parser.parse_number("1 234") == 1234
    assert parser.parse_number("12,345") == 12345
    assert parser.parse_number("1.234.567") == 1234567
    assert parser.parse_number("Zlecenie") is None


def test_rows_and_consistency_filter():
    boxes = [
        box("3", 0, 10), box("1 500", 100, 11), box("4 500", 200, 10),     # ok
        box("2", 0, 50), box("1 600", 100, 51), box("9 999", 200, 50),     # niespojne -> odrzucone
        box("10", 0, 90), box("1 700", 100, 90), box("17 000", 200, 91),   # ok
    ]
    rows = parser.parse_price_rows(boxes, ["quantity", "unit_price", "total_price"])
    assert [(r["quantity"], r["unit_price"]) for r in rows] == [(3, 1500), (10, 1700)]


def test_order_type_detection():
    kw = {"buy": ["kupna"], "sell": ["sprzedaz"]}
    assert parser.detect_order_type("Zlecenia kupna", kw, "sell") == "buy"
    assert parser.detect_order_type("Zlecenia sprzedaży", kw, "buy") == "sell"
    assert parser.detect_order_type("", kw, "sell") == "sell"


def test_missing_quantity_is_derived_from_total():
    boxes = [box("1 500", 100, 11), box("4 500", 200, 10)]
    rows = parser.parse_price_rows(boxes, ["quantity", "unit_price", "total_price"])
    assert rows == [{"quantity": 3, "unit_price": 1500, "total_price": 4500}]


def test_city_glued_with_keyword():
    assert parser.detect_city([box("LymhurstRynekG", 0, 0)], CITIES) == "Lymhurst"


def _real_boxes():
    # fragment prawdziwego odczytu OCR okienka (zrzut 1186x849)
    def b(t, x0, x1, cy, h):
        return {"text": t, "score": .9, "x0": x0, "x1": x1, "y0": cy - h / 2, "y1": cy + h / 2,
                "cx": (x0 + x1) / 2, "cy": cy}
    return [b("Skrawki skor", 213, 369, 220, 24), b("Zlecenia sprzedazy", 640, 807, 231, 26),
            b("Zlecenia kupna", 911, 1047, 232, 23), b("Zlecenie sprzedazy", 241, 388, 418, 25),
            b("Zlecenie kupna", 243, 360, 444, 17), b("Zaklecie: 0", 271, 347, 306, 19),
            b("St.1", 121, 151, 306, 14), b("Normalna", 426, 496, 306, 16)]


def test_popup_header_name_and_filters():
    info = parser.parse_popup_header(_real_boxes(), 849)
    assert (info["name"], info["tier"], info["enchantment"], info["quality"]) == ("Skrawki skor", 1, 0, "normalna")
    assert round(info["c"]) == 271


def test_popup_not_found_without_column_headings():
    assert parser.parse_popup_header(_real_boxes()[3:5], 849) is None


def test_headings_glued_by_ocr_are_found():
    def b(t, x0, x1, cy):
        return {"text": t, "score": .9, "x0": x0, "x1": x1, "y0": cy - 12, "y1": cy + 12, "cx": (x0 + x1) / 2, "cy": cy}
    boxes = [b("Zleceniasprzedazy", 640, 807, 231), b("Zleceniakupna", 911, 1047, 232)]
    assert parser.find_columns(boxes, 849) is not None


def test_item_types():
    from albion_tracker.categories import classify
    assert classify("Buty najemnika mistrza") == "Buty"
    assert classify("Runa adepta") == "Runy, dusze, relikty"
    assert classify("Tomisko wiedzy") == "Runy, dusze, relikty"
    assert classify("Oswojony olbrzymi jelen adepta") == "Wierzchowce"
    assert classify("Deski debowe") == "Surowce"
    assert classify("Skrawki skor") == "Surowce"
    assert classify("Coś zupełnie nowego") == "Inne"


def test_tier_words():
    from albion_tracker.categories import split_tier_word, classify
    assert split_tier_word("Buty najemnika adepta") == ("Buty najemnika", 4)
    assert split_tier_word("Hetm zotnierza nowicjusza") == ("Hetm zotnierza", 2)
    assert split_tier_word("Buty najemnika wszechmistrza") == ("Buty najemnika", 7)
    assert split_tier_word("Buty najemnika poczatkujacego") == ("Buty najemnika", 1)
    assert split_tier_word("Deski debowe") == ("Deski debowe", None)
    assert split_tier_word("Runa") == ("Runa", None)
    assert classify("Hetm zotnierza adepta") == "Hełmy"
