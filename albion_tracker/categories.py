"""Typ przedmiotu wyznaczany z nazwy (gra nie pokazuje typu w okienku zlecen).

Reguly sprawdzane po kolei, wygrywa pierwsza pasujaca. Slowa kluczowe pisz BEZ polskich znakow
i malymi literami (nazwa jest normalizowana). Czego nie rozpozna, dostaje typ 'Inne' - wystarczy
dopisac slowo do odpowiedniej grupy ponizej i uruchomic `python main.py init-db`, zeby
przeliczyc typy wszystkich przedmiotow w bazie.
"""
from .parser import normalize

DEFAULT_TYPE = "Inne"

RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Runy, dusze, relikty", ("runa", "dusza", "relikt", "tomisko", "szkic")),
    ("Wierzchowce", ("oswojony", "wierzchowiec", "kon ", "wol ", "rumak", "skorka dla", "siodlo")),
    ("Buty", ("buty", "sandaly", "obuwie")),
    ("Hełmy", ("helm", "hetm", "kaptur", "kask", "czapka", "maska")),
    ("Zbroje", ("zbroja", "kurtka", "szata", "koszula", "tunika", "kolczuga", "kamizelka")),
    ("Peleryny", ("peleryna", "plaszcz")),
    ("Torby", ("torba", "plecak", "sakwa")),
    ("Tarcze i dodatki", ("tarcza", "ksiega", "latarnia", "totem", "pochodnia", "zrodlo", "oko ", "czasz")),
    ("Broń", ("miecz", "topor", "mlot", "luk", "kusza", "kostur", "rozdzka", "wlocznia", "sztylet",
              "bulawa", "maczuga", "kosa", "halabarda", "szabla", "szpon", "rekawice", "glewia",
              "pika", "berlo", "laska", "rapier", "kilof", "klinga", "ostrze", "lanca")),
    ("Surowce", ("deski", "sztaby", "plotno", "skora", "skorki", "skrawki", "bloki", "kamien",
                 "drewno", "ruda", "wlokno", "belki", "klody", "wlokna", "sztaba")),
    ("Jedzenie i mikstury", ("mikstura", "omlet", "zupa", "salatka", "piecen", "kanapka", "ragu",
                             "tort", "napoj", "eliksir", "ciasto", "gulasz", "kielbasa")),
]


def classify(name: str) -> str:
    n = f" {normalize(name)} "
    for item_type, words in RULES:
        if any(w in n for w in words):
            return item_type
    return DEFAULT_TYPE


ALL_TYPES = [t for t, _ in RULES] + [DEFAULT_TYPE]


# Slowa tierowe w nazwach przedmiotow (Polish): 'Helm zolnierza adepta' = Helm zolnierza, tier 4.
TIER_WORDS = {"poczatkujacego": 1, "nowicjusza": 2, "czeladnika": 3, "adepta": 4,
              "znawcy": 5, "mistrza": 6, "wszechmistrza": 7, "starszego": 8}


def split_tier_word(name: str) -> tuple[str, int | None]:
    """'Buty najemnika adepta' -> ('Buty najemnika', 4). Bez slowa tierowego: (nazwa, None)."""
    from difflib import SequenceMatcher
    words = name.split()
    if len(words) < 2:
        return name, None
    last = normalize(words[-1])
    best = max(TIER_WORDS, key=lambda w: SequenceMatcher(None, last, w).ratio())
    if SequenceMatcher(None, last, best).ratio() >= 0.85:
        return " ".join(words[:-1]), TIER_WORDS[best]
    return name, None
