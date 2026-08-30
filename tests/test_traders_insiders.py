from __future__ import annotations

from snipe_rugg.dev.models import Severity
from snipe_rugg.traders.insiders import (
    InsiderSignalKind,
    build_funding_graph,
    detect_insider_signals,
)

CREATOR = "CreatorWallet11111111111111111111111111111"
ALICE = "AliceWallet111111111111111111111111111111"
BOB = "BobWallet1111111111111111111111111111111111"
CAROL = "CarolWallet111111111111111111111111111111"
FUNDER_X = "FunderX111111111111111111111111111111111111"
FUNDER_Y = "FunderY111111111111111111111111111111111111"


def test_wallet_directly_funded_by_creator_is_flagged_high():
    graph = build_funding_graph({ALICE: {CREATOR}, BOB: set()})
    signals = detect_insider_signals(graph, top_traders=[ALICE, BOB], creator=CREATOR)

    assert len(signals) == 1
    assert signals[0].wallet == ALICE
    assert signals[0].kind is InsiderSignalKind.DIRECTLY_FUNDED_BY_CREATOR
    assert signals[0].severity is Severity.HIGH
    assert signals[0].related_wallets == [CREATOR]


def test_wallet_sharing_a_funder_with_creator_is_flagged_medium():
    graph = build_funding_graph({ALICE: {FUNDER_X}, CREATOR: {FUNDER_X}})
    signals = detect_insider_signals(graph, top_traders=[ALICE], creator=CREATOR)

    assert len(signals) == 1
    assert signals[0].kind is InsiderSignalKind.SHARED_FUNDER_WITH_CREATOR
    assert signals[0].severity is Severity.MEDIUM


def test_direct_funding_signal_suppresses_the_weaker_shared_funder_signal():
    # Alice is both directly funded by the creator *and* (trivially) shares
    # that same funder with the creator - only the stronger signal should fire.
    graph = build_funding_graph({ALICE: {CREATOR}, CREATOR: set()})
    signals = detect_insider_signals(graph, top_traders=[ALICE], creator=CREATOR)

    assert len(signals) == 1
    assert signals[0].kind is InsiderSignalKind.DIRECTLY_FUNDED_BY_CREATOR


def test_two_top_traders_sharing_a_funder_form_a_wallet_cluster():
    graph = build_funding_graph({ALICE: {FUNDER_X}, BOB: {FUNDER_X}, CAROL: {FUNDER_Y}})
    signals = detect_insider_signals(graph, top_traders=[ALICE, BOB, CAROL], creator=None)

    by_wallet = {s.wallet: s for s in signals}
    assert set(by_wallet) == {ALICE, BOB}
    assert by_wallet[ALICE].kind is InsiderSignalKind.WALLET_CLUSTER
    assert by_wallet[ALICE].related_wallets == [BOB]
    assert by_wallet[BOB].related_wallets == [ALICE]


def test_unrelated_wallets_produce_no_signals():
    graph = build_funding_graph({ALICE: {FUNDER_X}, BOB: {FUNDER_Y}})
    signals = detect_insider_signals(graph, top_traders=[ALICE, BOB], creator=None)

    assert signals == []


def test_no_creator_means_no_creator_based_signals_but_clusters_still_detected():
    graph = build_funding_graph({ALICE: {FUNDER_X}, BOB: {FUNDER_X}})
    signals = detect_insider_signals(graph, top_traders=[ALICE, BOB], creator=None)

    assert all(s.kind is InsiderSignalKind.WALLET_CLUSTER for s in signals)


def test_creator_is_never_flagged_against_itself_for_creator_based_signals():
    # The creator can legitimately appear in top_traders (it traded its own
    # token) - it must never be checked for "shares a funder with the
    # creator" against itself.
    graph = build_funding_graph({CREATOR: {FUNDER_X}, ALICE: {FUNDER_X}})
    signals = detect_insider_signals(graph, top_traders=[CREATOR, ALICE], creator=CREATOR)

    creator_signals = [
        s
        for s in signals
        if s.wallet == CREATOR and s.kind in (InsiderSignalKind.DIRECTLY_FUNDED_BY_CREATOR, InsiderSignalKind.SHARED_FUNDER_WITH_CREATOR)
    ]
    assert creator_signals == []
