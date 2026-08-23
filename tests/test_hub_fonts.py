"""Tests for the self-hosted web fonts.

Every failure these catch is a *silent* one, which is the whole reason they
exist. A font stack ends in `sans-serif`, so a renamed file, a missing commit,
a stale preload or a font whose internal family name does not match the CSS all
produce a page that renders perfectly well in the wrong typeface. Nothing else
in the suite would notice, and neither would a screenshot taken on a machine
where the file is still cached.

The tests are written against the *served* stylesheet rather than the source
file, because what the browser gets is the thing that has to be true.

The ones that matter most:

* `TestServing` -- every URL the stylesheet asks for actually answers.
* `TestTheFontsMatchTheCss` -- the family names and weight ranges agree in both
  directions, so the CSS can never be quietly asking for a face that is not
  there and getting a synthesised imitation instead.
* `TestTheFeaturesTheDesignDependsOn` -- the slashed zero and the aligned
  figures are legibility requirements for a page made of hex, not preferences,
  and both live inside the binary where no code review can see them.
"""

import re
from pathlib import Path
from urllib.parse import urljoin

import pytest

FONTS_DIR = Path(__file__).resolve().parent.parent / "hub" / "static" / "fonts"

STYLESHEET = "/static/hub.css"

#: The two faces, and the token each is expected to head. Written out rather
#: than derived so that renaming a family has to be a deliberate edit here too.
EXPECTED = {
    "Public Sans": "--font",
    "Aethel Mono": "--mono",
}

#: woff2 files begin with this signature. Checked because a 404 page, an HTML
#: error, or a Git LFS pointer all return 200 with a plausible length.
WOFF2_MAGIC = b"wOF2"

_FACE_BLOCK = re.compile(r"@font-face\s*\{(?P<body>[^}]*)\}")
_DECLARATION = re.compile(r"([a-z-]+)\s*:\s*([^;]+)")
_URL = re.compile(r"""url\(\s*["']?(?P<href>[^"')]+)["']?\s*\)""")
_PRELOAD = re.compile(r"""<link[^>]*rel=["']preload["'][^>]*>""")
_HREF = re.compile(r"""href=["'](?P<href>[^"']+)["']""")


def stylesheet_text(client) -> str:
    response = client.get(STYLESHEET)

    assert response.status_code == 200, "the stylesheet itself is not being served"
    return response.text


def declared_faces(css: str) -> dict[str, dict[str, str]]:
    """Every `@font-face` in the stylesheet, keyed by family name."""
    faces: dict[str, dict[str, str]] = {}

    for block in _FACE_BLOCK.finditer(css):
        rules = {name: value.strip() for name, value in _DECLARATION.findall(block.group("body"))}
        family = rules.get("font-family", "").strip("\"'")
        if family:
            faces[family] = rules

    return faces


def face_url(rules: dict[str, str]) -> str:
    """The absolute path a face's `src` resolves to, as a browser would."""
    match = _URL.search(rules.get("src", ""))

    assert match, f"no url() in src: {rules.get('src')!r}"
    return urljoin(STYLESHEET, match.group("href"))


def weight_range(rules: dict[str, str]) -> tuple[int, int]:
    parts = rules.get("font-weight", "400").split()
    numbers = [int(part) for part in parts]

    return (numbers[0], numbers[-1])


@pytest.fixture
def faces(hub_client):
    found = declared_faces(stylesheet_text(hub_client))

    assert set(found) == set(EXPECTED), f"stylesheet declares {sorted(found)}"
    return found


def open_font(path: Path):
    """Open a built font, or skip if the [fonts] extra is not installed."""
    pytest.importorskip("fontTools", reason="reading a font needs the [fonts] extra")

    from fontTools.ttLib import TTFont

    return TTFont(path)


class TestServing:
    def test_every_face_the_stylesheet_declares_is_served(self, hub_client, faces):
        """The silent-fallback test. A missing file renders as sans-serif."""
        for family, rules in faces.items():
            response = hub_client.get(face_url(rules))

            assert response.status_code == 200, f"{family}: {face_url(rules)}"
            assert response.content.startswith(WOFF2_MAGIC), f"{family} is not woff2"

    def test_a_font_is_served_as_a_font(self, hub_client, faces):
        """`nosniff` is sent on every response, so a wrong type is not guessed
        past -- it is enforced, and the face is dropped."""
        for rules in faces.values():
            response = hub_client.get(face_url(rules))

            assert response.headers["content-type"] == "font/woff2"

    def test_the_pair_stays_small_enough_to_preload(self, hub_client, faces):
        """Preloading blocks nothing, but it does compete with the stylesheet
        for the first round trip. 60 KiB is roughly four times the current
        total and still cheap; passing it means the subset stopped subsetting."""
        total = sum(len(hub_client.get(face_url(r)).content) for r in faces.values())

        assert total < 60 * 1024, f"{total / 1024:.1f} KiB of font"


class TestPreloading:
    """A preload that names a file nobody uses is a wasted request that also
    looks correct. Both directions have to hold."""

    def preloaded(self, client) -> set[str]:
        page = client.get("/").text

        return {_HREF.search(tag).group("href") for tag in _PRELOAD.findall(page) if "font" in tag}

    def test_every_declared_face_is_preloaded(self, hub_client, faces):
        expected = {face_url(rules) for rules in faces.values()}

        assert self.preloaded(hub_client) >= expected

    def test_nothing_is_preloaded_that_the_stylesheet_never_asks_for(self, hub_client, faces):
        expected = {face_url(rules) for rules in faces.values()}

        assert self.preloaded(hub_client) <= expected


class TestTheFontsMatchTheCss:
    def test_each_family_heads_the_token_that_is_meant_to_use_it(self, hub_client):
        """A face nothing selects is dead weight that still downloads."""
        css = stylesheet_text(hub_client)

        for family, token in EXPECTED.items():
            declaration = re.search(rf"{token}\s*:\s*([^;]+)", css)

            assert declaration, f"{token} is not defined"
            assert declaration.group(1).strip().startswith(f'"{family}"')

    def test_the_file_reports_the_family_name_the_css_asks_for(self, faces):
        """The regression that produced 'Public Sans Thin'.

        A variable font's own family name describes its default instance, so a
        subset built without renaming reports a weight nobody asked for. The
        browser does not care -- `@font-face` overrides it -- but every font
        inspector, every DevTools pane and every future maintainer does.
        """
        for family, rules in faces.items():
            path = FONTS_DIR / Path(face_url(rules)).name

            assert open_font(path)["name"].getDebugName(1) == family

    def test_the_axis_covers_every_weight_the_css_declares(self, faces):
        """Asking for a weight outside the axis does not fail. The browser
        synthesises it by smearing the outlines, which looks like a font that
        was chosen badly rather than one that was built wrong."""
        for family, rules in faces.items():
            path = FONTS_DIR / Path(face_url(rules)).name
            axis = next(a for a in open_font(path)["fvar"].axes if a.axisTag == "wght")
            low, high = weight_range(rules)

            assert axis.minValue <= low and high <= axis.maxValue, family


class TestTheFeaturesTheDesignDependsOn:
    """Two OpenType features are load-bearing, and both live inside a binary
    where no diff will ever show them changing."""

    def substitution(self, font, feature: str, character: str) -> str | None:
        """The glyph `feature` substitutes `character` to, if it substitutes at all."""
        source = font.getBestCmap()[ord(character)]
        gsub = font["GSUB"].table

        for record in gsub.FeatureList.FeatureRecord:
            if record.FeatureTag != feature:
                continue
            for index in record.Feature.LookupListIndex:
                for subtable in gsub.LookupList.Lookup[index].SubTable:
                    target = getattr(subtable, "mapping", {}).get(source)
                    if target:
                        return target

        return None

    def ink(self, font, glyph: str) -> float:
        """The area a glyph's outline encloses -- a cheap fingerprint for 'is
        this actually a different drawing, or the same one under a new name'."""
        glyphs = font.getGlyphSet()

        from fontTools.pens.areaPen import AreaPen

        pen = AreaPen(glyphs)
        glyphs[glyph].draw(pen)

        return round(pen.value, 2)

    def test_the_mono_can_slash_its_zero(self, hub_client):
        """This page is read aloud off a projector. An unslashed zero in a
        64-character digest is indistinguishable from a capital O.

        Checking that the `zero` tag is present is not enough, and that is all
        the first version of this test did. A feature table outlives its target:
        the slashed glyph has no codepoint of its own, so a subset built from a
        list of codepoints can keep the rule and discard the drawing it points
        at. The font then advertises a slashed zero and renders the plain one.
        So follow the substitution to the end and confirm it lands on a glyph
        that both survived and is a different outline.
        """
        font = open_font(FONTS_DIR / "aethel-mono.woff2")
        slashed = self.substitution(font, "zero", "0")

        assert slashed, "the zero feature substitutes nothing"
        assert slashed in font.getGlyphOrder(), f"{slashed} was subset away"
        assert self.ink(font, slashed) != self.ink(font, "zero"), "the slash draws nothing"
        assert "slashed-zero" in stylesheet_text(hub_client)

    def test_the_sans_can_align_its_figures(self, hub_client):
        """Metric columns only line up if the digits share a width. Same trap as
        the slashed zero: the feature has to resolve, not merely exist."""
        font = open_font(FONTS_DIR / "public-sans.woff2")
        widths = font["hmtx"]
        tabular = {}

        for digit in "0123456789":
            target = self.substitution(font, "tnum", digit)

            assert target, f"tnum substitutes nothing for {digit}"
            assert target in font.getGlyphOrder(), f"{target} was subset away"
            tabular[digit] = widths[target][0]

        assert len(set(tabular.values())) == 1, f"tabular digits differ in width: {tabular}"
        assert "tabular-nums" in stylesheet_text(hub_client)

    def test_the_mono_draws_every_hexadecimal_character(self):
        """The face has exactly one job on this site."""
        cmap = open_font(FONTS_DIR / "aethel-mono.woff2").getBestCmap()
        missing = [c for c in "0123456789abcdefABCDEF" if ord(c) not in cmap]

        assert missing == []

    def test_the_sans_draws_an_author_name_with_diacritics(self):
        """Commit authors are user text. A subset font's most visible failure is
        falling back for one letter in the middle of a name."""
        cmap = open_font(FONTS_DIR / "public-sans.woff2").getBestCmap()
        missing = [c for c in "Zoë Škoda-Ünalç Łopez" if ord(c) not in cmap]

        assert missing == []


class TestLicensing:
    """Both faces are OFL. Shipping the binary without its licence text is a
    breach, and it is the kind that goes unnoticed for years."""

    def test_a_licence_ships_beside_every_font(self):
        for font in FONTS_DIR.glob("*.woff2"):
            licence = FONTS_DIR / f"{font.stem}-OFL.txt"

            assert licence.is_file(), f"{font.name} has no licence beside it"
            assert "SIL OPEN FONT LICENSE" in licence.read_text(encoding="utf-8")

    def test_the_renamed_face_keeps_its_original_copyright(self):
        """The rename is what the OFL requires, since 'Plex' is a Reserved Font
        Name. Rewriting the copyright notice while renaming would not be."""
        names = open_font(FONTS_DIR / "aethel-mono.woff2")["name"]

        assert "IBM" in names.getDebugName(0)
