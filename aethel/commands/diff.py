"""Compare two committed adapters without changing the workspace."""

import json
import tempfile
from pathlib import Path

import typer
from rich.table import Table

from aethel.commands._common import INTERSPERSED, console, handle_errors
from aethel.core.commits import resolve_commitish
from aethel.core.errors import AethelError
from aethel.core.repo import Repo

app = typer.Typer(context_settings=INTERSPERSED)


def diff_commits(repo, left: str, right: str):
    from aethel.ml.diff import compare_adapters

    _, left_hash = resolve_commitish(repo, left)
    _, right_hash = resolve_commitish(repo, right)
    left_commit = repo.objects.read_json("commits", left_hash)
    right_commit = repo.objects.read_json("commits", right_hash)
    if left_commit["base"] != right_commit["base"]:
        raise AethelError("Adapters reference different base models or revisions")
    left_info = left_commit.get("training_info", {})
    right_info = right_commit.get("training_info", {})
    for field in ("num_labels", "label_names", "task_type"):
        if left_info.get(field) != right_info.get(field):
            raise AethelError(f"Adapters have incompatible {field}")
    with tempfile.TemporaryDirectory(prefix="aethel_diff_") as temporary:
        left_path, right_path = Path(temporary) / "left", Path(temporary) / "right"
        repo.objects.extract_tree(left_commit["tree"], left_path)
        repo.objects.extract_tree(right_commit["tree"], right_path)
        result = compare_adapters(left_path, right_path)
    return {"left": left_hash, "right": right_hash, "base": left_commit["base"], **result}


@app.callback(invoke_without_command=True)
@handle_errors
def diff(
    left: str = typer.Argument(..., help="First commit or branch."),
    right: str = typer.Argument(..., help="Second commit or branch."),
    json_output: bool = typer.Option(False, "--json", help="Print the complete diff as JSON."),
):
    repo = Repo.discover()
    try:
        result = diff_commits(repo, left, right)
    except ImportError as exc:
        raise AethelError("Adapter diff needs the ML extra: pip install -e '.[ml]'") from exc
    except (OSError, ValueError) as exc:
        raise AethelError(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(result, indent=2, allow_nan=False))
        return
    table = Table(title=f"Adapter diff: {result['left'][:12]} -> {result['right'][:12]}")
    for label in ("Layer", "Left norm", "Right norm", "Change norm", "Cosine"):
        table.add_column(label)
    for row in result["layers"] + result["saved_modules"]:
        table.add_row(
            row["name"], f"{row['left_norm']:.6g}", f"{row['right_norm']:.6g}",
            f"{row['difference_norm']:.6g}",
            f"{row['cosine']:.6f}" if row["cosine"] is not None else "-",
        )
    console.print(table)
    for field, values in result["configuration_changes"].items():
        console.print(f"{field}: {values['left']} -> {values['right']}")
