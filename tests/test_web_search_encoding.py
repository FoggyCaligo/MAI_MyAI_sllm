from MK5.tools import web_search


def test_decode_page_bytes_uses_html_meta_charset_for_euc_kr() -> None:
    html = (
        '<html><head><meta charset="euc-kr">'
        '<title>대익보이차 공식몰</title></head>'
        '<body>7572(2401) 일루형향(2401)</body></html>'
    )

    decoded = web_search._decode_page_bytes(
        html.encode("euc-kr"),
        content_type="text/html",
    )

    assert "대익보이차 공식몰" in decoded
    assert "7572(2401)" in decoded
    assert "일루형향(2401)" in decoded
    assert "\ufffd" not in decoded


def test_decode_page_bytes_uses_http_charset_for_cp949_plain_text() -> None:
    text = "대익보이차 2401 배치"

    decoded = web_search._decode_page_bytes(
        text.encode("cp949"),
        content_type="text/plain; charset=cp949",
    )

    assert decoded == text


def test_decode_page_bytes_uses_legacy_http_equiv_meta_charset() -> None:
    html = (
        '<html><head><meta http-equiv="Content-Type" '
        'content="text/html; charset=euc-kr">'
        '<title>대익보이차</title></head></html>'
    )

    decoded = web_search._decode_page_bytes(
        html.encode("euc-kr"),
        content_type="text/html",
    )

    assert "대익보이차" in decoded
    assert "\ufffd" not in decoded


def test_decode_page_bytes_defaults_to_utf8_without_declared_charset() -> None:
    text = "UTF-8 한국어 페이지"

    decoded = web_search._decode_page_bytes(
        text.encode("utf-8"),
        content_type="text/html",
    )

    assert decoded == text
