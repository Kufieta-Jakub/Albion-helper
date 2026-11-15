"""Konsola webowa Albion Market Tracker.

Uruchom:  streamlit run web.py     (baza musi dzialac: docker compose up -d)

Widok glowny: kafelki przedmiotow (np. "Helm zolnierza" jako jeden przedmiot we wszystkich tierach).
Klikniecie kafelka otwiera przedmiot: wybor tieru (T2..T8), zaklecia i jakosci, porownanie miast,
historia cen i zlecenia.
"""
import base64
from difflib import SequenceMatcher

import pandas as pd
import streamlit as st

from albion_tracker import db
from albion_tracker.categories import ALL_TYPES, split_tier_word
from albion_tracker.parser import normalize

st.set_page_config(page_title="Albion Market Tracker", page_icon="📈", layout="wide")

TZ = "Europe/Warsaw"   # baza trzyma czas w UTC, na stronie pokazujemy czas lokalny
QUALITY_ORDER = ["", "normalna", "dobra", "wybitna", "znakomita", "arcydzielo"]
LATEST_COLS = ["item", "tier", "enchantment", "quality", "city", "order_type", "unit_price", "quantity",
               "captured_at", "item_type"]


CITY_STYLE = {
    "Lymhurst": ("#3f7d20", "#ffffff"),       # zielony
    "Bridgewatch": ("#d9731b", "#ffffff"),    # pomaranczowy
    "Thetford": ("#7a3f8f", "#ffffff"),       # fioletowy
    "Martlock": ("#4a7fb5", "#ffffff"),       # niebieski
    "Fort Sterling": ("#f1f1f1", "#222222"),  # bialy
    "Caerleon": ("#111111", "#eeeeee"),       # czarny
}
PER_PAGE = 30

# Caly kafelek jest klikalny: niewidzialny przycisk rozciagniety na caly kontener.
st.markdown("""
<style>
[class*="st-key-card_"] { position: relative; border: 1px solid rgba(128,128,128,.35); border-radius: 10px;
    padding: 12px 14px; transition: background .12s, border-color .12s; }
[class*="st-key-card_"]:hover { background: rgba(128,128,128,.13); border-color: rgba(128,128,128,.8); }
[class*="st-key-card_"] [data-testid="stElementContainer"]:has([data-testid="stButton"]) {
    position: absolute !important; inset: 0 !important; width: 100% !important; max-width: none !important;
    height: 100% !important; z-index: 5; margin: 0 !important; }
[class*="st-key-card_"] [data-testid="stButton"], [class*="st-key-card_"] [data-testid="stButton"] > div,
[class*="st-key-card_"] [data-testid="stButton"] button {
    width: 100% !important; height: 100% !important; opacity: 0; cursor: pointer; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------- dane

@st.cache_data(ttl=5)
def load_latest() -> pd.DataFrame:
    """Ostatni odczyt kazdego wariantu przedmiotu w kazdym miescie (najlepsza oferta kazdego typu)."""
    with db.connect() as conn:
        rows = conn.execute(f"SELECT {', '.join(LATEST_COLS)} FROM latest_prices").fetchall()
    df = pd.DataFrame(rows, columns=LATEST_COLS)
    df["captured_at"] = pd.to_datetime(df["captured_at"], utc=True).dt.tz_convert(TZ)
    return df


@st.cache_data(ttl=5)
def load_history(item: str, tier: int, enchantment: int, quality: str, city: str) -> pd.DataFrame:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT p.captured_at, p.order_type, p.unit_price, p.quantity "
            "FROM market_prices p JOIN items i ON i.id = p.item_id JOIN cities c ON c.id = p.city_id "
            "WHERE i.name = %s AND i.tier = %s AND i.enchantment = %s AND i.quality = %s AND c.name = %s "
            "ORDER BY p.captured_at",
            (item, tier, enchantment, quality, city),
        ).fetchall()
    df = pd.DataFrame(rows, columns=["captured_at", "order_type", "unit_price", "quantity"])
    df["captured_at"] = pd.to_datetime(df["captured_at"], utc=True).dt.tz_convert(TZ)
    return df


@st.cache_data(ttl=30)
def load_icons() -> dict:
    with db.connect() as conn:
        rows = conn.execute("SELECT name, tier, png FROM item_icons").fetchall()
    return {(n, int(t)): bytes(p) for n, t, p in rows}


def add_base(df: pd.DataFrame) -> pd.DataFrame:
    """Kolumna 'base': nazwa przedmiotu bez slowa tierowego ('Helm zolnierza adepta' -> 'Helm zolnierza').
    Drobne roznice OCR w nazwie (w obrebie tego samego typu) sa scalane w jeden przedmiot."""
    raw = df["item"].map(lambda n: split_tier_word(n)[0])
    mapping, reps = {}, {}
    order = raw.value_counts().index          # najczestsze nazwy sa wzorcami
    for name in order:
        typ = df.loc[raw == name, "item_type"].iloc[0]
        key = normalize(name)
        for rep in reps.get(typ, []):
            if SequenceMatcher(None, key, normalize(rep)).ratio() >= 0.9:
                mapping[name] = rep
                break
        else:
            reps.setdefault(typ, []).append(name)
            mapping[name] = name
    df["base"] = raw.map(mapping)
    return df


def summarize(latest: pd.DataFrame) -> pd.DataFrame:
    """Jeden wiersz na (wariant, miasto): najnizsza sprzedaz, najwyzsze kupno, rozpietosc, czas odczytu."""
    keys = ["item", "item_type", "tier", "enchantment", "quality", "city"]
    sell = latest[latest.order_type == "sell"].groupby(keys)["unit_price"].min().rename("sprzedaz_min")
    buy = latest[latest.order_type == "buy"].groupby(keys)["unit_price"].max().rename("kupno_max")
    seen = latest.groupby(keys)["captured_at"].max().rename("odczyt")
    df = pd.concat([sell, buy, seen], axis=1).reset_index()
    df["rozpietosc"] = df["sprzedaz_min"] - df["kupno_max"]
    df["przedmiot"] = df.apply(
        lambda r: f"{r['item']} · T{r['tier']}.{r['enchantment']}" + (f" {r['quality']}" if r["quality"] else ""),
        axis=1)
    return add_base(df)


def quality_sort(values):
    return sorted(values, key=lambda q: QUALITY_ORDER.index(q) if q in QUALITY_ORDER else 99)


def fmt(value) -> str:
    return "-" if pd.isna(value) else f"{int(value):,}".replace(",", " ")


# ---------------------------------------------------------------- strona przedmiotu

def item_page(latest: pd.DataFrame, summary: pd.DataFrame, base: str):
    if st.button("← Wroc do listy"):
        st.session_state["sel"] = None
        st.rerun()

    family = summary[summary.base == base]
    if family.empty:
        st.session_state["sel"] = None
        st.rerun()
        return

    # wybor wariantu: tier, zaklecie (T6.0, T6.1...) i jakosc
    tier_opts = sorted(family.tier.unique())
    c0, c1, c2 = st.columns([2, 2, 2])
    with c0:
        tier = st.radio("Tier", tier_opts, horizontal=True, format_func=lambda t: f"T{t}", key=f"tier_{base}")
    in_tier = family[family.tier == tier]
    name = in_tier["item"].value_counts().index[0]            # pelna nazwa (ze slowem tierowym)
    sub = in_tier[in_tier["item"] == name]
    ench_opts = sorted(sub.enchantment.unique())
    with c1:
        ench = st.radio("Zaklecie", ench_opts, horizontal=True, format_func=lambda e: f"T{tier}.{e}",
                        key=f"ench_{base}_{tier}")
    q_opts = quality_sort(sub[sub.enchantment == ench].quality.unique())
    with c2:
        quality = st.selectbox("Jakosc", q_opts, format_func=lambda q: q or "(brak)",
                               index=q_opts.index("normalna") if "normalna" in q_opts else 0,
                               key=f"qual_{base}_{tier}_{ench}")

    ic, tt = st.columns([1, 9])
    icon = load_icons().get((name, int(tier)))
    if icon:
        ic.image(icon, width=80)
    tt.title(base)
    st.caption(f"{name} · Typ: **{sub.item_type.iloc[0] or 'Inne'}** · Tier {tier} · "
               f"{sub.city.nunique()} miast(a) · ostatni odczyt {sub.odczyt.max():%d.%m %H:%M:%S}")

    # podsumowanie wszystkich wariantow tieru: najnizsza sprzedaz w dowolnym miescie
    st.subheader(f"Warianty T{tier}")
    pivot = sub.groupby(["quality", "enchantment"])["sprzedaz_min"].min().unstack("enchantment")
    pivot = pivot.reindex(quality_sort(pivot.index))
    pivot.index = [q or "(brak)" for q in pivot.index]
    pivot.columns = [f"T{tier}.{e}" for e in pivot.columns]
    st.caption("Najnizsza cena sprzedazy w dowolnym miescie (puste = brak odczytu).")
    st.dataframe(pivot.map(fmt) if hasattr(pivot, "map") else pivot.applymap(fmt))

    # wybrany wariant
    var = sub[(sub.enchantment == ench) & (sub.quality == quality)]
    st.subheader(f"T{tier}.{ench}" + (f" · {quality}" if quality else ""))
    tab_cmp, tab_hist, tab_ord, tab_del = st.tabs(["Porownanie miast", "Historia", "Zlecenia", "🗑 Usuwanie"])

    with tab_cmp:
        chart = var.set_index("city")[["sprzedaz_min", "kupno_max"]].rename(
            columns={"sprzedaz_min": "Sprzedaz min", "kupno_max": "Kupno max"})
        st.bar_chart(chart, stack=False)
        if chart["Sprzedaz min"].notna().any() and chart["Kupno max"].notna().any():
            cheap, rich = chart["Sprzedaz min"].idxmin(), chart["Kupno max"].idxmax()
            st.success(f"Najtaniej kupisz w: **{cheap}** ({fmt(chart.loc[cheap, 'Sprzedaz min'])}), "
                       f"najlepsze zlecenie kupna jest w: **{rich}** ({fmt(chart.loc[rich, 'Kupno max'])}).")
        st.dataframe(var[["city", "sprzedaz_min", "kupno_max", "rozpietosc", "odczyt"]].rename(columns={
            "city": "Miasto", "sprzedaz_min": "Sprzedaz min", "kupno_max": "Kupno max",
            "rozpietosc": "Rozpietosc", "odczyt": "Odczyt"}), hide_index=True,
            column_config={"Odczyt": st.column_config.DatetimeColumn(format="DD.MM HH:mm:ss")})

    with tab_hist:
        city = st.selectbox("Miasto", sorted(var.city.unique()), key=f"hc_{base}_{tier}_{ench}_{quality}")
        h = load_history(name, int(tier), int(ench), quality, city)
        if h.empty:
            st.info("Brak historii.")
        else:
            sells = h[h.order_type == "sell"].groupby("captured_at")["unit_price"].min().rename("Sprzedaz min")
            buys = h[h.order_type == "buy"].groupby("captured_at")["unit_price"].max().rename("Kupno max")
            hist = pd.concat([sells, buys], axis=1).sort_index()
            st.line_chart(hist)
            st.caption(f"{len(hist)} odczytow.")

    with tab_ord:
        st.caption("Najlepsza oferta sprzedazy i najlepsze zlecenie kupna z ostatniego odczytu w kazdym miescie.")
        o = latest[(latest.item == name) & (latest.tier == tier) & (latest.enchantment == ench) &
                   (latest.quality == quality)]
        o = o.assign(typ=o.order_type.map({"sell": "Sprzedaz", "buy": "Kupno"}))
        st.dataframe(o[["city", "typ", "unit_price", "quantity", "captured_at"]].rename(columns={
            "city": "Miasto", "typ": "Typ", "unit_price": "Cena", "quantity": "Ilosc",
            "captured_at": "Odczyt"}), hide_index=True,
            column_config={"Odczyt": st.column_config.DatetimeColumn(format="DD.MM HH:mm:ss")})

    with tab_del:
        st.caption("Kasuje odczyty z bazy, zeby przedmiot mozna bylo zgrac od nowa (np. blednie odczytana nazwa).")
        qlabel = quality or "(brak)"
        scopes = {
            f"Ten wariant (T{tier}.{ench}, {qlabel}) w jednym miescie": ("one", ench, quality),
            f"Ten wariant (T{tier}.{ench}, {qlabel}) we wszystkich miastach": ("var", ench, quality),
            f"Cale T{tier}.{ench} (wszystkie jakosci, wszystkie miasta)": ("ench", ench, None),
            f"CALY przedmiot \"{name}\" T{tier} (wszystko)": ("all", None, None),
        }
        scope = st.radio("Co usunac?", list(scopes), key=f"dscope_{base}_{tier}_{ench}_{quality}")
        kind, d_ench, d_qual = scopes[scope]
        d_city = None
        if kind == "one":
            d_city = st.selectbox("Miasto", sorted(var.city.unique()), key=f"dcity_{base}_{tier}_{ench}_{quality}")
        sure = st.checkbox("Rozumiem, ze to usuwa dane na stale", key=f"dsure_{base}_{tier}_{ench}_{quality}_{kind}")
        if st.button("Usun z bazy", type="primary", disabled=not sure,
                     key=f"dbtn_{base}_{tier}_{ench}_{quality}_{kind}"):
            with db.connect() as conn:
                n = db.delete_prices(conn, name, int(tier), d_ench, d_qual, d_city)
            st.cache_data.clear()
            st.session_state["flash"] = f"Usunieto {n} odczytow: {name} T{tier}."
            st.rerun()


# ---------------------------------------------------------------- lista przedmiotow

def price_chips(group: pd.DataFrame) -> str:
    """Plakietki z najnizsza cena sprzedazy w kazdym miescie (miasto bez danych = brak plakietki)."""
    best = group.dropna(subset=["sprzedaz_min"]).sort_values("sprzedaz_min").groupby("city").first()
    html = []
    for city in [c for c in CITY_STYLE if c in best.index] + [c for c in best.index if c not in CITY_STYLE]:
        bg, fg = CITY_STYLE.get(city, ("#555555", "#ffffff"))
        r = best.loc[city]
        tip = f"{city} · {r['odczyt']:%d.%m %H:%M}"
        html.append(f'<span title="{tip}" style="background:{bg};color:{fg};padding:2px 8px;border-radius:4px;'
                    f'font-weight:600;font-size:0.85rem;margin:0 4px 4px 0;display:inline-block;'
                    f'border:1px solid #555">{fmt(r["sprzedaz_min"])}</span>')
    return "".join(html) or '<span style="opacity:.5">brak cen sprzedazy</span>'


def card_html(base: str, typ: str, group: pd.DataFrame, icons: dict, tier_filter: list) -> str:
    tiers = sorted(group.tier.unique())
    shown = tiers[0]                                  # ceny na kafelku: najnizszy tier (albo wybrany filtrem)
    sel = group[group.tier == shown]
    name = sel["item"].value_counts().index[0]
    icon = icons.get((name, int(shown)))
    if icon:
        img = (f'<img src="data:image/png;base64,{base64.b64encode(icon).decode()}" '
               f'style="width:64px;height:64px;border-radius:6px;flex:none">')
    else:
        img = '<div style="width:64px;height:64px;border-radius:6px;background:#333;flex:none"></div>'
    span = f"T{tiers[0]}–T{tiers[-1]}" if len(tiers) > 1 else f"T{tiers[0]}"
    label = f"ceny T{shown}" if len(tiers) > 1 else ""
    return (f'<div style="display:flex;gap:12px;align-items:flex-start">{img}<div style="min-width:0">'
            f'<div style="font-weight:600;font-size:1.02rem;line-height:1.25">{base}</div>'
            f'<div style="opacity:.6;font-size:.82rem;margin:2px 0 6px">{span} · {typ or "Inne"}'
            f'{" · " + label if label else ""}</div>{price_chips(sel)}</div></div>')


def list_page(summary: pd.DataFrame):
    st.title("📈 Albion Market Tracker")

    with st.sidebar:
        st.header("Filtry")
        query = st.text_input("Szukaj przedmiotu", placeholder="np. helm, runa, deski")
        present = [t for t in ALL_TYPES if t in set(summary.item_type)] + \
                  sorted(t for t in set(summary.item_type) if t not in ALL_TYPES and t)
        item_type = st.selectbox("Typ przedmiotu", ["Wszystkie"] + present)
        all_cities = sorted(summary.city.unique())
        cities = st.multiselect("Miasta", all_cities, default=all_cities)
        quality = st.selectbox("Jakosc (ceny na kafelkach)", ["Dowolna"] + quality_sort(
            [q for q in summary.quality.unique() if q]))
        if st.button("🔄 Odswiez"):
            st.cache_data.clear()
            st.rerun()

    view = summary[summary.city.isin(cities)]
    if query:
        view = view[view.base.str.contains(query, case=False, na=False) | view.item.str.contains(query, case=False, na=False)]
    if item_type != "Wszystkie":
        view = view[view.item_type == item_type]
    if quality != "Dowolna":
        view = view[view.quality == quality]

    tab_items, tab_log = st.tabs(["Przedmioty", "Aktualne ceny (log odczytow)"])

    with tab_items:
        f1, f2 = st.columns(2)
        with f1:
            tiers = st.pills("Tier", sorted(view.tier.unique()), selection_mode="multi",
                             format_func=lambda t: f"T{t}") or []
        with f2:
            enchs = st.pills("Zaklecie", sorted(view.enchantment.unique()), selection_mode="multi",
                             format_func=lambda e: f".{e}") or []
        if tiers:
            view = view[view.tier.isin(tiers)]
        if enchs:
            view = view[view.enchantment.isin(enchs)]

        groups = [(k, g) for k, g in view.groupby(["item_type", "base"], sort=True)]
        st.caption(f"{len(groups)} przedmiotow · kolorowe plakietki = najnizsza cena sprzedazy w miescie "
                   "(zielony Lymhurst, pomaranczowy Bridgewatch, fioletowy Thetford, niebieski Martlock, "
                   "bialy Fort Sterling, czarny Caerleon). Kliknij kafelek, zeby otworzyc przedmiot.")
        if not groups:
            st.info("Brak przedmiotow dla tych filtrow.")
        else:
            pages = max(1, -(-len(groups) // PER_PAGE))
            page = st.number_input("Strona", 1, pages, 1) if pages > 1 else 1
            icons = load_icons()
            chunk = groups[(page - 1) * PER_PAGE: page * PER_PAGE]
            for i in range(0, len(chunk), 3):
                cols = st.columns(3)
                for j, (col, ((typ, base), g)) in enumerate(zip(cols, chunk[i:i + 3])):
                    with col, st.container(key=f"card_{(page - 1) * PER_PAGE + i + j}"):
                        st.markdown(card_html(base, typ, g, icons, tiers), unsafe_allow_html=True)
                        if st.button(" ", key=f"open_{(page - 1) * PER_PAGE + i + j}"):
                            st.session_state["sel"] = base
                            st.rerun()

    with tab_log:
        c1, c2, c3 = st.columns(3)
        c1.metric("Pozycji (przedmiot × miasto)", len(view))
        c2.metric("Przedmiotow", view.groupby(["item", "tier"]).ngroups)
        c3.metric("Ostatni odczyt", f"{view.odczyt.max():%d.%m %H:%M:%S}" if len(view) else "-")
        log = view.sort_values("odczyt", ascending=False)[
            ["odczyt", "przedmiot", "city", "sprzedaz_min", "kupno_max", "rozpietosc"]].rename(columns={
            "odczyt": "Odczyt", "przedmiot": "Przedmiot", "city": "Miasto", "sprzedaz_min": "Sprzedaz min",
            "kupno_max": "Kupno max", "rozpietosc": "Rozpietosc"})
        st.dataframe(log, hide_index=True, column_config={
            "Sprzedaz min": st.column_config.NumberColumn(format="%d"),
            "Kupno max": st.column_config.NumberColumn(format="%d"),
            "Rozpietosc": st.column_config.NumberColumn(format="%d"),
            "Odczyt": st.column_config.DatetimeColumn(format="DD.MM HH:mm:ss"),
        })


# ---------------------------------------------------------------- auto-odswiezanie

REFRESH_SECONDS = 5


def data_version():
    """Tani odcisk stanu bazy (ostatni odczyt + liczba wierszy): zmienia sie, gdy tracker zapisze nowe ceny."""
    with db.connect() as conn:
        return tuple(conn.execute("SELECT max(captured_at), count(*) FROM market_prices").fetchone())


@st.fragment(run_every=REFRESH_SECONDS)
def autorefresh():
    """Co kilka sekund sprawdza, czy w bazie sa nowe dane; jesli tak, przeladowuje strone sama."""
    try:
        ver = data_version()
    except Exception:
        return
    if st.session_state.get("_seen") not in (None, ver):
        st.session_state["_seen"] = ver
        st.cache_data.clear()
        st.rerun()
    st.session_state["_seen"] = ver


# ---------------------------------------------------------------- start

try:
    ver_now = data_version()   # przed odczytem danych, zeby nie przegapic zapisu w trakcie
except Exception:
    ver_now = None
try:
    latest = load_latest()
except Exception as e:  # baza nie dziala
    st.error(f"Nie moge polaczyc sie z baza: {e}\n\nUruchom `docker compose up -d` i odswiez strone.")
    st.stop()

if latest.empty:
    st.info("Baza jest jeszcze pusta. Uruchom `python main.py run` i otworz przedmioty na rynku w grze.")
    st.stop()

with st.sidebar:
    auto = st.toggle("Odswiezaj automatycznie", value=True,
                     help=f"Co {REFRESH_SECONDS} s sprawdza, czy tracker zapisal nowe ceny, i przeladowuje strone.")
if auto:
    st.session_state["_seen"] = ver_now
    autorefresh()

if st.session_state.get("flash"):
    st.success(st.session_state.pop("flash"))

summary = summarize(latest)
selected = st.session_state.get("sel")
if selected:
    item_page(latest, summary, selected)
else:
    list_page(summary)
