"""A very small Markdown subset, for prose that lives in Python.

The API reference is written as Python strings in `hub/apidocs.py`, and prose
needs three things that plain text cannot carry: a code span for a field name, an
emphasis for the one clause a reader must not skim, and a paragraph break. Those
three, and nothing else.

The alternative was writing HTML directly into `apidocs.py`. That reads badly at
the source, which matters -- the point of keeping the reference in Python is that
a maintainer edits sentences, not markup -- and it puts the escaping burden on
whoever adds a line rather than in one tested place.

The alternative in the other direction was a Markdown library. That is a
dependency, a parser, and a large surface area for a page with four inline forms
on it, and every Markdown renderer's default answer to raw HTML in the input is
to pass it straight through. Which is the important property here:

**Escaping happens first, always.** The input is escaped, and only then are the
markers substituted. Nothing in a source string can produce a tag, so a value
that reaches this from anywhere less trustworthy than a source file still cannot
inject anything. That ordering is the whole security argument, and there is a
test for it.
"""

import re

from markupsafe import Markup, escape

#: `code`, **strong**, *emphasis*. Matched against escaped text, so the pattern
#: can only ever see the marker characters themselves.
_CODE = re.compile(r"`([^`]+)`")
_STRONG = re.compile(r"\*\*([^*]+)\*\*")
_EMPHASIS = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")

#: One or more blank lines separate paragraphs.
_BREAK = re.compile(r"\n\s*\n")


def inline(text: str) -> Markup:
    """Render the inline markers in one run of text."""
    rendered = str(escape(text))

    # Strong before emphasis: `**x**` would otherwise match the emphasis pattern
    # first and leave a stray asterisk on each side.
    rendered = _CODE.sub(r"<code>\1</code>", rendered)
    rendered = _STRONG.sub(r"<strong>\1</strong>", rendered)
    rendered = _EMPHASIS.sub(r"<em>\1</em>", rendered)

    return Markup(rendered)


def prose(text: str) -> Markup:
    """Render blank-line-separated paragraphs, each with its inline markers."""
    if not text:
        return Markup("")

    paragraphs = [chunk.strip() for chunk in _BREAK.split(text) if chunk.strip()]

    return Markup("").join(Markup("<p>%s</p>") % inline(chunk) for chunk in paragraphs)
