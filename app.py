from flask import Flask, render_template, request, jsonify, Response
import requests
from bs4 import BeautifulSoup
import urllib.parse
import re
import os
import threading
import time

app = Flask(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

ZLIB_BASE = "https://z-lib.cv"
ZLIB_EMAIL = os.environ.get("ZLIB_EMAIL", "")
ZLIB_PASSWORD = os.environ.get("ZLIB_PASSWORD", "")

LANG_NAMES = {
    "fr": ["french", "français", "francais"],
    "en": ["english"],
}

# Session management
_session = None
_session_lock = threading.Lock()
_last_login = 0
SESSION_TTL = 3600  # re-login every hour


def get_session():
    """Return an authenticated session, re-logging in if needed."""
    global _session, _last_login

    with _session_lock:
        now = time.time()
        if _session is None or (now - _last_login) > SESSION_TTL:
            _session = _create_session()
            _last_login = now
        return _session


def _create_session():
    """Create a new requests Session, logging in if credentials are set."""
    session = requests.Session()
    session.headers.update(HEADERS)

    if not ZLIB_EMAIL or not ZLIB_PASSWORD:
        return session

    try:
        # Get CSRF token
        r = session.get(f"{ZLIB_BASE}/login", timeout=12)
        soup = BeautifulSoup(r.text, "lxml")
        token_input = soup.find("input", {"name": "_token"})
        if not token_input:
            print("[Login] Could not find CSRF token")
            return session
        token = token_input["value"]

        # Submit login form
        r2 = session.post(
            f"{ZLIB_BASE}/login",
            data={"_token": token, "email": ZLIB_EMAIL, "password": ZLIB_PASSWORD},
            headers={"Referer": f"{ZLIB_BASE}/login"},
            timeout=12,
            allow_redirects=True,
        )

        # Check if login worked by looking for profile indicators
        if "logout" in r2.text.lower() or "/profile" in r2.text.lower():
            print("[Login] Z-Library login successful")
        else:
            print("[Login] Z-Library login may have failed — check credentials")

    except Exception as e:
        print(f"[Login] Error: {e}")

    return session


def search_zlib(title, author, lang):
    """Search Z-Library for EPUB books."""
    results = []
    try:
        query = f"{title} {author}".strip()
        session = get_session()

        url = f"{ZLIB_BASE}/s?q={urllib.parse.quote(query)}&extension=epub"
        resp = session.get(url, timeout=15)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")
        items = soup.select(".resItemBox")

        for item in items:
            # Title + slug
            title_link = item.select_one("h3 a, .book-title a, [itemprop='name'] a")
            if not title_link:
                continue
            book_title = title_link.get_text(strip=True)
            book_slug = title_link.get("href", "").lstrip("/")  # e.g. "book/some-slug"

            # Book ID (for download URL)
            book_id = item.get("data-book_id", "")

            # Author
            author_links = item.select("a[href*='author=']")
            book_author = ", ".join(a.get_text(strip=True) for a in author_links) or ""

            # Year
            year_el = item.select_one(".property_year .property_value")
            year = year_el.get_text(strip=True) if year_el else ""

            # Language
            lang_el = item.select_one(".property_language .property_value")
            language = lang_el.get_text(strip=True) if lang_el else ""

            # Cover
            cover_img = item.select_one("img.cover, img.lazy")
            cover = ""
            if cover_img:
                cover = cover_img.get("data-src") or cover_img.get("src") or ""

            results.append({
                "source": "Z-Library",
                "title": book_title[:100],
                "author": book_author[:80],
                "year": year,
                "language": language,
                "size": "",
                "ext": "EPUB",
                "slug": book_slug,
                "book_id": book_id,
                "cover": cover,
                "detail_url": f"{ZLIB_BASE}/{book_slug}",
            })

    except Exception as e:
        print(f"[Z-Library search] Error: {e}")

    # Sort preferred language first
    preferred = LANG_NAMES.get(lang, [])
    results.sort(
        key=lambda r: 0 if any(p in r["language"].lower() for p in preferred) else 1
    )
    return results


def get_download_url(book_slug, book_id):
    """
    Get the direct EPUB download URL from Z-Library.
    Fetches the book page as a logged-in user and extracts the download href.
    """
    if not ZLIB_EMAIL or not ZLIB_PASSWORD:
        return None, "login_required"

    try:
        session = get_session()

        # Fetch the book page — when logged in, dlButton has the real href
        book_page_url = f"{ZLIB_BASE}/{book_slug}"
        r = session.get(book_page_url, timeout=12)

        # Redirected to login = session expired
        if "z-lib.cv/login" in r.url:
            global _last_login
            _last_login = 0  # force re-login on next call
            return None, "login_required"

        soup = BeautifulSoup(r.text, "lxml")

        # Find the download button — when authenticated href is the real download URL
        dl_btn = soup.select_one("a.dlButton, a.btn-download, a[class*='dlButton']")
        if dl_btn:
            href = dl_btn.get("href", "")
            # If still pointing to /login, session didn't authenticate
            if href == "/login" or not href:
                return None, "login_required"
            if not href.startswith("http"):
                href = ZLIB_BASE + href
            return href, "ok"

        return None, "not_found"

    except Exception as e:
        print(f"[Download URL] Error: {e}")
        return None, "error"


@app.route("/")
def index():
    logged_in = bool(ZLIB_EMAIL and ZLIB_PASSWORD)
    return render_template("index.html", logged_in=logged_in)


@app.route("/api/search")
def search():
    title = request.args.get("title", "").strip()
    author = request.args.get("author", "").strip()
    lang = request.args.get("lang", "fr")

    if not title and not author:
        return jsonify({"error": "Veuillez saisir un titre ou un auteur"}), 400

    results = search_zlib(title, author, lang)
    return jsonify({"results": results, "count": len(results)})


@app.route("/api/download")
def get_download():
    slug = request.args.get("slug", "").strip()
    book_id = request.args.get("book_id", "").strip()

    if not slug:
        return jsonify({"error": "Paramètre manquant"}), 400

    url, status = get_download_url(slug, book_id)

    if status == "login_required":
        return jsonify({"error": "login_required"}), 403
    if not url:
        return jsonify({"error": "Lien introuvable"}), 404

    return jsonify({"url": url})


@app.route("/api/proxy")
def proxy_download():
    """Proxy the download so the iPad receives the file directly."""
    url = request.args.get("url", "")
    if not url or not url.startswith(ZLIB_BASE):
        return "URL non autorisée", 403

    try:
        session = get_session()
        r = session.get(url, stream=True, timeout=60)
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
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
