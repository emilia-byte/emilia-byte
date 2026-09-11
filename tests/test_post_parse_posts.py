"""
Unit tests for post.parse_posts() -- the blank-line-delimited posts.txt
parser, including its URL-first reordering and standalone-URL-block merge
logic. No browser/Playwright involved.
"""

from post import parse_posts


def _write(tmp_path, content):
    path = tmp_path / "posts.txt"
    path.write_text(content, encoding="utf-8")
    return path


def test_url_inline_with_its_own_block_stays_first(tmp_path):
    path = _write(
        tmp_path,
        "Check this out\nhttps://example.com\n\nSecond post\n\nThird post",
    )
    posts = parse_posts(path)

    assert [p["text"] for p in posts] == ["Check this out", "Second post", "Third post"]
    assert posts[0]["url"] == "https://example.com"
    assert posts[1]["url"] is None
    assert posts[2]["url"] is None


def test_url_post_is_reordered_to_front(tmp_path):
    """The URL post is always posted first, regardless of its position in the file."""
    path = _write(
        tmp_path,
        "First post\n\nSecond post\nhttps://x.com\n\nThird post",
    )
    posts = parse_posts(path)

    assert posts[0]["text"] == "Second post"
    assert posts[0]["url"] == "https://x.com"
    assert [p["text"] for p in posts[1:]] == ["First post", "Third post"]


def test_standalone_url_block_merges_into_following_text_block(tmp_path):
    path = _write(
        tmp_path,
        "https://y.com\n\nOnly text here\n\nAnother text",
    )
    posts = parse_posts(path)

    assert len(posts) == 2
    assert posts[0] == {"text": "Only text here", "url": "https://y.com"}
    assert posts[1] == {"text": "Another text", "url": None}


def test_standalone_url_block_merges_into_preceding_text_block_when_no_next_block(tmp_path):
    path = _write(tmp_path, "Text one\n\nhttps://z.com")
    posts = parse_posts(path)

    assert posts == [{"text": "Text one", "url": "https://z.com"}]


def test_no_url_posts_preserve_original_order(tmp_path):
    path = _write(tmp_path, "Alpha\n\nBeta\n\nGamma")
    posts = parse_posts(path)

    assert [p["text"] for p in posts] == ["Alpha", "Beta", "Gamma"]
    assert all(p["url"] is None for p in posts)


def test_blank_blocks_are_dropped(tmp_path):
    path = _write(tmp_path, "\n\n\nAlpha\n\n\n\nBeta\n\n")
    posts = parse_posts(path)

    assert [p["text"] for p in posts] == ["Alpha", "Beta"]


def test_multiline_body_text_is_preserved(tmp_path):
    path = _write(tmp_path, "Line one\nLine two\nLine three")
    posts = parse_posts(path)

    assert posts[0]["text"] == "Line one\nLine two\nLine three"
