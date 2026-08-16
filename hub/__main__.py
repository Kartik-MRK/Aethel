"""`python -m hub` — start the Hub server.

A module entry point rather than a console script on purpose: it works from a
bare checkout with no `pip install`, which is the situation on a demo machine
that has just cloned the repository. It is also the command the CLI's
"is the Hub running?" error points at, so it has to exist and has to work.
"""

import sys

from hub.app import main

if __name__ == "__main__":
    try:
        main()
    except ImportError as exc:
        # A client-only install has no server stack. Name the command that
        # fixes it instead of printing a traceback about uvicorn.
        print(
            f"The Hub needs its server dependencies ({exc.name or exc}).\n"
            f"  Install them with: pip install -e '.[hub]'",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        # Ctrl-C is how the server is meant to be stopped; exit quietly.
        raise SystemExit(130) from None
