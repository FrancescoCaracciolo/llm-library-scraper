import requests
from bs4 import BeautifulSoup
import json
import re
import sys

BASE_URL = "https://ollama.com"
LIBRARY_URL = "https://ollama.com/library?sort=popular"

def get_soup(url):
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        if response.status_code == 200:
            return BeautifulSoup(response.text, "html.parser")
    except Exception as e:
        sys.stderr.write(f"Error fetching {url}: {e}\n")
    return None

def scrape_ollama_library():
    print(f"Scraping {LIBRARY_URL}...", file=sys.stderr)
    soup = get_soup(LIBRARY_URL)
    if not soup:
        return {}

    models = {}
    
    # The list items seem to be <a> tags with class "group" inside the main list
    # Based on grep: <a href="/library/llama3.3" class="group w-full space-y-5">
    
    # Finding the list container might be safer
    # Assuming the list is the main content.
    
    model_links = soup.find_all("a", href=True)
    
    for link in model_links:
        href = link.get("href")
        if not href.startswith("/library/"):
            continue
            
        # Check if it's a model card (has specific structure)
        title_div = link.find("div", attrs={"x-test-model-title": True})
        if not title_div:
            continue
            
        model_name = href.replace("/library/", "")
        model_url = BASE_URL + href
        
        # Extract basic info from list card
        description_p = title_div.find("p")
        description = description_p.get_text(strip=True) if description_p else ""
        
        # Categories/Capabilities from list
        # Look for spans with x-test-capability
        categories = []
        cap_spans = link.find_all("span", attrs={"x-test-capability": True})
        for span in cap_spans:
            categories.append(span.get_text(strip=True))
            
        # Initialize model entry
        models[model_name] = {
            "url": model_url,
            "tags": [], # To be filled from tags page
            "author": "", # To be filled from detail page
            "categories": categories,
            "languages": [], # To be filled from detail page
            "description": description
        }

    print(f"Found {len(models)} models. Fetching details...", file=sys.stderr)
    
    # Limit for testing? User didn't say. I'll process all found on first page.
    for model_name, data in models.items():
        scrape_model_details(model_name, data)
        
    return models

def scrape_model_details(model_name, data):
    url = data["url"]
    print(f"  Fetching details for {model_name}...", file=sys.stderr)
    soup = get_soup(url)
    if not soup:
        return

    # 1. Author
    # Try to find "from [Author]" in description or look for specific text patterns
    # In list page: "Llama 3.1 is a new state-of-the-art model from Meta..."
    # In detail page text: "New state-of-the-art 70B model from Meta..."
    
    # Default author
    author = "Ollama" 
    
    # Try to extract from text
    # Gather all text from paragraphs
    text_content = soup.get_text(" ", strip=True)
    
    # Simple heuristic for common authors
    known_authors = ["Meta", "Microsoft", "Google", "Mistral", "Qwen", "Alibaba", "DeepSeek", "Nvidia"]
    for auth in known_authors:
        if f"from {auth}" in text_content or f"by {auth}" in text_content:
            author = auth
            break
            
    # Specific fix for Qwen/QwQ if not found
    if model_name.startswith("qw") or model_name.startswith("qwen"):
        if author == "Ollama":
            author = "Qwen Team" # As per user example
            
    if author == "Ollama" and "Llama" in model_name:
         if "Meta" in text_content:
             author = "Meta"

    data["author"] = author

    # 2. Languages
    # Look for "Supported languages: ..."
    # Based on grep: <p><strong>Supported languages</strong>: English, ...</p>
    languages = []
    
    # Regex to find "Supported languages"
    # It might be in a p tag, potentially with strong tag
    # We search in the text of the description/readme area
    
    readme_div = soup.find("div", class_="prose") # Assuming prose class for markdown content
    if readme_div:
        readme_text = readme_div.get_text(" ", strip=True)
        # Regex for "Supported languages: lang1, lang2..."
        lang_match = re.search(r"Supported languages[:\s]+(.*?)(?:\.|$)", readme_text, re.IGNORECASE)
        if lang_match:
            langs_str = lang_match.group(1)
            # Split by comma or "and"
            # Remove "and"
            langs_str = re.sub(r"\band\b", ",", langs_str)
            langs = [l.strip() for l in langs_str.split(",") if l.strip()]
            
            # Convert full names to codes if possible? 
            # User example: ["en", "de", "fr"...]
            # Need a mapping or just use what is found. User example shows codes.
            # The site shows full names "English, German...".
            # I need a mapping.
            languages = convert_languages(langs)
    
    if not languages and "multilingual" in data["categories"]:
        # Fallback if no specific list found but categorized as multilingual
        pass 
        
    # If generic English
    if not languages:
        languages = ["en"]
        
    data["languages"] = languages

    # 3. Tags and Sizes
    # Scrape tags page
    tags_url = f"{url}/tags"
    scrape_model_tags(tags_url, data)

def convert_languages(lang_names):
    # Basic mapping
    mapping = {
        "english": "en", "german": "de", "french": "fr", "italian": "it", 
        "portuguese": "pt", "hindi": "hi", "spanish": "es", "thai": "th",
        "japanese": "ja", "chinese": "zh", "korean": "ko", "russian": "ru",
        "dutch": "nl", "polish": "pl", "turkish": "tr", "indonesian": "id",
        "vietnamese": "vi", "arabic": "ar"
    }
    codes = []
    for name in lang_names:
        code = mapping.get(name.lower())
        if code:
            codes.append(code)
    return codes

def scrape_model_tags(url, data):
    soup = get_soup(url)
    if not soup:
        return

    tags_list = []
    
    # Find list items for tags
    # Structure saw earlier:
    # <div class="flex items-center justify-between w-full">
    #   <div><span class="group-hover:underline">tagname</span></div>
    # </div>
    # ...
    # <p class="col-span-2 text-neutral-500 text-[13px]">43GB</p>
    
    # This structure is a bit loose in grep. Let's look for the rows.
    # The tags seem to be in a list.
    
    # Let's look for "a" tags with href including the model name
    # href="/library/llama3.3:70b"
    
    # Or iterate over rows if they are structured as rows.
    
    # Strategy: Find all links that look like tags, then find the size sibling.
    
    # It seems to be a grid or list.
    # <div class="flex flex-col space-y-[6px]"> ... list of rows ... </div>
    
    links = soup.find_all("a", href=True)
    for link in links:
        href = link.get("href")
        if not href or ":" not in href:
            continue
            
        if data["url"].replace(BASE_URL, "") not in href:
            continue
            
        # This link is a tag link, e.g. /library/llama3.3:70b
        tag_name = href.split(":")[-1]
        
        # Now find the size. 
        # The size is usually in the same row container.
        # <div class="grid grid-cols-12 items-center"> ... </div>
        
        row = link.find_parent("div", class_="grid")
        if not row:
            # Maybe it's the mobile view or different structure
            # Try finding parent of parent
            # The grep showed: 
            # <span class="flex items-center font-medium col-span-6 group text-sm"> <a ...> </a> </span>
            # <p class="col-span-2 text-neutral-500 text-[13px]">43GB</p>
            
            # The link is inside a span col-span-6.
            # The size is in a p col-span-2.
            
            row = link.find_parent("div", class_="grid")
            
        if row:
            # Find the size paragraph
            # It usually has text with "GB" or "MB"
            size_p = row.find("p", string=re.compile(r"GB|MB|KB"))
            if size_p:
                size = size_p.get_text(strip=True)
                # Add to tags if not exists
                if [tag_name, size] not in tags_list:
                    tags_list.append([tag_name, size])

    data["tags"] = tags_list

if __name__ == "__main__":
    data = scrape_ollama_library()
    with open("available_models.json", "w") as f:
        json.dump(data, f, indent=4)

