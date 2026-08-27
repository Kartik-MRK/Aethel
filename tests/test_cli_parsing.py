"""Argument parsing on the commands that take a positional.

These tests parse a command line and stop; they never run the command, so they
need no repository, no network and no ML extra. That is deliberate -- the defect
they guard is entirely in the parser, and building a repository to reach it would
only make them slow and flaky.

Every command is attached with ``add_typer``, which makes each one a Click
*group*, and a group stops parsing options at its first positional argument.
That is why ``aethel checkout main --force`` used to fail with
"Missing argument 'TARGET'" while ``aethel checkout --force main`` worked. Under
demo pressure nobody remembers which order a tool wants, so both are supported
and both are asserted here.
"""

import click
import pytest
import typer.main

from aethel.commands import branch, checkout


def parse(app, argv):
    """Bind ``argv`` to a command's parameters without invoking it."""
    command = typer.main.get_command(app)
    with command.make_context(command.name, list(argv), resilient_parsing=False) as ctx:
        return dict(ctx.params)


class TestCheckoutArgumentOrder:
    def test_option_after_the_target(self):
        assert parse(checkout.app, ["main", "--force"]) == {"target": "main", "force": True}

    def test_option_before_the_target(self):
        assert parse(checkout.app, ["--force", "main"]) == {"target": "main", "force": True}

    def test_short_flag_after_the_target(self):
        assert parse(checkout.app, ["main", "-f"]) == {"target": "main", "force": True}

    def test_target_alone_does_not_force(self):
        assert parse(checkout.app, ["main"]) == {"target": "main", "force": False}

    def test_a_commit_hash_is_a_target_like_any_other(self):
        argv = ["585e932f291e", "--force"]
        assert parse(checkout.app, argv)["target"] == "585e932f291e"

    def test_a_missing_target_is_still_an_error(self):
        # Relaxing the parser must not make the argument optional.
        with pytest.raises(click.UsageError):
            parse(checkout.app, ["--force"])


class TestBranchArgumentOrder:
    def test_delete_after_a_name(self):
        params = parse(branch.app, ["wide", "--delete", "wide"])
        assert params["delete"] == "wide"

    def test_delete_before_a_name(self):
        assert parse(branch.app, ["--delete", "wide"])["delete"] == "wide"

    def test_listing_takes_no_arguments(self):
        params = parse(branch.app, [])
        assert params["delete"] is None
