"""``aethel push`` -- publish a branch's history to a Hub.

The shape of a push, and why it is in this order:

1. **Plan.** Collect every object reachable from the branch tip, locally, and
   verify each one exists. A push that cannot be completed fails before a
   single byte leaves the machine.
2. **Negotiate.** Ask the Hub which of those it lacks. The delta is decided by
   the *server*, so a Hub that lost an object recovers it on the next push
   instead of staying quietly incomplete.
3. **Upload dependencies first** -- blobs, trees, bases, then commits
   oldest-first. Every object references only objects sent earlier, so an
   interrupted push leaves a remote missing objects (retryable) rather than one
   holding a commit that points into nothing.
4. **Move the ref last.** The Hub re-walks the history before accepting it, so
   the ref update doubles as the remote's completeness check: if an upload
   failed, the published branch does not move.

Nothing here trusts the network. Each upload is addressed by the hash the
client computed, the Hub recomputes it from the received bytes, and the client
re-checks the hash the Hub echoes back.
"""

import os

import typer
from rich.table import Table

from aethel.commands._common import console, handle_errors, short
from aethel.core.errors import AethelError, InvalidRef
from aethel.core.repo import Repo
from aethel.remote.http import HubClient
from aethel.remote.objects import UPLOAD_ORDER, PushPlan, build_push_plan

app = typer.Typer()

#: Where to push when nothing is specified. A localhost default is right for a
#: laptop demo; the LAN address of the shared Hub goes in AETHEL_HUB_URL.
DEFAULT_REMOTE = "http://127.0.0.1:8000"


def resolve_remote(explicit: str | None) -> str:
    """Flag, then environment, then localhost.

    The Hub's host is never hard-coded anywhere in the codebase: it may run on
    a teammate's laptop, a cloud VM, or behind a tunnel, and which one is a
    deployment decision rather than a code change.
    """
    for candidate in (explicit, os.environ.get("AETHEL_HUB_URL")):
        if candidate and candidate.strip():
            return candidate.strip()
    return DEFAULT_REMOTE


def resolve_repo_name(repo: Repo, explicit: str | None) -> str:
    """Flag, then config, then the working directory's name.

    Recorded in config on the first push so later pushes need no flags -- and,
    more usefully, so the published name cannot drift if someone renames the
    local folder.
    """
    if explicit and explicit.strip():
        return explicit.strip()

    try:
        configured = repo.read_config().get("hub_repo")
    except AethelError:
        configured = None

    if isinstance(configured, str) and configured.strip():
        return configured.strip()

    return repo.root.name


@app.callback(invoke_without_command=True)
def push_callback(
    ctx: typer.Context,
    remote: str | None = typer.Option(
        None, "--remote", "-r", help="Hub base URL. Defaults to $AETHEL_HUB_URL."
    ),
    repo_name: str | None = typer.Option(
        None, "--repo", help="Name to publish under. Defaults to the directory name."
    ),
    branch: str | None = typer.Option(
        None, "--branch", "-b", help="Branch to push. Defaults to the current branch."
    ),
    token: str | None = typer.Option(
        None, "--token", help="Push token. Defaults to $AETHEL_HUB_TOKEN."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be uploaded, then stop."
    ),
):
    """Publish a branch to a Hub."""
    if ctx.invoked_subcommand is None:
        run_push(
            remote=remote,
            repo_name=repo_name,
            branch=branch,
            token=token,
            dry_run=dry_run,
        )


@handle_errors
def run_push(
    remote: str | None,
    repo_name: str | None,
    branch: str | None,
    token: str | None,
    dry_run: bool,
) -> None:
    repo = Repo.discover()

    target_branch = _resolve_branch(repo, branch)
    tip = repo.refs.read_branch(target_branch)

    if tip is None:
        raise InvalidRef(
            f"Branch '{target_branch}' has no commits yet. "
            f"Run 'aethel train' then 'aethel commit' first."
        )

    plan = build_push_plan(repo, target_branch, tip)
    name = resolve_repo_name(repo, repo_name)
    base_url = resolve_remote(remote)
    push_token = token or os.environ.get("AETHEL_HUB_TOKEN") or None

    counts = plan.counts()
    console.print(
        f"Pushing [bold cyan]{target_branch}[/bold cyan] → "
        f"[bold]{name}[/bold] at [dim]{base_url}[/dim]"
    )
    console.print(
        f"[dim]{plan.total()} objects reachable from {short(tip)} "
        f"({counts['commits']} commits, {counts['trees']} trees, "
        f"{counts['blobs']} blobs, {counts['bases']} bases)[/dim]"
    )

    with HubClient(base_url, token=push_token) as client:
        remote_version = client.version()
        console.print(
            f"[dim]Hub {remote_version.get('hub', '?')} "
            f"build {remote_version.get('git_sha', '?')}[/dim]"
        )

        missing = client.negotiate(name, plan.have_map())
        needed = {kind: set(missing.get(kind, ())) for kind in UPLOAD_ORDER}
        total_needed = sum(len(hashes) for hashes in needed.values())

        console.print()
        if total_needed == 0:
            console.print("[green]Hub already holds every object — nothing to upload.[/green]")
        else:
            console.print(f"Hub needs [bold]{total_needed}[/bold] of {plan.total()} objects.")

        if dry_run:
            _render_plan(repo, plan, needed)
            console.print("[yellow]--dry-run: nothing was uploaded.[/yellow]")
            return

        sent, sent_bytes = _upload(client, repo, plan, needed)
        result = client.update_ref(name, target_branch, tip)

    console.print()
    console.print(
        f"[bold green]Pushed[/bold green] {sent} object(s)"
        + (f" · {_human(sent_bytes)}" if sent_bytes else "")
        + f" · {plan.total() - sent} already present"
    )
    console.print(f"[cyan]{name}/{target_branch}[/cyan] → {short(tip)}")

    logged = result.get("logged_indices") or []
    console.print(
        f"[cyan]Transparency log:[/cyan] {result.get('log_size', '?')} leaves"
        f" ({len(logged)} appended this push)"
    )
    if result.get("root"):
        console.print(f"[cyan]Log root:[/cyan] [dim]{result['root']}[/dim]")

    _remember_repo_name(repo, name)

    console.print()
    console.print(f"[dim]{base_url}/r/{name}[/dim]")


def _resolve_branch(repo: Repo, explicit: str | None) -> str:
    """The branch to push: the flag, or whatever HEAD is on.

    A detached HEAD is refused rather than pushed under some arbitrary name.
    Publishing a commit no local branch points at would create remote history
    with no local counterpart -- and `--branch` covers the rare case where that
    is genuinely what someone means.
    """
    if explicit and explicit.strip():
        return explicit.strip()

    head = repo.refs.read_head()

    if head.is_detached:
        raise InvalidRef(
            f"HEAD is detached at {short(head.commit or '')}. "
            f"Check out a branch first, or name one with --branch."
        )

    return head.branch


def _upload(
    client: HubClient,
    repo: Repo,
    plan: PushPlan,
    needed: dict[str, set[str]],
) -> tuple[int, int]:
    """Upload the missing objects in dependency order.

    Returns ``(objects_sent, bytes_sent)``. Commits go last and oldest-first:
    the Hub logs a pushed branch's ancestors in commit order, so sending them
    in that order keeps log indices matching history.
    """
    sent = 0
    sent_bytes = 0

    for kind in UPLOAD_ORDER:
        pending = needed.get(kind, set())
        if not pending:
            continue

        order = (
            [h for h in plan.commit_order if h in pending]
            if kind == "commits"
            else sorted(pending)
        )

        for object_hash in order:
            path = repo.objects.path_for(kind, object_hash)

            if kind == "blobs":
                size = path.stat().st_size
                client.put_blob(object_hash, path)
            else:
                data = path.read_bytes()
                size = len(data)
                client.put_object(kind, object_hash, data)

            sent += 1
            sent_bytes += size
            console.print(
                f"  [green]sent[/green] {kind[:-1]:7} {short(object_hash)} "
                f"[dim]{_human(size)}[/dim]"
            )

    return sent, sent_bytes


def _render_plan(repo: Repo, plan: PushPlan, needed: dict[str, set[str]]) -> None:
    """List what a real push would send, so --dry-run is inspectable."""
    table = Table(title="Would upload", show_lines=False)
    table.add_column("Kind", style="cyan", no_wrap=True)
    table.add_column("Hash", no_wrap=True)
    table.add_column("Size", justify="right")

    total = 0
    for kind in UPLOAD_ORDER:
        for object_hash in sorted(needed.get(kind, set())):
            path = repo.objects.path_for(kind, object_hash)
            size = path.stat().st_size if path.is_file() else 0
            total += size
            table.add_row(kind[:-1], short(object_hash), _human(size))

    if total or any(needed.values()):
        console.print(table)
        console.print(f"[dim]{_human(total)} total[/dim]")
    console.print()


def _remember_repo_name(repo: Repo, name: str) -> None:
    """Persist the published name so later pushes need no flags.

    Best-effort: a read-only config is a reason to skip a convenience, never to
    fail a push that has already succeeded.
    """
    try:
        config = repo.read_config()
        if config.get("hub_repo") != name:
            repo.write_config({**config, "hub_repo": name})
    except (AethelError, OSError):
        pass


def _human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"
