from flask import Flask, render_template, request, jsonify, Response
import requests
from bs4 import BeautifulSoup
import urllib.parse
import re
import os
import threading
import time
import subprocess

app = Flask(__name__)

# ── Source URLs — update here if a domain changes ────────────────────────────
ZLIB_BASE = "https://z-lib.cv"   # Change this if z-lib moves to a new domain
LIBGEN_BASE = "https://libgen.li"
# ─────────────────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

TOR_PROXIES = {
    "http":  "socks5h://127.0.0.1:9050",
    "https": "socks5h://127.0.0.1:9050",
}

_tor_ready = False

def _start_tor():
    global _tor_ready
    try:
        subprocess.Popen(["tor"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("[Tor] Process started, waiting for circuits...")
    except FileNotFoundError:
        print("[Tor] tor binary not found")
        return
    for _ in range(40):
        time.sleep(3)
        try:
            r = requests.get("https://check.torproject.org/api/ip",
                             proxies=TOR_PROXIES, timeout=5)
            if r.ok:
                print("[Tor] Ready —", r.json())
                _tor_ready = True
                return
        except Exception:
            pass
    print("[Tor] Failed to connect")

threading.Thread(target=_start_tor, daemon=True).start()


def zlib_session():
    """Return requests.Session with z-lib session cookie if configured."""
    s = requests.Session()
    s.headers.update(HEADERS)
    cookie = os.environ.get("ZLIB_SESSION", "")
    if cookie:
        domain = ZLIB_BASE.replace("https://", "").replace("http://", "")
        s.cookies.set("z_lib_session", cookie, domain=domain)
    return s

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
        r = zlib_session().get(url, timeout=15)
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


def find_epub_url_tor(title, author):
    """Search libgen.li via Tor and return a direct get.php download URL."""
    if not _tor_ready:
        return None, None
    q = urllib.parse.quote(f"{title} {author}".strip())
    url = f"{LIBGEN_BASE}/index.php?req={q}&res=10&ext=epub"
    try:
        r = requests.get(url, headers=HEADERS, proxies=TOR_PROXIES, timeout=25)
        soup = BeautifulSoup(r.text, "lxml")

        # Extract title from table
        book_title = title
        for row in soup.select("table.table-striped tr"):
            cells = row.select("td")
            if len(cells) >= 3:
                t = cells[2].get_text(strip=True)
                if t:
                    book_title = t[:100]
                    break

        # Try direct get.php link first
        for a in soup.select("a[href*='get.php']"):
            href = a["href"]
            if not href.startswith("http"):
                href = LIBGEN_BASE + "/" + href.lstrip("/")
            return href, book_title

        # Fallback: go through ads.php to find the real link
        for a in soup.select("a[href*='ads.php?md5']"):
            ads_href = a["href"]
            if not ads_href.startswith("http"):
                ads_href = LIBGEN_BASE + "/" + ads_href.lstrip("/")
            try:
                r2 = requests.get(ads_href, headers=HEADERS, proxies=TOR_PROXIES, timeout=20)
                soup2 = BeautifulSoup(r2.text, "lxml")
                for a2 in soup2.select("a[href*='get.php']"):
                    href2 = a2["href"]
                    if not href2.startswith("http"):
                        href2 = LIBGEN_BASE + "/" + href2.lstrip("/")
                    return href2, book_title
            except Exception as e2:
                print(f"[libgen ads] {e2}")
            break  # Only try first ads link

    except Exception as e:
        print(f"[libgen tor] {e}")
    return None, None


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
    title = request.args.get("title", "").strip()
    author = request.args.get("author", "").strip()
    if not title and not author:
        return jsonify({"error": "Titre manquant"}), 400

    if not _tor_ready:
        return jsonify({"error": "tor_not_ready"}), 503

    epub_url, book_title = find_epub_url_tor(title, author)
    if not epub_url:
        return jsonify({"error": "Introuvable sur Libgen"}), 404

    return jsonify({"url": epub_url, "title": book_title})


@app.route("/api/proxy")
def proxy_download():
    """Proxy an EPUB download so the iPad gets the file directly."""
    url = request.args.get("url", "")
    zlib_host = ZLIB_BASE.replace("https://", "").replace("http://", "")
    allowed = [zlib_host, "annas-archive.gs", "ipfs.io", "cloudflare-ipfs.com", "libgen"]
    if not url or not any(a in url for a in allowed):
        return "URL non autorisée", 403

    try:
        proxies = TOR_PROXIES if "libgen" in url else None
        r = zlib_session().get(url, stream=True, timeout=60, proxies=proxies)
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


@app.route("/api/tor-test")
def tor_test():
    out = [f"tor_ready: {_tor_ready}"]
    # Test Tor connectivity
    try:
        r = requests.get("https://check.torproject.org/api/ip", proxies=TOR_PROXIES, timeout=10)
        out.append(f"Tor IP check: {r.status_code} {r.text[:100]}")
    except Exception as e:
        out.append(f"Tor IP check error: {e}")
    # Test libgen via Tor
    try:
        r = requests.get(f"{LIBGEN_BASE}/index.php?req=dune&res=5&ext=epub",
                         headers=HEADERS, proxies=TOR_PROXIES, timeout=20)
        out.append(f"libgen via Tor: {r.status_code} size={len(r.text)}")
        soup = BeautifulSoup(r.text, "lxml")
        rows = soup.select("table.c tr")
        out.append(f"table rows found: {len(rows)}")
        # Test exact URL used by find_epub_url_tor
        q2 = urllib.parse.quote("dune frank herbert")
        url2 = f"{LIBGEN_BASE}/index.php?req={q2}&res=10&ext=epub&filesuns=all"
        r2 = requests.get(url2, headers=HEADERS, proxies=TOR_PROXIES, timeout=25)
        soup2 = BeautifulSoup(r2.text, "lxml")
        out.append(f"\nExact search URL: {url2}")
        out.append(f"Status: {r2.status_code} size={len(r2.text)}")
        get_links = soup2.select("a[href*='get.php']")
        out.append(f"get.php links: {len(get_links)}")
        for a in get_links[:3]:
            out.append(f"  href={a.get('href','')[:80]}")
        # Also try broader selector
        all_md5 = soup2.select("a[href*='md5']")
        out.append(f"any md5 links: {len(all_md5)}")
        for a in all_md5[:3]:
            out.append(f"  href={a.get('href','')[:80]}")
    except Exception as e:
        out.append(f"libgen via Tor error: {e}")
    return Response("\n".join(out), content_type="text/plain")


@app.route("/api/debug")
def debug_html():
    """Debug: show all links on a z-lib book page."""
    r = zlib_session().get(f"{ZLIB_BASE}/book/dune-674762", timeout=15)
    soup = BeautifulSoup(r.text, "lxml")
    output = [f"HTML size: {len(r.text)}, URL: {r.url}\n", "All links:\n"]
    for a in soup.find_all("a", href=True)[:40]:
        output.append(f"  text={a.get_text(strip=True)[:40]!r}  href={a['href'][:80]}")
    return Response("\n".join(output), content_type="text/plain")


@app.route("/api/test")
def test_sources():
    """Test which sources are reachable from this server's IP."""
    results = {}
    tests = {
        "library_lol": "https://library.lol/main/2F2DBA2A3AE42F8A90792C4B85B4B4D5",
        "libgen_st": "https://libgen.st/search.php?req=dune&res=5",
        "libgen_gs": "https://libgen.gs/index.php?req=dune&res=5",
        "libgen_fun": "https://libgen.fun/search.php?req=dune&res=5",
        "libgen_li_json": "https://libgen.li/json.php?ids=1&fields=id,title,md5,extension",
        "annas_archive_gs": "https://annas-archive.gs/search?q=dune&ext=epub",
        "annas_archive_org": "https://annas-archive.org/search?q=dune&ext=epub",
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
