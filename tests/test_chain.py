"""Tests for aafbrowser.core.chain — multi-hop SourceClip walker."""
from __future__ import annotations

import aaf2
import pytest

from aafbrowser.core.chain import Hop, walk_chain


def _comp_mob(handle):
    for m in handle.content.mobs:
        if type(m).__name__ == "CompositionMob":
            return m
    raise AssertionError("no CompositionMob in fixture")


def _master_mob(handle):
    for m in handle.content.mobs:
        if type(m).__name__ == "MasterMob":
            return m
    raise AssertionError("no MasterMob in fixture")


def test_three_hop_chain_terminates_at_source_mob(chain_aaf):
    with aaf2.open(str(chain_aaf), "r") as f:
        comp = _comp_mob(f)
        hops = walk_chain(f, comp)
    assert len(hops) == 3
    assert [h.mob_class for h in hops] == [
        "CompositionMob", "MasterMob", "SourceMob"
    ]
    assert [h.terminal for h in hops] == [False, False, True]
    assert hops[-1].terminal_reason == "essence"
    # Names propagate
    assert hops[0].mob_name == "PW_213_HOSTA ISO"
    assert hops[1].mob_name == "MstHostA"
    assert hops[2].mob_name == "SrcEPWD0209_HOSTA"


def test_hop_carries_edit_rate_and_slot_metadata(chain_aaf):
    with aaf2.open(str(chain_aaf), "r") as f:
        hops = walk_chain(f, _comp_mob(f))
    for h in hops:
        assert h.edit_rate == "48000/1"
        assert h.slot_id == 1
    # The CompositionMob and MasterMob hops have a SourceClip segment
    assert hops[0].segment_class == "SourceClip"
    assert hops[1].segment_class == "SourceClip"
    # The SourceMob's terminal segment is a Sequence (default for sound slots)
    assert hops[2].segment_class == "Sequence"


def test_walk_chain_accepts_mob_id_string(chain_aaf):
    with aaf2.open(str(chain_aaf), "r") as f:
        urn = str(_comp_mob(f).mob_id)
    with aaf2.open(str(chain_aaf), "r") as f:
        hops = walk_chain(f, urn)
    assert len(hops) == 3
    assert hops[0].mob_class == "CompositionMob"


def test_walk_chain_from_master_mob_skips_first_hop(chain_aaf):
    with aaf2.open(str(chain_aaf), "r") as f:
        master = _master_mob(f)
        hops = walk_chain(f, master)
    # Starting at the MasterMob skips the CompositionMob
    assert [h.mob_class for h in hops] == ["MasterMob", "SourceMob"]


def test_walk_chain_unknown_mob_id_raises(chain_aaf):
    with aaf2.open(str(chain_aaf), "r") as f:
        with pytest.raises(ValueError):
            walk_chain(f, "not-a-mob-id")


def test_hop_to_dict_is_json_friendly(chain_aaf):
    with aaf2.open(str(chain_aaf), "r") as f:
        hops = walk_chain(f, _comp_mob(f))
    d = hops[0].to_dict()
    assert d["mob_class"] == "CompositionMob"
    assert d["terminal"] is False
    assert d["terminal_reason"] is None


# --- terminal-reason coverage ---


def test_cycle_terminates_with_cycle_reason(cycle_aaf):
    with aaf2.open(str(cycle_aaf), "r") as f:
        a = next(iter(f.content.mobs))
        hops = walk_chain(f, a)
    # A -> B -> back to A (cycle terminal)
    assert hops[-1].terminal is True
    assert hops[-1].terminal_reason == "cycle"
    # Should be at most 3 hops (A, B, A-as-cycle-marker)
    assert 2 <= len(hops) <= 3


def test_broken_reference_terminates_with_broken_ref(broken_ref_aaf):
    with aaf2.open(str(broken_ref_aaf), "r") as f:
        master = next(iter(f.content.mobs))
        hops = walk_chain(f, master)
    assert hops[-1].terminal is True
    assert hops[-1].terminal_reason == "broken_ref"


def test_max_hops_enforced(chain_aaf):
    """
    max_hops bounds the number of *advance* iterations. With max_hops=1
    we walk one hop, run out of budget, and append a synthetic terminal
    marker — so the natural-terminal SourceMob hop never appears.
    """
    with aaf2.open(str(chain_aaf), "r") as f:
        hops = walk_chain(f, _comp_mob(f), max_hops=1)
    assert hops[-1].terminal is True
    assert hops[-1].terminal_reason == "max_hops_reached"
    # The natural 3-hop terminal would be a SourceMob; clipped before that
    assert all(h.mob_class != "SourceMob" for h in hops)


def test_walk_chain_never_returns_empty_list(chain_aaf):
    """Even a single-mob start must yield at least one hop."""
    with aaf2.open(str(chain_aaf), "r") as f:
        # Single SourceMob -> no chain to follow, but we still get 1 hop
        src = next(m for m in f.content.mobs if type(m).__name__ == "SourceMob")
        hops = walk_chain(f, src)
    assert len(hops) == 1
    assert hops[0].terminal is True
    assert hops[0].mob_class == "SourceMob"


def test_walk_chain_from_source_clip(chain_aaf):
    """Starting from a SourceClip should produce the same chain past it."""
    with aaf2.open(str(chain_aaf), "r") as f:
        comp = _comp_mob(f)
        clip = list(comp.slots)[0].segment
        assert type(clip).__name__ == "SourceClip"
        hops = walk_chain(f, clip)
    # Starts at the MasterMob (the clip's target), descends to SourceMob
    assert [h.mob_class for h in hops] == ["MasterMob", "SourceMob"]
