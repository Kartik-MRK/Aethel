"""Repository discovery, layout, and configuration.

Layout::

    .aethel/
        HEAD                     active branch or detached commit
        config.json              repository configuration
        index.db                 DERIVED CACHE ONLY -- rebuildable by `aethel reindex`
        refs/heads/<branch>      branch tips
        objects/blobs/...        content-addressed object store
        objects/trees/...
        objects/commits/...
        objects/bases/...
        workspace/               staging area written by `aethel train`

The index is a cache, never a source of truth. Every read path must work from
the object store alone. The old implementation violated this -- checkout
could not resolve a commit without SQLite (checkout.py:51-70) -- which made
the documented "copy the folder and you have the version" claim false. Here,
deleting index.db costs a reindex and nothing else.
"""

import json
from pathlib import Path

from aethel.core.atomic import atomic_write_text
from aethel.core.errors import RepositoryCorrupt, RepositoryNotFound
from aethel.core.objects import OBJECT_KINDS, ObjectStore
from aethel.core.refs import DEFAULT_BRANCH, Refs

AETHEL_DIR_NAME = ".aethel"
CONFIG_NAME = "config.json"
INDEX_NAME = "index.db"
WORKSPACE_NAME = "workspace"

#: Bumped when the on-disk layout changes incompatibly. Version 1 was the
#: adapter-hash-keyed folder layout that carried defect S1.1; version 2 keys
#: commits by their own hash.
SCHEMA_VERSION = 2


class Repo:
    """An Aethel repository rooted at a working directory."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.aethel_dir = self.root / AETHEL_DIR_NAME
        self.objects = ObjectStore(self.aethel_dir / "objects")
        self.refs = Refs(self.aethel_dir)

    # -- discovery ---------------------------------------------------------

    @classmethod
    def discover(cls, start: Path | str | None = None) -> "Repo":
        """Find the repository at or above `start` (default: CWD).

        Walking upward means commands work from any subdirectory, the way
        `git` does. The old code only ever looked at the literal relative path
        ".aethel", so every command silently required you to stand in the
        repository root.
        """
        current = Path(start or Path.cwd()).resolve()

        for candidate in (current, *current.parents):
            if (candidate / AETHEL_DIR_NAME).is_dir():
                return cls(candidate)

        raise RepositoryNotFound(
            "Not an Aethel repository (or any parent directory). "
            "Run 'aethel init --model <owner/model>' first."
        )

    @classmethod
    def create(cls, root: Path | str, config: dict) -> "Repo":
        """Create the repository layout and write its configuration.

        Idempotent: re-initializing an existing repository preserves its
        objects, refs and history, and only rewrites configuration.
        """
        repo = cls(Path(root))

        for kind in OBJECT_KINDS:
            (repo.aethel_dir / "objects" / kind).mkdir(parents=True, exist_ok=True)

        repo.refs.heads_dir.mkdir(parents=True, exist_ok=True)
        repo.workspace_dir.mkdir(parents=True, exist_ok=True)

        # An unborn default branch: the branch exists, with no commits yet.
        if not repo.refs.branch_path(DEFAULT_BRANCH).exists():
            repo.refs.create_branch(DEFAULT_BRANCH, None)

        if not repo.refs.head_path.is_file():
            repo.refs.set_head_to_branch(DEFAULT_BRANCH)

        repo.write_config({**config, "schema_version": SCHEMA_VERSION})

        return repo

    # -- paths -------------------------------------------------------------

    @property
    def workspace_dir(self) -> Path:
        return self.aethel_dir / WORKSPACE_NAME

    @property
    def config_path(self) -> Path:
        return self.aethel_dir / CONFIG_NAME

    @property
    def index_path(self) -> Path:
        return self.aethel_dir / INDEX_NAME

    # -- configuration -----------------------------------------------------

    def read_config(self) -> dict:
        if not self.config_path.is_file():
            raise RepositoryCorrupt(
                "Repository config is missing. Re-run 'aethel init --model <owner/model>'."
            )

        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RepositoryCorrupt(f"Repository config is invalid JSON: {exc}") from exc
        except OSError as exc:
            raise RepositoryCorrupt(f"Could not read repository config: {exc}") from exc

        if not isinstance(payload, dict):
            raise RepositoryCorrupt("Repository config must be a JSON object.")

        return payload

    def write_config(self, config: dict) -> None:
        atomic_write_text(self.config_path, json.dumps(config, indent=2, sort_keys=True) + "\n")

    # -- convenience -------------------------------------------------------

    def exists(self) -> bool:
        return self.aethel_dir.is_dir()

    def __repr__(self) -> str:
        return f"Repo({self.root})"
