# Albion Market Tracker

Odczytuje ceny z okna rynku w Albion Online (OCR ekranu), zapisuje je do PostgreSQL i pokazuje w konsoli
webowej (Streamlit). Działa wyłącznie na obrazie ekranu: nie dotyka procesu gry ani ruchu sieciowego.

## Jak to działa

1. Co kilka sekund (`detect_interval_seconds`) program robi zrzut ekranu i szuka napisu „`<miasto> Rynek`”.
2. Gdy rynek jest otwarty, przechodzi w tryb szybki: co 0,5 s sprawdza, czy okienko przedmiotu się zmieniło.
   Okienko jest zawsze w tym samym miejscu, więc czytane są tylko jego małe wycinki (nie cały ekran).
3. Z okienka odczytywane są: nazwa przedmiotu, tier, zaklęcie, jakość oraz 6 najlepszych „Zleceń sprzedaży”
   i „Zleceń kupna” (cena + ilość). Ikona przedmiotu jest wycinana z gry i zapisywana w bazie.
4. Odczyt jest potwierdzany drugą klatką po ok. 0,15 s. Jeśli gra jeszcze przerysowuje okienko, odczyt jest
   odrzucany i robiony od nowa (koniec ze starymi cenami pod nowym tytułem).
5. Zrzuty są zapisywane tylko na czas analizy i od razu usuwane. Ten sam ekran nie jest zapisywany dwa razy
   (ponowny zapis najwcześniej po `resave_unchanged_after_seconds`).

Typowy odczyt trwa ok. 0,5–1 s. Rozbicie czasu jest w logu: `[okno / tytuł / komórki (+dod.)]`.

## Instalacja (Windows)

1. Zainstaluj **Python 3.11+** (zaznacz „Add to PATH”) oraz **Docker Desktop**.
2. W folderze projektu:
   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   docker compose up -d
   python main.py init-db
   ```
   Dane logowania do bazy są w pliku `.env` (np. `DATABASE_URL=postgresql://albion:albion@localhost:5433/albion_market`;
   port musi zgadzać się z `docker-compose.yml`).

## Użycie

**Tracker** (otwórz w grze rynek i klikaj kolejne przedmioty):
```
python main.py run
```
Przykładowa linia logu:
`[Lymhurst] Deski cedrowe T5.0 normalna: zapisano 12 (sprzedaz min 823, kupno max 700) [0.5s: okno 0.1 / tytul 0.0 / komorki 0.2 (+0 dod.)]`

**Konsola webowa** (w drugim terminalu, baza musi działać):
```
streamlit run web.py
```
- kafelki przedmiotów z kolorowymi plakietkami najniższej ceny sprzedaży w każdym mieście
  (zielony Lymhurst, pomarańczowy Bridgewatch, fioletowy Thetford, niebieski Martlock, biały Fort Sterling,
  czarny Caerleon); przedmioty o różnych „słowach tierowych” (np. nowicjusza, adepta) są jednym kafelkiem w kilku tierach,
- filtry: wyszukiwarka, **typ przedmiotu** (lista rozwijana), miasta, jakość, tier, zaklęcie,
- po kliknięciu kafelka: wybór tieru / zaklęcia (T6.1…) / jakości, porównanie miast, historia cen, aktualne zlecenia,
- zakładka **🗑 Usuwanie**: kasuje odczyty (wariant w mieście / wariant / całe zaklęcie / cały przedmiot), żeby zgrać je od nowa,
- zakładka „Aktualne ceny (log odczytów)”, czasy w strefie Europe/Warsaw, strona odświeża się sama po nowym zapisie.

**Pozostałe polecenia:**
```
python main.py init-db                  # tworzy/aktualizuje schemat (bezpieczne, nic nie kasuje)
python main.py reset-db --yes           # KASUJE wszystkie ceny i przedmioty
python main.py scan-image zrzut.png     # test odczytu na zrzucie z otwartym okienkiem
python main.py latest --item deski      # najniższa sprzedaż / najwyższe kupno
python main.py history deski --city Lymhurst
python main.py export ceny.csv          # CSV (separator ;) do Excela
```

## Ustawienia (`config.json`)

| klucz | znaczenie |
|---|---|
| `detect_interval_seconds` | co ile sekund szukać rynku, gdy go nie ma |
| `fast_interval_seconds` | odstęp skanów po wykryciu rynku (domyślnie 0,5 s) |
| `no_popup_interval_seconds` | odstęp, gdy rynek jest, ale okienko przedmiotu zamknięte |
| `resave_unchanged_after_seconds` | ponowny zapis tego samego, niezmienionego ekranu |
| `confirm_delay_seconds` | opóźnienie drugiej klatki potwierdzającej odczyt (domyślnie 0,15) |
| `roi_upscale`, `rescan_every` | dokładność i częstość pełnego skanu nagłówka rynku |
| `market_keyword`, `cities` | słowo rozpoznawane w nagłówku i lista miast |
| `monitor`, `keep_frames` | numer monitora; `true` zostawia zrzuty na dysku (do debugowania) |

## Baza (PostgreSQL)

- `cities`, `items` (nazwa, tier, zaklęcie, jakość, **typ przedmiotu**), `market_prices` (przedmiot, miasto,
  `sell`/`buy`, cena, ilość, czas), `item_icons` (ikona na nazwę + tier),
- widok `latest_prices`: ostatni odczyt każdego wariantu w każdym mieście wraz z typem przedmiotu.

Typ przedmiotu jest rozpoznawany po nazwie (`albion_tracker/categories.py`); nierozpoznane trafiają do „Inne”.
Schemat: `sql/schema.sql` (idempotentny, `init-db` dopisze brakujące kolumny w istniejącej bazie).

## Struktura projektu

| plik | zadanie |
|---|---|
| `main.py` | polecenia CLI |
| `web.py` | konsola webowa (Streamlit) |
| `albion_tracker/scanner.py` | pętla skanowania, śledzenie okienka, odczyt tytułu i komórek |
| `albion_tracker/parser.py` | geometria okienka, rozpoznawanie miasta, tieru, zaklęcia, jakości |
| `albion_tracker/ocr.py` | opakowanie RapidOCR |
| `albion_tracker/categories.py` | typy przedmiotów i słowa tierowe |
| `albion_tracker/db.py` | zapis i odczyt PostgreSQL, usuwanie odczytów |
| `albion_tracker/capture.py` | zrzuty ekranu |

## Znane ograniczenia

- OCR gubi polskie znaki („Hetm żołnierza” → „Hetm zotnierza”); nazwy są spójne, ale bez diakrytyków.
- Nazwy, których OCR nie rozpozna wg reguł z `categories.py`, dostają typ „Inne”.

## Testy

```
python -m pytest tests
```
