"""keccak commits for picks, anchored on Arc testnet (USDC gas, free faucet).

No web3 dependency: calldata is hand-encoded and the tx is signed with eth-account and sent over JSON-RPC.
If ARC_PRIVATE_KEY / PICK_REGISTRY_ADDRESS are unset or the RPC fails, picks stay "unanchored"
with their hash stored locally, and `anchor_pending` retries later.
"""
from __future__ import annotations

import json
import logging

import httpx
from eth_utils import keccak

from .config import settings

log = logging.getLogger(__name__)

COMMIT_SELECTOR = keccak(text="commit(bytes32,string)")[:4]
PICK_COMMITTED_TOPIC = "0x" + keccak(text="PickCommitted(bytes32,address,string,uint256)").hex()


def commit_payload(*, capper_id: str, source: str, post_id: str, text: str, event_ticker: str,
                   side_label: str, units: float, lock_price: float | None, posted_at: str) -> dict:
    return {
        "v": 1, "capper": capper_id, "source": source, "post_id": str(post_id), "text": text,
        "event": event_ticker, "side": side_label, "units": units,
        "gemini_price": lock_price, "posted_at": posted_at,
    }


def commit_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "0x" + keccak(text=canonical).hex()


def _encode_commit(hash_hex: str, pick_ref: str) -> bytes:
    h = bytes.fromhex(hash_hex.removeprefix("0x"))
    assert len(h) == 32
    ref = pick_ref.encode()
    padded = ref + b"\x00" * ((32 - len(ref) % 32) % 32)
    return (COMMIT_SELECTOR + h + (64).to_bytes(32, "big") + len(ref).to_bytes(32, "big") + padded)


def _rpc(method: str, params: list):
    r = httpx.post(settings.arc_rpc, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=20)
    r.raise_for_status()
    body = r.json()
    if "error" in body:
        raise RuntimeError(body["error"])
    return body["result"]


def anchor(hash_hex: str, pick_ref: str) -> str | None:
    """Send commit(hash, ref) to PickRegistry. Returns the tx hash, or None if anchoring is off/failed."""
    if not settings.chain_enabled:
        return None
    try:
        from eth_account import Account

        from eth_utils import to_checksum_address

        acct = Account.from_key(settings.arc_private_key)
        # eth-account refuses a lowercase "to", which is how explorers and RPCs often print addresses.
        contract = to_checksum_address(settings.arc_contract)
        data = "0x" + _encode_commit(hash_hex, pick_ref).hex()
        nonce = int(_rpc("eth_getTransactionCount", [acct.address, "pending"]), 16)
        gas_price = int(_rpc("eth_gasPrice", []), 16)
        call = {"from": acct.address, "to": contract, "data": data}
        gas = int(int(_rpc("eth_estimateGas", [call]), 16) * 1.3)
        tx = {"to": contract, "data": data, "nonce": nonce, "gas": gas,
              "gasPrice": gas_price, "chainId": settings.arc_chain_id, "value": 0}
        signed = acct.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
        return _rpc("eth_sendRawTransaction", ["0x" + raw.hex().removeprefix("0x")])
    except Exception as e:  # never block a lock on the chain
        log.warning("Arc anchoring failed, pick stays unanchored: %s", e)
        return None


def tx_url(tx_hash: str | None) -> str | None:
    return f"{settings.arc_explorer}/tx/{tx_hash}" if tx_hash else None


def recent_commits(from_block: str | None = None) -> list[dict]:
    """Free alternative to a paid stream: read PickCommitted logs straight from the public RPC.
    Arc's RPC caps eth_getLogs at 10,000 blocks, so by default this reads the latest 9,000."""
    if not settings.arc_contract:
        return []
    if from_block is None:
        from_block = hex(max(0, int(_rpc("eth_blockNumber", []), 16) - 9000))
    logs = _rpc("eth_getLogs", [{"address": settings.arc_contract, "fromBlock": from_block,
                                 "toBlock": "latest", "topics": [PICK_COMMITTED_TOPIC]}])
    return [{"hash": l["topics"][1], "tx": l["transactionHash"], "block": int(l["blockNumber"], 16)} for l in logs]
