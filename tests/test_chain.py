"""Tests for aafbrowser.core.chain — multi-hop SourceClip walker."""
from __future__ import annotations

import aaf2
import pytest

from aafbrowser.core.chain import Hop, HopBranch, walk_chain, walk_chain_tree


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


def test_hop_captures_physical_track_number(chain_aaf):
    """
    Regression guard: PhysicalTrackNumber is a slot Property, not a
    Python attribute. Earlier chain-walk used getattr(slot, "PhysicalTrackNumber")
    which silently returned None on every real session AAF. Every hop in
    the chain fixture has PTN=7; this test must see them all.
    """
    with aaf2.open(str(chain_aaf), "r") as f:
        hops = walk_chain(f, _comp_mob(f))
    assert [h.physical_track_number for h in hops] == [7, 7, 7]


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


# ---------- walk_chain_tree (Phase 7 multi-input combiner recursion) ----------


def _comp_by_name(handle, name):
    for m in handle.content.mobs:
        if type(m).__name__ == "CompositionMob" and getattr(m, "name", None) == name:
            return m
    raise AssertionError(f"no CompositionMob named {name!r}")


def test_walk_chain_tree_matches_walk_chain_for_flat_chain(chain_aaf):
    """On a single-input chain, walk_chain_tree returns the same flat
    list of Hops as walk_chain (no HopBranch nodes)."""
    with aaf2.open(str(chain_aaf), "r") as f:
        comp = _comp_mob(f)
        flat = walk_chain(f, comp)
        tree = walk_chain_tree(f, comp)
    assert len(tree) == len(flat)
    for t, h in zip(tree, flat):
        assert isinstance(t, Hop)
        assert t.mob_class == h.mob_class
        assert t.terminal == h.terminal
        assert t.terminal_reason == h.terminal_reason


def test_walk_chain_tree_branches_on_multi_input_combiner(combiner_aaf):
    """Walking from a composition mob whose chain hits a multi-input
    OperationGroup mid-chain produces a HopBranch with one sub-chain
    per input. Each input recovers its own leaf SourceMob."""
    with aaf2.open(str(combiner_aaf), "r") as f:
        comp = _comp_by_name(f, "CombMidComp")
        tree = walk_chain_tree(f, comp)
    # First node = the comp itself (single-clip step into the master)
    # Second node = HopBranch (the master's slot.segment is the OG)
    assert len(tree) == 2
    assert isinstance(tree[0], Hop)
    assert tree[0].mob_class == "CompositionMob"
    branch = tree[1]
    assert isinstance(branch, HopBranch)
    assert branch.combiner_class == "OperationGroup"
    assert branch.operation_def_name == "TestStereoMix"
    assert branch.mob_class == "MasterMob"
    assert branch.mob_name == "CombMidMaster"
    # Two inputs, each a 2-hop chain (MasterMob -> SourceMob).
    assert len(branch.inputs) == 2
    for sub_chain in branch.inputs:
        assert len(sub_chain) == 2
        assert sub_chain[0].mob_class == "MasterMob"
        assert sub_chain[1].mob_class == "SourceMob"
        assert sub_chain[1].terminal is True
    # Recovered identities: input[0] -> CombSrcA (PTN 1), input[1] -> CombSrcB (PTN 2).
    assert branch.inputs[0][1].mob_name == "CombSrcA"
    assert branch.inputs[0][1].physical_track_number == 1
    assert branch.inputs[1][1].mob_name == "CombSrcB"
    assert branch.inputs[1][1].physical_track_number == 2


def test_walk_chain_tree_to_dict_round_trips(combiner_aaf):
    """HopBranch.to_dict carries _type marker and recursive inputs."""
    with aaf2.open(str(combiner_aaf), "r") as f:
        tree = walk_chain_tree(f, _comp_by_name(f, "CombMidComp"))
    branch = tree[1]
    d = branch.to_dict()
    assert d["_type"] == "hop_branch"
    assert d["combiner_class"] == "OperationGroup"
    assert d["operation_def_name"] == "TestStereoMix"
    assert len(d["inputs"]) == 2
    assert all(len(chain) == 2 for chain in d["inputs"])
    # Each leaf hop in each sub-chain is a Hop dict
    for chain in d["inputs"]:
        for hop_dict in chain:
            assert isinstance(hop_dict, dict)
            # Hop.to_dict has these keys (from Phase 3)
            assert "mob_class" in hop_dict
            assert "terminal" in hop_dict


def test_walk_chain_tree_max_depth_clipped(combiner_aaf):
    """max_depth=0 disables branching: a multi-input combiner becomes
    a normal terminal Hop with reason 'operation_group'."""
    with aaf2.open(str(combiner_aaf), "r") as f:
        tree = walk_chain_tree(f, _comp_by_name(f, "CombMidComp"), max_depth=0)
    # No HopBranch nodes
    assert all(isinstance(n, Hop) for n in tree)
    assert tree[-1].terminal is True
    assert tree[-1].terminal_reason == "operation_group"


def test_walk_chain_tree_cycle_safe(cycle_aaf):
    """The shared visited set prevents infinite recursion through cycles."""
    with aaf2.open(str(cycle_aaf), "r") as f:
        a = next(iter(f.content.mobs))
        tree = walk_chain_tree(f, a)
    assert all(isinstance(n, Hop) for n in tree)
    assert tree[-1].terminal is True
    assert tree[-1].terminal_reason == "cycle"


def test_walk_chain_tree_max_combiner_inputs_reports_truncation(combiner_aaf):
    """max_combiner_inputs=1 keeps only the first input and reports the
    rest as truncated."""
    with aaf2.open(str(combiner_aaf), "r") as f:
        tree = walk_chain_tree(
            f, _comp_by_name(f, "CombMidComp"), max_combiner_inputs=1
        )
    branch = tree[-1]
    assert isinstance(branch, HopBranch)
    assert len(branch.inputs) == 1
    assert branch.truncated_input_count == 1
