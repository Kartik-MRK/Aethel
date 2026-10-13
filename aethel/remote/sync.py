"""Verified history retrieval and fast-forward workspace installation."""

import hashlib
import json
import os
import shutil
import tempfile
from contextlib import suppress
from pathlib import Path

from aethel.core.atomic import atomic_copy_file
from aethel.core.commits import (
    find_adapter_filename,
    validate_base_object,
    validate_commit_object,
    walk_history,
)
from aethel.core.errors import AethelError, CorruptObject, InvalidRef
from aethel.core.hashing import normalize_hash
from aethel.core.objects import ObjectStore
from aethel.core.refs import validate_branch_name
from aethel.core.repo import Repo
from aethel.core.workspace import staged_workspace
from aethel.remote.http import HubClient, RemoteError

MAX_BLOB_BYTES = 64 * 1024 * 1024
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024 * 1024
MAX_OBJECTS = 100_000
MAX_COMMITS = 10_000


class HistoryDownload:
    """Install exact, hash-verified bytes; publish no refs until traversal succeeds.

    Limits apply to the complete graph, including objects already cached locally.
    Failed pulls retain verified objects so a retry downloads only missing data.
    """

    def __init__(self, client: HubClient, store: ObjectStore):
        self.client = client
        self.store = store
        self.verified: set[tuple[str, str]] = set()
        self.commits: dict[str, dict] = {}
        self.total_bytes = 0
        self.downloaded = 0

    def object(self, kind: str, digest: str) -> None:
        digest = normalize_hash(digest)
        key = (kind, digest)
        if key in self.verified:
            return
        if len(self.verified) >= MAX_OBJECTS:
            raise RemoteError("History exceeds the object count limit")
        limit = MAX_BLOB_BYTES if kind == "blobs" else MAX_JSON_BYTES
        destination = self.store.path_for(kind, digest)
        if destination.is_file() and destination.stat().st_size > limit:
            raise RemoteError(f"{kind}/{digest[:12]} exceeds the object size limit")
        if self.store.verify(kind, digest):
            self.total_bytes += destination.stat().st_size
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=".download-", dir=destination.parent)
            temporary = Path(name)
            try:
                size = 0
                hasher = hashlib.sha256()
                with os.fdopen(fd, "wb") as handle:
                    for chunk in self.client.fetch_object(kind, digest):
                        size += len(chunk)
                        self.total_bytes += len(chunk)
                        if size > limit or self.total_bytes > MAX_TOTAL_BYTES:
                            raise RemoteError("Download exceeds the object or total size limit")
                        hasher.update(chunk)
                        handle.write(chunk)
                if hasher.hexdigest() != digest:
                    raise CorruptObject(f"Downloaded {kind}/{digest[:12]} does not match its hash")
                if kind != "blobs":
                    self._json(temporary)
                atomic_copy_file(temporary, destination, expected_hash=digest)
                self.downloaded += 1
            finally:
                temporary.unlink(missing_ok=True)
        if self.total_bytes > MAX_TOTAL_BYTES:
            raise RemoteError("History exceeds the total size limit")
        if kind != "blobs":
            self._json(destination)
        self.verified.add(key)

    @staticmethod
    def _json(path: Path) -> dict:
        try:
            payload = json.loads(path.read_bytes())
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise CorruptObject("Downloaded object is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise CorruptObject("Downloaded object must be a JSON object")
        return payload

    def history(self, tip: str) -> None:
        current = normalize_hash(tip)
        pending: dict[str, dict] = {}
        while current not in self.commits:
            if current in pending:
                raise CorruptObject("Cycle in remote commit history")
            if len(self.commits) + len(pending) >= MAX_COMMITS:
                raise RemoteError("History exceeds the commit count limit")
            self.object("commits", current)
            commit = self.store.read_json("commits", current)
            validate_commit_object(commit)
            self.object("trees", commit["tree"])
            files = self.store.read_tree(commit["tree"])
            if len(files) > MAX_OBJECTS:
                raise RemoteError("Workspace exceeds the file count limit")
            if normalize_hash(files[find_adapter_filename(files)]) != normalize_hash(commit["adapter_blob"]):
                raise CorruptObject("Commit adapter_blob does not match its tree")
            workspace_bytes = 0
            for digest in files.values():
                self.object("blobs", digest)
                workspace_bytes += self.store.path_for("blobs", digest).stat().st_size
                if workspace_bytes > MAX_TOTAL_BYTES:
                    raise RemoteError("Restored workspace exceeds the total size limit")
            self.object("bases", commit["base"])
            validate_base_object(self.store.read_json("bases", commit["base"]))
            # Keep only traversal fields: training metadata can be large and
            # already lives on disk, so retaining it for all ancestors is wasteful.
            pending[current] = {key: commit[key] for key in ("tree", "base", "parent_hash")}
            if commit["parent_hash"] is None:
                break
            current = normalize_hash(commit["parent_hash"])
        self.commits.update(pending)

    def require_base(self, config: dict) -> None:
        for digest in {commit["base"] for commit in self.commits.values()}:
            base = self.store.read_json("bases", digest)
            if any(base[field] != config.get(field) for field in ("model_id", "revision_sha")):
                raise RemoteError("Remote history uses a different pinned base model or revision")


def clone_repository(
    client: HubClient, name: str, destination: Path, *, branch: str | None, author: str,
) -> Repo:
    """Reserve a new directory and install a complete repository on success."""
    name = validate_branch_name(name)
    # Do not resolve the destination: that would follow a dangling symlink.
    destination = Path(destination).absolute()
    try:
        destination.mkdir()
    except FileExistsError as exc:
        raise AethelError(f"Clone destination already exists: {destination}") from exc
    staging = None
    installed = False
    try:
        staging = Path(tempfile.mkdtemp(prefix=".clone-", dir=destination))
        branches = client.repository(name)
        selected = validate_branch_name(branch) if branch else (
            "main" if "main" in branches else next(iter(branches)) if len(branches) == 1 else None
        )
        if selected not in branches:
            raise InvalidRef("Select a published branch with --branch; no unambiguous default was found")
        repo = Repo(staging)
        download = HistoryDownload(client, repo.objects)
        for tip in branches.values():
            download.history(tip)
        commit = download.commits[branches[selected]]
        base = repo.objects.read_json("bases", commit["base"])
        config = {key: base[key] for key in ("model_id", "revision_sha")}
        download.require_base(config)
        config.update(author=author, hub_url=client.base_url, hub_repo=name)
        repo = Repo.create(staging, config)
        for ref, tip in branches.items():
            if repo.refs.branch_exists(ref):
                repo.refs.update_branch(ref, tip)
            else:
                repo.refs.create_branch(ref, tip)
        repo.refs.set_head_to_branch(selected)
        if "main" not in branches:
            repo.refs.delete_branch("main")
        repo.objects.extract_tree(commit["tree"], repo.workspace_dir)
        if set(destination.iterdir()) != {staging}:
            raise AethelError("Clone destination changed during download; refusing to install")
        os.rename(repo.aethel_dir, destination / ".aethel")
        installed = True
        return Repo(destination)
    finally:
        if staging is not None:
            shutil.rmtree(staging)
        if not installed:
            # Leave files created by somebody else during the download alone.
            with suppress(OSError):
                destination.rmdir()


def pull_repository(client: HubClient, repo: Repo, name: str, *, branch: str | None = None) -> str:
    """Advance the attached branch only if it is an ancestor of the remote tip."""
    attached = repo.refs.require_attached_branch()
    if branch is not None and validate_branch_name(branch) != attached:
        raise InvalidRef("Pull updates the current branch; checkout the requested branch first")
    previous = repo.refs.read_branch(attached)
    branches = client.repository(name)
    if attached not in branches:
        raise InvalidRef(f"Remote repository has no branch '{attached}'")
    tip = branches[attached]
    download = HistoryDownload(client, repo.objects)
    download.history(tip)
    download.require_base(repo.read_config())
    if previous == tip:
        return "Already up to date"
    if previous is not None and previous not in download.commits:
        for index, (digest, _) in enumerate(walk_history(repo, previous)):
            if index >= MAX_COMMITS:
                raise RemoteError("Local history exceeds the commit count limit")
            if digest == tip:
                return "Local branch is ahead; nothing to pull"
        raise InvalidRef("Local and remote histories have diverged. Pull only supports fast-forward updates")

    def advance():
        repo.refs.update_branch(
            attached, tip, expected_tip=previous, check_expected=True, expected_head=attached,
        )

    with staged_workspace(repo, require_clean=True, on_install=advance) as workspace:
        repo.objects.extract_tree(download.commits[tip]["tree"], workspace)
    return f"Pulled {attached} to {tip[:12]} ({download.downloaded} objects downloaded)"
