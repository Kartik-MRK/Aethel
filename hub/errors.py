"""Error responses for the two kinds of client the Hub serves.

A Hub request comes from one of two places, and they want opposite things when
something goes wrong. `aethel push` wants a JSON body it can parse and print; a
browser wants a page it can read, in the site's own type, with a way back. One
handler cannot serve both, and the usual compromise -- content negotiation on
`Accept` -- guesses wrong exactly when it matters, because a `curl` with no
`Accept` header and a browser both end up in the same branch.

So the split is by path, which is not a guess: everything under `/api/` is the
machine surface and everything else is the dashboard. That boundary is already
how the routers are organised, and it is stable in a way a request header is not.

Two properties these handlers must not break:

*The status code survives.* A styled 404 that returns 200 is worse than an
unstyled one -- it tells every crawler, cache and monitor that a missing
repository is a perfectly good page. The template is cosmetic; the code is the
contract.

*The security headers survive.* For an `HTTPException` they come for free: the
handler runs inside the middleware stack, so its response is decorated on the way
out like any other. The 500 handler is the exception and it is not a small one --
Starlette installs it outermost, above every middleware the application added, so
its response never passes through ours. It asks `hub.security` for the same
header set directly. That is also why these pages extend the ordinary base layout
instead of being self-contained HTML strings: an error page with an inline style
attribute would be the one page on the site the policy blocks, and it would be
discovered by whoever hits it rather than by us.
"""

import http
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from hub.security import security_headers

#: What each status code means *here*, in the vocabulary of this system rather
#: than the RFC's. "Not Found" tells a reader nothing they did not already know
#: from arriving at the page; "no repository, commit or page at this address"
#: tells them which of the three things they were probably looking for.
#:
#: Em dashes are the real character, not `--`. These strings are rendered as page
#: prose and go to the template unfiltered, so whatever is written here is what a
#: reader sees -- the same convention `hub/apidocs.py` follows, for the same
#: reason. `--` belongs in comments and docstrings, which are read as source.
EXPLANATIONS = {
    404: (
        "Nothing at this address",
        "No repository, commit, or page here. A commit URL wants the full "
        "64-character hash, a truncated one will not resolve, because a prefix "
        "is not a name.",
    ),
    405: (
        "Wrong method for this address",
        "The address exists but does not answer this verb. The dashboard is "
        "read-only; writes go to the API under /api/v1.",
    ),
    500: (
        "The Hub failed to answer",
        "Something broke on this end, not yours. The details are in the server "
        "log; /ops reports which subsystem is unhealthy.",
    ),
}

#: Fallback for a code with no entry above. Kept deliberately vague: inventing a
#: specific explanation for a status we have not thought about is how an error
#: page ends up confidently misdescribing what happened.
GENERIC = ("This request could not be completed", "")


def _is_api(request: Request) -> bool:
    """Whether the caller is the machine surface rather than the dashboard.

    Path prefix, not the `Accept` header. `aethel push` sends no `Accept` and
    neither does `curl`, so negotiating on it would hand both of them an HTML
    page -- and the CLI's error reporting reads `detail` out of a JSON body.
    """
    return request.url.path.startswith("/api/")


def register_error_handlers(app: FastAPI, templates: Jinja2Templates) -> None:
    """Install the HTML/JSON error split on an app.

    The `HTTPException` handler is registered against Starlette's class rather
    than FastAPI's subclass, and that is the registration that matters: Starlette
    raises the base class from its own routing layer, so a 404 for an unmatched
    path never reaches a route handler at all. Handler lookup walks the
    exception's MRO, so the base-class registration also catches every
    `HTTPException` this application raises itself.
    """

    async def http_error(request: Request, exc: StarletteHTTPException):
        if _is_api(request):
            return JSONResponse(
                {"detail": exc.detail},
                status_code=exc.status_code,
                headers=getattr(exc, "headers", None),
            )
        return _page(request, templates, exc.status_code, exc.detail)

    async def unhandled_error(request: Request, exc: Exception):
        """Last resort, for an exception no route caught.

        `exc` is deliberately not shown. A traceback or an exception message on a
        public page leaks paths, versions and sometimes values from the request
        that produced it; the log already has all of it, with the stack.

        This handler is where Starlette re-raises after answering, which is what
        makes uvicorn log that stack -- so it stays where Starlette puts it, above
        the middleware, and pays for that by attaching the security headers by
        hand. Moving it inward to inherit them would buy a tidier handler and lose
        the traceback, and an error page that renders while the error goes
        unlogged is the worse trade.
        """
        if _is_api(request):
            response = JSONResponse({"detail": "Internal server error."}, status_code=500)
        else:
            response = _page(request, templates, 500, "")

        response.headers.update(
            security_headers(_nonce(request), over_tls=request.url.scheme == "https")
        )
        return response

    app.add_exception_handler(StarletteHTTPException, http_error)
    app.add_exception_handler(Exception, unhandled_error)


def _nonce(request: Request) -> str:
    """The response nonce, minted here if the request never reached middleware.

    Normally this is already set -- `SecurityHeadersMiddleware` puts it on the
    scope before anything downstream runs, and it survives an exception because
    the scope is the same object all the way up. The fallback exists because the
    template reads it unconditionally: if it were ever missing, rendering the
    error page would itself raise, inside the handler for a raise, which is the
    one place a second failure has nowhere left to go.
    """
    nonce = getattr(request.state, "csp_nonce", None)
    if not nonce:
        nonce = secrets.token_urlsafe(18)
        request.state.csp_nonce = nonce
    return nonce


def _specific(status: int, detail: str) -> str:
    """The detail, unless it is only the status code spelled out again.

    Starlette fills an unraised detail in from the status phrase, so a 404 for a
    path no route matched arrives here carrying the literal string "Not Found".
    Printed under a "Detail" heading below a page already titled "Nothing at this
    address", that is the same sentence three times -- and a page that repeats
    itself teaches the reader that its sections are not worth reading.

    So the phrase for this status is filtered out, and only a message the
    application actually wrote survives. `http.HTTPStatus` is what Starlette
    derives its default from, which is why the comparison is against that rather
    than a hand-copied list of phrases that would drift from it.
    """
    if not detail:
        return ""
    try:
        phrase = http.HTTPStatus(status).phrase
    except ValueError:
        return detail
    return "" if detail.strip().casefold() == phrase.casefold() else detail


def _page(request: Request, templates: Jinja2Templates, status: int, detail: str) -> HTMLResponse:
    """Render the error template, preserving the status code.

    `detail` is passed through as the specific thing that went wrong -- "No
    repository 'nope'." -- alongside the generic explanation of the code. It is a
    message this application wrote, not user input echoed back; Jinja escapes it
    regardless, which is what keeps a crafted path out of the markup.

    A 500's detail is dropped rather than shown, for the same reason its
    exception is: Starlette's default text for the code is "Internal Server
    Error", and a page whose heading and detail both say that twice reads as a
    system with nothing to say.
    """
    _nonce(request)
    heading, explanation = EXPLANATIONS.get(status, GENERIC)
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={
            "status": status,
            "heading": heading,
            "explanation": explanation,
            "detail": "" if status == 500 else _specific(status, detail),
        },
        status_code=status,
    )


__all__ = ["EXPLANATIONS", "register_error_handlers"]
