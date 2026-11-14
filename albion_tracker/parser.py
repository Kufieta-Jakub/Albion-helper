"""Czysta logika parsowania tekstu z OCR (bez zaleznosci od OCR i bazy)."""
import re
import unicodedata
from difflib import SequenceMatcher
from statistics import median


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("ł", "l").replace("Ł", "L")
    return re.sub(r"\s+", " ", text).strip().lower()


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def detect_city(boxes: list[dict], cities: list[str], market_keyword: str = "rynek"):
    """Szuka tekstu '<miasto> Rynek' (takze sklejonego: 'LymhurstRynek'). Zwraca miasto albo None."""
    keyword = normalize(market_keyword)
    texts = [normalize(b["text"]) for b in boxes]
    candidates = list(texts) + [f"{a} {b}" for a, b in zip(texts, texts[1:])]
    best, best_score = None, 0.0
    for cand in candidates:
        ns = cand.replace(" ", "")
        words = cand.split()
        if not (keyword in ns or any(_ratio(w, keyword) >= 0.7 for w in words)):
            continue
        for city in cities:
            c = normalize(city)
            if c.replace(" ", "") in ns:
                score = 1.0
            else:
                n = len(c.split())
                score = max(
                    (_ratio(" ".join(words[i:i + n]), c) for i in range(max(1, len(words) - n + 1))),
                    default=0.0,
                )
            if score > best_score:
                best, best_score = city, score
    return best if best_score >= 0.82 else None


def find_city_box(boxes: list[dict], city: str) -> dict | None:
    """Box OCR z nazwa miasta (do zapamietania polozenia naglowka rynku)."""
    c = normalize(city).replace(" ", "")
    hits = [b for b in boxes if c in normalize(b["text"]).replace(" ", "")]
    return max(hits, key=lambda b: b["x1"] - b["x0"]) if hits else None


def detect_order_type(text: str, keywords: dict, default: str) -> str:
    t = normalize(text)
    for otype, words in keywords.items():
        if any(normalize(w) in t for w in words):
            return otype
    return default


def clean_item_name(boxes: list[dict]) -> str | None:
    if not boxes:
        return None
    ordered = sorted(boxes, key=lambda b: (round(b["cy"] / 10), b["x0"]))
    name = " ".join(b["text"] for b in ordered)
    name = re.sub(r"\s+", " ", name).strip(" .,:;|-_")
    return name or None


_DIGITS = re.compile(r"\d")


def parse_number(text: str) -> int | None:
    """'1 234', '1,234', '1.234', '12345' -> int. Litery (np. ikona monety odczytana jako 'i') pomija."""
    digits = len(_DIGITS.findall(text))
    letters = sum(c.isalpha() for c in text)
    if digits == 0 or letters > 2 * digits:
        return None
    return int(re.sub(r"\D", "", text))


def group_rows(boxes: list[dict]) -> list[list[dict]]:
    if not boxes:
        return []
    tol = 0.6 * median(b["y1"] - b["y0"] for b in boxes)
    rows: list[list[dict]] = []
    for b in sorted(boxes, key=lambda b: b["cy"]):
        if rows and abs(b["cy"] - median(x["cy"] for x in rows[-1])) <= tol:
            rows[-1].append(b)
        else:
            rows.append([b])
    return [sorted(r, key=lambda b: b["x0"]) for r in rows]


def parse_price_rows(boxes: list[dict], columns: list[str]) -> list[dict]:
    """Zamienia boxy OCR listy ofert na wiersze {quantity, unit_price, total_price}.

    Jesli w wierszu jest ilosc, cena jednostkowa i suma, sprawdza spojnosc
    (ilosc * cena = suma) i odrzuca wiersze, w ktorych OCR sie pomylil.
    """
    parsed = []
    for row in group_rows(boxes):
        nums = [n for n in (parse_number(b["text"]) for b in row) if n is not None]
        if len(nums) < 2 or "unit_price" not in columns:
            continue
        # jesli OCR dal wiecej liczb niz kolumn, bierz ostatnie (kolumny liczbowe sa po prawej)
        nums = nums[-len(columns):] if len(nums) > len(columns) else nums
        rec = dict(zip(columns[-len(nums):], nums))
        if rec.get("unit_price", 0) <= 0:
            continue
        q, u, t = rec.get("quantity"), rec["unit_price"], rec.get("total_price")
        if q is None and t and t % u == 0:
            q = t // u  # OCR czesto gubi samotne cyfry - ilosc wynika z sumy
        if q and t and q * u != t:
            continue  # niespojny odczyt
        parsed.append({"quantity": q, "unit_price": u, "total_price": t})
    return parsed


# ---------------------------------------------------------------------------
# Okienko szczegolow przedmiotu: lewa kolumna 'Zlecenia sprzedazy', prawa 'Zlecenia kupna'
# Geometria jest liczona wzgledem naglowkow kolumn, wiec dziala przy kazdej rozdzielczosci
# i skali interfejsu. Jednostka c = odleglosc miedzy lewymi krawedziami obu naglowkow.
# ---------------------------------------------------------------------------
QUALITIES = ["normalna", "dobra", "wybitna", "znakomita", "arcydzielo"]
# Gra odmienia nazwe jakosci przez rodzaj przedmiotu ('Znakomity', 'Wybitny'...), wiec dopasowujemy po temacie slowa
QUALITY_STEMS = {"norm": "normalna", "dobr": "dobra", "wybi": "wybitna", "znak": "znakomita", "arcy": "arcydzielo"}


def match_quality(words) -> str:
    for w in words:
        w = w.strip(".,:;|[]()")
        if w[:4] in QUALITY_STEMS:
            return QUALITY_STEMS[w[:4]]
    return next((q for q in QUALITIES if any(_ratio(w, q) >= 0.8 for w in words)), "")
ROWS = 6                              # tyle ofert miesci sie w oknie bez przewijania
FIRST_ROW, ROW_PITCH = 0.183, 0.0996  # srodek 1. wiersza i odstep wierszy (w c, od linii naglowkow)
SELL_PRICE, BUY_PRICE, QTY = (0.155, 0.455), (0.064, 0.43), (0.44, 0.66)  # zakresy x komorek (w c)


def _word(box: dict, word: str, thr: float = 0.8) -> bool:
    """Czy tekst zawiera slowo (takze sklejony z innymi: 'Zleceniakupna', i z 1 bledem OCR)."""
    t = normalize(box["text"]).replace(" ", "")
    if word in t:
        return True
    n = len(word)
    return any(_ratio(t[i:i + n], word) >= thr for i in range(max(0, len(t) - n + 1)))


def find_columns(boxes: list[dict], u: float):
    """Naglowki kolumn 'Zlecenia sprzedazy' i 'Zlecenia kupna' (ta sama linia, kupno po prawej)."""
    sells = [b for b in boxes if _word(b, "sprzedazy")]
    buys = [b for b in boxes if _word(b, "kupna")]
    pair = None
    for s in sells:
        for k in buys:
            dy = abs(s["cy"] - k["cy"])
            # odsiewa opcje 'Zlecenie ...' po lewej (inna linia, ta sama kolumna)
            if k["x0"] > s["x0"] + 0.1 * u and dy <= 0.02 * u and (pair is None or dy < pair[2]):
                pair = (s, k, dy)
    return (pair[0], pair[1]) if pair else None


def parse_popup_header(boxes: list[dict], u: float) -> dict | None:
    """Znajduje okienko i czyta nazwe oraz filtry (stopien, zaklecie, jakosc).

    u = wysokosc ekranu w px. Zwraca {name, tier, enchantment, quality, hy, c, sx, kx} albo None.
    """
    cols = find_columns(boxes, u)
    if not cols:
        return None
    s, k = cols
    hy = (s["cy"] + k["cy"]) / 2
    c = k["x0"] - s["x0"]

    left = [b for b in boxes if b["x1"] < s["x0"] - 0.005 * c and b["x0"] > s["x0"] - 2.3 * c]
    cands = [b for b in left if hy - 0.10 * c <= b["cy"] <= hy + 0.045 * c]   # tytul tuz nad naglowkami kolumn
    if not cands:
        return None
    best = max(cands, key=lambda b: b["y1"] - b["y0"])
    # w tej samej linii bywa przycisk zamykania odczytany jako 'x' - pomijamy krotkie smieci
    line = sorted((b for b in cands if abs(b["cy"] - best["cy"]) <= 0.04 * c
                   and (b is best or len(b["text"].strip()) > 2)), key=lambda b: b["x0"])
    name = clean_item_name(line)
    name = re.sub(r"\s+[x×]$", "", name, flags=re.I) if name else name
    if not name:
        return None
    name = normalize(name).capitalize()   # OCR gubi polskie znaki i bywa niestabilny w wielkosci liter
    title = (min(b["x0"] for b in line), min(b["y0"] for b in line),
             max(b["x1"] for b in line), max(b["y1"] for b in line))

    fboxes = [b for b in left if hy + 0.19 * c <= b["cy"] <= hy + 0.38 * c]
    ftext = normalize(" ".join(b["text"] for b in sorted(fboxes, key=lambda b: b["x0"])))
    m = re.search(r"st\w*\.?\s*(\d)", ftext)
    tier = int(m.group(1)) if m else None
    m = re.search(r"zakl\w*\W*(\d)", ftext)
    enchant = int(m.group(1)) if m else 0
    quality = match_quality(ftext.split())
    return {"name": name, "tier": tier, "enchantment": enchant, "quality": quality,
            "hy": hy, "c": c, "sx": s["x0"], "kx": k["x0"], "title": title}


# Szybka sciezka (bez pelnego OCR): pas z naglowkami kolumn jest staly, wiec jego obraz mowi,
# czy okienko jest w tym samym miejscu; filtry (stopien, zaklecie, jakosc) czytamy z trzech pol.
FILTER_Y = 0.28                                   # srodek rzedu list rozwijanych (w c, od linii naglowkow)
FILTER_X = {"tier": (-1.99, -1.40), "ench": (-1.39, -0.88), "quality": (-0.82, -0.33)}   # (w c, od sx)
TITLE_WIDTH = 1.30                                # szerokosc wycinka tytulu (w c, od jego lewej krawedzi)


def head_region(info: dict) -> tuple:
    hy, c = info["hy"], info["c"]
    return (int(info["sx"] - 0.05 * c), int(hy - 0.05 * c), int(info["kx"] + 0.95 * c), int(hy + 0.06 * c))


def filter_specs(info: dict) -> dict:
    """{'tier'|'ench'|'quality': (x0, x1)} oraz 'cy' - polozenie pol filtrow w px."""
    c, sx = info["c"], info["sx"]
    out = {k: (sx + a * c, sx + b * c) for k, (a, b) in FILTER_X.items()}
    out["cy"] = info["hy"] + FILTER_Y * c
    return out


def parse_filters(tier_text: str, ench_text: str, quality_text: str):
    """Tekst z trzech pol -> (stopien, zaklecie, jakosc); None/'' gdy nie da sie odczytac."""
    m = re.search(r"(\d)", normalize(tier_text))
    tier = int(m.group(1)) if m else None
    m = re.search(r"(\d)", normalize(ench_text))
    ench = int(m.group(1)) if m else None
    qt = normalize(quality_text).split()
    quality = match_quality(qt)
    return tier, ench, quality


# Ikona przedmiotu (lewy gorny rog okienka): polozenie wzgledem lewej krawedzi naglowka "Zlecenia sprzedazy"
# (sx) i linii naglowkow kolumn (hy), w jednostkach c.
ICON_X = (-1.945, -1.64)
ICON_Y = (-0.09, 0.19)


def icon_box(info: dict) -> tuple:
    c, sx, hy = info["c"], info["sx"], info["hy"]
    return (int(sx + ICON_X[0] * c), int(hy + ICON_Y[0] * c), int(sx + ICON_X[1] * c), int(hy + ICON_Y[1] * c))


def popup_regions(info: dict, size: tuple[int, int]) -> dict:
    """Prostokaty (px) w obrazie: caly obszar okienka i waski pas z naglowkami/nazwa/filtrami."""
    W, H = size
    hy, c = info["hy"], info["c"]
    x0, x1 = max(0, int(info["sx"] - 2.3 * c)), min(W, int(info["kx"] + 0.75 * c))
    return {
        "area": (x0, max(0, int(hy - 0.2 * c)), x1, min(H, int(hy + 0.78 * c))),
        "header": (x0, max(0, int(hy - 0.2 * c)), x1, min(H, int(hy + 0.42 * c))),
    }


def cell_specs(info: dict) -> list[dict]:
    """Komorki do odczytu: dla kazdej strony i wiersza cena i ilosc (x0, x1, cy w px)."""
    hy, c = info["hy"], info["c"]
    out = []
    for side, col_x, price in (("sell", info["sx"], SELL_PRICE), ("buy", info["kx"], BUY_PRICE)):
        for row in range(ROWS):
            cy = hy + (FIRST_ROW + ROW_PITCH * row) * c
            out.append({"side": side, "row": row, "cy": cy,
                        "price": (col_x + price[0] * c, col_x + price[1] * c),
                        "qty": (col_x + QTY[0] * c, col_x + QTY[1] * c), "half": 0.04 * c})
    return out
