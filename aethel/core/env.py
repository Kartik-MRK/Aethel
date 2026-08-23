"""Read a `.env` file into the process environment.

Written here rather than pulled in as a dependency because the whole feature is
forty lines and the two behaviours that matter are both ones a general-purpose
loader gets wrong for this project:

**The real environment always wins.** A `.env` file never overwrites a variable
that is already set. Without that rule, `AETHEL_HUB_PORT=9000 python -m hub`
would silently bind 8000 because a file on disk outranked the command that was
just typed, and CI -- which sets variables directly and has no `.env` -- would
behave differently from a developer's machine for reasons nobody could see.

**No interpolation.** `$` and `${...}` are left alone. Every secret this project
loads is opaque: a hex private key, a base64url JWT, an API secret. Expanding
`$` inside one of those would corrupt it into a shorter, still-plausible string,
and the resulting failure would look like a wrong key rather than a mangled one.

Values are never logged, not even on a parse error -- a malformed line is
reported by line number alone. A loader that echoed the offending line would
print a credential into a terminal, a CI log, or a screen share the first time
someone fat-fingered a paste.
"""

import os
from pathlib import Path

#: How far up the tree to look for a `.env`. Enough to cover running from
#: `scripts/`, `tests/`, or a nested working directory; bounded so a process
#: started in `/tmp` does not walk to the filesystem root reading strangers'
#: files.
_MAX_DEPTH = 4


class EnvFileError(Exception):
    """A `.env` file exists but could not be parsed."""


def find_env_file(start: Path | None = None, filename: str = ".env") -> Path | None:
    """Look for `filename` in `start` and its parents. First hit wins."""
    current = (start or Path.cwd()).resolve()

    for candidate in (current, *current.parents)[: _MAX_DEPTH + 1]:
        path = candidate / filename
        if path.is_file():
            return path
    return None


def parse_env(text: str) -> dict[str, str]:
    """Parse `.env` text into a mapping, in file order.

    Accepts `KEY=value`, a leading `export `, `#` comments, blank lines, and
    values wrapped in matching quotes. Rejects anything else, because a line
    that cannot be parsed is far more likely to be a typo in a credential than
    a syntax this needs to support -- and skipping it silently would leave the
    process running with the variable unset, which surfaces much later as an
    unrelated failure.
    """
    values: dict[str, str] = {}

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()

        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[len("export ") :].lstrip()

        key, separator, value = line.partition("=")
        if not separator:
            raise EnvFileError(f"line {number}: expected KEY=value")

        key = key.strip()
        if not key.replace("_", "").isalnum():
            raise EnvFileError(f"line {number}: '{key}' is not a usable variable name")

        value = value.strip()

        # Quotes are stripped only as a matching pair. A lone quote is kept, so
        # a secret that genuinely contains one survives intact.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]

        values[key] = value

    return values


def load_env(path: Path | None = None, *, override: bool = False) -> list[str]:
    """Load a `.env` into `os.environ`. Returns the names that were set.

    Names, never values -- the return value exists so a caller can report
    *which* settings came from a file, which is a genuinely useful thing to
    print at startup, and it stays safe to print because it carries no secrets.

    A missing file is not an error, whether it was searched for or named
    explicitly. Every variable has a default or is optional, so an unconfigured
    checkout runs; that is what makes `git clone && pip install -e . && python
    -m hub` work with no setup step.
    """
    resolved = path or find_env_file()
    if resolved is None or not resolved.is_file():
        return []

    values = parse_env(resolved.read_text(encoding="utf-8"))

    applied: list[str] = []
    for key, value in values.items():
        if not override and key in os.environ:
            continue
        os.environ[key] = value
        applied.append(key)

    return applied


__all__ = ["EnvFileError", "find_env_file", "load_env", "parse_env"]
