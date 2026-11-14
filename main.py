"""Albion Market Tracker - CLI.

  python main.py init-db            utworz tabele w PostgreSQL
  python main.py reset-db           skasuj tabele cen i przedmiotow i utworz od nowa
  python main.py run                tryb ciagly: wykrywanie rynku co 10 s, odczyt zlecen co 0,5 s
  python main.py scan-image F.png   przetestuj odczyt na zapisanym zrzucie (bez bazy)
  python main.py latest [--item X] [--city Y]
  python main.py history ITEM [--city Y]
  python main.py export out.csv
"""
import argparse
import csv
import sys

from albion_tracker.config import load_config


def cmd_init_db(_):
    from albion_tracker import db
    with db.connect() as conn:
        db.init_db(conn)
    print("Baza gotowa.")


def cmd_reset_db(args):
    from albion_tracker import db
    if not args.yes and input("Skasowac wszystkie ceny i przedmioty? [t/N] ").lower() not in ("t", "y"):
        return print("Anulowano.")
    with db.connect() as conn:
        db.reset_db(conn)
    print("Baza utworzona od nowa.")


def cmd_run(args):
    from albion_tracker import db, scanner
    from albion_tracker.capture import grab_screen
    cfg = load_config()
    if args.fast:
        cfg["fast_interval_seconds"] = args.fast
    holder = {"conn": db.connect()}

    def save(city, popup):
        try:
            return db.save_snapshot(holder["conn"], city, popup)
        except Exception as e:  # np. restart bazy: polacz ponownie i sprobuj raz jeszcze
            print(f"blad bazy ({e!r}), ponawiam polaczenie", flush=True)
            holder["conn"] = db.connect()
            return db.save_snapshot(holder["conn"], city, popup)

    print(f"Wykrywanie rynku co {cfg['detect_interval_seconds']} s, "
          f"po wykryciu odczyt co {cfg['fast_interval_seconds']} s. Ctrl+C konczy.")
    try:
        scanner.run_loop(cfg, lambda: grab_screen(cfg.get("monitor", 1)), save,
                         log=lambda m: print(m, flush=True))
    except KeyboardInterrupt:
        print("Koniec.")


def cmd_scan_image(args):
    from PIL import Image
    from albion_tracker import scanner
    cfg = load_config()
    state: dict = {}
    status, popup = scanner.scan_once(Image.open(args.file).convert("RGB"), cfg, state)
    print("status:", status, "| miasto:", state.get("city"))
    if popup:
        print(f"przedmiot: {popup['name']}  stopien: {popup['tier']}  zaklecie: {popup['enchantment']}  "
              f"jakosc: {popup['quality']}")
        print("Zlecenia sprzedazy:", popup["sell"])
        print("Zlecenia kupna:   ", popup["buy"])


def _label(item, tier, ench, quality):
    return f"{item} T{tier}.{ench} {quality}".strip()


def cmd_latest(args):
    from albion_tracker import db
    with db.connect() as conn:
        for item, tier, ench, quality, city, otype, price, qty, ts in db.latest(conn, args.item, args.city):
            print(f"{_label(item, tier, ench, quality):40} {city:14} {otype:5} {price:>12,}  x{qty}  {ts:%Y-%m-%d %H:%M}")


def cmd_history(args):
    from albion_tracker import db
    with db.connect() as conn:
        for ts, item, tier, ench, quality, city, otype, price, qty in db.history(conn, args.item, args.city, args.limit):
            print(f"{ts:%Y-%m-%d %H:%M:%S}  {_label(item, tier, ench, quality):38} {city:14} {otype:5} {price:>12,}  x{qty}")


def cmd_export(args):
    from albion_tracker import db
    with db.connect() as conn, open(args.file, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["captured_at", "item", "tier", "enchantment", "quality", "city", "order_type",
                    "unit_price", "quantity"])
        for row in conn.execute(
            "SELECT p.captured_at, i.name, i.tier, i.enchantment, i.quality, c.name, p.order_type, "
            "p.unit_price, p.quantity FROM market_prices p JOIN items i ON i.id=p.item_id "
            "JOIN cities c ON c.id=p.city_id ORDER BY p.captured_at"
        ):
            w.writerow(row)
    print("Zapisano", args.file)


def main():
    p = argparse.ArgumentParser(description="Albion Market Tracker")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db").set_defaults(fn=cmd_init_db)
    rd = sub.add_parser("reset-db")
    rd.add_argument("--yes", action="store_true")
    rd.set_defaults(fn=cmd_reset_db)
    r = sub.add_parser("run")
    r.add_argument("--fast", type=float, help="odstep odczytu po wykryciu rynku, s (domyslnie z config.json)")
    r.set_defaults(fn=cmd_run)
    s = sub.add_parser("scan-image")
    s.add_argument("file")
    s.set_defaults(fn=cmd_scan_image)
    l = sub.add_parser("latest")
    l.add_argument("--item")
    l.add_argument("--city")
    l.set_defaults(fn=cmd_latest)
    h = sub.add_parser("history")
    h.add_argument("item")
    h.add_argument("--city")
    h.add_argument("--limit", type=int, default=50)
    h.set_defaults(fn=cmd_history)
    e = sub.add_parser("export")
    e.add_argument("file")
    e.set_defaults(fn=cmd_export)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
