from flask import Flask, render_template, request, jsonify, Response
import requests
from bs4 import BeautifulSoup
import urllib.parse
import re
import threading

app = Flask(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

LIBGEN_BASE = "https://libgen.li"


LANG_NAMES = {
    "fr": ["french", "français", "francais"],
    "en": ["english"],
}


def search_libgen(title, author, lang):
    """Search libgen.li for EPUB books."""
    results = []
    try:
        query = f"{title} {author}".strip()

        # Fetch without lang filter (libgen's filter is unreliable)
        url = (
            f"{LIBGEN_BASE}/index.php"
            f"?req={urllib.parse.quote(query)}"
            f"&res=50&ext=epub"
        )

        session = requests.Session()
        resp = session.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")
        table = soup.find("table", class_="table-striped")
        if not table:
            return results

        rows = table.find_all("tr")[1:]  # skip header

        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 8:
                continue

            # --- Title ---
            title_links = cells[0].find_all("a", href=True)
            book_title = ""
            edition_id = None
            for l in title_links:
                href = l.get("href", "")
                txt = l.get_text(strip=True)
                if href.startswith("edition.php") and txt and not re.match(r"^[\d;: -]+$", txt):
                    book_title = txt
                    m = re.search(r"id=(\d+)", href)
                    if m:
                        edition_id = m.group(1)
                    break
            if not book_title:
                # fallback: first non-empty edition link text
                for l in title_links:
                    if l.get("href", "").startswith("edition.php") and l.get_text(strip=True):
                        book_title = l.get_text(strip=True)
                        break
            if not book_title:
                book_title = cells[0].get_text(" ", strip=True)[:80]

            # --- Author ---
            book_author = cells[1].get_text(strip=True)[:80]

            # --- Publisher ---
            publisher = cells[2].get_text(strip=True)[:50] if len(cells) > 2 else ""

            # --- Year ---
            year = cells[3].get_text(strip=True) if len(cells) > 3 else ""

            # --- Language ---
            language = cells[4].get_text(strip=True) if len(cells) > 4 else ""

            # --- Size ---
            size = cells[6].get_text(strip=True) if len(cells) > 6 else ""

            # --- Extension ---
            ext = cells[7].get_text(strip=True) if len(cells) > 7 else ""
            if ext.lower() != "epub":
                continue

            # --- MD5 from ads.php link ---
            md5 = ""
            if len(cells) > 8:
                ads_link = cells[8].find("a", href=re.compile(r"ads\.php"))
                if ads_link:
                    m = re.search(r"md5=([a-fA-F0-9]+)", ads_link["href"])
                    if m:
                        md5 = m.group(1)

            if not md5:
                continue

            # --- Cover image ---
            cover_img = cells[0].find("img")
            cover = cover_img["src"] if cover_img and cover_img.get("src") else ""
            if cover and not cover.startswith("http"):
                cover = LIBGEN_BASE + "/" + cover.lstrip("/")

            results.append({
                "source": "Libgen",
                "title": book_title[:100],
                "author": book_author,
                "publisher": publisher,
                "year": year,
                "language": language,
                "size": size,
                "ext": "EPUB",
                "md5": md5,
                "cover": cover,
                "edition_id": edition_id,
            })

    except Exception as e:
        print(f"[Libgen] Error: {e}")

    # Deduplicate by MD5
    seen_md5 = set()
    deduped = []
    for r in results:
        if r["md5"] not in seen_md5:
            seen_md5.add(r["md5"])
            deduped.append(r)

    # Sort: preferred language first, then others
    preferred = LANG_NAMES.get(lang, [])
    deduped.sort(
        key=lambda r: 0 if any(p in r["language"].lower() for p in preferred) else 1
    )

    return deduped


def get_download_link(md5):
    """Get direct download URL from libgen.li ads page."""
    try:
        url = f"{LIBGEN_BASE}/ads.php?md5={md5}"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(resp.text, "lxml")
        get_link = soup.find("a", href=re.compile(r"get\.php"))
        if get_link:
            href = get_link["href"]
            if not href.startswith("http"):
                href = LIBGEN_BASE + "/" + href.lstrip("/")
            return href
    except Exception as e:
        print(f"[get_download_link] Error: {e}")
    return None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/search")
def search():
    title = request.args.get("title", "").strip()
    author = request.args.get("author", "").strip()
    lang = request.args.get("lang", "fr")

    if not title and not author:
        return jsonify({"error": "Veuillez saisir un titre ou un auteur"}), 400

    results = search_libgen(title, author, lang)
    return jsonify({"results": results, "count": len(results)})


@app.route("/api/download")
def get_download():
    md5 = request.args.get("md5", "").strip()
    if not md5 or not re.match(r"^[a-fA-F0-9]{32}$", md5):
        return jsonify({"error": "MD5 invalide"}), 400

    link = get_download_link(md5)
    if link:
        return jsonify({"url": link})
    return jsonify({"error": "Lien introuvable"}), 404


@app.route("/api/proxy")
def proxy_download():
    """Proxy a download through the server so iPad gets the file directly."""
    url = request.args.get("url", "")
    if not url or not url.startswith("https://libgen.li/"):
        return "URL non autorisée", 403

    try:
        r = requests.get(url, headers=HEADERS, stream=True, timeout=30)
        content_type = r.headers.get("Content-Type", "application/epub+zip")
        content_disp = r.headers.get(
            "Content-Disposition", "attachment; filename=book.epub"
        )

        def generate():
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk

        return Response(
            generate(),
            headers={
                "Content-Type": content_type,
                "Content-Disposition": content_disp,
            },
        )
    except Exception as e:
        return f"Erreur: {e}", 500


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
