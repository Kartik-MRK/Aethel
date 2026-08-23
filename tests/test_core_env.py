"""Tests for the `.env` reader (aethel/core/env.py).

Two of these matter more than the rest. `TestEnvironmentWins` pins the rule
that a file on disk never outranks a variable typed on the command line --
without it, `AETHEL_HUB_PORT=9000 python -m hub` would silently bind 8000.
`TestValuesAreNeverEchoed` pins the rule that a parse error reports a line
number and nothing else, because the lines being parsed are credentials.
"""

import os

import pytest

from aethel.core.env import EnvFileError, find_env_file, load_env, parse_env


class TestParsing:
    def test_reads_simple_assignments(self):
        assert parse_env("A=1\nB=two\n") == {"A": "1", "B": "two"}

    def test_ignores_blank_lines_and_comments(self):
        text = "\n# a note\nA=1\n\n   # indented note\nB=2\n"

        assert parse_env(text) == {"A": "1", "B": "2"}

    def test_accepts_a_leading_export(self):
        assert parse_env("export A=1\n") == {"A": "1"}

    def test_strips_surrounding_whitespace(self):
        assert parse_env("  A = 1  \n") == {"A": "1"}

    def test_keeps_an_empty_value(self):
        """An explicitly blank setting is meaningful: it means "unset"."""
        assert parse_env("A=\n") == {"A": ""}

    def test_a_value_may_contain_equals_signs(self):
        """base64 padding and JWTs contain `=`; only the first one separates."""
        assert parse_env("A=eyJhbGci==\n") == {"A": "eyJhbGci=="}

    def test_later_lines_win(self):
        assert parse_env("A=1\nA=2\n") == {"A": "2"}


class TestQuoting:
    def test_strips_a_matching_pair_of_double_quotes(self):
        assert parse_env('A="value"\n') == {"A": "value"}

    def test_strips_a_matching_pair_of_single_quotes(self):
        assert parse_env("A='value'\n") == {"A": "value"}

    def test_keeps_an_unmatched_quote(self):
        """A secret that genuinely contains a quote must survive intact."""
        assert parse_env("A=\"value\n") == {"A": '"value'}
        assert parse_env("A=val'ue\n") == {"A": "val'ue"}

    def test_keeps_mismatched_quotes(self):
        assert parse_env("A=\"value'\n") == {"A": "\"value'"}


class TestNoInterpolation:
    """Every secret this loads is opaque. Expanding `$` would corrupt one into
    a shorter, still-plausible string, and the failure would look like a wrong
    key rather than a mangled one."""

    def test_a_dollar_sign_is_literal(self):
        assert parse_env("A=pa$$word\n") == {"A": "pa$$word"}

    def test_brace_syntax_is_not_expanded(self, monkeypatch):
        monkeypatch.setenv("OTHER", "expanded")

        assert parse_env("A=${OTHER}\n") == {"A": "${OTHER}"}

    def test_a_backslash_is_literal(self):
        assert parse_env("A=one\\ntwo\n") == {"A": "one\\ntwo"}


class TestMalformedLines:
    def test_a_line_without_a_separator_is_rejected(self):
        with pytest.raises(EnvFileError):
            parse_env("A=1\nthis is not a setting\n")

    def test_an_unusable_variable_name_is_rejected(self):
        with pytest.raises(EnvFileError):
            parse_env("not a name=1\n")

    def test_the_error_names_the_line_number(self):
        with pytest.raises(EnvFileError) as caught:
            parse_env("A=1\nB=2\noops\n")

        assert "line 3" in str(caught.value)


class TestValuesAreNeverEchoed:
    """A loader that printed the offending line would put a credential into a
    terminal, a CI log, or a screen share the first time someone fat-fingered
    a paste."""

    def test_a_malformed_line_does_not_appear_in_the_message(self):
        secret = "3f8a1c00deadbeef"

        with pytest.raises(EnvFileError) as caught:
            parse_env(f"PRIVATE_KEY {secret}\n")

        assert secret not in str(caught.value)

    def test_a_bad_name_message_does_not_carry_the_value(self):
        secret = "9b2e77aaf00dcafe"

        with pytest.raises(EnvFileError) as caught:
            parse_env(f"my key={secret}\n")

        assert secret not in str(caught.value)

    def test_load_env_returns_names_not_values(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AETHEL_TEST_TOKEN", raising=False)
        path = tmp_path / ".env"
        path.write_text("AETHEL_TEST_TOKEN=s3cret\n", encoding="utf-8")

        applied = load_env(path)

        assert applied == ["AETHEL_TEST_TOKEN"]
        assert "s3cret" not in str(applied)


class TestEnvironmentWins:
    def test_an_existing_variable_is_not_replaced(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AETHEL_TEST_PORT", "9000")
        path = tmp_path / ".env"
        path.write_text("AETHEL_TEST_PORT=8000\n", encoding="utf-8")

        applied = load_env(path)

        assert os.environ["AETHEL_TEST_PORT"] == "9000"
        assert applied == []

    def test_an_unset_variable_is_populated(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AETHEL_TEST_PORT", raising=False)
        path = tmp_path / ".env"
        path.write_text("AETHEL_TEST_PORT=8000\n", encoding="utf-8")

        load_env(path)

        assert os.environ["AETHEL_TEST_PORT"] == "8000"

    def test_override_is_available_but_opt_in(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AETHEL_TEST_PORT", "9000")
        path = tmp_path / ".env"
        path.write_text("AETHEL_TEST_PORT=8000\n", encoding="utf-8")

        load_env(path, override=True)

        assert os.environ["AETHEL_TEST_PORT"] == "8000"

    def test_a_missing_file_is_not_an_error(self, tmp_path):
        """An unconfigured checkout has to run, or `pip install -e .` is not
        enough to start the Hub."""
        assert load_env(tmp_path / "nothing-here") == []


class TestFindEnvFile:
    def test_finds_a_file_in_the_starting_directory(self, tmp_path):
        (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")

        assert find_env_file(tmp_path) == tmp_path / ".env"

    def test_walks_up_to_a_parent(self, tmp_path):
        (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")
        nested = tmp_path / "tests" / "fixtures"
        nested.mkdir(parents=True)

        assert find_env_file(nested) == tmp_path / ".env"

    def test_the_nearest_file_wins(self, tmp_path):
        (tmp_path / ".env").write_text("A=outer\n", encoding="utf-8")
        nested = tmp_path / "inner"
        nested.mkdir()
        (nested / ".env").write_text("A=inner\n", encoding="utf-8")

        assert find_env_file(nested) == nested / ".env"

    def test_the_search_is_depth_bounded(self, tmp_path):
        """A process started deep in /tmp must not walk to the filesystem root
        reading other people's files."""
        (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")
        deep = tmp_path.joinpath("a", "b", "c", "d", "e", "f")
        deep.mkdir(parents=True)

        assert find_env_file(deep) != tmp_path / ".env"

    def test_a_directory_named_env_is_not_mistaken_for_a_file(self, tmp_path):
        (tmp_path / ".env").mkdir()

        assert find_env_file(tmp_path) != tmp_path / ".env"
