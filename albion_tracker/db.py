from datetime import datetime, timezone
from pathlib import Path

import psycopg

from .categories import classify
from .config import database_url

SCHEMA = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"


def connect() -> psycopg.Connection:
    return psycopg.connect(database_url())


def init_db(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA.read_text(encoding="utf-8"))
    refresh_item_types(conn)
    conn.commit()


def refresh_item_types(conn: psycopg.Connection) -> None:
    """Przelicza typ (Buty, Zbroje...) wszystkich przedmiotow wedlug aktualnych regul z categories.py."""
    for (name,) in conn.execute("SELECT DISTINCT name FROM items").fetchall():
        conn.execute("UPDATE items SET item_type = %s WHERE name = %s AND item_type <> %s",
                     (classify(name), name, classify(name)))


def reset_db(conn: psycopg.Connection) -> None:
    """Kasuje tabele cen i przedmiotow (miasta zostaja) i tworzy je od nowa."""
    conn.execute("DROP VIEW IF EXISTS latest_prices")
    conn.execute("DROP TABLE IF EXISTS market_prices, items, item_icons CASCADE")
    init_db(conn)


def delete_prices(conn: psycopg.Connection, name: str, tier: int, enchantment: int | None = None,
                  quality: str | None = None, city: str | None = None) -> int:
    """Kasuje odczyty cen wybranego przedmiotu (zawezone opcjonalnie do zaklecia, jakosci, miasta).

    Zwraca liczbe skasowanych odczytow. Warianty bez zadnych odczytow i ikona zostaja usuniete,
    zeby przedmiot mogl byc zgrany od nowa.
    """
    where, params = ["i.name = %s", "i.tier = %s"], [name, tier]
    if enchantment is not None:
        where.append("i.enchantment = %s"); params.append(enchantment)
    if quality is not None:
        where.append("i.quality = %s"); params.append(quality)
    if city is not None:
        where.append("c.name = %s"); params.append(city)
    cond = " AND ".join(where)
    cur = conn.execute(
        f"DELETE FROM market_prices mp USING items i, cities c "
        f"WHERE mp.item_id = i.id AND mp.city_id = c.id AND {cond}", params)
    deleted = cur.rowcount
    conn.execute("DELETE FROM items i WHERE NOT EXISTS (SELECT 1 FROM market_prices WHERE item_id = i.id)")
    conn.execute("DELETE FROM item_icons ic WHERE NOT EXISTS "
                 "(SELECT 1 FROM items i WHERE i.name = ic.name AND i.tier = ic.tier)")
    conn.commit()
    return deleted


def _get_or_create_city(conn, name: str) -> int:
    return conn.execute(
        "INSERT INTO cities (name) VALUES (%s) "
        "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        (name,),
    ).fetchone()[0]


def save_snapshot(conn, city: str, popup: dict) -> int:
    """Zapisuje jeden odczyt okienka (oferty sprzedazy i zlecenia kupna z tym samym czasem)."""
    rows = [("sell", r) for r in popup["sell"]] + [("buy", r) for r in popup["buy"]]
    if not rows:
        return 0
    now = datetime.now(timezone.utc)
    city_id = _get_or_create_city(conn, city)
    item_id = conn.execute(
        "INSERT INTO items (name, tier, enchantment, quality, item_type) VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (name, tier, enchantment, quality) DO UPDATE SET item_type = EXCLUDED.item_type "
        "RETURNING id",
        (popup["name"], popup["tier"] or 0, popup["enchantment"] or 0, popup["quality"] or "",
         classify(popup["name"])),
    ).fetchone()[0]
    if popup.get("icon"):
        conn.execute(
            "INSERT INTO item_icons (name, tier, png, captured_at) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (name, tier) DO UPDATE SET png = EXCLUDED.png, captured_at = EXCLUDED.captured_at",
            (popup["name"], popup["tier"] or 0, popup["icon"], now),
        )
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO market_prices (item_id, city_id, order_type, unit_price, quantity, captured_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            [(item_id, city_id, t, r["unit_price"], r.get("quantity"), now) for t, r in rows],
        )
    conn.commit()
    return len(rows)


def latest(conn, item: str | None = None, city: str | None = None):
    q = ("SELECT item, tier, enchantment, quality, city, order_type, unit_price, quantity, captured_at "
         "FROM latest_prices WHERE true")
    args: list = []
    if item:
        q += " AND item ILIKE %s"
        args.append(f"%{item}%")
    if city:
        q += " AND city ILIKE %s"
        args.append(f"%{city}%")
    q += " ORDER BY item, tier, enchantment, quality, city, order_type"
    return conn.execute(q, args).fetchall()


def history(conn, item: str, city: str | None = None, limit: int = 50):
    q = (
        "SELECT p.captured_at, i.name, i.tier, i.enchantment, i.quality, c.name, p.order_type, "
        "p.unit_price, p.quantity "
        "FROM market_prices p JOIN items i ON i.id = p.item_id JOIN cities c ON c.id = p.city_id "
        "WHERE i.name ILIKE %s"
    )
    args: list = [f"%{item}%"]
    if city:
        q += " AND c.name ILIKE %s"
        args.append(f"%{city}%")
    q += " ORDER BY p.captured_at DESC, p.order_type, p.unit_price LIMIT %s"
    args.append(limit)
    return conn.execute(q, args).fetchall()
