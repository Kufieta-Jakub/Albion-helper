# Albion Market Tracker

Automatycznie odczytuje ceny z okienka zleceń rynku w Albion Online i zapisuje je do PostgreSQL.
(np. „Lymhurst Rynek”, „Fort Sterling Rynek”), a jeśli tak, odczytuje przez OCR nazwę przedmiotu
i listę ofert (ilość, cena jednostkowa, suma) i zapisuje je do PostgreSQL.

OCR działa tylko na obrazie ekranu: nie dotyka procesu gry ani ruchu sieciowego.

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
   Dane logowania do bazy są w pliku `.env` (domyślnie albion/albion, port 5432).

## Użycie

1. `python main.py reset-db` (tylko przy pierwszym uruchomieniu po zmianie schematu) lub `init-db`.
2. Test odczytu na zrzucie z otwartym okienkiem przedmiotu: `python main.py scan-image zrzut.png`
3. `python main.py run`: co 10 s zrzut ekranu i szukanie „<miasto> Rynek”; po wykryciu rynku
   zrzuty co 0,5 s i odczyt okienka ze zleceniami sprzedaży i kupna (cena, ilość, stopień,
   zaklęcie, jakość). Każdy zrzut jest zapisywany na czas analizy i zaraz usuwany.
   Okienko przedmiotu musi być otwarte; ten sam ekran nie jest zapisywany dwa razy.

```
python main.py latest --item skrawki   # najnizsza sprzedaz / najwyzsze kupno
python main.py history skrawki --city Lymhurst
python main.py export ceny.csv         # CSV (separator ;) do Excela
```

Ustawienia (interwały, lista miast) są w `config.json`.

## Baza (PostgreSQL)

Tabele: `cities`, `items` (nazwa, stopień, zaklęcie, jakość), `market_prices` (item, miasto, `sell`/`buy`, cena, ilość, czas)
oraz widok `latest_prices`. Schemat: `sql/schema.sql`. Dodatkowe miasta dopisz w `config.json`.

## Testy

`python -m pytest tests`
