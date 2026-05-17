"""Tests for the core.recovery seam.

Every branch of the classification dispatch is exercised with synthetic
inputs — no fixture AAFs required, because recovery.classify takes plain
values and returns a plain RecoveryResult.
"""
from __future__ import annotations

from aafbrowser.core import recovery as recovery_mod


def _classify(**overrides):
    """Defaults that produce the most boring case ('avid_chain_walk' miss);
    each test overrides only the kwargs relevant to its branch."""
    base = dict(
        authoring_kind="unknown",
        pan_channel=None,
        is_recorder=False,
        terminal_class=None,
        terminal_reason=None,
        source_mob_name=None,
        mic_identity=None,
    )
    base.update(overrides)
    return recovery_mod.classify(**base)


# --- Premiere branches ------------------------------------------------------


def test_premiere_stereo_split_pan_left():
    r = _classify(authoring_kind="premiere", pan_channel="L")
    assert r.status == "recoverable"
    assert r.method == "premiere_stereo_split_pan_l"


def test_premiere_stereo_split_pan_right():
    r = _classify(authoring_kind="premiere", pan_channel="R")
    assert r.status == "recoverable"
    assert r.method == "premiere_stereo_split_pan_r"


def test_premiere_stereo_split_name_l_suffix():
    r = _classify(authoring_kind="premiere", source_mob_name="Boom01_L")
    assert r.status == "recoverable"
    assert r.method == "premiere_stereo_split_name"


def test_premiere_stereo_split_name_r_suffix():
    r = _classify(authoring_kind="premiere", source_mob_name="Boom01_R")
    assert r.status == "recoverable"
    assert r.method == "premiere_stereo_split_name"


def test_premiere_polywav_indeterminate():
    r = _classify(authoring_kind="premiere", source_mob_name="Audio 3")
    assert r.status == "unrecoverable"
    assert r.method == "premiere_polywav_indeterminate"


def test_premiere_unmatched_falls_through_to_generic_dispatch():
    """Premiere with no pan / no _L_R / no polywav pattern falls through
    to the generic is_recorder / terminal_class branches."""
    r = _classify(
        authoring_kind="premiere",
        source_mob_name="Some Custom Name",
        is_recorder=True,
    )
    assert r.status == "recoverable"
    assert r.method == "avid_chain_walk"


# --- Avid / generic chain-walk ---------------------------------------------


def test_avid_chain_walk_when_recorder_signal_present():
    r = _classify(authoring_kind="avid", is_recorder=True)
    assert r.status == "recoverable"
    assert r.method == "avid_chain_walk"


def test_chain_ended_at_sourcemob_without_recorder_signal_is_ambiguous():
    r = _classify(authoring_kind="avid", terminal_class="SourceMob")
    assert r.status == "ambiguous"
    assert r.method == "no_recorder_signal"


# --- Unrecoverable terminals -----------------------------------------------


def test_chain_terminated_at_filler_is_unrecoverable():
    r = _classify(terminal_reason="filler")
    assert r.status == "unrecoverable"
    assert r.method == "chain_terminated_filler"


def test_chain_terminated_unknown_uses_unknown_suffix():
    r = _classify(terminal_reason=None)
    assert r.status == "unrecoverable"
    assert r.method == "chain_terminated_unknown"


def test_chain_terminated_at_operation_group():
    r = _classify(terminal_reason="operation_group")
    assert r.status == "unrecoverable"
    assert r.method == "chain_terminated_operation_group"


# --- Dispatch order edge cases ---------------------------------------------


def test_premiere_pan_takes_priority_over_name_suffix():
    """If both pan_channel and _L/_R suffix are present, pan wins."""
    r = _classify(
        authoring_kind="premiere",
        pan_channel="L",
        source_mob_name="Boom_R",
    )
    assert r.method == "premiere_stereo_split_pan_l"


def test_avid_authoring_does_not_invoke_premiere_polywav_pattern():
    """An Avid clip named 'Audio 3' must NOT be classified as polywav."""
    r = _classify(
        authoring_kind="avid",
        source_mob_name="Audio 3",
        terminal_class="SourceMob",
    )
    assert r.status == "ambiguous"
    assert r.method == "no_recorder_signal"


def test_polywav_pattern_requires_exact_match():
    """'Audio 3 extra' should not match polywav indeterminacy."""
    r = _classify(authoring_kind="premiere", source_mob_name="Audio 3 extra")
    # Falls through to generic chain-walk; no recorder signal → unrecoverable.
    assert r.status == "unrecoverable"
    assert r.method == "chain_terminated_unknown"
