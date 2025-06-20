# app.py  ────────────────────────────────────────────────────────────
import os, json, email
from collections import defaultdict

import streamlit as st
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ── ENV / API (we don’t call GPT in this version, but keep key check) ──
load_dotenv()
if not os.getenv("OPENAI_API_KEY"):
    st.warning("OPENAI_API_KEY not set (only needed if you later add GPT).")

# ── Utility to read HTML from .mhtml ─────────────────────────────────
def extract_html_from_mhtml(file):
    """Return the first text/html payload from an MHTML upload."""
    content = file.read().decode("utf-8", errors="ignore")
    msg = email.message_from_string(content)
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            html = part.get_payload(decode=True)
            if html:
                return html.decode(errors="ignore")
    return None

# ── Helper: keyword match in class or id ────────────────────────────
def has_kw(tag, *kws):
    """True if tag has class/id attribute containing any keyword (case-insensitive)."""
    text = " ".join(tag.get("class", [])) + " " + (tag.get("id") or "")
    text = text.lower()
    return any(kw in text for kw in kws)

# ── Main parser ─────────────────────────────────────────────────────
def parse_components(html: str):
    soup = BeautifulSoup(html, "lxml")
    comps = defaultdict(list)

    # Header (semantic tags OR class/id keywords OR h1/h2/h3)
    for tag in soup.find_all(
        lambda t: t.name in ["header", "h1", "h2", "h3"] or has_kw(t, "header", "topbar", "title")
    ):
        text = tag.get_text(" ", strip=True)
        if text:
            comps["header"].append({"tag": tag.name, "text": text[:120]})

    # Footer
    for tag in soup.find_all(
        lambda t: t.name == "footer" or has_kw(t, "footer", "bottom")
    ):
        text = tag.get_text(" ", strip=True)
        if text:
            comps["footer"].append({"tag": tag.name, "text": text[:120]})

    # Paragraphs
    for p in soup.find_all(lambda t: t.name == "p" or has_kw(t, "paragraph", "text-block")):
        text = p.get_text(" ", strip=True)
        if text:
            comps["text_block"].append({"tag": p.name, "text": text[:120]})

    # Navigation bars (<nav> OR class/id contains nav / menu / navbar)
    nav_elems = soup.find_all(
        lambda t: t.name == "nav" or has_kw(t, "nav", "navbar", "menu")
    )
    for nav in nav_elems:
        for a in nav.find_all("a", href=True):
            comps["nav_bar"].append({"href": a["href"], "text": a.get_text(" ", strip=True)})

    # Image galleries: <div class*="gallery"> imgs  + standalone imgs not in gallery
    for div in soup.find_all(lambda t: has_kw(t, "gallery", "carousel", "slider")):
        for img in div.find_all("img"):
            comps["image_gallery"].append({"src": img.get("src"), "alt": img.get("alt")})

    for img in soup.find_all("img"):
        if img.find_parent(lambda t: has_kw(t, "gallery", "carousel", "slider")) is None:
            comps["image_gallery"].append({"src": img.get("src"), "alt": img.get("alt")})

    # Generic links outside nav
    for a in soup.find_all("a", href=True):
        if not a.find_parent(nav_elems):
            comps["link_block"].append({"href": a["href"], "text": a.get_text(" ", strip=True)})

    # Buttons (button tag, input type=button/submit, OR class/id has btn/button)
    for btn in soup.find_all(
        lambda t: (
            t.name == "button"
            or (t.name == "input" and t.get("type") in {"button", "submit", "reset"})
            or has_kw(t, "btn", "button")
        )
    ):
        comps["button_block"].append({"html": str(btn)[:150]})

    # Forms
    for form in soup.find_all("form"):
        comps["forms"].append(
            {"action": form.get("action"), "method": (form.get("method") or "GET").upper()}
        )

    # Modals (class/id contains modal)
    for div in soup.find_all(lambda t: has_kw(t, "modal", "dialog")):
        comps["modals"].append({"html": str(div)[:150]})

    return comps

# ── De-duplication / standardisation ───────────────────────────────
def clean_components(comps):
    cleaned = defaultdict(list)
    seen = defaultdict(set)

    for ctype, items in comps.items():
        for itm in items:
            sig = json.dumps(itm, sort_keys=True)
            if sig not in seen[ctype]:
                seen[ctype].add(sig)
                # normalise empty src/alt/text
                if "src" in itm:
                    itm["src"] = itm["src"] or ""
                if "alt" in itm:
                    itm["alt"] = itm["alt"] or ""
                if "text" in itm:
                    itm["text"] = itm["text"] or ""
                cleaned[ctype].append(itm)

    return cleaned

# ── Streamlit app ──────────────────────────────────────────────────
def main():
    st.title("MHTML Component Extractor → JSON")

    uploaded = st.file_uploader(
        "Upload up to 5 .mhtml files",
        type="mhtml",
        accept_multiple_files=True,
    )

    if not uploaded:
        st.info("No files uploaded – displaying stub example.")
        pages = {
            "sample.mhtml": {
                "header": [{"tag": "h1", "text": "Welcome"}],
                "footer": [{"tag": "footer", "text": "Contact us"}],
                "text_block": [{"tag": "p", "text": "Lorem ipsum…"}],
                "image_gallery": [{"src": "logo.png", "alt": "logo"}],
                "nav_bar": [{"href": "/home", "text": "Home"}],
                "link_block": [{"href": "/about", "text": "About"}],
                "button_block": [{"html": "<button>Click</button>"}],
                "forms": [{"action": "/submit", "method": "POST"}],
                "modals": [{"html": "<div class='modal'>Demo</div>"}],
            }
        }
    else:
        pages = {}
        for file in uploaded[:5]:
            html = extract_html_from_mhtml(file)
            if not html:
                st.warning(f"⚠️  Couldn’t extract HTML from {file.name}")
                continue
            comps = clean_components(parse_components(html))
            pages[file.name] = comps

    # ---- Display final JSON only ----
    st.subheader("Parsed component JSON")
    st.json(pages, expanded=False)


if __name__ == "__main__":
    main()
