"""Training and checkout must preserve staged work on failure."""

import os

import pytest
import typer

from aethel.commands.checkout import run_checkout
from aethel.core.errors import AethelError, InvalidRef
from aethel.core.workspace import staged_workspace, workspace_files
from tests.conftest import ADAPTER_BYTES_A, make_commit


def test_failed_run_preserves_uncommitted_work_even_with_force(core_repo):
    path = core_repo.workspace_dir / "adapter_model.safetensors"
    path.write_bytes(b"uncommitted")
    with pytest.raises(RuntimeError), staged_workspace(core_repo, force=True) as output:
        (output / path.name).write_bytes(b"partial")
        raise RuntimeError("training failed")
    assert path.read_bytes() == b"uncommitted"


def test_successful_run_requires_force_for_uncommitted_work(core_repo):
    path = core_repo.workspace_dir / "adapter_model.safetensors"
    path.write_bytes(b"uncommitted")
    with pytest.raises(AethelError, match="uncommitted"), staged_workspace(core_repo):
        pytest.fail("Run should not start")
    assert path.read_bytes() == b"uncommitted"


def test_committed_workspace_can_be_replaced(core_repo, base_hash):
    make_commit(core_repo, base_hash, "saved", ADAPTER_BYTES_A)
    with staged_workspace(core_repo) as output:
        (output / "adapter_model.safetensors").write_bytes(b"new")
    assert workspace_files(core_repo.workspace_dir).keys() == {"adapter_model.safetensors"}
    assert not (core_repo.workspace_dir.parent / "workspace.backup").exists()


def test_failed_install_restores_original_workspace(core_repo, monkeypatch):
    path = core_repo.workspace_dir / "adapter_model.safetensors"
    path.write_bytes(b"original")
    original_replace = os.replace

    def fail_install(source, destination):
        if source.name.startswith(".workspace-"):
            raise OSError("simulated rename failure")
        return original_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_install)
    with pytest.raises(OSError), staged_workspace(core_repo, force=True) as output:
        (output / path.name).write_bytes(b"new")
    assert path.read_bytes() == b"original"


def test_edits_during_training_are_not_overwritten(core_repo):
    path = core_repo.workspace_dir / "adapter_model.safetensors"
    with pytest.raises(AethelError, match="changed during"), staged_workspace(core_repo) as output:
        (output / path.name).write_bytes(b"trained")
        path.write_bytes(b"edited during training")
    assert path.read_bytes() == b"edited during training"


def test_interrupted_backup_is_not_deleted(core_repo):
    backup = core_repo.workspace_dir.parent / "workspace.backup"
    backup.mkdir()
    with pytest.raises(AethelError, match="backup exists"), staged_workspace(core_repo, force=True):
        pytest.fail("Recovery must happen first")
    assert backup.exists()


def test_checkout_of_current_commit_preserves_dirty_work(core_repo, base_hash):
    commit = make_commit(core_repo, base_hash, "saved", ADAPTER_BYTES_A)
    path = core_repo.workspace_dir / "adapter_model.safetensors"
    path.write_bytes(b"uncommitted")
    with pytest.raises(typer.Exit):
        run_checkout(commit, force=False)
    assert path.read_bytes() == b"uncommitted"


def test_ref_update_rejects_stale_parent(core_repo, base_hash):
    first = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
    second = make_commit(core_repo, base_hash, "second", ADAPTER_BYTES_A)
    with pytest.raises(InvalidRef, match="changed during"):
        core_repo.refs.update_branch("main", first, expected_tip=first, check_expected=True)
    assert core_repo.refs.read_branch("main") == second


def test_commit_refuses_metrics_for_different_weights(core_repo, base_hash):
    from aethel.core.commits import create_commit
    from tests.conftest import stage_adapter

    stage_adapter(core_repo.root, ADAPTER_BYTES_A)
    with pytest.raises(InvalidRef, match="changed after evaluation"):
        create_commit(
            core_repo, message="stale evaluation", author="test", base_hash=base_hash,
            training_info={"evaluation": {"current": {"adapter_sha256": "f" * 64}}},
        )
    assert core_repo.refs.read_branch("main") is None
