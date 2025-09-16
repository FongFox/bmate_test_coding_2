import re
import sys
import json
from typing import Optional, Dict, Tuple
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# ==============================
# 1) HTTP: download HTML (use bytes)
# ==============================
def fetch_html(url: str) -> bytes:
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
    }

    res = requests.get(url, headers=headers, timeout=20)
    res.raise_for_status()

    return res.content

# ==============================
# 2) Helpers
# ==============================
def extract_property_csv_id(url: str) -> Optional[str]:
    m = re.search(r"/rent/(\d+)/(\d+)", url)

    if m:
        return f"{m.group(1)}_{m.group(2)}"
    nums = re.findall(r"\d+", url)

    return "_".join(nums) if nums else None

def extract_postcode(soup: BeautifulSoup) -> Optional[str]:
    candidates = [
        '[itemprop="address"]', ".address", ".addr", ".p-address",
        ".detailAddress", "#address", ".l-property__address",
        ".c-detailAddress", ".p-detail__address"
    ]

    for sel in candidates:
        el = soup.select_one(sel)
        if el:
            t = el.get_text(" ", strip=True)
            m = re.search(r"\b(\d{3}-\d{4})\b", t)
            if m:
                return m.group(1)

    page = soup.get_text(" ", strip=True)
    m = re.search(r"\b(\d{3}-\d{4})\b", page)
    return m.group(1) if m else None

def extract_address_text_simple(soup: BeautifulSoup) -> Optional[str]:
    page = soup.get_text("\n", strip=True)
    m = re.search(r"(\d{3}-\d{4}[^\n]{0,200})", page)
    if not m:
        return None
    line = m.group(1)

    line = re.split(r"(TEL|Fax|FAX|電話)", line, maxsplit=1)[0].strip()
    return line or None

def split_japanese_address_simple(addr: str) -> Dict[str, Optional[str]]:
    addr = re.sub(r"^\s*\d{3}-\d{4}\s*", "", addr)

    prefecture = None
    city = None
    rest = addr

    m_pref = re.match(r"(.+?[都道府県])", rest)
    if m_pref:
        prefecture = m_pref.group(1)
        rest = rest[m_pref.end():].strip()

    m_city = re.match(r"(.+?(市|区|郡|町|村))", rest)
    if m_city:
        city = m_city.group(1)
        rest = rest[m_city.end():].strip()

    chome_banchi = rest if rest else None
    district = None

    return {
        "prefecture": prefecture,
        "city": city,
        "district": district,
        "chome_banchi": chome_banchi,
    }

def get_side_info_map_simple(soup: BeautifulSoup) -> dict:
    out = {}
    dl = soup.select_one("dl.rent_view_side_info")
    if not dl:
        return out
    cur = None
    for el in dl.find_all(["dt", "dd"]):
        if el.name == "dt":
            cur = el.get_text(strip=True)
        elif el.name == "dd" and cur:
            out[cur] = el.get_text(" ", strip=True)
            cur = None
    return out

def extract_year_simple(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"(\d{4})年", text)
    return m.group(1) if m else None

def normalize_building_type_simple(jp: Optional[str]) -> Optional[str]:
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

# --- Romanize name (optional) ---
try:
    from pykakasi import kakasi
    _kk = kakasi()
    _kk.setMode("H", "a"); _kk.setMode("K", "a"); _kk.setMode("J", "a")
    _conv = _kk.getConverter()
except Exception:
    _conv = None

def to_english_name_simple(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    t = text.strip()
    if re.search(r"[A-Za-z]", t):
        return t
    if _conv:
        romaji = _conv.do(t).replace("  ", " ").strip()
        return " ".join(w.capitalize() for w in romaji.split())
    return t

def extract_building_name_jp_simple(soup: BeautifulSoup) -> Optional[str]:
    info = get_side_info_map_simple(soup)
    name = info.get("物件名") or info.get("建物名") or info.get("マンション名")
    if name:
        return name.strip()

    h = soup.select_one("h1") or soup.select_one("h2") or soup.select_one(".rent_view_ttl")
    if h:
        txt = h.get_text(" ", strip=True)
        if txt:
            return txt

    if soup.title:
        t = soup.title.get_text(strip=True)
        t = re.split(r"[｜|\\|]", t)[0].strip()
        if t:
            return t
    return None

def extract_map_coords_simple(soup: BeautifulSoup) -> Tuple[Optional[str], Optional[str]]:
    sc = soup.find("script", type="application/ld+json")
    if sc and sc.string:
        try:
            data = json.loads(sc.string)
            if isinstance(data, dict) and "geo" in data and isinstance(data["geo"], dict):
                lat = data["geo"].get("latitude")
                lng = data["geo"].get("longitude")
                if lat and lng:
                    return str(lat), str(lng)
        except Exception:
            pass
    return None, None

def extract_map_coords_basic(soup: BeautifulSoup):
    lat_el = soup.select_one(".latitude")
    lng_el = soup.select_one(".longitude")
    lat = lat_el.get_text(strip=True) if lat_el else None
    lng = lng_el.get_text(strip=True) if lng_el else None
    return lat, lng

def extract_stations_basic(soup: BeautifulSoup, limit: int = 5):
    result = []

    for dt in soup.select("dt"):
        if dt.get_text(strip=True) == "交通":
            dd = dt.find_next_sibling("dd")
            if not dd:
                break
            for li in dd.select("li"):
                t = li.get_text(" ", strip=True)

                m = re.search(r"(.+?)／(.+?駅)", t) or re.search(r"(.+?)/(.+?駅)", t)
                line = m.group(1).strip() if m else None
                station = m.group(2).strip() if m else None

                m2 = re.search(r"徒歩\s*(\d+)\s*分", t)
                walk = m2.group(1) if m2 else None
                result.append({"line": line, "station": station, "walk": walk})
                if len(result) >= limit:
                    break
            break
    return result

def extract_images_basic(soup: BeautifulSoup, base_url: str, limit: int = 8):
    urls = []
    for img in soup.select("img"):
        src = img.get("data-src") or img.get("src")
        if not src or src.startswith("data:"):
            continue
        abs_url = urljoin(base_url, src)
        if abs_url not in urls:
            urls.append(abs_url)
        if len(urls) >= limit:
            break
    return urls

# ==============================
# 3) Orchestrator
# ==============================
def parse_property(url: str, html: bytes) -> Dict[str, Optional[str]]:
    soup = BeautifulSoup(html, "lxml")

    info = get_side_info_map_simple(soup)

    addr_text = info.get("所在地") or extract_address_text_simple(soup)
    addr_parts = split_japanese_address_simple(addr_text) if addr_text else {
        "prefecture": None, "city": None, "district": None, "chome_banchi": None
    }

    building_type_en = normalize_building_type_simple(info.get("種別"))
    year_built = extract_year_simple(info.get("築年月"))

    name_jp = extract_building_name_jp_simple(soup)
    building_name_en = to_english_name_simple(name_jp)

    lat, lng = extract_map_coords_simple(soup)
    lat, lng = extract_map_coords_basic(soup)

    stations = extract_stations_basic(soup, limit=5)

    imgs = extract_images_basic(soup, url, limit=8)

    data: Dict[str, Optional[str]] = {
        "link": url,
        "property_csv_id": extract_property_csv_id(url),
        "postcode": extract_postcode(soup),

        "prefecture": addr_parts.get("prefecture"),
        "city": addr_parts.get("city"),
        "district": addr_parts.get("district"),
        "chome_banchi": addr_parts.get("chome_banchi"),

        "building_type": building_type_en,
        "year": year_built,

        "building_name_en": building_name_en,
        "building_name_ja": name_jp,
        "building_name_zh_CN": None,
        "building_name_zh_TW": None,

        "building_description_en": None,
        "building_description_ja": None,
        "building_description_zh_CN": None,
        "building_description_zh_TW": None,

        "map_lat": lat,
        "map_lng": lng,

        "numeric_guarantor_max": None,
        "discount": None,
        "create_date": None,
    }

    data.update({
        "map_lat": lat,
        "map_lng": lng,
    })

    for i in range(1, 6):
        src = stations[i - 1] if i - 1 < len(stations) else {}
        data[f"station_name_{i}"] = src.get("station")
        data[f"train_line_name_{i}"] = src.get("line")
        data[f"walk_{i}"] = src.get("walk")
        data[f"bus_{i}"] = None
        data[f"car_{i}"] = None
        data[f"cycle_{i}"] = None

    for i in range(1, 17):
        url_i = imgs[i - 1] if i - 1 < len(imgs) else None
        data[f"image_url_{i}"] = url_i
        data[f"image_category_{i}"] = None

    return data

# ==============================
# 4) CLI
# ==============================
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

    # In ra JSON + lưu file để xem Unicode chuẩn
    print(json.dumps(data, ensure_ascii=False, indent=2))
    with open("out.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    with open("page.html", "wb") as f:
        f.write(html)
    print("Wrote out.json & page.html")

if __name__ == "__main__":
    main()
