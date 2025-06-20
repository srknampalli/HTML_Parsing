# app.py
# ────────────────────────────────────────────────────────────────────
# Streamlit tool that:
# 1. Lets users upload .mhtml files (max 5 for speed)
# 2. Extracts the HTML payload
# 3. Parses UI components (headers, nav bars, etc.)
# 4. Optionally asks GPT-4o for insights
# 5. Shows the component counts + JSON in the UI
# ────────────────────────────────────────────────────────────────────

# STEP 0 ── Imports & environment ────────────────────────────────────
import os, json, email
from collections import defaultdict

import streamlit as st
import pandas as pd
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

OPENAI_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_KEY:
    st.error("OPENAI_API_KEY not found. Add it to .env, env vars, or Streamlit Secrets.")
    st.stop()

client = OpenAI(api_key=OPENAI_KEY)

# STEP 1 ── Helper functions ────────────────────────────────────────
def extract_html_from_mhtml(file):
    """Return the first text/html part in an .mhtml file (or None)."""
    content = file.read().decode("utf-8", errors="ignore")
    msg = email.message_from_string(content)
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            html = part.get_payload(decode=True)
            if html:
                return html.decode(errors="ignore")
    return None


def parse_components(html: str):
    """Parse HTML and collect UI component snippets."""
    soup = BeautifulSoup(html, "lxml")
    comps = defaultdict(list)

    # headers / section titles
    for tag in soup.find_all(["h1", "h2", "h3"]):
        text = tag.get_text(strip=True)
        if text:
            comps["header"].append({"tag": tag.name, "text": text[:100]})

    # footers
    for tag in soup.find_all("footer"):
        text = tag.get_text(strip=True)
        if text:
            comps["footer"].append({"tag": tag.name, "text": text[:100]})

    # paragraphs
    for p in soup.find_all("p"):
        text = p.get_text(strip=True)
        if text:
            comps["text_block"].append({"tag": p.name, "text": text[:100]})

    # navigation bars
    for nav in soup.find_all("nav"):
        for a in nav.find_all("a", href=True):
            comps["nav_bar"].append({"href": a["href"], "text": a.get_text(strip=True)})

    # image galleries (div class contains "gallery")
    for div in soup.find_all("div", class_=lambda c: c and "gallery" in c.lower()):
        for img in div.find_all("img"):
            comps["image_gallery"].append({"src": img.get("src"), "alt": img.get("alt")})

    # standalone images (not inside gallery div)
    for img in soup.find_all("img"):
        if img.find_parent("div", class_=lambda c: c and "gallery" in c.lower()) is None:
            comps["image_gallery"].append({"src": img.get("src"), "alt": img.get("alt")})

    # generic links outside nav
    for a in soup.find_all("a", href=True):
        if a.find_parent("nav") is None:
            comps["link_block"].append({"href": a["href"], "text": a.get_text(strip=True)})

    # buttons
    for btn in soup.find_all(["button", "input"]):
        if btn.name == "button" or btn.get("type") in {"button", "submit", "reset"}:
            comps["button_block"].append({"html": str(btn)[:120]})

    # forms
    for form in soup.find_all("form"):
        comps["forms"].append(
            {"action": form.get("action"), "method": (form.get("method") or "GET").upper()}
        )

    # modals
    for div in soup.find_all("div", class_=lambda c: c and "modal" in c.lower()):
        comps["modals"].append({"html": str(div)[:120]})

    return comps


def clean_components(comps):
    """Remove duplicates and standardise some fields."""
    clean = defaultdict(list)
    for ctype, items in comps.items():
        seen = set()
        for itm in items:
            sig = json.dumps(itm, sort_keys=True)
            if sig not in seen:
                seen.add(sig)
                clean[ctype].append(itm)

    # post-processing tweaks
    for img in clean["image_gallery"]:
        img["src"] = img["src"] or ""
        img["alt"] = img["alt"] or ""

    for link in clean["link_block"] + clean["nav_bar"]:
        link["text"] = link["text"] or ""

    return clean


def analyze_components_with_gpt(pages_dict):
    """Send a trimmed summary of components to GPT-4o for insights."""
    if not pages_dict:
        return "No pages to analyse."

    compact = {
        fname: {
            "header": [f'{x["tag"]}: {x["text"]}' for x in comps.get("header", [])[:3]],
            "footer": [f'{x["tag"]}: {x["text"]}' for x in comps.get("footer", [])[:3]],
            "text_block": [f'{x["tag"]}: {x["text"]}' for x in comps.get("text_block", [])[:3]],
            "nav_bar": [x["text"] for x in comps.get("nav_bar", [])[:3]],
            "image_gallery": [x["alt"] or x["src"] for x in comps.get("image_gallery", [])[:2]],
            "link_block": [x["text"] for x in comps.get("link_block", [])[:2]],
            "button_block": [x["html"] for x in comps.get("button_block", [])[:1]],
            "forms": comps.get("forms", []),
            "modals": [x["html"] for x in comps.get("modals", [])[:1]],
        }
        for fname, comps in pages_dict.items()
    }

    prompt = (
        "You are a senior UI/UX architect.\n\n"
        "Given summaries of up to 5 HTML pages, identify reusable component models, "
        "common patterns, unique elements, and how many component types cover 90% "
        "of the content.\n\n"
        f"Component summaries:\n{json.dumps(compact, indent=2)}"
    )

    rsp = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt},
        ],
    )
    return rsp.choices[0].message.content


def generate_components_json(pages_dict):
    """Flatten cleaned components into a simpler JSON shape for UI."""
    mapping = {
        "header": "Header/Footer",
        "footer": "Header/Footer",
        "text_block": "Text Block",
        "image_gallery": "Image Gallery",
        "nav_bar": "Navigation Bar",
        "link_block": "Link Block",
        "button_block": "Button Block",
        "forms": "Form",
        "modals": "Modal",
    }
    result = {}
    for fname, comps in pages_dict.items():
        file_obj = defaultdict(lambda: {"count": 0, "details": []})
        for ctype, items in comps.items():
            group = mapping[ctype]
            file_obj[group]["count"] += len(items)
            file_obj[group]["details"].extend(items)
        result[fname] = file_obj
    return result


def visualize_components(components_json):
    """Show counts per component per file as a dataframe + raw JSON."""
    if not components_json:
        st.info("Nothing to display yet.")
        return

    # Table of counts
    types = [
        "Header/Footer",
        "Navigation Bar",
        "Text Block",
        "Image Gallery",
        "Link Block",
        "Button Block",
        "Form",
        "Modal",
    ]
    tbl = {t: [] for t in types}
    files = list(components_json.keys())
    for fname in files:
        for t in types:
            tbl[t].append(components_json[fname][t]["count"] if t in components_json[fname] else 0)
    df = pd.DataFrame(tbl, index=files)
    st.subheader("Component counts")
    st.dataframe(df)

    # Raw JSON per file
    st.subheader("Details (per file)")
    for fname in files:
        with st.expander(fname):
            st.json(components_json[fname])


# STEP 2 ── Streamlit UI & app flow ─────────────────────────────────
def main():
    st.title("MHTML Component Analyzer")

    # File uploader
    st.header("Upload .mhtml files")
    uploaded_files = st.file_uploader(
        "Select up to 5 .mhtml files",
        type="mhtml",
        accept_multiple_files=True,
    )

    # Parse & clean
    if not uploaded_files:
        st.info("Upload files to begin (using sample data instead).")
        page_summary = {
            "sample.mhtml": {
                "header": [{"tag": "h1", "text": "Welcome"}],
                "footer": [{"tag": "footer", "text": "Contact"}],
                "text_block": [{"tag": "p", "text": "Lorem ipsum"}],
                "image_gallery": [{"src": "logo.png", "alt": "Logo"}],
                "nav_bar": [{"href": "/home", "text": "Home"}],
                "link_block": [{"href": "/about", "text": "About"}],
                "button_block": [{"html": "<button>Click</button>"}],
                "forms": [{"action": "/send", "method": "POST"}],
                "modals": [{"html": "<div class='modal'>Modal</div>"}],
            }
        }
    else:
        page_summary = {}
        for file in uploaded_files[:5]:
            html = extract_html_from_mhtml(file)
            if not html:
                st.warning(f"Could not extract HTML from {file.name}")
                continue
            comps = parse_components(html)
            page_summary[file.name] = clean_components(comps)

    # Component JSON
    components_json = generate_components_json(page_summary)
    visualize_components(components_json)

    # GPT analysis
    if st.button("Analyse patterns with GPT-4o"):
        with st.spinner("Talking to GPT-4o…"):
            analysis = analyze_components_with_gpt(page_summary)
        st.subheader("GPT-4o insights")
        st.markdown(analysis)


if __name__ == "__main__":
    main()
