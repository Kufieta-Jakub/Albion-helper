from datetime import datetime, timezone
from pathlib import Path

import psycopg

from .config import database_url

SCHEMA = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"


def connect() -> psycopg.Connection:
    return psycopg.connect(database_url())


def init_db(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA.read_text(encoding="utf-8"))
    conn.commit()


def reset_db(conn: psycopg.Connection) -> None:
    """Kasuje tabele cen i przedmiotow (miasta zostaja) i tworzy je od nowa."""
    conn.execute("DROP VIEW IF EXISTS latest_prices")
    conn.execute("DROP TABLE IF EXISTS market_prices, items CASCADE")
    init_db(conn)


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
        "INSERT INTO items (name, tier, enchantment, quality) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (name, tier, enchantment, quality) DO UPDATE SET name = EXCLUDED.name "
        "RETURNING id",
        (popup["name"], popup["tier"] or 0, popup["enchantment"] or 0, popup["quality"] or ""),
    ).fetchone()[0]
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
