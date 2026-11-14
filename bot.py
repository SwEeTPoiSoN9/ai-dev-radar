import datetime
import json
import os
import re
import time
import random
from google import genai
from google.genai import errors
import requests

HISTORY_FILE = "data/history.json"
POSTS_DIR = "content/posts"

# ✅ Valid model names — pick one that's available to your API key
PRIMARY_MODEL = "gemini-2.5-flash"       # or "gemini-2.0-flash"
FALLBACK_MODEL = "gemini-2.5-flash-lite"  # cheaper, more available fallback
MAX_RETRIES = 4
BASE_DELAY = 5  # seconds


def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_history(history):
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history[-200:], f, indent=2)


def get_trending_topic(history):
    seen_ids = {item["id"] for item in history}
    top_ids = requests.get(
        "https://hacker-news.firebaseio.com/v0/topstories.json", timeout=15
    ).json()[:30]

    for story_id in top_ids:
        if story_id in seen_ids:
            continue
        item = requests.get(
            f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
            timeout=15,
        ).json()
        if (
            item
            and item.get("type") == "story"
            and item.get("url")
            and item.get("score", 0) >= 80
        ):
            return item
    return None


def slugify(text):
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:65]


def _call_gemini(client, model, prompt):
    """Single attempt to call Gemini, returns response text or raises."""
    response = client.models.generate_content(model=model, contents=prompt)
    return response.text


def generate_article(story, recent_posts):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    date_str = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    slug = slugify(story["title"])

    related_links = "\n".join(
        [f"- [{p['title']}](/posts/{p['slug']}/)" for p in recent_posts[-4:]]
    )

    prompt = f"""
    You are a senior software engineer and technical editor. Write an authoritative, deeply informative, SEO-optimized article (1,100 to 1,400 words) analyzing this trending tech/AI release:

    Title: {story['title']}
    Reference URL: {story['url']}
    Community Score: {story.get('score')} points | {story.get('descendants', 0)} comments

    Format strictly as valid Hugo Markdown starting with YAML frontmatter:
    ---
    title: "<SEO-friendly, clear headline>"
    date: {date_str}
    slug: "{slug}"
    description: "<Compelling 145-character meta description>"
    tags: ["AI", "Open Source", "Developer Tools"]
    draft: false
    ---

    Article Structure Requirements:
    1. **Executive Overview:** Explain what this tool/project is and why developers are paying attention right now.
    2. **Key Technical Features & Architecture:** Break down how it works in practical terms using clear H2/H3 headings and bullet points.
    3. **Comparison Table:** Include a clean Markdown table comparing this approach/tool against 2 established alternatives across Ease of Use, Performance, Pricing/License, and Best Use Case.
    4. **Practical Implementation / Getting Started:** Give concrete steps or an example workflow for developers.
    5. **Limitations & Trade-offs:** Be objective—mention potential bottlenecks or drawbacks.
    6. **Frequently Asked Questions (FAQ):** 3 realistic questions users search on Google about this topic with concise answers.

    Style rules:
    - Write naturally with zero AI clichés ("In conclusion", "delve", "game-changer", "landscape").
    - Output ONLY the raw Markdown (no ```markdown code fences wrapping the whole output).
    """

    # ✅ Try primary model with retries, then fall back
    models_to_try = [PRIMARY_MODEL, FALLBACK_MODEL]
    content = None

    for model in models_to_try:
        for attempt in range(MAX_RETRIES):
            try:
                content = _call_gemini(client, model, prompt)
                break  # success — exit retry loop
            except errors.ServerError as e:
                # 503 UNAVAILABLE / 429 RATE_LIMITED / 500 — retry with backoff
                if e.code in (429, 500, 503) and attempt < MAX_RETRIES - 1:
                    delay = BASE_DELAY * (2 ** attempt) + random.uniform(0, 3)
                    print(
                        f"⚠️  {model} returned {e.code}. "
                        f"Retrying in {delay:.1f}s "
                        f"(attempt {attempt + 1}/{MAX_RETRIES})…"
                    )
                    time.sleep(delay)
                else:
                    print(f"❌ {model} failed after {MAX_RETRIES} attempts: {e}")
                    break  # try next model
            except Exception as e:
                print(f"❌ Unexpected error with {model}: {e}")
                break  # try next model

        if content:
            break  # got a response, stop trying other models

    if not content:
        raise RuntimeError(
            "All Gemini models failed. Check API key / quota at "
            "https://aistudio.google.com/apikey"
        )

    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```[a-zA-Z]*\n", "", content)
        content = re.sub(r"\n```$", "", content)

    if related_links:
        content += f"\n\n## Related Technical Guides\n{related_links}\n"

    return slug, content


def main():
    history = load_history()
    story = get_trending_topic(history)
    if not story:
        print("No new high-scoring stories found.")
        return

    slug, markdown_content = generate_article(story, history)
    os.makedirs(POSTS_DIR, exist_ok=True)
    filepath = os.path.join(POSTS_DIR, f"{slug}.md")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(markdown_content)

    history.append({"id": story["id"], "title": story["title"], "slug": slug})
    save_history(history)
    print(f"✅ Published: {filepath}")


if __name__ == "__main__":
    main()
