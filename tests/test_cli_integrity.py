"""CLI integrity checks include HEAD and the adapter's pinned base identity."""

import pytest
import typer

from aethel.commands.fsck import run_fsck
from aethel.core.commits import build_base_object, create_commit
from aethel.core.errors import InvalidRef, ObjectNotFound
from tests.conftest import ADAPTER_BYTES_A, TRAINING_INFO, make_commit, stage_adapter


def test_fsck_rejects_missing_detached_head(core_repo):
    core_repo.refs.set_head_detached("f" * 64)
    with pytest.raises(typer.Exit):
        run_fsck(verbose=False)


def test_fsck_checks_dependencies_reachable_only_from_detached_head(core_repo, base_hash):
    tip = make_commit(core_repo, base_hash, "detached", ADAPTER_BYTES_A)
    core_repo.refs.set_head_detached(tip)
    core_repo.refs.delete_branch("main")
    core_repo.objects.path_for("bases", base_hash).unlink()
    with pytest.raises(typer.Exit):
        run_fsck(verbose=False)


def test_commit_refuses_missing_base(core_repo):
    stage_adapter(core_repo.root, ADAPTER_BYTES_A)
    with pytest.raises(ObjectNotFound):
        create_commit(core_repo, message="missing base", author="test", base_hash="f" * 64, training_info=TRAINING_INFO)
    assert core_repo.refs.resolve_head_commit() is None


def test_commit_refuses_adapter_trained_on_a_different_base(core_repo):
    stage_adapter(core_repo.root, ADAPTER_BYTES_A)
    base = core_repo.objects.write_json("bases", build_base_object("another-model", "a" * 40))
    with pytest.raises(InvalidRef):
        create_commit(core_repo, message="wrong base", author="test", base_hash=base, training_info=TRAINING_INFO)
    assert core_repo.refs.resolve_head_commit() is None


def test_fsck_rejects_commit_adapter_that_disagrees_with_tree(core_repo, base_hash):
    tip = make_commit(core_repo, base_hash, "original", ADAPTER_BYTES_A)
    commit = core_repo.objects.read_json("commits", tip)
    commit["adapter_blob"] = core_repo.objects.write_blob(b"unrelated adapter")
    tampered = core_repo.objects.write_json("commits", commit)
    core_repo.refs.update_branch("main", tampered)
    with pytest.raises(typer.Exit):
        run_fsck(verbose=False)
