"""The API reference, as data.

Written by hand rather than generated from the OpenAPI schema, which is a
deliberate reversal of the obvious choice and worth explaining.

FastAPI does generate a schema, and `/openapi.json` still serves it. But the
handlers here return plain dicts and accept plain dicts, so the generated
document describes every response as "Successful Response" with no shape, every
request body as a bare `object`, and lists only the status codes FastAPI itself
raises -- 200 and 422. The codes that a client actually has to handle, 401 on a
token-protected Hub, 409 when a ref cannot move, 422 when the bytes do not match
the digest, appear nowhere in it. Rendering that schema would produce a page
that looks authoritative and answers nothing.

The alternative is to annotate every route with Pydantic response models until
the schema is honest. That is the right long-term fix and it is a large change to
a working, tested API, so it is not this task.

So the prose is written by hand, and the risk that comes with hand-written docs
-- drift -- is closed by a test instead: `tests/test_hub_apidocs.py` compares the
paths documented here against the routes the application actually serves, in both
directions. An endpoint added without a description fails CI, and so does a
description for an endpoint that no longer exists.

One consequence worth stating: this is the reference for the *wire protocol*. It
describes what a client sends and receives, not how the Hub is implemented, so
it stays true across any refactor that keeps the protocol.
"""

from dataclasses import dataclass, field

#: Where the value comes from. `path` and `query` render differently in the
#: template because a path parameter is part of the address and a query
#: parameter is optional decoration on it.
PATH = "path"
QUERY = "query"
HEADER = "header"
BODY = "body"


@dataclass(frozen=True)
class Param:
    name: str
    where: str
    note: str
    required: bool = True


@dataclass(frozen=True)
class Endpoint:
    method: str
    path: str
    purpose: str
    #: The paragraph a reader needs and cannot infer from the signature. Left
    #: empty where the purpose line genuinely says everything.
    detail: str = ""
    params: tuple[Param, ...] = ()
    returns: str = ""
    #: Status codes beyond the shared table, or ones whose meaning is specific
    #: to this endpoint.
    notable: tuple[tuple[str, str], ...] = ()
    example: str = ""
    #: True for the endpoints the push token gates.
    writes: bool = False


@dataclass(frozen=True)
class Group:
    title: str
    note: str
    endpoints: tuple[Endpoint, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Step:
    """One step of a push.

    These are numbered on the page, which everything else on this site avoids.
    It is earned here: the order is a constraint, not a presentation choice.
    Step 4 returns 409 if step 3 has not finished, and step 1 exists only to
    make step 3 smaller. A flat list of endpoints cannot say that.

    Method and path are separate fields rather than one `call` string so a step
    renders through the same route line as the reference entries below it. The
    page teaches that device once, and a reader can match a step against its full
    entry by shape instead of by re-reading the path.
    """

    title: str
    method: str
    path: str
    note: str


PUBLISHING = Group(
    title="Publishing",
    note=(
        "The write path, in the order a push uses it. Every endpoint here is "
        "gated by the push token when one is configured."
    ),
    endpoints=(
        Endpoint(
            method="POST",
            path="/api/v1/repos/{repo_name}/negotiate",
            purpose="Ask which objects the Hub is missing.",
            detail=(
                "Send the hashes the client holds; get back only the ones the Hub "
                "does not. This is what makes a push incremental, a second push of "
                "a branch that added one commit uploads one commit, not the whole "
                "history. Unknown object kinds and malformed hashes are dropped from "
                "the request rather than rejected, so a newer client talking to an "
                "older Hub degrades to uploading more than it needed to."
            ),
            params=(Param("repo_name", PATH, "Repository name. Created on first ref update."),),
            returns=(
                "`{repo, missing}` where `missing` maps each object kind to the "
                "hashes the Hub wants."
            ),
            example="""{
  "have": {
    "commits": ["1b855137d2897c3e…", "335ac4c8aeeaad6d…"],
    "blobs":   ["2813bce55a21473b…"]
  }
}""",
            writes=True,
        ),
        Endpoint(
            method="PUT",
            path="/api/v1/blobs/{blob_hash}",
            purpose="Upload adapter weights, addressed by their own hash.",
            detail=(
                "The body is the raw file. No multipart, no filename field: content "
                "addressing means the hash in the URL *is* the name, so a filename "
                "would be a second identifier that has to be ignored anyway.\n\n"
                "The Hub re-hashes what it receives and rejects a mismatch. A client "
                "therefore never has to trust that the Hub stored what was sent, and "
                "a truncated upload fails loudly instead of poisoning the store."
            ),
            params=(
                Param("blob_hash", PATH, "SHA-256 of the bytes being sent, lower-case hex."),
            ),
            returns=(
                "`{hash, status, bytes}`. `status` is `stored` or `already-present`, "
                "re-uploading a blob the Hub has is a no-op, not an error."
            ),
            notable=(
                ("413", "Body exceeds `AETHEL_HUB_MAX_BLOB` (64 MiB by default)."),
                ("422", "The received bytes do not hash to the digest in the URL."),
            ),
            writes=True,
        ),
        Endpoint(
            method="PUT",
            path="/api/v1/{kind}/{object_hash}",
            purpose="Upload a commit, tree, or base reference.",
            detail=(
                "Same contract as a blob upload, for the three structured object "
                "kinds. The body is the object's canonical JSON, canonical because "
                "the hash is taken over those exact bytes, so re-serialising with "
                "different key order or spacing would produce a different digest and "
                "be rejected."
            ),
            params=(
                Param("kind", PATH, "One of `commits`, `trees`, `bases`."),
                Param("object_hash", PATH, "SHA-256 of the canonical JSON body."),
            ),
            returns="`{hash, status}`, with the same `already-present` shortcut.",
            notable=(("404", "Unknown object kind."),),
            writes=True,
        ),
        Endpoint(
            method="POST",
            path="/api/v1/repos/{repo_name}/refs",
            purpose="Advance a branch and append its history to the log.",
            detail=(
                "The last step of a push, and the only one that changes what the Hub "
                "publishes. The ref moves only after every object reachable from the "
                "commit is present, so a published branch can never point at "
                "something a client cannot fetch.\n\n"
                "Ancestors are appended oldest-first, so log order matches commit "
                "order. The append is idempotent: re-pushing a branch adds only the "
                "genuinely new commits and leaves the root unchanged when there are "
                "none. That is why the response separates the two, `logged_indices` "
                "is where the whole history sits, `appended_indices` only what this "
                "call created."
            ),
            params=(Param("repo_name", PATH, "Repository name."),),
            returns=(
                "`{repo, branch, commit, logged_indices, appended_indices, log_size, "
                "root, branches}`."
            ),
            notable=(
                ("409", "The commit, or one of its ancestors, is not in the store yet."),
            ),
            example="""{
  "repo": "imdb-sentiment",
  "branch": "main",
  "commit": "1b855137d2897c3e…",
  "logged_indices": [3, 4, 5],
  "appended_indices": [5],
  "log_size": 9,
  "root": "a0519b9817e888ab…",
  "branches": {"main": "1b855137d2897c3e…"}
}""",
            writes=True,
        ),
    ),
)


READING = Group(
    title="Reading",
    note="Open to everyone, always. Nothing here depends on a token.",
    endpoints=(
        Endpoint(
            method="GET",
            path="/api/v1/repos",
            purpose="Every repository the Hub knows about.",
            returns="`{repos: [{name, branches, created_at, updated_at}]}`.",
        ),
        Endpoint(
            method="GET",
            path="/api/v1/repos/{repo_name}",
            purpose="One repository and its branch tips.",
            params=(Param("repo_name", PATH, "Repository name."),),
            returns="`{name, branches, created_at, updated_at}`.",
        ),
        Endpoint(
            method="GET",
            path="/api/v1/repos/{repo_name}/commits",
            purpose="Commit history, newest first.",
            detail=(
                "Walks every branch tip unless one is named, then merges and "
                "de-duplicates. Two branches that share ancestors are reported as the "
                "distinct commits they are, not as the sum of their lengths."
            ),
            params=(
                Param("repo_name", PATH, "Repository name."),
                Param("branch", QUERY, "Restrict to one branch.", required=False),
                Param("limit", QUERY, "1–500, default 50.", required=False),
            ),
            returns="`{repo, commits}` sorted by timestamp, newest first.",
            notable=(("404", "No such repository, or no such branch."),),
        ),
        Endpoint(
            method="GET",
            path="/api/v1/commits/{commit_hash}",
            purpose="One commit record.",
            detail=(
                "The full record: message, author, parent, tree, adapter blob, base "
                "reference, and the training info including metrics, seed, dataset and "
                "pinned base revision. This is the object the transparency log's "
                "leaves are computed from."
            ),
            params=(Param("commit_hash", PATH, "Commit hash, 64 hex characters."),),
            returns="`{hash, message, author, timestamp, parent_hash, tree, adapter_blob, base, training_info}`.",
        ),
        Endpoint(
            method="GET",
            path="/api/v1/trees/{tree_hash}",
            purpose="A commit's file manifest.",
            params=(Param("tree_hash", PATH, "Tree hash."),),
            returns="`{hash, files}` mapping each filename to its blob hash.",
        ),
        Endpoint(
            method="GET",
            path="/api/v1/bases/{base_hash}",
            purpose="The base model a commit was trained against.",
            detail=(
                "Base weights are never stored here, a version is a patch plus a "
                "pinned reference. This object is that reference: model id, immutable "
                "revision SHA, architecture, and the hashes of the config and "
                "tokenizer as they were fetched. It is the reproducibility contract."
            ),
            params=(Param("base_hash", PATH, "Base reference hash."),),
            returns="`{hash, model_id, revision_sha, architecture, …}`.",
        ),
        Endpoint(
            method="GET",
            path="/api/v1/blobs/{blob_hash}",
            purpose="Download adapter weights.",
            detail=(
                "Returns the raw file with `X-Aethel-Blob-Sha256` set to the digest "
                "requested. **Re-hash what you receive and compare.** That single "
                "check is what lets any transport (this Hub, an IPFS gateway, a USB "
                "stick) be untrusted without risking integrity, and it is the reason "
                "nothing in this system needs to trust the server it downloaded from."
            ),
            params=(Param("blob_hash", PATH, "Blob hash."),),
            returns="`application/octet-stream`, streamed.",
        ),
    ),
)


LOG = Group(
    title="Transparency log",
    note=(
        "An append-only Merkle log over commit hashes. Its root is what gets "
        "anchored on chain, which is what makes published history tamper-evident."
    ),
    endpoints=(
        Endpoint(
            method="GET",
            path="/api/v1/log",
            purpose="Log size, current root, and every entry.",
            returns=(
                "`{size, root, entries, last_anchor, current_root_anchored}`. "
                "`current_root_anchored` is false whenever commits have been accepted "
                "since the last anchor, a normal state, not a fault."
            ),
        ),
        Endpoint(
            method="GET",
            path="/api/v1/log/proof/{commit_hash}",
            purpose="An inclusion proof for one commit.",
            detail=(
                "The response is self-contained: leaf index, sibling path, root, and "
                "log size. A verifier recomputes the root from those alone and never "
                "calls back here. That independence is the whole point; it is what "
                "makes a dishonest Hub unable to fake inclusion, because forging a "
                "proof would mean finding a SHA-256 collision."
            ),
            params=(Param("commit_hash", PATH, "Commit hash."),),
            returns="`{commit_hash, leaf_index, log_size, root, proof}`.",
            notable=(("404", "The commit is not in the log; it was never pushed."),),
            example="""{
  "commit_hash": "1b855137d2897c3e…",
  "leaf_index": 7,
  "log_size": 9,
  "root": "a0519b9817e888ab…",
  "proof": [
    {"sibling": "d9b2e0b7d7112cc9…", "side": "left"},
    {"sibling": "34a35c9a63d1dae3…", "side": "left"},
    {"sibling": "dbf6002e8f5371a5…", "side": "left"},
    {"sibling": "ac1420fdeb29bd8c…", "side": "right"}
  ]
}""",
        ),
        Endpoint(
            method="POST",
            path="/api/v1/log/verify",
            purpose="Check a proof server-side.",
            detail=(
                "A convenience and a test hook, never the authoritative check. Asking "
                "the Hub whether the Hub is honest proves nothing. The verification "
                "that counts runs in the browser, or in any client, against the root "
                "published on chain."
            ),
            returns="`{valid, commit_hash, root}`.",
        ),
    ),
)


OPERATIONS = Group(
    title="Operations",
    note="What the ops board reads. Useful for monitoring, and for knowing what is deployed.",
    endpoints=(
        Endpoint(
            method="GET",
            path="/api/v1/health",
            purpose="Per-subsystem health.",
            detail=(
                "Every check is wrapped so one dead dependency degrades its own row "
                "rather than failing the page, a status endpoint that cannot answer "
                "during an incident is useless precisely when it is needed. The "
                "overall verdict is the worst single row.\n\n"
                "The row worth watching is **Log vs anchored root**: it recomputes the "
                "root from the log on disk and compares it against the root published "
                "on chain. A mismatch means the log has been altered since it was "
                "anchored."
            ),
            returns="`{status, checks, counts}` where `status` is `good`, `warning`, or `critical`.",
            notable=(("503", "Returned instead of 200 when any row is critical."),),
        ),
        Endpoint(
            method="GET",
            path="/api/v1/version",
            purpose="What is actually running.",
            detail="So a stale process is visible rather than guessed at.",
            returns="`{hub, git_sha, started_at, data_dir, chain_configured, auth_required}`.",
        ),
    ),
)


GROUPS = (PUBLISHING, READING, LOG, OPERATIONS)


#: The four facts a client author needs before reading a single endpoint.
CONVENTIONS = (
    (
        "Base path",
        "`/api/v1`",
        "The version is in the path, so a breaking change can ship beside the old "
        "shape instead of replacing it under a client's feet.",
    ),
    (
        "Content addressing",
        "the hash is the name",
        "Every object is stored under the SHA-256 of its own bytes. There are no "
        "server-assigned ids, so the same object uploaded twice is the same object, "
        "and a client can compute an address without asking.",
    ),
    (
        "Authentication",
        "`Authorization: Bearer <token>`",
        "Required on the four publishing endpoints, and only when the Hub was "
        "started with a token configured. Reads are always open, a transparency "
        "log that needs a credential to read is not transparent.",
    ),
    (
        "Encoding",
        "JSON, except blob bodies",
        "Requests and responses are JSON. A blob upload sends raw bytes and a blob "
        "download returns them; wrapping several megabytes of weights in base64 "
        "would cost a third of the transfer for nothing.",
    ),
)


#: A push, in the order it happens.
PUSH_SEQUENCE = (
    Step(
        title="Ask what is missing",
        method="POST",
        path="/api/v1/repos/{repo_name}/negotiate",
        note=(
            "The client sends every hash it holds. The Hub replies with the subset it "
            "lacks. Skipping this step is legal and simply uploads more."
        ),
    ),
    Step(
        title="Upload the weights",
        method="PUT",
        path="/api/v1/blobs/{blob_hash}",
        note=(
            "One request per missing blob, raw bytes, verified against the digest in "
            "the URL. These are the large transfers; everything else is kilobytes."
        ),
    ),
    Step(
        title="Upload the metadata",
        method="PUT",
        path="/api/v1/{kind}/{object_hash}",
        note=(
            "Bases, then trees, then commits, referents before referrers, so the "
            "store never holds an object that points at nothing."
        ),
    ),
    Step(
        title="Move the branch",
        method="POST",
        path="/api/v1/repos/{repo_name}/refs",
        note=(
            "The only step that changes what the Hub publishes. It fails with 409 "
            "unless every object reachable from the commit already arrived, which is "
            "what makes an interrupted push harmless: nothing is visible until this "
            "call succeeds, and re-running the whole sequence resumes it."
        ),
    ),
)


#: Status codes that mean the same thing everywhere, documented once. Repeating
#: them per endpoint would trade a reader's attention for nothing: the pattern is
#: the useful information, and a table of it is shorter than sixteen copies.
SHARED_ERRORS = (
    ("400", "Malformed request: a hash that is not 64 hex characters, an empty body, a missing field."),
    ("401", "A push token is configured and the `Authorization: Bearer …` header does not match."),
    ("404", "The repository, branch, or object does not exist."),
    ("422", "The bytes received do not hash to the digest in the URL."),
)


#: Every path this reference documents. Compared against the application's own
#: route table by the test suite, in both directions.
def documented_paths() -> set[str]:
    return {endpoint.path for group in GROUPS for endpoint in group.endpoints}


def served_paths(app) -> set[str]:
    """Every `/api/v1` path the application actually serves.

    Read from the generated OpenAPI document rather than by walking
    `app.routes`. The route list is an implementation detail that has already
    changed shape once -- current FastAPI wraps an included router in an opaque
    object instead of flattening its routes into the parent -- so a test that
    walks it breaks on an upgrade that changed nothing about the API. The schema
    is the public, documented view of the same information.
    """
    return {path for path in app.openapi()["paths"] if path.startswith("/api/v1")}


__all__ = [
    "BODY",
    "CONVENTIONS",
    "GROUPS",
    "HEADER",
    "PATH",
    "PUSH_SEQUENCE",
    "QUERY",
    "SHARED_ERRORS",
    "Endpoint",
    "Group",
    "Param",
    "Step",
    "documented_paths",
    "served_paths",
]
