"""Hub environment loading does not import outbound credentials."""

from hub.config import HubConfig


def test_hub_dotenv_ignores_worker_signing_and_pinning_keys(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("AETHEL_ANCHOR_KEY", "AETHEL_PINATA_JWT", "AETHEL_CHAIN_ID"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text("AETHEL_ANCHOR_KEY=private\nAETHEL_PINATA_JWT=private\nAETHEL_CHAIN_ID=11155111\n")
    config = HubConfig.from_environment()
    import os

    assert config.chain_id == 11155111
    assert "AETHEL_ANCHOR_KEY" not in os.environ
    assert "AETHEL_PINATA_JWT" not in os.environ
