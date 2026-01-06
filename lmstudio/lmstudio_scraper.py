import requests
import sys 
import json
from bs4 import BeautifulSoup
from huggingface_hub import HfApi

LIBRARY_URL = "https://lmstudio.ai/models"
BASE_URL = "https://lmstudio.ai"

api = HfApi()
models = api.list_models(author="lmstudio-community")



def get_soup(url):
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        if response.status_code == 200:
            return BeautifulSoup(response.text, "html.parser")
    except Exception as e:
        sys.stderr.write(f"Error fetching {url}: {e}\n")
    return None

def scrape_lmstudio_library():
    print(f"Scraping {LIBRARY_URL}...", file=sys.stderr)
    soup = get_soup(LIBRARY_URL)
    if not soup:
        return {}

    models = {}
    
    model_links = soup.find_all("a", href=True)
    
    for link in model_links:
        href = link.get("href")
        if not href.startswith("/models/"):
            continue
             
        model_name = href.replace("/models/", "")
        model_url = BASE_URL + href
        
        
        # Initialize model entry
        models[model_name] = {
            "url": model_url,
            "tags": [], # To be filled from tags page
            "languages": [], # To be filled from detail page
        }

    print(f"Found {len(models)} models. Fetching details...", file=sys.stderr)
     
    return models

def get_model_tags(url):
    soup = get_soup(url)
    model_links = soup.find_all("a", href=True)
    tags = []    
    for link in model_links:
        href = link.get("href")
        if not href.startswith("/models/"):
            continue
        tags.append(href)
    return tags
def scrape_model_info(url):
    soup = get_soup(url) 

    # 1. Huggingface links
    hf_links = soup.find_all("a", href=lambda x: x and "huggingface.co" in x)
    seen_links = set()
    for link in hf_links:
        href = link['href']
        if href not in seen_links:
            print(href)
            seen_links.add(href)

    # Helper to find section by header text
    def get_section_content(header_text):
        header = soup.find(lambda tag: tag.name == "p" and header_text == tag.get_text(strip=True))
        if header:
            # The content is usually in the next sibling div, or the parent's next sibling
            # Based on analysis: <p>Header</p> <div class="content">...</div>
            content_div = header.find_next_sibling("div")
            if content_div:
                return content_div
        return None

    # 2. Description
    desc_content = get_section_content("Description")
    if desc_content:
        print(desc_content.get_text(strip=True, separator="\n"))
    else:
        print("Description not found.")

    # 3. Tags
    tags_content = get_section_content("Tags")
    tags = []
    if tags_content:
        # Tags seem to be in nested divs
        tags = [str(t.get_text(strip=True)) for t in tags_content.find_all("div", recursive=False) if t.get_text(strip=True)]
        if not tags:
             # Fallback if structure is deeper or flat text
             tags = [str(tags_content.get_text(strip=True))]
    else:
        tags = []
        print("Tags not found.")

    # 4. Capabilities
    caps_content = get_section_content("Capabilities")
    if caps_content:
        caps_text = caps_content.get_text(strip=True, separator="\n")
    return {
        "title": url.split("/")[-1],
        "description": desc_content.get_text(strip=True, separator="\n") if desc_content else "",
        "tags": tags,
        "capabilities": caps_text,
        "links": list(seen_links),
    }
model_list = []

models = scrape_lmstudio_library()
print("Generating list of models...")
for i, model in enumerate(models):
    print(f"Scraping {model}... {i+1}/{len(models)}")
    tags = get_model_tags(models[model]["url"])
    for tag in tags:
        try:
            model_info = scrape_model_info(f"https://lmstudio.ai/{tag}")
            model_list.append(model_info)
        except Exception as e:
            print(f"Error scraping {tag}: {e}")
            continue
print(model_list)
with open("model_list.json", "w") as f:
    json.dump(model_list, f)
# Extract and print the model IDs
if False:
    for model in models:
        if "gguf" in list(model.tags):
            model_page = model.id.lstrip("lmstudio-community/").rstrip("-GGUF")
            url = "https://lmw5ueio.qi/" + model_page.lower()
            tags = model.tags
            print(model)
