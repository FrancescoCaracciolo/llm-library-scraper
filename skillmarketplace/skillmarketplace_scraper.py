import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

# skill-marketplace.com (SkillHub) is a React SPA: the marketplace page ships no
# data, everything is loaded from the backend below (found in the JS bundle).
API_BASE = "https://skillhub-api.fly.dev/api"
SITE_BASE = "https://www.skill-marketplace.com"
PAGE_SIZE = 100  # maximum allowed by the API

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})


def get_json(path, params=None, retries=3):
    url = f"{API_BASE}{path}"
    for attempt in range(retries):
        try:
            response = session.get(url, params=params, timeout=60)
            if response.status_code == 200:
                return response.json()
            sys.stderr.write(f"HTTP {response.status_code} for {url}\n")
        except Exception as e:
            sys.stderr.write(f"Error fetching {url}: {e}\n")
        time.sleep(2 * (attempt + 1))
    return None


def parse_github_url(github_url):
    """Split a GitHub URL into owner, repo, branch and in-repo path.

    Skill URLs look like
    https://github.com/owner/repo/tree/main/skills/my-skill
    but some point at a repository root instead.
    """
    if not github_url:
        return None

    match = re.match(
        r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?"
        r"(?:/(?:tree|blob)/([^/]+)(?:/(.*))?)?/?$",
        github_url,
    )
    if not match:
        return None

    owner, repo, branch, path = match.groups()
    return {
        "owner": owner,
        "repo": repo,
        "branch": branch or "main",
        "path": (path or "").strip("/"),
    }


def build_download_links(skill):
    """Collect every way of getting the skill's content.

    The website itself has no server-side download: its download button builds a
    SKILL.md blob (or a zip) in the browser from the API response, so the links
    below are the API endpoints plus the GitHub locations they came from.
    """
    skill_id = skill.get("id")
    links = {
        # SKILL.md text, ready to save as a file (also inlined in this JSON as
        # "skill_md_content" unless the scraper ran with --no-details).
        "skill_md_api": f"{API_BASE}/skills/{skill_id}",
        "files_api": f"{API_BASE}/skills/{skill_id}/files",
        "marketplace_page": f"{SITE_BASE}/skills/{skill_id}",
        "github_url": skill.get("github_url"),
        "import_url": skill.get("import_url"),
        "skill_md_raw": None,
        "repo_zip": None,
    }

    parts = parse_github_url(skill.get("github_url"))
    if parts:
        raw_dir = (
            f"https://raw.githubusercontent.com/{parts['owner']}/{parts['repo']}"
            f"/{parts['branch']}"
        )
        if parts["path"]:
            raw_dir = f"{raw_dir}/{parts['path']}"
        links["skill_md_raw"] = f"{raw_dir}/SKILL.md"
        links["repo_zip"] = (
            f"https://github.com/{parts['owner']}/{parts['repo']}"
            f"/archive/refs/heads/{parts['branch']}.zip"
        )

    return links


def list_skills(sort="-stars", limit=None):
    """Page through /api/skills and return the summary records."""
    skills = []
    page = 1
    total = None

    while True:
        data = get_json(
            "/skills", params={"page": page, "page_size": PAGE_SIZE, "sort": sort}
        )
        if not data:
            break

        items = data.get("items") or []
        total = data.get("total")
        skills.extend(items)
        sys.stderr.write(f"Fetched page {page}: {len(skills)}/{total} skills\n")

        if limit and len(skills) >= limit:
            skills = skills[:limit]
            break
        if not items or (total is not None and len(skills) >= total):
            break
        page += 1

    return skills


def fetch_detail(skill):
    """Fetch the full record for one skill (SKILL.md content, files, versions)."""
    detail = get_json(f"/skills/{skill['id']}")
    if not detail:
        sys.stderr.write(f"No detail for skill {skill['id']} ({skill['name']})\n")
        return dict(skill)
    return detail


def get_top_ranking():
    """Map skill id -> ranking info from the marketplace sidebar."""
    top = get_json("/skills/top") or []
    return {
        entry["id"]: {
            "rank": entry.get("rank"),
            "stars_change_7d": entry.get("stars_change_7d"),
            "trend": entry.get("trend"),
        }
        for entry in top
        if entry.get("id") is not None
    }


def scrape(with_details=True, workers=8, sort="-stars", limit=None):
    sys.stderr.write(f"Scraping {SITE_BASE}/marketplace via {API_BASE}...\n")

    summaries = list_skills(sort=sort, limit=limit)
    if not summaries:
        return []

    if with_details:
        sys.stderr.write(f"Fetching details for {len(summaries)} skills...\n")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            records = list(pool.map(fetch_detail, summaries))
    else:
        records = [dict(s) for s in summaries]

    ranking = get_top_ranking()

    skills = []
    for record in records:
        source = record.get("source") or {}
        skill = {
            "id": record.get("id"),
            "name": record.get("name"),
            "description": record.get("description"),
            "url": f"{SITE_BASE}/skills/{record.get('id')}",
            "source": {
                "id": source.get("id"),
                "name": source.get("name"),
                "url": f"https://github.com/{source['name']}" if source.get("name") else None,
            },
            "language": record.get("language"),
            "license": record.get("license"),
            "version": record.get("version"),
            "tags": record.get("tags") or [],
            "platforms": record.get("platforms") or [],
            "stars": record.get("stars"),
            "forks": record.get("forks"),
            "downloads": record.get("downloads"),
            "ranking": ranking.get(record.get("id")),
            "created_at": record.get("created_at"),
            "last_updated_at": record.get("last_updated_at"),
            "download": build_download_links(record),
        }

        if with_details:
            skill["skill_md_content"] = record.get("skill_md_content")
            skill["files"] = record.get("files") or []
            skill["versions"] = record.get("versions") or []
            skill["related_skills"] = record.get("related_skills") or []
            skill["star_history"] = record.get("star_history") or []
        else:
            skill["skill_md_preview"] = record.get("skill_md_preview")

        skills.append(skill)

    return skills


def main():
    parser = argparse.ArgumentParser(
        description="Scrape all skills from skill-marketplace.com into a JSON file."
    )
    parser.add_argument("-o", "--output", default="skills.json", help="output JSON file")
    parser.add_argument(
        "--no-details",
        action="store_true",
        help="skip per-skill requests (no SKILL.md content, much faster)",
    )
    parser.add_argument("--workers", type=int, default=8, help="parallel detail requests")
    parser.add_argument("--sort", default="-stars", help="API sort order, e.g. -stars")
    parser.add_argument("--limit", type=int, help="only scrape the first N skills")
    args = parser.parse_args()

    skills = scrape(
        with_details=not args.no_details,
        workers=args.workers,
        sort=args.sort,
        limit=args.limit,
    )

    with open(args.output, "w") as f:
        json.dump(skills, f, indent=4)

    sys.stderr.write(f"Wrote {len(skills)} skills to {args.output}\n")


if __name__ == "__main__":
    main()
