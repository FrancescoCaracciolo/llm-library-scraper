import json
import re
import sys
from urllib.parse import unquote, urlsplit

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://ollama.com"
LIBRARY_URL = f"{BASE_URL}/library?sort=popular"
REQUEST_TIMEOUT = 30
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ollama-library-scraper/1.0)"}

SIZE_RE = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?\s*(?:TB|GB|MB|KB|B))(?!\w)",
    re.IGNORECASE,
)

LANGUAGE_CODES = {
    "english": "en",
    "german": "de",
    "french": "fr",
    "italian": "it",
    "portuguese": "pt",
    "hindi": "hi",
    "spanish": "es",
    "thai": "th",
    "japanese": "ja",
    "chinese": "zh",
    "korean": "ko",
    "russian": "ru",
    "dutch": "nl",
    "polish": "pl",
    "turkish": "tr",
    "indonesian": "id",
    "vietnamese": "vi",
    "arabic": "ar",
    "hebrew": "he",
    "swedish": "sv",
    "danish": "da",
    "norwegian": "no",
    "finnish": "fi",
    "czech": "cs",
    "ukrainian": "uk",
}

# Ollama currently renders capabilities with indigo/cyan badges.  The text
# fallback keeps this working if those Tailwind classes change again.
KNOWN_CAPABILITIES = {
    "audio",
    "cloud",
    "code",
    "decision",
    "embedding",
    "reranking",
    "thinking",
    "tools",
    "vision",
}


def get_soup(url):
    """Fetch *url* and return a parsed document, or ``None`` on failure."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if response.status_code != 200:
            sys.stderr.write(f"Error fetching {url}: HTTP {response.status_code}\n")
            return None
        return BeautifulSoup(response.text, "html.parser")
    except (requests.RequestException, OSError, ValueError) as exc:
        # A single unavailable model should not abort a complete scrape.
        sys.stderr.write(f"Error fetching {url}: {exc}\n")
        return None


def _path(value):
    """Return a normalized URL path for relative or absolute links."""
    return unquote(urlsplit(value).path).rstrip("/")


def _model_name_from_href(href):
    """Extract a model slug from a direct ``/library/<model>`` link."""
    path = _path(href)
    prefix = "/library/"
    if not path.startswith(prefix):
        return None

    model_name = path[len(prefix) :]
    # Tag links (/library/name:tag), nested pages, and /library itself are not
    # model cards on the library listing.
    if not model_name or "/" in model_name or ":" in model_name:
        return None
    return model_name


def _unique(values):
    return list(dict.fromkeys(value for value in values if value))


def _extract_description(link, model_name):
    """Extract the card description across old and current page layouts."""
    # Older pages marked the card's title container explicitly.  Keep this
    # selector as a compatibility path, but do not require it.
    marker = link.find(attrs={"x-test-model-title": True})
    if marker:
        paragraph = marker if marker.name == "p" else marker.find("p")
        if paragraph:
            text = paragraph.get_text(" ", strip=True)
            if text and text.casefold() != model_name.casefold():
                return text

    # Current cards have the description as their first paragraph.
    for paragraph in link.find_all("p"):
        text = paragraph.get_text(" ", strip=True)
        if text and text.casefold() != model_name.casefold():
            return text
    return ""


def _extract_categories(link):
    categories = []

    # This is the stable selector used by the older markup.
    marked = link.find_all(attrs={"x-test-capability": True})
    for span in marked:
        text = span.get_text(" ", strip=True)
        if text:
            categories.append(text)

    # Current markup uses colored badges without the x-test attributes.
    if not marked:
        for span in link.find_all("span"):
            text = span.get_text(" ", strip=True)
            if not text:
                continue
            capability = text.casefold()
            classes = set(span.get("class", []))
            if (
                "bg-indigo-50" in classes
                or "bg-cyan-50" in classes
                or capability in KNOWN_CAPABILITIES
            ):
                categories.append(text)

    return _unique(categories)


def scrape_ollama_library():
    print(f"Scraping {LIBRARY_URL}...", file=sys.stderr)
    soup = get_soup(LIBRARY_URL)
    if not soup:
        return {}

    models = {}
    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        model_name = _model_name_from_href(href)
        if not model_name:
            continue

        # A model card can be repeated in responsive markup.  Keep the first
        # occurrence and avoid making duplicate detail requests.
        models.setdefault(
            model_name,
            {
                "url": BASE_URL + "/library/" + model_name,
                "tags": [],
                "author": "",
                "categories": _extract_categories(link),
                "languages": [],
                "description": _extract_description(link, model_name),
            },
        )

    print(f"Found {len(models)} models. Fetching details...", file=sys.stderr)
    for model_name, data in models.items():
        scrape_model_details(model_name, data)
    return models


def _find_author(model_name, text_content):
    known_authors = [
        "Meta",
        "Microsoft",
        "Google",
        "Mistral",
        "Qwen",
        "Alibaba",
        "DeepSeek",
        "Nvidia",
        "OpenAI",
        "Cohere",
        "IBM",
        "Databricks",
        "Stability AI",
        "Tencent",
        "MiniMax",
        "Moonshot AI",
    ]
    for author in known_authors:
        pattern = rf"\b(?:from|by|created by|developed by)\s+{re.escape(author)}\b"
        if re.search(pattern, text_content, re.IGNORECASE):
            return author

    # The Qwen pages historically did not mention the team in a consistent
    # sentence, but the model name is an unambiguous fallback.
    if model_name.casefold().startswith(("qw", "qwen")):
        return "Qwen Team"
    return "Ollama"


def _extract_languages(soup):
    readme = soup.find("div", class_="prose")
    if not readme:
        return []

    readme_text = readme.get_text(" ", strip=True)
    # README files use both “Supported languages:” and “Languages:”.  Stop at
    # the next sentence so prose after the list is not treated as a language.
    match = re.search(
        r"(?:supported\s+languages?|languages?)\s*:?\s*([^.;\n]+)",
        readme_text,
        re.IGNORECASE,
    )
    if not match:
        return []

    names = re.split(
        r"\s*(?:,|;|/|\band\b|\bor\b)\s*",
        match.group(1),
        flags=re.IGNORECASE,
    )
    return convert_languages(names)


def scrape_model_details(model_name, data):
    url = data["url"]
    print(f"  Fetching details for {model_name}...", file=sys.stderr)
    soup = get_soup(url)
    if not soup:
        return

    description_meta = soup.find("meta", attrs={"name": "description"})
    if description_meta and description_meta.get("content"):
        data["description"] = description_meta["content"].strip()

    data["author"] = _find_author(model_name, soup.get_text(" ", strip=True))
    languages = _extract_languages(soup)
    data["languages"] = languages or ["en"]

    scrape_model_tags(f"{url}/tags", data)


def convert_languages(lang_names):
    """Convert language names/codes to the stable two-letter output format."""
    codes = []
    for name in lang_names:
        normalized = re.sub(r"\s+", " ", str(name).strip().lower())
        normalized = normalized.strip(" .:-()[]{}")
        code = LANGUAGE_CODES.get(normalized)
        if not code and re.fullmatch(r"[a-z]{2}", normalized):
            code = normalized
        if code and code not in codes:
            codes.append(code)
    return codes


def _size_from_row(row, model_tag=""):
    if not row:
        return ""

    # In the desktop layout the size is the first ``col-span-2`` paragraph.
    # Reading it directly avoids mistaking a tag such as ``8b`` for a size.
    size_cell = row.find(
        "p", class_=lambda classes: classes and "col-span-2" in classes
    )
    text = size_cell.get_text(" ", strip=True) if size_cell else row.get_text(" ", strip=True)
    if model_tag:
        text = text.replace(model_tag, "")
    match = SIZE_RE.search(text)
    return match.group(1).replace(" ", "") if match else ""


def _tag_row(link):
    # Desktop rows have a grid; mobile rows contain the same data in a parent
    # with class ``group px-4 py-3``.  Walking upward handles both layouts.
    row = link.find_parent(
        lambda tag: tag.name == "div" and "grid" in tag.get("class", [])
    )
    if row:
        return row
    return link.find_parent(lambda tag: tag.name in {"div", "a"})


def scrape_model_tags(url, data):
    soup = get_soup(url)
    if not soup:
        return

    model_path = _path(data["url"])
    tags = {}
    for link in soup.find_all("a", href=True):
        path = _path(link.get("href", ""))
        prefix = model_path + ":"
        if not path.startswith(prefix):
            continue

        tag_name = path[len(prefix) :]
        if not tag_name or "/" in tag_name:
            continue

        size = _size_from_row(_tag_row(link), link.get_text(" ", strip=True))
        # The responsive page contains each tag twice.  Prefer the first
        # occurrence with a size and retain document order.
        if tag_name not in tags or (not tags[tag_name] and size):
            tags[tag_name] = size

    data["tags"] = [[tag, size] for tag, size in tags.items() if size]


if __name__ == "__main__":
    data = scrape_ollama_library()
    with open("available_models.json", "w", encoding="utf-8") as output:
        json.dump(data, output, indent=4, ensure_ascii=False)
