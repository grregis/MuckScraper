from aggregator.html_safety import clean_html, plain_text_br, sanitize_html


def test_strips_everything_that_can_run():
    attacks = [
        "<p>hi</p><script>alert(1)</script>",
        '<img src="x" onerror="alert(1)">',
        '<a href="javascript:alert(1)">click</a>',
        '<iframe src="https://evil.example"></iframe>',
        '<form action="https://evil.example"><input name="pw"></form>',
        '<p onclick="steal()">text</p>',
        '<svg onload="alert(1)"></svg>',
        '<img src="data:text/html;base64,PHNjcmlwdD4=">',
        "<style>body{display:none}</style>",
    ]
    for raw in attacks:
        out = sanitize_html(raw).lower()
        for bad in ("<script", "onerror", "onclick", "onload", "javascript:", "<iframe", "<form", "<input", "data:", "<style", "<svg"):
            assert bad not in out, (raw, out)


def test_keeps_article_formatting():
    raw = ('<h2>Head</h2><p>One <strong>bold</strong> <a href="https://example.com/x" title="t">link</a></p>'
           '<ul><li>item</li></ul><blockquote>q</blockquote><img src="https://example.com/a.jpg" alt="a">')
    assert sanitize_html(raw) == raw


def test_clean_html_is_markup():
    assert str(clean_html("<p>ok</p><script>x</script>")) == "<p>ok</p>x"


def test_plain_text_br_escapes_and_keeps_lines():
    assert str(plain_text_br("A & B\n<script>x</script>")) == "A &amp; B<br>&lt;script&gt;x&lt;/script&gt;"
    assert str(plain_text_br(None)) == ""
