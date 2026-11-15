import io
import os
import time
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

try:
    import cv2
except ImportError:   # bez OpenCV zostaje tylko wolna sciezka
    cv2 = None

from . import ocr, parser

CAPTURES = Path(__file__).resolve().parent.parent / "captures"


def _shift(boxes, dx, dy):
    for b in boxes:
        b["x0"] += dx; b["x1"] += dx; b["cx"] += dx
        b["y0"] += dy; b["y1"] += dy; b["cy"] += dy
    return boxes


def _signature(img: Image.Image, region) -> np.ndarray:
    g = ImageOps.grayscale(img.crop(region))
    return np.asarray(g.resize((96, max(1, int(96 * g.height / g.width)))), dtype=np.int16)


def _content_sig(img: Image.Image, info: dict) -> list:
    """Odcisk tresci okienka: tytul z filtrami oraz tabele zlecen, w polowie rozdzielczosci.
    Zmiana pojedynczej cyfry musi byc widoczna (dawna miniatura 96 px ja zacierala)."""
    hy, c, sx, kx = info["hy"], info["c"], info["sx"], info["kx"]
    regs = [(sx - 2.0 * c, hy - 0.12 * c, sx - 0.15 * c, hy + 0.38 * c),     # tytul + filtry
            (sx - 0.05 * c, hy + 0.08 * c, kx + 0.9 * c, hy + 0.75 * c)]     # tabele zlecen
    out = []
    for x0, y0, x1, y1 in regs:
        g = ImageOps.grayscale(img.crop((max(0, int(x0)), max(0, int(y0)), int(x1), int(y1))))
        out.append(np.asarray(g.resize((max(1, g.width // 2), max(1, g.height // 2)), Image.BILINEAR), dtype=np.int16))
    return out


def _content_same(a: list | None, b: list | None) -> bool:
    if not a or not b or any(x.shape != y.shape for x, y in zip(a, b)):
        return False
    return sum(int((np.abs(x - y) > 40).sum()) for x, y in zip(a, b)) < 4


def _same(a: np.ndarray, b: np.ndarray, tol: float = 4.0) -> bool:
    return a.shape == b.shape and float(np.abs(a - b).mean()) < tol


# Warianty odczytu komorki: (wysokosc wycinka w c, powiekszenie, autokontrast).
# Dla kazdej komorki wygrywa wynik o najwyzszej pewnosci. Zestaw dobrany na prawdziwych zrzutach:
# 48/48 poprawnych odczytow w rapidocr-onnxruntime 1.2 i 1.4.
CELL_VARIANTS = ((0.035, 4, False), (0.03, 4, False), (0.035, 4, True))


def _cell_image(img: Image.Image, x0: float, x1: float, cy: float, half: float, canvas_w: int,
                up: int, autocontrast: bool) -> Image.Image:
    """Ciasny wycinek jednej komorki (cyfry zajmuja wiekszosc wysokosci), umieszczony na plotnie
    o stalym rozmiarze. Stala wielkosc sprawia, ze wynik nie zalezy od reszty komorek w partii."""
    crop = img.crop((int(x0), int(cy - half), int(x1), int(cy + half)))
    g = ImageOps.grayscale(crop)
    if autocontrast:
        g = ImageOps.autocontrast(g, cutoff=1)
    g = g.resize((max(1, g.width * up), max(1, g.height * up)), Image.LANCZOS)
    bg = int(np.median(np.asarray(g)))
    canvas = Image.new("L", (max(canvas_w, g.width + 20), g.height), bg)
    canvas.paste(g, (10, 0))
    return canvas.convert("RGB")


def _to_int(text: str) -> int | None:
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits and len(digits) <= 12 else None


CONF_OK = 0.55   # komorka odczytana pierwszym wariantem z pewnoscia >= tej wartosci nie wymaga dalszych


def read_orders(img: Image.Image, info: dict, timing: dict | None = None) -> dict:
    """Czyta zlecenia sprzedazy i kupna. Najpierw kazda komorka 1 wariantem (24 wycinki); kolejne
    warianty tylko dla komorek pustych lub niepewnych. Kolumna konczy sie na pierwszym pustym wierszu."""
    t0 = time.time()
    cells = parser.cell_specs(info)
    c = info["c"]
    up_max = max(v[1] for v in CELL_VARIANTS)
    canvas_w = int(max(cell[k][1] - cell[k][0] for cell in cells for k in ("price", "qty")) * up_max) + 20

    def crop(i, key, v):
        half, up, ac = CELL_VARIANTS[v]
        cell = cells[i]
        return _cell_image(img, cell[key][0], cell[key][1], cell["cy"], half * c, canvas_w, up, ac)

    # wyniki: (i, key) -> lista (pewnosc, liczba) ze wszystkich dotychczasowych wariantow
    results: dict = {}
    done_variants: dict = {}

    def run(jobs):
        if not jobs:
            return
        rec = ocr.recognize([crop(i, key, v) for i, key, v in jobs])
        for (i, key, v), (text, score) in zip(jobs, rec):
            done_variants.setdefault((i, key), set()).add(v)
            val = _to_int(text)
            results.setdefault((i, key), [])
            if val is not None:
                results[(i, key)].append((score, val))

    def best(i, key):
        vals = results.get((i, key))
        return max(vals) if vals else None

    run([(i, key, 0) for i in range(len(cells)) for key in ("price", "qty")])
    t1 = time.time()
    extra = 0
    nv = len(CELL_VARIANTS)

    def needs(i, key):
        b = best(i, key)
        return (b is None or b[0] < CONF_OK) and len(done_variants.get((i, key), ())) < nv

    def more(i, key):
        return [(i, key, v) for v in range(nv) if v not in done_variants.get((i, key), ())]

    while True:
        jobs, ended = [], set()
        for i, cell in enumerate(cells):
            side = cell["side"]
            if side in ended:
                continue
            if needs(i, "price"):
                jobs += more(i, "price")
                if best(i, "price") is None:
                    ended.add(side)      # poczekaj na wynik tej komorki, dalsze wiersze pozniej
                    continue
            elif best(i, "price") is None:
                ended.add(side)          # pusty wiersz = koniec listy
                continue
            if needs(i, "qty"):
                jobs += more(i, "qty")
        if not jobs:
            break
        extra += len(jobs)
        run(jobs)

    out = {"sell": [], "buy": []}
    ended = set()
    for i, cell in enumerate(cells):
        side = cell["side"]
        if side in ended:
            continue
        bp = best(i, "price")
        if not bp or not bp[1]:
            ended.add(side)
            continue
        bq = best(i, "qty")
        out[side].append({"unit_price": bp[1], "quantity": bq[1] if bq else None})
    if timing is not None:
        timing["komorki"] = time.time() - t0
        timing["komorki_dodatkowe"] = extra
    return out


def _title_sig(crop: Image.Image) -> np.ndarray:
    return np.asarray(ImageOps.grayscale(crop), dtype=np.int16)    # w pelnej rozdzielczosci


def _title_same(a: np.ndarray, b: np.ndarray) -> bool:
    """Ten sam napis = praktycznie identyczne piksele. Dawna miniatura 96x12 uznawala za jedno
    'Deski cedrowe' i 'Deski sosnowe', co dawalo bledna nazwe przy zmianie przedmiotu."""
    return a.shape == b.shape and int((np.abs(a - b) > 40).sum()) < 4


def read_title(img: Image.Image, info: dict, state: dict | None = None) -> str:
    """Nazwa przedmiotu. Jesli wycinek tytulu sie nie zmienil, bierzemy poprzednia nazwe. Nowy tytul
    czytamy 3 razy z roznymi marginesami i bierzemy wynik powtarzajacy sie (inaczej: najpewniejszy).
    Nazwa bardzo podobna do juz znanej jest do niej sprowadzana (np. 'Zdeski debowe' -> 'Deski debowe')."""
    state = state if state is not None else {}
    x0, y0, x1, y1 = info["title"]
    base = img.crop((max(0, int(x0 - 4)), max(0, int(y0 - 4)), int(x1 + 4), int(y1 + 4)))
    sig = _title_sig(base)
    cached = state.get("title_cache")
    if cached and _title_same(cached[0], sig):
        return cached[1]

    crops = []
    for m in (2, 4, 6):
        cr = img.crop((max(0, int(x0 - m)), max(0, int(y0 - m)), int(x1 + m), int(y1 + m)))
        g = ImageOps.autocontrast(ImageOps.grayscale(cr), cutoff=1).convert("RGB")
        crops.append(g.resize((g.width * 2, g.height * 2), Image.LANCZOS))
    reads = [(parser.normalize(t).capitalize(), sc) for t, sc in ocr.recognize(crops)]
    reads = [(t, sc) for t, sc in reads if t]
    if not reads:
        return state.get("title_cache", (None, info["name"]))[1]
    votes = Counter(t for t, _ in reads)
    top, n = votes.most_common(1)[0]
    name = top if n >= 2 else max(reads, key=lambda r: r[1])[0]

    names = state.setdefault("names", Counter())
    if name not in names:
        for known, _cnt in names.most_common():
            if SequenceMatcher(None, name, known).ratio() >= 0.9:
                name = known
                break
    state["cand"] = name              # do licznika znanych nazw trafi dopiero po potwierdzeniu (run_loop)
    state["title_cache"] = (sig, name)
    return name


def fast_info(img: Image.Image, last: dict, state: dict) -> dict | None:
    """Okienko stoi w tym samym miejscu co poprzednio (pas naglowkow identyczny): zamiast OCR calego
    paska czytamy tylko tytul i trzy pola filtrow. None = nie udalo sie, trzeba uzyc wolnej sciezki."""
    c = last["c"]
    f = parser.filter_specs(last)
    canvas_w = int(max(f[k][1] - f[k][0] for k in ("tier", "ench", "quality")) * 3) + 20
    crops = [_cell_image(img, f[k][0], f[k][1], f["cy"], 0.04 * c, canvas_w, 3, False)
             for k in ("tier", "ench", "quality")]
    texts = [t for t, _ in ocr.recognize(crops)]
    tier, ench, quality = parser.parse_filters(*texts)
    if tier is None or ench is None or not quality:
        return None
    x0, y0, x1, y1 = last["title"]
    wide = (x0, y0, x0 + parser.TITLE_WIDTH * c, y1)
    info = dict(last, tier=tier, enchantment=ench, quality=quality, title=wide)
    info["name"] = read_title(img, info, state)
    info["title"] = last["title"]
    info["_named"] = True
    return info


_SEARCH_PAD = 40   # okienko stoi w stalym miejscu: sprawdzamy tylko male otoczenie zapamietanej pozycji


def _gray(img: Image.Image, region=None) -> np.ndarray:
    g = ImageOps.grayscale(img if region is None else img.crop(region))
    return np.asarray(g)


def _remember_head(img: Image.Image, info: dict, state: dict):
    """Zapamietuje wyglad paska z naglowkami kolumn - wzorzec do szybkiego odnajdywania okienka."""
    x0, y0, x1, y1 = parser.head_region(info)
    state["head_tmpl"] = (_gray(img, (x0, y0, x1, y1)), (x0, y0))


def _locate_popup(img: Image.Image, last: dict | None, state: dict) -> dict | None:
    """Czy okienko przedmiotu jest otwarte w zapamietanym miejscu? Porownuje wzorzec paska z naglowkami
    kolumn tylko w malym otoczeniu dawnej pozycji (kilka ms, bez OCR). Zwraca info (z ewentualnym
    drobnym przesunieciem) albo None, gdy okienka tam nie ma."""
    tm = state.get("head_tmpl")
    if not last or not tm or cv2 is None:
        return None
    tmpl, (ox, oy) = tm
    th, tw = tmpl.shape
    W, H = img.size
    x0, y0 = max(0, ox - _SEARCH_PAD), max(0, oy - _SEARCH_PAD)
    x1, y1 = min(W, ox + tw + _SEARCH_PAD), min(H, oy + th + _SEARCH_PAD)
    win = _gray(img, (x0, y0, x1, y1))
    if win.shape[0] < th or win.shape[1] < tw:
        return None
    res = cv2.matchTemplate(win, tmpl, cv2.TM_CCOEFF_NORMED)
    _, mx, _, loc = cv2.minMaxLoc(res)
    if mx < 0.9:
        return None
    dx, dy = x0 + loc[0] - ox, y0 + loc[1] - oy
    info = dict(last)
    info["sx"] += dx
    info["kx"] += dx
    info["hy"] += dy
    tx0, ty0, tx1, ty1 = last["title"]
    info["title"] = (tx0 + dx, ty0 + dy, tx1 + dx, ty1 + dy)
    return info


def _header_region(box: dict, size) -> tuple:
    """Prostokat wokol naglowka rynku (z zapasem na dluzsze nazwy miast)."""
    W, H = size
    w, h = box["x1"] - box["x0"], box["y1"] - box["y0"]
    return (max(0, int(box["x0"] - 0.2 * w)), max(0, int(box["y0"] - 0.8 * h)),
            min(W, int(box["x1"] + 1.0 * w)), min(H, int(box["y1"] + 0.8 * h)))


def _crop_boxes(img, region, upscale):
    return _shift(ocr.read(img.crop(region), upscale=upscale), region[0], region[1])


def scan_once(img: Image.Image, cfg: dict, state: dict):
    """Jeden krok analizy.

    Pelny OCR ekranu jest drogi, wiec po pierwszym wykryciu zapamietujemy gdzie jest naglowek
    rynku i okienko przedmiotu i sprawdzamy tylko te male wycinki.
    state: city, info (okienko sledzone teraz), last_info (ostatnio widziane polozenie okienka),
    header_region, sig, ticks.  Zwraca (status, popup|None);
    status: 'no_market' | 'no_popup' | 'unchanged' | 'ok' | 'empty'.
    """
    W, H = img.size
    up = cfg.get("roi_upscale", 1.5)
    info = None
    state["t_start"] = time.time()
    state["timing"] = {}

    # 1. Tryb szybki: okienko juz sledzimy - jesli obraz sie nie zmienil, nic nie robimy
    if state.get("info"):
        regions = parser.popup_regions(state["info"], img.size)
        if _content_same(_content_sig(img, state["info"]), state.get("sig")):
            return "unchanged", None
        moved = _locate_popup(img, state["info"], state)
        if moved:
            info = fast_info(img, moved, state)
        if info is None:
            info = parser.parse_popup_header(_crop_boxes(img, regions["header"], up), H)
        if info is None:
            state["info"] = state["sig"] = None   # okienko zniknelo

    if info is None and state.get("known_city"):
        moved = _locate_popup(img, state.get("last_info"), state)
        if moved:
            info = fast_info(img, moved, state)    # okienko wrocilo (moze w innym miejscu)
            if info is not None:
                state["city"] = state["known_city"]
        if info is None and state.get("last_info"):
            state["pmiss"] = state.get("pmiss", 0) + 1

    if info is None:
        state["ticks"] = state.get("ticks", 0) + 1
        full_boxes = None

        # 2. Czy jest rynek? Najpierw tani test na zapamietanym naglowku...
        city = None
        if state.get("header_region"):
            city = parser.detect_city(_crop_boxes(img, state["header_region"], up),
                                      cfg["cities"], cfg.get("market_keyword", "rynek"))
        # ...a pelny skan ekranu tylko gdy nie znamy naglowka (albo co N-ty skan, gdyby okno ruszono)
        if not city and (not state.get("header_region") or state["ticks"] % cfg.get("rescan_every", 3) == 0):
            full_boxes = ocr.read(img)
            city = parser.detect_city(full_boxes, cfg["cities"], cfg.get("market_keyword", "rynek"))
            hb = parser.find_city_box(full_boxes, city) if city else None
            state["header_region"] = _header_region(hb, img.size) if hb else state.get("header_region")
        state["city"] = city
        if city:
            state["known_city"] = city
        if not city:
            return "no_market", None

        # 3. Czy otwarte jest okienko przedmiotu? Tani test w miejscu, gdzie ostatnio bylo
        if state.get("last_info") and state.get("pmiss", 0) % 4 == 3:
            # okienka nie widac w zapamietanym miejscu od kilku sprawdzen: dopiero teraz (rzadko) kosztowny odczyt
            info = parser.parse_popup_header(
                _crop_boxes(img, parser.popup_regions(state["last_info"], img.size)["header"], up), H)
        if info is None and full_boxes is None and not state.get("last_info"):
            full_boxes = ocr.read(img)          # nie wiemy jeszcze, gdzie bywa okienko
        if info is None and full_boxes is not None:
            info = parser.parse_popup_header(full_boxes, H)
        if info is None:
            return "no_popup", None

    # 4. Odczyt zlecen
    t = state.setdefault("timing", {})
    t["okno"] = time.time() - state.get("t_start", time.time())
    t0 = time.time()
    if "_named" not in info:
        info = dict(info, name=read_title(img, info, state))
    t["tytul"] = time.time() - t0
    orders = read_orders(img, info, t)
    state["info"] = state["last_info"] = info
    state["pmiss"] = 0
    _remember_head(img, info, state)
    state["sig"] = _content_sig(img, info)
    popup = {k: info[k] for k in ("name", "tier", "enchantment", "quality")}
    popup.update(orders)
    popup["icon"] = icon_png(img, info)
    return ("ok" if orders["sell"] or orders["buy"] else "empty"), popup


def icon_png(img: Image.Image, info: dict) -> bytes | None:
    """Wycinek ikony przedmiotu z okienka (PNG) - zapisywany w bazie do wyswietlania w konsoli webowej."""
    x0, y0, x1, y1 = parser.icon_box(info)
    W, H = img.size
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    if x1 - x0 < 16 or y1 - y0 < 16:
        return None
    buf = io.BytesIO()
    img.crop((x0, y0, x1, y1)).save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def fingerprint(city: str, popup: dict) -> tuple:
    return (city, popup["name"], popup["tier"], popup["enchantment"], popup["quality"],
            tuple((r["unit_price"], r["quantity"]) for r in popup["sell"]),
            tuple((r["unit_price"], r["quantity"]) for r in popup["buy"]))


def _store_frame(img: Image.Image) -> Path:
    CAPTURES.mkdir(exist_ok=True)
    path = CAPTURES / f"frame_{int(time.time() * 1000)}.png"
    img.save(path, compress_level=1)
    return path


def run_loop(cfg: dict, grab, save, log=print, once: bool = False):
    """Tryb spokojny: zrzut co detect_interval_seconds, szukamy '<miasto> Rynek'.
    Po wykryciu rynku: zrzut co fast_interval_seconds, odczyt okienka zlecen i zapis do bazy.
    Kazdy zrzut jest zapisywany na dysk na czas analizy i zaraz po niej usuwany."""
    state: dict = {"city": None, "info": None, "sig": None}
    last_fp, last_saved, prev_status = None, 0.0, None
    while True:
        started = time.time()
        img = grab()
        path = None if state.get("info") else _store_frame(img)   # w trybie szybkim bez zapisu na dysk
        try:
            status, popup = scan_once(img, cfg, state)
        except Exception as e:  # petla ma dzialac dalej
            log(f"blad analizy: {e!r}")
            status, popup = "error", None
            state["info"] = state["sig"] = None
        finally:
            if path and not cfg.get("keep_frames"):
                try:
                    os.remove(path)
                except OSError:
                    pass

        if status != prev_status:
            msgs = {"no_market": "Brak rynku na ekranie, skan co %ss" % cfg["detect_interval_seconds"],
                    "no_popup": f"Rynek {state['city']}: otworz przedmiot, zeby zobaczyc zlecenia",
                    "empty": "Okienko otwarte, ale nie odczytano zlecen"}
            if status in msgs:
                log(msgs[status])
            prev_status = status

        if status in ("ok", "empty") and state.get("info") and not once:
            # potwierdzenie: kolejna klatka po chwili musi byc taka sama (gra mogla jeszcze przerysowywac okienko,
            # np. nowy tytul ze starymi cenami). Jesli sie rozni - odrzucamy odczyt i czytamy od nowa.
            time.sleep(cfg.get("confirm_delay_seconds", 0.15))
            img2 = grab()
            if not _content_same(_content_sig(img2, state["info"]), state.get("sig")):
                state["sig"] = None
                if cfg.get("debug_unstable"):
                    log("(okienko jeszcze sie zmienia - odczyt odrzucony, czytam ponownie)")
                time.sleep(cfg.get("confirm_delay_seconds", 0.15))
                continue
            if popup and popup.get("name"):
                state.setdefault("names", Counter())[popup["name"]] += 1

        if status == "ok":
            fp = fingerprint(state["city"], popup)
            if fp != last_fp or time.time() - last_saved > cfg.get("resave_unchanged_after_seconds", 300):
                n = save(state["city"], popup)
                last_fp, last_saved = fp, time.time()
                tm = state.get("timing", {})
                sells = [r["unit_price"] for r in popup["sell"]]
                buys = [r["unit_price"] for r in popup["buy"]]
                log(f"[{state['city']}] {popup['name']} T{popup['tier']}.{popup['enchantment']} "
                    f"{popup['quality']}: zapisano {n} (sprzedaz min {min(sells) if sells else '-'}, "
                    f"kupno max {max(buys) if buys else '-'}) [{time.time() - started:.1f}s: "
                    f"okno {tm.get('okno', 0):.1f} / tytul {tm.get('tytul', 0):.1f} / "
                    f"komorki {tm.get('komorki', 0):.1f} (+{tm.get('komorki_dodatkowe', 0)} dod.)]")

        if once:
            return status, popup
        if status in ("no_market", "error"):
            interval = cfg["detect_interval_seconds"]
        elif state.get("info"):
            interval = cfg["fast_interval_seconds"]
        else:
            interval = cfg.get("no_popup_interval_seconds", 2)
        time.sleep(max(0.0, interval - (time.time() - started)))
