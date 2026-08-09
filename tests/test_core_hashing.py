"""Tests for hashing and canonical serialization (aethel/core/hashing.py)."""

import json

import pytest

from aethel.core.errors import InvalidHash
from aethel.core.hashing import (
    canonical_json,
    hash_bytes,
    hash_file,
    hash_json,
    hash_text,
    is_valid_hash,
    normalize_hash,
)


class TestCanonicalJson:
    def test_key_order_does_not_affect_output(self):
        """The whole point: logical equality implies identical bytes."""
        a = canonical_json({"b": 1, "a": 2, "c": 3})
        b = canonical_json({"c": 3, "a": 2, "b": 1})

        assert a == b

    def test_nested_keys_are_also_sorted(self):
        a = canonical_json({"outer": {"z": 1, "a": 2}})
        b = canonical_json({"outer": {"a": 2, "z": 1}})

        assert a == b

    def test_no_incidental_whitespace(self):
        assert canonical_json({"a": 1, "b": 2}) == '{"a":1,"b":2}'

    def test_non_ascii_is_emitted_literally(self):
        """Matters for the browser-side verifier: JS does not escape either."""
        result = canonical_json({"msg": "café ☕"})

        assert "café ☕" in result
        assert "\\u" not in result

    def test_nan_is_rejected(self):
        """NaN is not valid JSON; emitting it would produce unparseable data."""
        with pytest.raises(ValueError):
            canonical_json({"metric": float("nan")})

    def test_infinity_is_rejected(self):
        with pytest.raises(ValueError):
            canonical_json({"metric": float("inf")})

    def test_round_trips_to_an_identical_string(self):
        """Re-serializing a decoded canonical object must be a fixed point.

        If this failed, verifying an object by re-hashing it would report
        corruption on healthy data.
        """
        original = canonical_json({"z": [3, 2, 1], "a": {"n": "café"}})

        assert canonical_json(json.loads(original)) == original


class TestHashing:
    def test_known_sha256_of_empty_input(self):
        """Pinned against the published SHA-256 of the empty string."""
        expected = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

        assert hash_bytes(b"") == expected
        assert hash_text("") == expected

    def test_text_hashing_is_utf8_encoded(self):
        assert hash_text("café") == hash_bytes("café".encode())

    def test_hash_json_matches_hashing_the_canonical_form(self):
        payload = {"b": 1, "a": 2}

        assert hash_json(payload) == hash_text(canonical_json(payload))

    def test_hash_json_is_insensitive_to_key_order(self):
        assert hash_json({"a": 1, "b": 2}) == hash_json({"b": 2, "a": 1})

    def test_hash_file_matches_hash_bytes(self, tmp_path):
        payload = b"some adapter bytes" * 1000
        path = tmp_path / "blob.bin"
        path.write_bytes(payload)

        assert hash_file(path) == hash_bytes(payload)

    def test_hash_file_streams_content_larger_than_one_chunk(self, tmp_path):
        """Guards the chunked read loop against an off-by-one at the boundary."""
        payload = bytes(range(256)) * 8192  # 2 MiB, spans multiple 1 MiB reads
        path = tmp_path / "big.bin"
        path.write_bytes(payload)

        assert hash_file(path) == hash_bytes(payload)


class TestHashValidation:
    def test_accepts_a_valid_digest(self):
        assert is_valid_hash("a" * 64)

    @pytest.mark.parametrize(
        "value",
        [
            "a" * 63,          # too short
            "a" * 65,          # too long
            "g" * 64,          # not hex
            "",
            "A" * 64,          # uppercase is not the stored form
        ],
    )
    def test_rejects_invalid_digests(self, value):
        assert not is_valid_hash(value)

    def test_normalize_lowercases_mixed_case(self):
        assert normalize_hash("A" * 64) == "a" * 64

    def test_normalize_strips_surrounding_whitespace(self):
        assert normalize_hash(f"  {'a' * 64}\n") == "a" * 64

    def test_normalize_rejects_a_bad_value(self):
        with pytest.raises(InvalidHash):
            normalize_hash("not-a-hash")

    def test_normalize_rejects_a_non_string(self):
        with pytest.raises(InvalidHash):
            normalize_hash(12345)

    def test_error_message_names_the_field(self):
        """Error text should say which hash was wrong, not just that one was."""
        with pytest.raises(InvalidHash, match="commit hash"):
            normalize_hash("nope", label="commit hash")
