from flask import Flask, render_template, request, jsonify, Response
import requests
from bs4 import BeautifulSoup
import urllib.parse
import re
import os
import asyncio
import threading
import time

from playwright.async_api import async_playwright

app = Flask(__name__)

ANNA_BASE = "https://annas-archive.gs"

LANG_NAMES = {
    "fr": ["french", "français", "francais"],
    "en": ["english"],
}

# ── Playwright browser (singleton) ──────────────────────────────────────────
_pw_instance = None
_browser = None
_browser_lock = threading.Lock()


def _get_or_create_loop():
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
        return loop
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def run_async(coro):
    """Run an async coroutine from synchronous Flask code."""
    loop = _get_or_create_loop()
    return loop.run_until_complete(coro)


async def get_browser():
    """Return a persistent Playwright browser (Chromium)."""
    global _pw_instance, _browser
    if _browser is None or not _browser.is_connected():
        _pw_instance = await async_playwright().start()
        _browser = await _pw_instance.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
                "--disable-extensions",
                "--single-process",
            ],
        )
        print("[Browser] Chromium launched")
    return _browser


async def new_stealth_page(browser):
    """Create a new browser page with stealth settings."""
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="fr-FR",
        viewport={"width": 1280, "height": 800},
        extra_http_headers={
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        },
    )
    # Hide navigator.webdriver
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    page = await context.new_page()
    return context, page


async def solve_challenge(page, target_url):
    """Navigate to Anna's Archive, solve the JS challenge, reach target_url."""
    challenge_url = (
        f"{ANNA_BASE}/challenge?next={urllib.parse.quote(target_url)}"
    )
    await page.goto(challenge_url, wait_until="domcontentloaded", timeout=20000)

    # Wait for the challenge JS to fire POST /challenge/ok and redirect
    try:
        await page.wait_for_url(
            lambda url: "annas-archive" in url and "challenge" not in url,
            timeout=8000,
        )
    except Exception:
        # If no redirect happened, try navigating directly
        await page.goto(target_url, wait_until="domcontentloaded", timeout=15000)


async def _search_annas(title, author, lang):
    """Use Playwright to search Anna's Archive and return results."""
    results = []
    query = f"{title} {author}".strip()
    lang_code = "fr" if lang == "fr" else "en"
    search_url = (
        f"{ANNA_BASE}/search"
        f"?q={urllib.parse.quote(query)}"
        f"&ext=epub&lang={lang_code}"
    )

    with _browser_lock:
        browser = await get_browser()
        context, page = await new_stealth_page(browser)

    try:
        await solve_challenge(page, search_url)
        await page.wait_for_load_state("networkidle", timeout=10000)

        content = await page.content()
        soup = BeautifulSoup(content, "lxml")

        # Extract result cards
        seen_md5 = set()
        for link in soup.select("a[href^='/md5/']"):
            md5 = link["href"].replace("/md5/", "").strip("/")
            if md5 in seen_md5 or not re.match(r"^[a-fA-F0-9]{32}$", md5):
                continue
            seen_md5.add(md5)

            # Walk up to find the card container
            card = link
            for _ in range(5):
                if card.parent:
                    card = card.parent
                if len(card.get_text()) > 50:
                    break

            title_el = link.select_one(".truncate, h3, [class*='title']") or link
            book_title = title_el.get_text(strip=True)[:100]
            if not book_title:
                book_title = card.get_text(" ", strip=True)[:80]

            # Detect language from text
            card_text = card.get_text(" ", strip=True).lower()
            language = ""
            if "french" in card_text or "français" in card_text:
                language = "French"
            elif "english" in card_text:
                language = "English"

            # Cover
            cover_img = card.find("img")
            cover = ""
            if cover_img:
                cover = cover_img.get("src") or cover_img.get("data-src") or ""

            results.append({
                "source": "Anna's Archive",
                "title": book_title,
                "author": "",
                "year": "",
                "language": language,
                "size": "",
                "ext": "EPUB",
                "md5": md5,
                "cover": cover,
                "detail_url": f"{ANNA_BASE}/md5/{md5}",
            })

        # If no lang results, try without lang filter
        if not results:
            search_url2 = (
                f"{ANNA_BASE}/search"
                f"?q={urllib.parse.quote(query)}&ext=epub"
            )
            await page.goto(search_url2, wait_until="networkidle", timeout=15000)
            content2 = await page.content()
            soup2 = BeautifulSoup(content2, "lxml")
            for link in soup2.select("a[href^='/md5/']"):
                md5 = link["href"].replace("/md5/", "").strip("/")
                if md5 in seen_md5 or not re.match(r"^[a-fA-F0-9]{32}$", md5):
                    continue
                seen_md5.add(md5)
                book_title = link.get_text(strip=True)[:100] or "Livre"
                results.append({
                    "source": "Anna's Archive",
                    "title": book_title,
                    "author": "",
                    "year": "",
                    "language": "",
                    "size": "",
                    "ext": "EPUB",
                    "md5": md5,
                    "cover": "",
                    "detail_url": f"{ANNA_BASE}/md5/{md5}",
                })

    except Exception as e:
        print(f"[Anna's search] Error: {e}")
    finally:
        await context.close()

    # Sort preferred language first
    preferred = LANG_NAMES.get(lang, [])
    results.sort(
        key=lambda r: 0 if any(p in r["language"].lower() for p in preferred) else 1
    )
    return results


async def _get_download_links(md5):
    """Get EPUB download links from Anna's Archive MD5 page."""
    links = []
    detail_url = f"{ANNA_BASE}/md5/{md5}"

    with _browser_lock:
        browser = await get_browser()
        context, page = await new_stealth_page(browser)

    try:
        await solve_challenge(page, detail_url)
        await page.wait_for_load_state("networkidle", timeout=10000)
        content = await page.content()
        soup = BeautifulSoup(content, "lxml")

        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            # IPFS, fast download, libgen mirrors
            if any(
                x in href
                for x in [
                    "/fast_download/",
                    "/slow_download/",
                    "ipfs",
                    "libgen",
                    "/download/",
                ]
            ):
                if not href.startswith("http"):
                    href = ANNA_BASE + href
                label = text[:60] or href[:60]
                if href not in [l["url"] for l in links]:
                    links.append({"label": label, "url": href})

    except Exception as e:
        print(f"[Anna's download] Error: {e}")
    finally:
        await context.close()

    return links[:5]


# ── Flask routes ─────────────────────────────────────────────────────────────

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
        results = run_async(_search_annas(title, author, lang))
        return jsonify({"results": results, "count": len(results)})
    except Exception as e:
        print(f"[search] Error: {e}")
        return jsonify({"error": "Erreur de recherche", "results": [], "count": 0})


@app.route("/api/download")
def get_download():
    md5 = request.args.get("md5", "").strip()
    if not md5 or not re.match(r"^[a-fA-F0-9]{32}$", md5):
        return jsonify({"error": "MD5 invalide"}), 400

    try:
        links = run_async(_get_download_links(md5))
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
    allowed = ["annas-archive.gs", "ipfs.io", "cloudflare-ipfs.com", "libgen"]
    if not url or not any(a in url for a in allowed):
        return "URL non autorisée", 403

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
        }
        r = requests.get(url, headers=headers, stream=True, timeout=60)
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


@app.route("/api/test")
def test_sources():
    """Test which sources are reachable from this server's IP."""
    results = {}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
    }
    tests = {
        "libgen_li": "https://libgen.li/index.php?req=test&res=25&filesuns=all",
        "libgen_json": "https://libgen.li/json.php?ids=1&fields=id,title",
        "annas_archive": "https://annas-archive.gs/search?q=test&ext=epub",
        "zlib_id": "https://z-lib.id/s?q=test&extension=epub",
        "zlib_cv": "https://z-lib.cv/s?q=test&extension=epub",
        "libgen_rs": "https://libgen.rs/search.php?req=test&lg_topic=libgen&res=25",
    }
    for name, url in tests.items():
        try:
            r = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
            results[name] = {"status": r.status_code, "size": len(r.content), "url": r.url}
        except Exception as e:
            results[name] = {"error": str(e)}
    return jsonify(results)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
