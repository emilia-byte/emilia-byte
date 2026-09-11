"""
Unit tests for generate_posts.py's pure logic: category-suffix extraction
and the template-based post builder. No browser/Playwright involved.
"""

import pytest

from generate_posts import CATEGORY_MAP, TEMPLATES, build_post, extract_category, generate_three_posts


# ── extract_category ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text,expected_suffix",
    [
        ("Buffalo Page LS", "LS"),
        ("some_page_HOB", "HOB"),
        ("Golden Hours Club CSI", "CSI"),
        ("Market Talk MF", "MF"),
        ("lowercase suffix ls", "LS"),          # case-insensitive
        ("Page Name - CSI.", "CSI"),            # trailing punctuation stripped
        ("Page/Name/HOB", "HOB"),               # slash-delimited
    ],
)
def test_extract_category_finds_known_suffix(text, expected_suffix):
    suffix, category = extract_category(text)
    assert suffix == expected_suffix
    assert category == CATEGORY_MAP[expected_suffix]


def test_extract_category_no_match_returns_none_pair():
    assert extract_category("No matching suffix here") == (None, None)


def test_extract_category_picks_last_matching_token():
    # "LS" appears as a token but "MF" is the trailing (real) suffix.
    assert extract_category("LS Page Name MF") == ("MF", CATEGORY_MAP["MF"])


# ── build_post / generate_three_posts ────────────────────────────────────

@pytest.mark.parametrize("category", list(TEMPLATES.keys()))
def test_build_post_returns_nonempty_text_and_known_image(category):
    post, image = build_post(category, used_openers=set(), used_closers=set())

    assert isinstance(post, str) and post.strip()
    assert image in TEMPLATES[category]["images"]


def test_build_post_avoids_reusing_opener_and_closer_within_a_run():
    t = TEMPLATES["Lifestyle"]
    used_openers = set(t["openers"][:-1])  # all but one opener already used
    used_closers = set()

    post, _ = build_post("Lifestyle", used_openers, used_closers)

    remaining_opener = t["openers"][-1]
    assert remaining_opener in post
    assert used_openers == set(t["openers"])  # the last opener got marked used too


@pytest.mark.parametrize("category", list(TEMPLATES.keys()))
def test_generate_three_posts_returns_three_posts_and_images(category):
    posts, images = generate_three_posts(category, "https://example.com")

    assert len(posts) == 3
    assert len(images) == 3


def test_generate_three_posts_puts_url_only_in_first_post():
    posts, _ = generate_three_posts("Hobbies", "https://example.com/page")

    assert posts[0].endswith("https://example.com/page")
    assert "https://example.com/page" not in posts[1]
    assert "https://example.com/page" not in posts[2]


def test_generate_three_posts_unknown_category_raises():
    with pytest.raises(KeyError):
        generate_three_posts("Not A Real Category", "https://example.com")
