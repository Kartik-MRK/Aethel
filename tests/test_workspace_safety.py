"""Training and checkout must preserve staged work on failure."""

import os
import sys
from types import SimpleNamespace

import pytest
import typer

from aethel.commands.checkout import run_checkout
from aethel.core.errors import AethelError, InvalidRef
from aethel.core.workspace import staged_workspace, workspace_files
from tests.conftest import ADAPTER_BYTES_A, make_commit


def test_commit_refuses_a_parent_changed_during_evaluation(core_repo, base_hash, monkeypatch):
    from aethel.commands.commit import run_commit

    first = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
    second = make_commit(core_repo, base_hash, "second", ADAPTER_BYTES_A)
    core_repo.refs.update_branch("main", first)

    def concurrent_commit(repo, workspace):
        repo.refs.update_branch("main", second)
        return None

    monkeypatch.setitem(sys.modules, "aethel.evaluation.evaluator", SimpleNamespace(evaluate_current_vs_parent=concurrent_commit))
    with pytest.raises(typer.Exit):
        run_commit("must not silently reparent")
    assert core_repo.refs.read_branch("main") == second


def test_delete_current_branch_with_surrounding_spaces_is_refused(core_repo, base_hash):
    from aethel.commands.branch import run_delete

    tip = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
    with pytest.raises(typer.Exit):
        run_delete(" main ")
    assert core_repo.refs.resolve_head_commit() == tip


def test_checkout_removes_files_absent_from_target(core_repo, base_hash):
    first = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
    extra = core_repo.workspace_dir / "extra.json"
    extra.write_text("{}")
    make_commit(core_repo, base_hash, "second", ADAPTER_BYTES_A)
    run_checkout(first, force=False)
    tree = core_repo.objects.read_tree(core_repo.objects.read_json("commits", first)["tree"])
    assert workspace_files(core_repo.workspace_dir) == tree


def test_checkout_copy_failure_preserves_workspace_and_head(core_repo, base_hash, monkeypatch):
    import aethel.core.objects as objects

    first = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
    make_commit(core_repo, base_hash, "second", b"second adapter")
    original = workspace_files(core_repo.workspace_dir)
    head = core_repo.refs.read_head()
    copy = objects.atomic_copy_file
    calls = 0

    def fail_last_copy(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("copy interrupted")
        return copy(*args, **kwargs)

    monkeypatch.setattr(objects, "atomic_copy_file", fail_last_copy)
    with pytest.raises((OSError, typer.Exit)):
        run_checkout(first, force=False)
    assert workspace_files(core_repo.workspace_dir) == original
    assert core_repo.refs.read_head() == head


def test_checkout_head_failure_rolls_back_workspace(core_repo, base_hash, monkeypatch):
    from aethel.core.refs import Refs

    first = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
    make_commit(core_repo, base_hash, "second", b"second adapter")
    original = workspace_files(core_repo.workspace_dir)
    head = core_repo.refs.read_head()

    def fail_head(*args, **kwargs):
        raise OSError("HEAD write failed")

    monkeypatch.setattr(Refs, "set_head_detached", fail_head)
    with pytest.raises((OSError, typer.Exit)):
        run_checkout(first, force=False)
    assert workspace_files(core_repo.workspace_dir) == original
    assert core_repo.refs.read_head() == head


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


def test_checkout_refuses_branch_moved_during_extraction(core_repo, base_hash, monkeypatch):
    from aethel.core.objects import ObjectStore

    first = make_commit(core_repo, base_hash, "first", ADAPTER_BYTES_A)
    second = make_commit(core_repo, base_hash, "second", b"second adapter")
    core_repo.refs.create_branch("target", first)
    original = workspace_files(core_repo.workspace_dir)
    extract = ObjectStore.extract_tree

    def extract_then_move(*args, **kwargs):
        restored = extract(*args, **kwargs)
        core_repo.refs.update_branch("target", second)
        return restored

    monkeypatch.setattr(ObjectStore, "extract_tree", extract_then_move)
    with pytest.raises(typer.Exit):
        run_checkout("target", force=False)
    assert core_repo.refs.read_head().branch == "main"
    assert workspace_files(core_repo.workspace_dir) == original


def test_commit_refuses_metrics_for_changed_training_metadata(core_repo, base_hash):
    from aethel.core.commits import create_commit
    from tests.conftest import stage_adapter

    stage_adapter(core_repo.root, ADAPTER_BYTES_A)
    with pytest.raises(InvalidRef, match="metadata changed after evaluation"):
        create_commit(
            core_repo, message="stale metadata", author="test", base_hash=base_hash,
            training_info={"evaluation": {"current": {"training_info_sha256": "f" * 64}}},
        )
    assert core_repo.refs.read_branch("main") is None


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
