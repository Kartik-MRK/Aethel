"""Stage replacement workspaces without discarding a previous adapter on failure."""

import os
import shutil
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from aethel.core.atomic import file_lock
from aethel.core.errors import AethelError
from aethel.core.hashing import hash_file


def workspace_files(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return {
        str(file.relative_to(path)): hash_file(file)
        for file in path.rglob("*")
        if file.is_file()
    }


@contextmanager
def staged_workspace(repo, *, force: bool = False, on_install: Callable[[], None] | None = None):
    """Keep the old workspace until a complete replacement is ready."""
    root = repo.workspace_dir.parent
    workspace = repo.workspace_dir
    backup = root / "workspace.backup"
    with file_lock(root / "workspace.lock"):
        if backup.exists():
            raise AethelError(
                f"A workspace backup exists at {backup}. Inspect it before retrying; "
                "it may contain work preserved after an interrupted replacement."
            )
        original = workspace_files(workspace)
        tip = repo.refs.resolve_head_commit()
        committed = {}
        if tip:
            commit = repo.objects.read_json("commits", tip)
            committed = repo.objects.read_tree(commit["tree"])
        if original and original != committed and not force:
            raise AethelError("Workspace has uncommitted changes. Commit them or use --force to replace them.")
        temporary = Path(tempfile.mkdtemp(prefix=".workspace-", dir=root))
        try:
            yield temporary
            if workspace_files(workspace) != original:
                raise AethelError("Workspace changed during training. The new run was not installed.")
            if workspace.exists():
                os.replace(workspace, backup)
            try:
                os.replace(temporary, workspace)
                if on_install is not None:
                    on_install()
            except BaseException:
                if not temporary.exists() and workspace.exists():
                    os.replace(workspace, temporary)
                if backup.exists() and not workspace.exists():
                    os.replace(backup, workspace)
                raise
            if backup.exists():
                shutil.rmtree(backup)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
