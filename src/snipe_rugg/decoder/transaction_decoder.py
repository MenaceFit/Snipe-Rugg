"""Raw getTransaction(jsonParsed) response -> DecodedTransaction (spec section 10).

Deliberately balance-delta-based rather than per-program instruction parsing:
pre/postBalances and pre/postTokenBalances tell us exactly what a transaction did
to SOL and SPL token holdings without needing each DEX's IDL. System/Token/Stake
program instructions still get read directly, since the RPC already parses those
into a structured "type"/"info" shape (see EventType classification in
classifier.py) — but Pump.fun/PumpSwap/Raydium/Jupiter instructions arrive
unparsed and are only used for DEX labeling (constants.dex_label), not decoded
instruction-by-instruction.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from snipe_rugg.decoder.models import DecodedTransaction, SolBalanceChange, SplBalanceChange


class TransactionDecoder:
    def decode(self, raw: dict[str, Any]) -> DecodedTransaction:
        transaction = raw.get("transaction") or {}
        message = transaction.get("message") or {}
        meta = raw.get("meta") or {}
        signatures = transaction.get("signatures") or []
        signature = signatures[0] if signatures else ""

        account_keys = message.get("accountKeys") or []
        accounts = [self._pubkey(a) for a in account_keys]
        signer = next((self._pubkey(a) for a in account_keys if self._is_signer(a)), None)

        top_level = message.get("instructions") or []
        inner: list[dict[str, Any]] = []
        for group in meta.get("innerInstructions") or []:
            inner.extend(group.get("instructions") or [])

        programs = sorted(
            {instr["programId"] for instr in (*top_level, *inner) if instr.get("programId")}
        )

        block_time_raw = raw.get("blockTime")
        block_time = datetime.fromtimestamp(block_time_raw, tz=UTC) if block_time_raw is not None else None

        return DecodedTransaction(
            signature=signature,
            slot=raw.get("slot", 0),
            block_time=block_time,
            success=meta.get("err") is None,
            err=meta.get("err"),
            signer=signer,
            programs=programs,
            accounts=accounts,
            instructions=list(top_level),
            inner_instructions=inner,
            sol_balance_changes=self._decode_sol_balances(accounts, meta),
            spl_balance_changes=self._decode_spl_balances(accounts, meta),
            log_messages=list(meta.get("logMessages") or []),
            fee_lamports=meta.get("fee", 0),
            raw=raw,
        )

    @staticmethod
    def _pubkey(account: Any) -> str:
        if isinstance(account, dict):
            return account.get("pubkey", "")
        return str(account)

    @staticmethod
    def _is_signer(account: Any) -> bool:
        return bool(account.get("signer")) if isinstance(account, dict) else False

    @staticmethod
    def _decode_sol_balances(accounts: list[str], meta: dict[str, Any]) -> list[SolBalanceChange]:
        pre = meta.get("preBalances") or []
        post = meta.get("postBalances") or []
        changes = []
        for i, account in enumerate(accounts):
            if i >= len(pre) or i >= len(post) or pre[i] == post[i]:
                continue
            changes.append(
                SolBalanceChange(account=account, account_index=i, pre_lamports=pre[i], post_lamports=post[i])
            )
        return changes

    @staticmethod
    def _decode_spl_balances(accounts: list[str], meta: dict[str, Any]) -> list[SplBalanceChange]:
        pre_by_index = {b["accountIndex"]: b for b in (meta.get("preTokenBalances") or [])}
        post_by_index = {b["accountIndex"]: b for b in (meta.get("postTokenBalances") or [])}
        changes = []
        for idx in sorted(set(pre_by_index) | set(post_by_index)):
            pre, post = pre_by_index.get(idx), post_by_index.get(idx)
            source = post or pre
            assert source is not None
            pre_amount = int(pre["uiTokenAmount"]["amount"]) if pre else 0
            post_amount = int(post["uiTokenAmount"]["amount"]) if post else 0
            if pre_amount == post_amount:
                continue
            changes.append(
                SplBalanceChange(
                    account=accounts[idx] if idx < len(accounts) else "",
                    owner=source.get("owner"),
                    mint=source["mint"],
                    decimals=source["uiTokenAmount"]["decimals"],
                    account_index=idx,
                    pre_amount=pre_amount,
                    post_amount=post_amount,
                )
            )
        return changes
