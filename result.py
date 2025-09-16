# result.py
import re
import sys
import json
import requests
from bs4 import BeautifulSoup
from typing import Optional, Dict

# --- HTTP ---
def fetch_html(url: str) -> bytes:
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
    }
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    return resp.content

# --- Parsing helpers ---
def text_of(el) -> str:
    return el.get_text(separator=" ", strip=True) if el else ""

def first_text(soup: BeautifulSoup, selectors: list[str]) -> str:
    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            t = text_of(el)
            if t:
                return t
    return ""

def extract_property_csv_id(url: str) -> Optional[str]:
    m = re.search(r"/rent/(\d+)/(\d+)", url)
    if m:
        return f"{m.group(1)}_{m.group(2)}"

    nums = re.findall(r"\d+", url)
    return "_".join(nums) if nums else None

def extract_postcode(soup: BeautifulSoup) -> Optional[str]:
    address_candidates = [
        '[itemprop="address"]',
        ".address", ".addr", ".p-address",
        ".detailAddress", "#address", ".l-property__address",
        ".c-detailAddress", ".p-detail__address",
    ]
    addr_text = first_text(soup, address_candidates)

    pattern = re.compile(r"\b(\d{3}-\d{4})\b")
    if addr_text:
        m = pattern.search(addr_text)
        if m:
            return m.group(1)

    page_text = soup.get_text(separator=" ", strip=True)
    m = pattern.search(page_text)
    return m.group(1) if m else None

def extract_address_text(soup: BeautifulSoup) -> str:
    candidates = [
        '[itemprop="address"]', ".address", ".addr", ".p-address",
        ".detailAddress", "#address", ".l-property__address",
        ".c-detailAddress", ".p-detail__address"
    ]
    t = first_text(soup, candidates)
    if t:
        return t

    page = soup.get_text("\n", strip=True)

    m = re.search(r"(\d{3}-\d{4}[^\n]{0,80})", page)
    if not m:
        return ""

    line = m.group(1)

    line = re.split(r"(TEL|Fax|FAX|電話)", line, maxsplit=1)[0].strip()
    return line

def split_japanese_address(addr: str) -> dict:
    addr_clean = re.sub(r"^\s*\d{3}-\d{4}\s*", "", addr)

    m_pref = re.match(r"(?P<pref>.+?[都道府県])", addr_clean)
    pref, rest = (None, addr_clean)
    if m_pref:
        pref = m_pref.group("pref")
        rest = addr_clean[m_pref.end():].strip()

    m_city = re.match(r"(?P<city>.+?(市|区|郡|町|村))", rest)
    city, rest2 = (None, rest)
    if m_city:
        city = m_city.group("city")
        rest2 = rest[m_city.end():].strip()

    chome = rest2 if rest2 else None

    district = None

    return {
        "prefecture": pref,
        "city": city,
        "district": district,
        "chome_banchi": chome
    }

def extract_address_parts(soup: BeautifulSoup) -> dict:
    addr = extract_address_text(soup)
    if not addr:
        return {"prefecture": None, "city": None, "district": None, "chome_banchi": None}
    return split_japanese_address(addr)

def get_side_info_map(soup: BeautifulSoup) -> dict:
    out = {}
    dl = soup.select_one("dl.rent_view_side_info")
    if not dl:
        return out
    cur_label = None
    for el in dl.find_all(["dt", "dd"]):
        if el.name == "dt":
            cur_label = el.get_text(strip=True)
        elif el.name == "dd" and cur_label:
            out[cur_label] = el.get_text(" ", strip=True)
            cur_label = None
    return out

def extract_building_name_jp(soup: BeautifulSoup) -> str | None:
    info = get_side_info_map(soup) if 'get_side_info_map' in globals() else {}
    name = info.get("物件名") or info.get("建物名") or info.get("マンション名")
    if name:
        return name.strip()

    h = soup.select_one("h1") or soup.select_one("h2") or soup.select_one(".rent_view_ttl")
    if h:
        txt = text_of(h)
        if txt:
            return txt

    if soup.title:
        t = soup.title.get_text(strip=True)
        t = re.split(r"[｜|\\|]", t)[0].strip()
        if t:
            return t

    return None

def normalize_building_type(jp: Optional[str]) -> Optional[str]:
    if not jp:
        return None
    jp = jp.strip()
    mapping = {
        "マンション": "apartment",
        "アパート": "apartment",
        "一戸建て": "house",
        "戸建": "house",
        "テラスハウス": "townhouse",
        "タウンハウス": "townhouse",
    }
    return mapping.get(jp, None)

def extract_year_from_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"(\d{4})年", text)
    return m.group(1) if m else None

# --- romanization helper (safe import) ---
try:
    from pykakasi import kakasi  # chuyển JP -> romaji
    _kk = kakasi()
    _kk.setMode("H", "a")  # Hiragana -> ascii
    _kk.setMode("K", "a")  # Katakana -> ascii
    _kk.setMode("J", "a")  # Kanji -> ascii
    _conv = _kk.getConverter()
except Exception:
    _conv = None

def to_english_name(text: str) -> str | None:
    if not text:
        return None
    t = text.strip()
    if re.search(r"[A-Za-z]", t):
        return t
    if _conv:
        romaji = _conv.do(t).replace("  ", " ").strip()
        return " ".join(w.capitalize() for w in romaji.split())
    return t

# --- Orchestrator ---
def parse_property(url: str, html: str) -> Dict[str, Optional[str]]:
    soup = BeautifulSoup(html, "lxml")

    info = get_side_info_map(soup)

    addr_text = info.get("所在地")
    if not addr_text:
        addr_text = extract_address_text(soup)

    addr_parts = split_japanese_address(addr_text) if addr_text else {}

    building_type_en = normalize_building_type(info.get("種別"))
    year_built = extract_year_from_text(info.get("築年月"))

    name_jp = extract_building_name_jp(soup)
    building_name_en = to_english_name(name_jp)

    data: Dict[str, Optional[str]] = {
        "link": url,
        "property_csv_id": extract_property_csv_id(url),
        "postcode": extract_postcode(soup),

        "prefecture": addr_parts.get("prefecture"),
        "city": addr_parts.get("city"),
        "district": addr_parts.get("district"),
        "chome_banchi": addr_parts.get("chome_banchi"),

        "building_type":  building_type_en,
        "year": year_built,
        "building_name_en": building_name_en,
    }
    return data

# --- CLI ---
def main():
    if len(sys.argv) < 2:
        print("Usage: python result.py <URL>")
        print("Example:\n  python result.py https://rent.tokyu-housing-lease.co.jp/rent/8034884/117024")
        sys.exit(1)

    url = sys.argv[1]
    try:
        html = fetch_html(url)
    except requests.RequestException as e:
        print(json.dumps({"error": f"request_failed: {e}"}, ensure_ascii=False))
        sys.exit(2)

    data = parse_property(url, html)

    print(json.dumps(data, ensure_ascii=False, indent=2))

    with open("out.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    with open("page.html", "wb") as f:
        f.write(html)
    print("Wrote out.json & page.html")

if __name__ == "__main__":
    main()
