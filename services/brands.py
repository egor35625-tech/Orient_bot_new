"""Бренды: нормализация, словарь популярных (для автозаполнения у продавца) и ключ для фильтра."""
import re

KNOWN_BRANDS = [
    "Nike", "Adidas", "Puma", "Reebok", "New Balance", "Converse", "Vans", "Asics", "Fila", "Under Armour",
    "The North Face", "Columbia", "Patagonia", "Arc'teryx", "Stone Island", "C.P. Company", "Carhartt",
    "Dickies", "Stussy", "Supreme", "Palace", "Off-White", "Fear of God", "Essentials", "Corteiz",
    "Gucci", "Prada", "Louis Vuitton", "Balenciaga", "Dior", "Chanel", "Versace", "Burberry", "Fendi",
    "Givenchy", "Saint Laurent", "Valentino", "Dolce & Gabbana", "Armani", "Emporio Armani", "Moncler",
    "Canada Goose", "Napapijri", "Ralph Lauren", "Polo Ralph Lauren", "Tommy Hilfiger", "Calvin Klein",
    "Lacoste", "Hugo Boss", "Levi's", "Wrangler", "Lee", "Diesel", "Guess", "Pepe Jeans", "Superdry",
    "Zara", "H&M", "Bershka", "Pull&Bear", "Stradivarius", "Massimo Dutti", "Mango", "Uniqlo", "Reserved",
    "Gap", "Befree", "Colin's", "LC Waikiki", "DeFacto", "Koton", "Oodji", "Sela", "Gloria Jeans",
    "Bosco", "Bosco Sport", "Zasport", "Marmot", "Salomon", "Merrell", "Timberland", "Dr. Martens",
    "UGG", "Crocs", "Birkenstock", "Skechers", "Clarks", "Geox", "Ecco", "Jordan", "Yeezy", "Champion",
    "Kappa", "Umbro", "Lotto", "Hummel", "Jack & Jones", "Only", "Vero Moda", "Tom Tailor", "Esprit",
    "Massimo", "Stefano Ricci", "Brioni", "Kiton", "Zegna", "Brunello Cucinelli", "Loro Piana",
    "Hermes", "Bottega Veneta", "Celine", "Loewe", "Maison Margiela", "Acne Studios", "Maison Kitsune",
    "A.P.C.", "Ami", "Sandro", "Maje", "Zadig & Voltaire", "Tory Burch", "Michael Kors", "Coach",
    "Kate Spade", "Furla", "Longchamp", "Samsonite", "Herschel", "Fjallraven", "Eastpak", "Kanken",
    "Orient",
]


def brand_key(name: str | None) -> str | None:
    """Ключ для сравнения: нижний регистр, без пробелов по краям и точек/дефисов-«шума»."""
    if not name:
        return None
    s = re.sub(r"\s+", " ", name).strip().casefold()
    s = re.sub(r"[.'’`]", "", s)          # C.P. Company == CP Company, Levi's == Levis
    s = re.sub(r"\s*&\s*", " & ", s)
    s = s.replace("-", " ")[:60]
    return s or None


_KNOWN_BY_KEY = {brand_key(b): b for b in KNOWN_BRANDS}


def clean_brand(raw: str | None) -> str | None:
    """Пусто -> None. Слишком длинное/странное -> ValueError."""
    s = re.sub(r"\s+", " ", (raw or "")).strip()
    if not s:
        return None
    if len(s) > 40:
        raise ValueError("Бренд слишком длинный (максимум 40 символов)")
    if not re.search(r"[A-Za-zА-Яа-яЁё0-9]", s):
        raise ValueError("Некорректный бренд")
    return s


def canonical_brand(name: str, existing: dict[str, str] | None = None) -> str:
    """Красивое написание: из словаря популярных, затем из уже встречавшихся в базе, иначе — как ввёл продавец
    (весь нижний/ВЕРХНИЙ регистр превращается в Title Case)."""
    key = brand_key(name)
    if key in _KNOWN_BY_KEY:
        return _KNOWN_BY_KEY[key]
    if existing and key in existing:
        return existing[key]
    if name.islower() or (name.isupper() and len(name) > 3):
        return " ".join(w[:1].upper() + w[1:] for w in name.split(" "))
    return name


def suggest_from_known(q: str, limit: int = 8) -> list[str]:
    """Подсказки из словаря: сначала начинающиеся с запроса (или слова в названии), затем содержащие."""
    k = brand_key(q)
    if not k:
        return []
    starts, contains = [], []
    for b in KNOWN_BRANDS:
        bk = brand_key(b)
        if bk.startswith(k) or any(w.startswith(k) for w in bk.split(" ")):
            starts.append(b)
        elif k in bk:
            contains.append(b)
    return (starts + contains)[:limit]
