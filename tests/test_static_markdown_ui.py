from pathlib import Path


INDEX = Path(__file__).resolve().parents[1] / "mai" / "static" / "index.html"


def _html() -> str:
    return INDEX.read_text(encoding="utf-8")


def test_assistant_messages_use_markdown_renderer_but_user_messages_stay_plain_text() -> None:
    html = _html()
    assert "if(role==='bot')b.appendChild(renderMarkdown(text));else b.textContent=text" in html


def test_markdown_renderer_does_not_use_inner_html_or_external_cdn() -> None:
    html = _html()
    assert "innerHTML" not in html
    assert "<script src=" not in html
    assert "document.createTextNode" in html
    assert "document.createElement" in html


def test_markdown_renderer_supports_common_assistant_formatting() -> None:
    html = _html()
    for marker in (
        "node('strong')",
        "node('em')",
        "node('del')",
        "node('code'",
        "node('blockquote')",
        "node('hr')",
        "node(ul?'ul':'ol')",
        "const heading=line.match",
        "el.target='_blank'",
        "el.rel='noopener noreferrer'",
    ):
        assert marker in html


def test_markdown_styles_cover_headings_lists_code_and_quotes() -> None:
    html = _html()
    for selector in (
        ".markdown h1",
        ".markdown ul",
        ".markdown blockquote",
        ".markdown code",
        ".markdown pre",
        ".markdown a",
    ):
        assert selector in html
