"""Small, pure Telegram HTML primitives; escape data before adding trusted tags."""

from html import escape, unescape
from html.parser import HTMLParser


def esc(value) -> str:
    """Escape a text node, preserving quotes and literal Markdown punctuation."""
    return escape(str(value), quote=False)


def b(value) -> str:
    """Bold a plain value (never pass already formatted HTML)."""
    return f'<b>{esc(value)}</b>'


def i(value) -> str:
    """Italicize a plain note."""
    return f'<i>{esc(value)}</i>'


def quote(value) -> str:
    """Quote raw input without interpreting its contents as markup."""
    return f'<blockquote>{esc(value)}</blockquote>'


def fold(value) -> str:
    """Keep long details together in an expandable quote."""
    return f'<blockquote expandable>{esc(value)}</blockquote>'


class _VisibleText(HTMLParser):
    """Collect text before unescaping, so literal user tags remain visible."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_entityref(self, name):
        self.parts.append(f'&{name};')

    def handle_charref(self, name):
        self.parts.append(f'&#{name};')


def to_plain(html_text: str) -> str:
    """Return exactly the visible text, without adding whitespace around tags."""
    parser = _VisibleText()
    parser.feed(html_text)
    parser.close()
    return unescape(''.join(parser.parts))


def visible_len(html_text: str) -> int:
    """Telegram's text budget excludes tags and counts decoded entities once."""
    return len(to_plain(html_text))
