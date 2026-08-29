"""Build the two web fonts the Hub serves from `hub/static/fonts/`.

Run this once; the output is committed. It exists so the fonts in the repository
are not mystery binaries -- anyone can re-run it and get byte-comparable files,
which is the same standard the rest of the project holds itself to.

    python scripts/build_fonts.py

Why self-host at all, when a CDN link is one line? Three reasons, in order of
how much they matter here:

1. **The page must render with no network.** The Hub is demonstrated on a LAN,
   sometimes on a phone hotspot, sometimes with campus wifi refusing to route
   between two laptops. A font that arrives over the internet is a font that
   might not arrive.
2. **The Content-Security-Policy has no external origins.** Every other asset
   is same-origin; a font from elsewhere would mean widening `font-src` and
   giving up the property that no third party can inject anything into this
   page.
3. **What was designed is what gets rendered.** A system-font stack renders as
   Cantarell on this machine and Segoe UI on the Windows demo machine -- two
   different sets of metrics, so the layout that was tuned on one is not the
   layout shown on the other.

**Why these two faces.** Both choices are legibility decisions, not taste ones.

*IBM Plex Mono* for every hash, hex string, and CID. This page is largely
64-character hex, read aloud and compared by eye, and Plex Mono distinguishes
the characters that hex confuses: with the `zero` feature enabled the zero is
slashed and cannot be an O, the `1` has a foot serif and cannot be an `l`, and
`b` and `6` are drawn differently enough to survive a projector.

*Public Sans* for everything else. It is a Libre Franklin derivative made for
US federal public-facing records: unmodulated strokes, wide apertures, and a
large x-height, all tuned for reading at small sizes on bad screens. A
transparency log is a public record, so the register is right as well as the
legibility.

**Both are subset and range-limited, and the sources are pinned by hash.** A
project whose entire argument is "pin the input, hash it, verify it" should not
then pull an unpinned binary off the internet at build time.

**The mono is renamed.** The OFL reserves the name "Plex" for IBM's own
releases, and a subset with a restricted weight axis is a modified version, so
shipping it as "IBM Plex Mono" would breach the licence. It goes out as "Aethel
Mono" with the original copyright, trademark and licence records left intact,
and both licence texts are committed beside the fonts. Public Sans declares no
reserved name, so it keeps its own.
"""

import hashlib
import sys
import urllib.request
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "hub" / "static" / "fonts"
CACHE_DIR = REPO / ".font-cache"

PUBLIC_SANS = "https://raw.githubusercontent.com/uswds/public-sans/develop"
PLEX = "https://raw.githubusercontent.com/IBM/plex/master/packages/plex-mono-variable/fonts/complete/ttf"

#: Pinned by digest for the same reason a base model is pinned by revision SHA:
#: a URL says where a file was, not what it is.
SOURCES = {
    "PublicSans-VF.ttf": (
        f"{PUBLIC_SANS}/fonts/variable/PublicSans%5Bwght%5D.ttf",
        "d75a7dc1a27eb9e336d5b33f55489d2ecb5621bf694d5c43b2415bce2ca830a8",
    ),
    "PublicSans-OFL.txt": (
        f"{PUBLIC_SANS}/OFL.txt",
        "157a9e77f7580246e97c769490e2e977ae94399f9d30f4556015c41fe8c28bac",
    ),
    "PlexMono-VF.ttf": (
        f"{PLEX}/IBM%20Plex%20Mono%20Var-Roman.ttf",
        "7e514ee3a359d82b2f572751dc2184f380a72160299c040b4c978a7093013743",
    ),
    "PlexMono-OFL.txt": (
        f"{PLEX}/license.txt",
        "91c25c350d3cac39da2736d74f7ba37ef648f5237a4e330a240615bc8d8c4360",
    ),
}


def _ranges(*pairs: tuple[int, int]) -> set[int]:
    return {code for low, high in pairs for code in range(low, high + 1)}


#: Commit messages, author names and repository names are user text, so the
#: body face has to cover more than the interface's own vocabulary: Latin-1 and
#: Latin Extended-A between them handle European names, which is where a subset
#: font most visibly fails by falling back mid-word.
TEXT_CODEPOINTS = _ranges(
    (0x0020, 0x007E),  # printable ASCII
    (0x00A0, 0x00FF),  # Latin-1 supplement: accents, and x for dimensions
    (0x0100, 0x017F),  # Latin Extended-A: Polish, Czech, Turkish, Baltic
) | {
    0x2013,  # en dash, numeric ranges
    0x2014,  # em dash
    0x2018,
    0x2019,  # single quotes, apostrophe
    0x201C,
    0x201D,  # double quotes
    0x2022,  # bullet
    0x2026,  # ellipsis, truncated digests
    0x2212,  # true minus, aligns with digits unlike hyphen
    0x2206,  # increment, weight deltas -- the operator, not Greek delta
}

#: The mono face only ever sets hashes, CIDs, addresses, paths and numbers.
#: Accented letters never reach it, so it stays at ASCII plus the handful of
#: marks that appear inside identifiers and byte counts. It carries the arrow
#: and the fixed-width spaces because Public Sans has neither, which is the
#: reason lineage arrows on the page are drawn rather than typed.
CODE_CODEPOINTS = _ranges((0x0020, 0x007E)) | {
    0x00A0,
    0x00D7,  # multiplication sign, tensor shapes
    0x2013,
    0x2014,
    0x2026,
    0x2192,  # right arrow, parent -> child
    0x2212,
    0x2206,
    0x2009,  # thin space
    0x202F,  # narrow no-break space
}

#: Characters the source font genuinely does not draw. Declared so the coverage
#: check below reports only surprises -- a warning that fires on every clean
#: build is a warning everybody learns to scroll past. All four are archaic
#: (Dutch IJ/ij ligatures, the pre-1900 Afrikaans 'n, and long s) and none can
#: appear in a commit message, an author name or a repository name.
KNOWN_ABSENT = {0x0132, 0x0133, 0x0149, 0x017F}

#: Everything not listed is dropped. `tnum` earns its place: metric columns
#: only line up if the digits are the same width. `zero` is what makes the
#: slashed zero available to the CSS.
TEXT_FEATURES = ["ccmp", "kern", "liga", "locl", "mark", "mkmk", "tnum"]
CODE_FEATURES = ["ccmp", "locl", "mark", "rvrn", "zero"]

#: Restricting the axis to the weights actually used drops the interpolation
#: deltas for the rest, which is most of the saving. Nothing on the page is
#: lighter than 400 or heavier than 700.
TEXT_WEIGHTS = (400, 700)
CODE_WEIGHTS = (400, 600)

#: nameID 0 (copyright), 7 (trademark) and 13/14 (licence) are deliberately
#: absent: those records describe the original and must not be rewritten.
RENAMEABLE_NAME_IDS = (1, 3, 4, 6, 16, 17, 18, 20, 21, 22, 25)


def fetch(name: str) -> Path:
    """Download a pinned source into the cache, or reuse a matching copy."""
    url, expected = SOURCES[name]
    path = CACHE_DIR / name

    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected:
        return path

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  fetching {name}")
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - pinned https
        payload = response.read()

    digest = hashlib.sha256(payload).hexdigest()
    if digest != expected:
        raise SystemExit(f"{name}: expected sha256 {expected}, got {digest}")

    path.write_bytes(payload)
    return path


def rename_family(font: TTFont, family: str, style: str = "Regular") -> None:
    table = font["name"]
    for record in list(table.names):
        if record.nameID not in RENAMEABLE_NAME_IDS:
            continue
        table.removeNames(record.nameID, record.platformID, record.platEncID, record.langID)

    postscript = f"{family.replace(' ', '')}-{style}"
    for name_id, value in (
        (1, family),
        (2, style),
        (3, f"{family} {style}; Aethel subset"),
        (4, f"{family} {style}"),
        (6, postscript),
        (16, family),
        (17, style),
    ):
        table.setName(value, name_id, 3, 1, 0x409)
        table.setName(value, name_id, 1, 0, 0)


def build(
    source: Path,
    target: Path,
    *,
    codepoints: set[int],
    features: list[str],
    weights: tuple[int, int],
    family: str,
) -> None:
    font = TTFont(source)

    options = subset.Options()
    options.layout_features = features
    options.name_IDs = ["*"]  # kept so the licence records survive
    options.name_legacy = True
    options.notdef_outline = True
    options.recalc_bounds = True
    options.glyph_names = False
    options.hinting = True  # ClearType uses it; the demo machine is Windows
    options.drop_tables += ["DSIG"]

    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=codepoints)
    subsetter.subset(font)

    # Range-limit after subsetting, not before. Instancing a full font first
    # leaves glyphs with no `gvar` entry -- the deltas collapse to nothing when
    # a glyph does not move across the surviving range -- and the subsetter then
    # fails looking them up. Discarding the glyphs first sidesteps it entirely,
    # and the output is byte-identical either way.
    instancer.instantiateVariableFont(font, {"wght": weights}, inplace=True, updateFontNames=False)

    # Always rewrite the family name. A variable font's nameID 1 describes its
    # default instance, which for both of these sources is a weight nobody asked
    # for -- Public Sans ships as "Public Sans Thin" -- and a font that reports a
    # family name the CSS never mentions is a debugging trap waiting to happen.
    rename_family(font, family)

    target.parent.mkdir(parents=True, exist_ok=True)

    # Keep the source's `head.modified` instead of stamping now. Without this the
    # output changes every run, and a build that cannot reproduce itself cannot
    # be checked -- which is the entire reason this script is committed.
    font.recalcTimestamp = False

    font.flavor = "woff2"
    font.save(target)

    missing = sorted(set(codepoints) - set(TTFont(target).getBestCmap()) - KNOWN_ABSENT)
    if missing:
        # Not fatal: a source font is allowed to lack a character. It is fatal to
        # not know, because the page then falls back mid-word to whatever the
        # machine has, which is exactly the failure self-hosting is meant to end.
        listing = ", ".join(f"U+{code:04X}" for code in missing[:12])
        print(f"  ! {target.name} has no glyph for: {listing}")

    kib = target.stat().st_size / 1024
    print(f"  {target.name}: {kib:.1f} KiB, {len(font.getGlyphOrder())} glyphs, {family!r}")


def main() -> int:
    print("sources")
    sans_src = fetch("PublicSans-VF.ttf")
    mono_src = fetch("PlexMono-VF.ttf")
    sans_ofl = fetch("PublicSans-OFL.txt")
    mono_ofl = fetch("PlexMono-OFL.txt")

    print("building")
    build(
        sans_src,
        OUT_DIR / "public-sans.woff2",
        codepoints=TEXT_CODEPOINTS,
        features=TEXT_FEATURES,
        weights=TEXT_WEIGHTS,
        family="Public Sans",
    )
    build(
        mono_src,
        OUT_DIR / "aethel-mono.woff2",
        codepoints=CODE_CODEPOINTS,
        features=CODE_FEATURES,
        weights=CODE_WEIGHTS,
        family="Aethel Mono",
    )

    print("licences")
    OUT_DIR.joinpath("public-sans-OFL.txt").write_bytes(sans_ofl.read_bytes())
    OUT_DIR.joinpath("aethel-mono-OFL.txt").write_bytes(mono_ofl.read_bytes())
    OUT_DIR.joinpath("NOTICE.md").write_text(NOTICE, encoding="utf-8")
    print("  public-sans-OFL.txt, aethel-mono-OFL.txt, NOTICE.md")

    return 0


NOTICE = """# Fonts

Both faces are third-party, licensed under the SIL Open Font License 1.1, and
subset for this project by `scripts/build_fonts.py`. The licence texts are
beside this file.

## public-sans.woff2

Public Sans, from the United States Web Design System.
<https://github.com/uswds/public-sans>, Copyright 2015 The Public Sans Project
Authors. Licence: `public-sans-OFL.txt`.

Subset to Latin-1 and Latin Extended-A, weight axis limited to 400-700, and all
OpenType features dropped except `ccmp`, `kern`, `liga`, `locl`, `mark`, `mkmk`
and `tnum`. Public Sans declares no Reserved Font Name, so the subset keeps the
original family name.

## aethel-mono.woff2

Derived from IBM Plex Mono. <https://github.com/IBM/plex>, Copyright 2017 IBM
Corp. with Reserved Font Name "Plex". Licence: `aethel-mono-OFL.txt`.

Subset to ASCII plus a few identifier and numeric marks, weight axis limited to
400-600, and all OpenType features dropped except `ccmp`, `locl`, `mark`, `rvrn`
and `zero`.

**Renamed.** The OFL reserves "Plex" for IBM's own releases, and a subset with a
restricted axis is a modified version, so this file cannot ship under the
original name. The copyright, trademark and licence records inside the font are
unchanged; only the family and style names differ.
"""


if __name__ == "__main__":
    sys.exit(main())
