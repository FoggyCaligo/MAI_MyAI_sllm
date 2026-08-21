from pathlib import Path


def test_static_ui_uses_mk4_style_thinking_loader() -> None:
    html = (Path(__file__).parents[1] / "mai" / "static" / "index.html").read_text(encoding="utf-8")

    assert "thinking-loader" in html
    assert "thinking-bounce" in html
    assert "function thinkingBubble()" in html
    assert "const loading=thinkingBubble()" in html
    assert "bubble('bot','생각 중...')" not in html
    assert ".loading{opacity:.6" not in html
