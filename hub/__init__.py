"""Aethel Hub — a registry and transparency log for adapter provenance.

The Hub is deliberately treated as UNTRUSTED by its own clients. It stores
adapters and lineage, and it maintains an append-only Merkle log over the
commits it has accepted. The root of that log is what gets anchored on-chain,
so a client can verify its commit is included without taking the Hub's word
for anything.

That framing is what makes the later chain layer meaningful rather than
decorative: on a purely local tool there is no untrusted party, so anchoring
answers no question. A hosted Hub has an operator who *could* rewrite
published history — and the anchored log is what makes that detectable.

Two invariants hold everywhere in this package:

1. **Nothing is stored without verifying its hash.** Uploads name the hash
   they claim to be; the Hub recomputes it from the bytes and rejects a
   mismatch. A client never has to trust that the Hub stored what it sent.
2. **The log only ever appends.** There is no endpoint that edits or removes a
   log entry, so "append-only" is a property of the code surface, not a
   promise in prose.
"""

__all__ = ["create_app"]


def create_app(*args, **kwargs):
    """Construct the Hub FastAPI application.

    Imported lazily so that `import hub` stays cheap and does not require
    FastAPI to be installed for tooling that only needs the package metadata.
    """
    from hub.app import create_app as _create_app

    return _create_app(*args, **kwargs)
