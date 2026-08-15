"""Which objects a push must consider.

Pure logic: given a repository and a branch tip, work out every object the
remote needs in order to hold that history completely. No network, no HTTP, no
terminal -- so the interesting half of `aethel push` is unit-testable and a
wrong plan fails a test instead of producing a mystery upload.

**Why a whole-history plan rather than "just the new commit".** The plan lists
everything reachable, and the Hub is then asked which of those it lacks
(`/negotiate`). Computing the delta on the client would mean trusting the
client's belief about remote state; asking the server means a Hub that lost an
object recovers it on the next push instead of staying quietly incomplete.

**Why upload order matters.** Blobs, then trees, then bases, then commits
oldest-first. Every object references only objects that come earlier in that
order, so at no point is a partially-uploaded remote holding an object whose
dependencies are absent. An interrupted push therefore leaves a remote that is
missing objects (harmless, retryable) rather than one holding a commit that
points into nothing.
"""

from dataclasses import dataclass, field

from aethel.core.commits import walk_history
from aethel.core.errors import ObjectNotFound
from aethel.core.objects import OBJECT_KINDS
from aethel.core.repo import Repo

#: Upload order. Dependencies first: a tree names blobs, a commit names a tree,
#: a base and a parent commit.
UPLOAD_ORDER = ("blobs", "trees", "bases", "commits")


@dataclass
class PushPlan:
    """Every object reachable from a branch tip, grouped by kind.

    `commit_order` is the oldest-first commit sequence. It exists separately
    from `objects["commits"]` because a set has no order, and both the upload
    and the Hub's transparency log want ancestors before descendants.
    """

    branch: str
    tip: str
    objects: dict[str, set[str]] = field(default_factory=dict)
    commit_order: list[str] = field(default_factory=list)

    def kinds(self) -> tuple[str, ...]:
        return UPLOAD_ORDER

    def have_map(self) -> dict[str, list[str]]:
        """The `have` payload for negotiation: sorted, so it is reproducible."""
        return {kind: sorted(self.objects.get(kind, set())) for kind in UPLOAD_ORDER}

    def total(self) -> int:
        return sum(len(hashes) for hashes in self.objects.values())

    def counts(self) -> dict[str, int]:
        return {kind: len(self.objects.get(kind, set())) for kind in UPLOAD_ORDER}


def build_push_plan(repo: Repo, branch: str, tip: str) -> PushPlan:
    """Collect every object reachable from `tip`.

    Walks the commit DAG and, for each commit, adds its tree, that tree's
    blobs, and its base reference.

    Missing objects raise rather than being skipped. A push that silently
    omitted an unreadable tree would publish a branch the remote cannot serve
    -- exactly the failure the Hub's ref-update check exists to prevent, and
    better caught here where the error can name the local object.
    """
    plan = PushPlan(
        branch=branch,
        tip=tip,
        objects={kind: set() for kind in OBJECT_KINDS},
    )

    for commit_hash, commit in walk_history(repo, tip):
        plan.objects["commits"].add(commit_hash)
        plan.commit_order.append(commit_hash)

        tree_hash = commit.get("tree")
        if not tree_hash:
            raise ObjectNotFound(f"commit {commit_hash[:12]} has no tree reference")

        plan.objects["trees"].add(tree_hash)

        # read_tree verifies the tree against its own hash, so a corrupt tree
        # fails the push instead of being pushed onward.
        for blob_hash in repo.objects.read_tree(tree_hash).values():
            if not repo.objects.exists("blobs", blob_hash):
                raise ObjectNotFound(
                    f"blob {blob_hash[:12]} is missing locally "
                    f"(referenced by commit {commit_hash[:12]})"
                )
            plan.objects["blobs"].add(blob_hash)

        base_hash = commit.get("base")
        if base_hash:
            if not repo.objects.exists("bases", base_hash):
                raise ObjectNotFound(
                    f"base {base_hash[:12]} is missing locally "
                    f"(referenced by commit {commit_hash[:12]})"
                )
            plan.objects["bases"].add(base_hash)

    # walk_history yields newest-first; the log and the upload both want the
    # reverse, so ancestors are always sent and logged before descendants.
    plan.commit_order.reverse()

    return plan


__all__ = ["UPLOAD_ORDER", "PushPlan", "build_push_plan"]
