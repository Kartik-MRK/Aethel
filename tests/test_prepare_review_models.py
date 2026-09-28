"""Review preparation must preserve existing files before loading ML or using the network."""

import sys

import pytest

from scripts import prepare_review_models


@pytest.mark.parametrize("existing", ["empty", "files", "repo", "file", "symlink", "dangling_symlink"])
def test_existing_work_path_is_refused(tmp_path, monkeypatch, existing):
    target = tmp_path / "review"
    target.mkdir()
    work = target / "work"
    saved_files = {}
    if existing in ("empty", "files", "repo"):
        work.mkdir()
        if existing != "empty":
            for name in ("reviews.csv", "seed-42.yaml", "seed-43.yaml"):
                path = work / name
                path.write_bytes(b"existing data")
                saved_files[path] = path.read_bytes()
        if existing == "repo":
            (work / ".aethel").mkdir()
    elif existing == "file":
        work.write_bytes(b"existing file")
        saved_files[work] = work.read_bytes()
    else:
        destination = tmp_path / "other-work"
        if existing == "symlink":
            destination.mkdir()
            path = destination / "reviews.csv"
            path.write_bytes(b"existing external data")
            saved_files[path] = path.read_bytes()
        work.symlink_to(destination, target_is_directory=True)

    monkeypatch.setattr(sys, "argv", ["prepare_review_models.py", "--dir", str(target)])
    monkeypatch.setitem(sys.modules, "torch", None)

    def unexpected_network(*args, **kwargs):
        pytest.fail("Existing work must be refused before network access")

    monkeypatch.setattr(prepare_review_models.httpx, "Client", unexpected_network)
    with pytest.raises(SystemExit, match="Review work path already exists"):
        prepare_review_models.main()
    for path, data in saved_files.items():
        assert path.read_bytes() == data
    if "symlink" in existing:
        assert work.is_symlink()
        assert work.readlink() == destination
        assert destination.exists() == (existing == "symlink")


def test_invalid_sample_count_does_not_create_work(tmp_path, monkeypatch):
    target = tmp_path / "review"
    monkeypatch.setattr(sys, "argv", ["prepare_review_models.py", "--dir", str(target), "--samples", "0"])
    with pytest.raises(SystemExit, match="between 100 and 5000"):
        prepare_review_models.main()
    assert not target.exists()
