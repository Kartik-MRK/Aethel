"""Required worker configuration differs between deploying and using a contract."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aethel.commands.provenance import configured_chain
from aethel.core.errors import AethelError


def config(**changes):
    return SimpleNamespace(**{
        "chain_rpc_url": "https://rpc.example.test",
        "chain_id": 11155111,
        "log_id": "ab" * 32,
        "anchor_contract": "0x" + "12" * 20,
        "chain_confirmations": 2,
        **changes,
    })


@pytest.mark.parametrize("deploying", [True, False])
def test_missing_settings_message_matches_command_requirements(deploying):
    empty = config(chain_rpc_url=None, chain_id=None, log_id=None, anchor_contract=None)
    with pytest.raises(AethelError) as exc:
        configured_chain(empty, deploying=deploying)
    expected = "Set AETHEL_CHAIN_RPC, AETHEL_CHAIN_ID, AETHEL_LOG_ID"
    if not deploying:
        expected += ", AETHEL_ANCHOR_CONTRACT"
    assert str(exc.value) == expected


def test_deployment_accepts_an_unset_contract(monkeypatch):
    from aethel.provenance import chain

    constructor = Mock()
    monkeypatch.setattr(chain, "ChainClient", constructor)
    settings = config(anchor_contract=None)
    assert configured_chain(settings, deploying=True) is constructor.return_value
    constructor.assert_called_once_with(
        settings.chain_rpc_url, settings.chain_id, None, settings.log_id,
        confirmations=settings.chain_confirmations,
    )


def test_publication_requires_a_deployed_contract():
    with pytest.raises(AethelError, match="^Set AETHEL_ANCHOR_CONTRACT$"):
        configured_chain(config(anchor_contract=None))


def test_message_lists_only_missing_settings():
    with pytest.raises(AethelError, match="^Set AETHEL_CHAIN_RPC$"):
        configured_chain(config(chain_rpc_url=None), deploying=True)


def test_missing_web3_during_construction_has_install_guidance(monkeypatch):
    from aethel.provenance import chain

    monkeypatch.setattr(chain, "ChainClient", Mock(side_effect=ImportError("web3")))
    with pytest.raises(AethelError, match="Install the provenance extra"):
        configured_chain(config())
