-- Schemat bazy Albion Market Tracker (idempotentny)
CREATE TABLE IF NOT EXISTS cities (
    id   SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

-- Wariant przedmiotu = nazwa + stopien (tier) + zaklecie + jakosc
CREATE TABLE IF NOT EXISTS items (
    id          SERIAL PRIMARY KEY,
    name        TEXT     NOT NULL,
    tier        SMALLINT NOT NULL DEFAULT 0,
    enchantment SMALLINT NOT NULL DEFAULT 0,
    quality     TEXT     NOT NULL DEFAULT '',
    UNIQUE (name, tier, enchantment, quality)
);

CREATE TABLE IF NOT EXISTS market_prices (
    id          BIGSERIAL PRIMARY KEY,
    item_id     INTEGER NOT NULL REFERENCES items(id),
    city_id     INTEGER NOT NULL REFERENCES cities(id),
    order_type  TEXT    NOT NULL CHECK (order_type IN ('sell', 'buy')),
    unit_price  BIGINT  NOT NULL CHECK (unit_price > 0),
    quantity    INTEGER,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_prices_lookup
    ON market_prices (item_id, city_id, order_type, captured_at DESC);

INSERT INTO cities (name) VALUES
    ('Caerleon'), ('Bridgewatch'), ('Fort Sterling'),
    ('Lymhurst'), ('Martlock'), ('Thetford')
ON CONFLICT (name) DO NOTHING;

-- Ostatni skan dla kazdej pary (wariant, miasto): najnizsza oferta sprzedazy
-- i najwyzsze zlecenie kupna.
CREATE OR REPLACE VIEW latest_prices AS
SELECT i.name AS item, i.tier, i.enchantment, i.quality, c.name AS city,
       p.order_type, p.unit_price, p.quantity, p.captured_at
FROM (
    SELECT DISTINCT ON (item_id, city_id, order_type) *
    FROM (
        SELECT mp.*,
               CASE WHEN order_type = 'sell' THEN unit_price ELSE -unit_price END AS rank_key
        FROM market_prices mp
        WHERE captured_at = (
            SELECT max(captured_at) FROM market_prices m2
            WHERE m2.item_id = mp.item_id AND m2.city_id = mp.city_id
              AND m2.order_type = mp.order_type
        )
    ) q
    ORDER BY item_id, city_id, order_type, rank_key
) p
JOIN items  i ON i.id = p.item_id
JOIN cities c ON c.id = p.city_id;
