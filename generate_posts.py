"""
generate_posts.py

Detects the active Facebook Page category from the Multilogin profile,
then generates 3 unique posts from a local template bank — no API key needed.

Saves two files:
  posts.txt             — ready for post.py
  image_suggestions.txt — one image idea per post, for your reference

Usage:
    python3 generate_posts.py --account "EMI_AUTO_2"
    python3 generate_posts.py --account "EMI_AUTO_2" --category LS
    python3 generate_posts.py --account "EMI_AUTO_2" --url "https://example.com"

Requirements:
    MLX_EMAIL, MLX_PASSWORD env vars must be set.
"""

from __future__ import annotations

import argparse
import os
import random
import re
import sys
import time
import urllib.parse
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

from mlx_context import start_profile_for
from fb_dom import js_navigate

# ── Config ────────────────────────────────────────────────────────────────────

CATEGORY_MAP = {
    "LS":  "Lifestyle",
    "HOB": "Hobbies",
    "CSI": "Career and Self Improvement",
    "MF":  "Market and Finance",
}

DEFAULT_URL = "https://visioncompassdesk.com"
POSTS_PATH   = Path(__file__).parent / "posts.txt"
IMAGES_PATH  = Path(__file__).parent / "image_suggestions.txt"
IMAGES_DIR   = Path(__file__).parent / "images"

# ── Template bank ─────────────────────────────────────────────────────────────

TEMPLATES = {
    "Lifestyle": {
        "openers": [
            "The little things you choose daily shape everything.",
            "There's a version of your day that actually feels good.",
            "Not every day needs to be productive to matter.",
            "Some routines are worth protecting at all costs.",
            "Your environment shapes your mood more than you think.",
            "Comfort and intention aren't opposites — they belong together.",
            "There's something powerful about slowing down on purpose.",
            "The way you start your morning sets the tone for everything.",
            "Some of the best upgrades to your life cost absolutely nothing.",
            "Living well is mostly about paying attention to the right things.",
            "The details of how you live are worth taking seriously.",
            "A few small shifts in your day can change how the whole week feels.",
        ],
        "bodies": [
            "Small changes in how you structure your day can shift your entire energy.",
            "The way you set up your space, your meals, your evenings — it all adds up.",
            "Most people overlook the micro-moments that make or break a good day.",
            "A few intentional choices can change how a whole week feels.",
            "Lifestyle isn't about aesthetics — it's about how things actually feel to live.",
            "It's not about doing more, it's about doing what actually fits your life.",
            "The small things you do consistently end up defining your days.",
            "How you spend the quiet hours matters just as much as the busy ones.",
            "Rest, rhythm, and a little intention go further than most people expect.",
            "The gap between a draining day and a good one is usually smaller than it seems.",
        ],
        "closers": [
            "What's one small thing that makes your day feel more like yours? 💬",
            "What would you change about your daily routine if you could? 🤔",
            "Which part of your day do you protect the most?",
            "What's something simple that consistently improves your mood? ✨",
            "When did you last feel like your day truly worked for you?",
            "What would your ideal Tuesday actually look like?",
            "What's one habit you keep coming back to no matter what?",
            "Which part of your routine are you most proud of right now?",
        ],
        "hashtags_pool": [
            "#lifestyle", "#intentionalliving", "#dailyroutine", "#slowliving",
            "#mindfullife", "#lifestylegoals", "#morningroutine", "#selfcare",
            "#everydaylife", "#livingwell", "#goodvibes", "#lifetips",
            "#dailylife", "#homevibe", "#lifebydesign",
        ],
        "emojis_pool": ["🌿", "✨", "☀️", "🧘", "🍃", "💫", "🌸", "🕯️", "🫧", "🌙"],
        "images": [
            "A cozy corner with soft morning light, a warm drink, and an open journal on a wooden table.",
            "An overhead shot of a neatly arranged desk with plants, a candle, and a glass of water.",
            "A person walking barefoot on a wooden floor, sun streaming through sheer curtains.",
            "A simple breakfast spread with fresh fruit, warm tones, and natural textures.",
            "A quiet room with plants, diffused light, and a single chair facing a window.",
            "A flat lay of a planner, a coffee cup, and small everyday items on a linen surface.",
            "Someone sitting outside in the early morning with a blanket and a warm drink.",
        ],
    },

    "Hobbies": {
        "openers": [
            "There's a reason some activities feel completely different from the rest.",
            "The things you do for no reason other than enjoyment say a lot about you.",
            "Some of the best parts of the week have nothing to do with work.",
            "Side projects and passions have a way of becoming something more.",
            "The hobby you almost gave up might be the one most worth keeping.",
            "Not everything needs a purpose to be worth your time.",
            "Getting genuinely good at something just for the fun of it is underrated.",
            "The best creative energy often shows up when there's no pressure attached.",
            "There's a version of 'productive' that looks a lot like pure play.",
            "Some of the most interesting people you'll ever meet share niche interests.",
            "Doing something just because it lights you up is reason enough.",
            "The activities that make time disappear are worth paying attention to.",
        ],
        "bodies": [
            "Hobbies create space for a different version of yourself to show up.",
            "The things you do just for enjoyment are doing more for you than you realize.",
            "Getting absorbed in something you love is one of the most underrated forms of rest.",
            "Some of the most interesting conversations start with 'what are you into lately?'",
            "The skills you pick up casually often surprise you with where they lead.",
            "Doing something with your hands — or your mind — just for you changes your whole energy.",
            "Creative outlets don't have to be serious to be meaningful.",
            "The progress you make in a hobby you love tends to sneak up on you.",
            "There's no wrong way to spend time on something that genuinely makes you happy.",
            "Sharing what you're into with others is one of the fastest ways to find your people.",
        ],
        "closers": [
            "What hobby have you been meaning to get back into? 🎯",
            "Which activity makes time completely disappear for you?",
            "What's something you'd do every single day if you had the time? 💬",
            "When did you last try something creative just for the fun of it? 🤔",
            "What's a hobby you picked up recently that surprised you?",
            "Which skill are you quietly building right now?",
            "What's something you do that most people don't know about you?",
            "What would you spend a whole free weekend doing? 🎨",
        ],
        "hashtags_pool": [
            "#hobbies", "#creativetime", "#hobbylife", "#dowhatyoulove",
            "#passion", "#creativelife", "#learningsomethingNew", "#skillbuilding",
            "#funtime", "#sidehustle", "#craftlife", "#hobbyofthemonth",
            "#getcreatve", "#maketime", "#justforfun",
        ],
        "emojis_pool": ["🎨", "🎯", "🖌️", "📷", "🎸", "✂️", "🪴", "📚", "🎭", "🧩"],
        "images": [
            "Close-up of hands working on a craft project — thread, clay, or wood — in warm natural light.",
            "A flat lay of hobby tools and materials arranged neatly on a textured surface.",
            "Someone fully focused on a creative activity at a well-lit table, from behind.",
            "A bookshelf, instrument, or creative corner shot in golden hour light.",
            "A satisfying before-and-after shot of a completed project on a neutral background.",
            "A cozy creative space with supplies laid out and good lighting.",
            "Two people sharing a hobby activity at a table, laughing, from a side angle.",
        ],
    },

    "Career and Self Improvement": {
        "openers": [
            "The gap between where you are and where you want to be is mostly information.",
            "Most people know what to do — the tricky part is doing it consistently.",
            "The skills you build quietly tend to pay off loudly.",
            "Growth rarely feels dramatic while it's actually happening.",
            "There's a real difference between being busy and actually moving forward.",
            "The version of you a year from now reflects the choices you make this week.",
            "Most career breakthroughs start with an uncomfortable conversation or decision.",
            "The questions you ask matter just as much as the answers you give.",
            "Some of the most valuable things you can learn aren't taught in any classroom.",
            "Self-improvement works best when it's specific, not general.",
            "The clarity you're looking for often comes from doing, not just thinking.",
            "Reputation is built in the small moments, not the big ones.",
        ],
        "bodies": [
            "Small, consistent steps have a compounding effect most people underestimate.",
            "The clarity you're looking for often comes from doing, not just planning.",
            "Your career is shaped as much by how you show up as what you know.",
            "Self-improvement works best when it's tied to something specific you actually want.",
            "Most people overestimate what they can change in a week and underestimate a year.",
            "The feedback you avoid is usually the feedback that would help you most.",
            "Investing in how you think tends to improve everything else downstream.",
            "The habits that feel small right now are the ones compounding quietly.",
            "Getting clear on what you actually want is harder than it sounds — and more important.",
            "Most plateaus are really just signals that something needs to change.",
        ],
        "closers": [
            "What's one skill you've been meaning to develop for a while now? 💬",
            "What would it mean to feel genuinely ahead of where you are right now?",
            "Which area of your life could use the most intentional attention this month? 🤔",
            "What's one habit that's been harder to build than you expected?",
            "What would your work life look like if everything clicked into place?",
            "What's a piece of advice you'd give your past self about your career?",
            "What's one thing you'd change about how you spend your working hours? ✨",
            "What does growth actually feel like for you right now?",
        ],
        "hashtags_pool": [
            "#selfimprovement", "#careergrowth", "#personaldevelopment", "#mindset",
            "#growthmindset", "#learning", "#productivity", "#careeradvice",
            "#skillbuilding", "#worksmarter", "#levleup", "#professionalgrowth",
            "#habits", "#success", "#focus",
        ],
        "emojis_pool": ["📈", "💡", "🎯", "🧠", "📚", "🔑", "⚡", "🚀", "💼", "🪜"],
        "images": [
            "A person writing in a notebook at a clean, minimal desk with good lighting and a coffee cup.",
            "An overhead view of a planner open to a weekly spread, with a pen and a simple plant.",
            "Someone reviewing notes or a book at a quiet café table, from a side angle.",
            "A whiteboard or notebook with a clear, simple diagram — not branded, just structure.",
            "A calm workspace at the end of the day — one lamp on, organized desk.",
            "Two people in a focused conversation at a table, no phones visible.",
            "A close-up of a highlighted page in an open book with a coffee in the background.",
        ],
    },

    "Market and Finance": {
        "openers": [
            "The way most people think about money was never designed to help them.",
            "Financial literacy isn't about being wealthy — it's about having options.",
            "The market rewards patience in ways that are hard to see in the moment.",
            "Understanding money is a skill, and skills can always be learned.",
            "Most financial mistakes come from a lack of information, not bad intentions.",
            "The relationship between time and money is more interesting than most people realize.",
            "A lot of financial anxiety comes from uncertainty — and uncertainty can be reduced.",
            "Most people spend more time planning a vacation than planning their finances.",
            "The basics of personal finance aren't complicated — they're just not widely taught.",
            "Small financial decisions made consistently create outcomes that look like luck.",
            "How you think about money shapes how you use it — more than the numbers do.",
            "The best financial decisions are usually the boring ones made repeatedly.",
        ],
        "bodies": [
            "Small financial decisions repeated over time create the outcomes most people attribute to luck.",
            "The gap between knowing and doing is where most financial progress gets lost.",
            "Uncertainty in markets is the price of participation — there's no way to avoid it.",
            "The most useful financial knowledge isn't complicated, it's just not widely shared.",
            "Understanding where your money actually goes is the first step to changing it.",
            "Most people underestimate how much small, recurring costs add up over time.",
            "Building financial awareness is less about restriction and more about clarity.",
            "The habits that feel minor now are the ones that show up significantly later.",
            "Learning to distinguish between wants and needs sounds simple — in practice, it's not.",
            "A lot of financial confidence comes from just knowing the basics well.",
        ],
        "closers": [
            "What's one financial habit you're actively trying to build this year? 💬",
            "What's something about money you wish you'd understood earlier? 🤔",
            "What would change if you felt truly confident about your finances?",
            "Which financial concept do you think more people should understand? 📊",
            "What's the best money advice you've ever actually followed?",
            "What would it feel like to have a clear picture of your financial situation?",
            "What's one money move you keep putting off that you know you should make? 💡",
            "What does financial freedom actually mean to you?",
        ],
        "hashtags_pool": [
            "#personalfinance", "#financialliteracy", "#moneymatters", "#investing",
            "#financialfreedom", "#moneymindset", "#savings", "#wealthbuilding",
            "#financetips", "#stockmarket", "#moneymanagement", "#financialgoals",
            "#budget", "#passiveincome", "#invest",
        ],
        "emojis_pool": ["📊", "💰", "📈", "🏦", "💡", "🔑", "💵", "🧾", "⚖️", "🪙"],
        "images": [
            "A clean desk with a laptop showing a graph, a notebook, and a coffee — no branding visible.",
            "A person reviewing a printed chart or document at a table, from above.",
            "An aesthetic flat lay of a calculator, pen, and open notebook on a neutral background.",
            "A simple bar or line graph printed on paper, placed on a wooden table.",
            "Someone looking at a phone screen with charts, photographed from the side in good light.",
            "A tidy workspace with a single plant, a notebook, and a glass of water.",
            "Close-up of hands writing numbers in a notebook — warm, natural light.",
        ],
    },
}


# ── Post builder ──────────────────────────────────────────────────────────────

def build_post(category: str, used_openers: set, used_closers: set) -> tuple[str, str]:
    """
    Build one post from templates. Returns (post_text, image_suggestion).
    Avoids reusing openers and closers within the same run.
    """
    t = TEMPLATES[category]

    available_openers = [o for o in t["openers"] if o not in used_openers]
    available_closers = [c for c in t["closers"] if c not in used_closers]

    if not available_openers:
        available_openers = t["openers"]
    if not available_closers:
        available_closers = t["closers"]

    opener = random.choice(available_openers)
    closer = random.choice(available_closers)
    used_openers.add(opener)
    used_closers.add(closer)

    # Pick 1 or 2 body sentences
    body_count = random.randint(1, 2)
    bodies = random.sample(t["bodies"], min(body_count, len(t["bodies"])))

    # Emojis (1-3, placed at end of text before hashtags)
    emojis = " ".join(random.sample(t["emojis_pool"], random.randint(1, 3)))

    # Hashtags (3-5)
    hashtags = " ".join(random.sample(t["hashtags_pool"], random.randint(3, 5)))

    # Assemble
    sentences = [opener] + bodies + [closer]
    text = " ".join(sentences)
    post = f"{text} {emojis}\n{hashtags}"

    # Image suggestion
    image = random.choice(t["images"])

    return post, image


def generate_three_posts(category: str, url: str) -> tuple[list[str], list[str]]:
    """Generate 3 unique posts. URL goes in the first post only."""
    used_openers: set = set()
    used_closers: set = set()

    posts = []
    images = []

    for i in range(3):
        post, image = build_post(category, used_openers, used_closers)
        if i == 0:
            post = f"{post}\n{url}"
        posts.append(post)
        images.append(image)

    return posts, images


# ── Page name detection ───────────────────────────────────────────────────────

def detect_page_name(page) -> str | None:
    # Use window.location, not page.goto() — Playwright's own navigation bypasses
    # Multilogin's proxy-auth injection and fails with ERR_INVALID_AUTH_CREDENTIALS.
    js_navigate(page, "https://www.facebook.com/")
    time.sleep(3)

    # Scroll the left sidebar down so "Your shortcuts" / Pages section loads
    for _ in range(4):
        try:
            page.evaluate(
                "document.querySelector('[data-pagelet=\"LeftRail\"]')?.scrollBy(0, 300)"
            )
        except Exception:
            pass
        time.sleep(0.7)

    # Scan all sidebar links for a known category suffix in their text
    try:
        links = page.locator("[data-pagelet='LeftRail'] a").all()
        for link in links:
            text = (link.text_content() or "").strip()
            suffix, _ = extract_category(text)
            if suffix:
                return text
    except Exception:
        pass

    # Fallback: scan all links on the page
    try:
        links = page.locator("a").all()
        for link in links:
            text = (link.text_content() or "").strip()
            suffix, _ = extract_category(text)
            if suffix:
                return text
    except Exception:
        pass

    return None


def extract_category(text: str) -> tuple[str, str] | tuple[None, None]:
    tokens = re.split(r'[\s_\-/]+', text.upper())
    for token in reversed(tokens):
        clean = token.strip(".,!?()-")
        if clean in CATEGORY_MAP:
            return clean, CATEGORY_MAP[clean]
    return None, None


# ── Image generation ─────────────────────────────────────────────────────────

def generate_image(prompt: str, index: int) -> Path | None:
    IMAGES_DIR.mkdir(exist_ok=True)
    out = IMAGES_DIR / f"post_{index + 1}.jpg"

    encoded = urllib.parse.quote(prompt)
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1024&height=1024&nologo=true&model=flux"
    )
    print(f"  Generating image {index + 1}...", end=" ", flush=True)
    try:
        resp = requests.get(url, timeout=60)
        if resp.ok:
            out.write_bytes(resp.content)
            print(f"saved → {out.name}")
            return out
        else:
            print(f"failed ({resp.status_code})")
    except requests.RequestException as exc:
        print(f"error: {exc}")
    return None


# ── File writers ──────────────────────────────────────────────────────────────

def write_posts_txt(posts: list[str]) -> None:
    POSTS_PATH.write_text("\n\n".join(posts) + "\n", encoding="utf-8")
    print(f"posts.txt saved → {POSTS_PATH}")


def write_images_txt(images: list[str]) -> None:
    lines = [f"Post {i+1}: {img}" for i, img in enumerate(images)]
    IMAGES_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"image_suggestions.txt saved → {IMAGES_PATH}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate Facebook posts from templates.")
    parser.add_argument("--account",  required=True, help="Account name (must match mlx_profiles.json)")
    parser.add_argument("--url",      default=DEFAULT_URL, help="URL to include in the first post")
    parser.add_argument("--category", default=None,
                        help="Force category (LS, HOB, CSI, MF) — skips browser detection")
    args = parser.parse_args()

    # ── Resolve category ───────────────────────────────────────────────────
    if args.category:
        suffix = args.category.upper()
        if suffix not in CATEGORY_MAP:
            print(f"Error: unknown category '{suffix}'. Valid: {list(CATEGORY_MAP)}")
            sys.exit(1)
        category = CATEGORY_MAP[suffix]
        print(f"Category (manual): {category}")
    else:
        print(f"Opening Multilogin profile '{args.account}' to detect page category...")
        mlx, started = start_profile_for(args.account)

        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(started.cdp_url)
                context = browser.contexts[0]
                page = context.pages[0] if context.pages else context.new_page()
                page_name = detect_page_name(page)
        finally:
            mlx.stop_profile(started.profile_id)

        if not page_name:
            print(
                "Could not detect the page name automatically.\n"
                "Re-run with --category LS|HOB|CSI|MF to set it manually."
            )
            sys.exit(1)

        print(f"Detected page name: {page_name}")
        suffix, category = extract_category(page_name)

        if not category:
            print(
                f"No known suffix found in '{page_name}'.\n"
                f"Known suffixes: {list(CATEGORY_MAP)}\n"
                f"Re-run with --category LS|HOB|CSI|MF to override."
            )
            sys.exit(1)

        print(f"Category: {category} ({suffix})")

    # ── Generate and save ──────────────────────────────────────────────────
    print(f"\nGenerating 3 posts for: {category}...")
    posts, image_prompts = generate_three_posts(category, args.url)

    write_posts_txt(posts)
    write_images_txt(image_prompts)

    print("\nGenerating images...")
    image_paths = [generate_image(prompt, i) for i, prompt in enumerate(image_prompts)]
    saved = [p for p in image_paths if p]
    print(f"{len(saved)}/3 images saved to images/")

    print("\nDone. Run next:")
    print(f'  python3 post.py --account "{args.account}" --posts posts.txt')


if __name__ == "__main__":
    main()
