from flask import Flask, render_template, request, jsonify, Response
import requests
from bs4 import BeautifulSoup
import urllib.parse
import re
import os

app = Flask(__name__)

# ── Source URLs — update here if a domain changes ────────────────────────────
ZLIB_BASE = "https://z-lib.id"   # Change this if z-lib moves to a new domain
# ─────────────────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

LANG_NAMES = {
    "fr": ["french", "français", "francais"],
    "en": ["english"],
}


def search_zlib(title, author, lang):
    """Search z-lib.id for EPUB books."""
    results = []
    query = f"{title} {author}".strip()
    url = f"{ZLIB_BASE}/s?q={urllib.parse.quote(query)}&extension=epub"

    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "lxml")

        for item in soup.select(".resItemBox"):
            # Title & book URL
            title_el = item.select_one("h3[itemprop='name'] a, h3 a")
            book_title = title_el.get_text(strip=True) if title_el else ""
            book_link = item.select_one("a[href^='/book/']")
            book_url = ""
            if book_link:
                book_url = ZLIB_BASE + book_link["href"]

            # Author
            author_el = item.select_one(".authors, .itemAuthors, [itemprop='author']")
            book_author = author_el.get_text(strip=True)[:80] if author_el else ""

            # Language
            lang_text = item.get_text(" ", strip=True).lower()
            language = ""
            if "french" in lang_text or "français" in lang_text:
                language = "French"
            elif "english" in lang_text:
                language = "English"

            # Cover (lazy-loaded via data-src)
            cover_img = item.select_one("img.cover, img[data-src]")
            cover = ""
            if cover_img:
                cover = cover_img.get("data-src") or cover_img.get("src") or ""

            # Year
            year = ""
            year_el = item.select_one(".property_year .property_value")
            if year_el:
                year = year_el.get_text(strip=True)

            if not book_title:
                continue

            results.append({
                "source": "Z-Library",
                "title": book_title,
                "author": book_author,
                "year": year,
                "language": language,
                "size": "",
                "ext": "EPUB",
                "book_url": book_url,
                "cover": cover,
            })

    except Exception as e:
        print(f"[zlib search] Error: {e}")

    # Sort preferred language first
    preferred = LANG_NAMES.get(lang, [])
    results.sort(
        key=lambda r: 0 if any(p in r["language"].lower() for p in preferred) else 1
    )
    return results


def get_zlib_download(book_url):
    """Get the direct EPUB download link from a z-lib.id book page."""
    links = []
    try:
        r = requests.get(book_url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "lxml")

        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if "/dl/" in href or "download" in href.lower():
                if not href.startswith("http"):
                    href = ZLIB_BASE + href
                label = text[:60] or href[:60]
                if href not in [l["url"] for l in links]:
                    links.append({"label": label, "url": href})

    except Exception as e:
        print(f"[zlib download] Error: {e}")

    return links[:5]


# ── Flask routes ──────────────────────────────────────────────────────────────

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

    try:
        results = search_zlib(title, author, lang)
        return jsonify({"results": results, "count": len(results)})
    except Exception as e:
        print(f"[search] Error: {e}")
        return jsonify({"error": "Erreur de recherche", "results": [], "count": 0})


@app.route("/api/download")
def get_download():
    book_url = request.args.get("url", "").strip()
    if not book_url or ZLIB_BASE not in book_url:
        return jsonify({"error": "URL invalide"}), 400

    try:
        links = get_zlib_download(book_url)
        if links:
            return jsonify({"links": links})
        return jsonify({"error": "Aucun lien trouvé"}), 404
    except Exception as e:
        print(f"[download] Error: {e}")
        return jsonify({"error": "Erreur"}), 500


@app.route("/api/proxy")
def proxy_download():
    """Proxy an EPUB download so the iPad gets the file directly."""
    url = request.args.get("url", "")
    zlib_host = ZLIB_BASE.replace("https://", "").replace("http://", "")
    allowed = [zlib_host, "annas-archive.gs", "ipfs.io", "cloudflare-ipfs.com", "libgen"]
    if not url or not any(a in url for a in allowed):
        return "URL non autorisée", 403

    try:
        r = requests.get(url, headers=HEADERS, stream=True, timeout=60)
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


@app.route("/api/debug")
def debug_html():
    """Debug: show all links on a z-lib book page."""
    r = requests.get(f"{ZLIB_BASE}/book/dune-674762", headers=HEADERS, timeout=15)
    soup = BeautifulSoup(r.text, "lxml")
    output = [f"HTML size: {len(r.text)}, URL: {r.url}\n", "All hrefs containing 'dl', 'download', 'get':\n"]
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(x in href.lower() for x in ["/dl/", "download", "/get", "epub"]):
            output.append(f"  text={a.get_text(strip=True)[:50]!r}  href={href}")
    return Response("\n".join(output), content_type="text/plain")


@app.route("/api/test")
def test_sources():
    """Test which sources are reachable from this server's IP."""
    results = {}
    tests = {
        "zlib_current": f"{ZLIB_BASE}/s?q=dune&extension=epub",
        "zlib_cv": "https://z-lib.cv/s?q=dune&extension=epub",
        "libgen_li": "https://libgen.li/index.php?req=dune&res=5",
        "libgen_rs": "https://libgen.rs/search.php?req=dune&res=5",
        "annas_archive": "https://annas-archive.gs/search?q=dune&ext=epub",
    }
    for name, url in tests.items():
        try:
            r = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True)
            results[name] = {
                "status": r.status_code,
                "size": len(r.content),
                "final_url": r.url,
            }
        except Exception as e:
            results[name] = {"error": str(e)[:120]}
    return jsonify(results)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
