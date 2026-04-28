"""
Programmatic fixture AAFs.

We never check binary AAFs into the repo. Each fixture is a tiny AAF
constructed by pyaaf2 in a temp directory. Tests open the resulting file
read-only and treat it as a black box.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import aaf2
import pytest


def _write_minimal_aaf(path: Path) -> None:
    """A MasterMob with one timeline slot containing a Sequence segment."""
    with aaf2.open(str(path), "w") as f:
        mob = f.create.MasterMob("FixtureMob")
        f.content.mobs.append(mob)
        slot = mob.create_sound_slot(edit_rate=48000)
        slot.segment.length = 96000


def _write_two_mob_aaf(path: Path) -> None:
    """Two Mobs so resolver and find tests have something to bite on."""
    with aaf2.open(str(path), "w") as f:
        mob_a = f.create.MasterMob("Channel_1_Host")
        mob_b = f.create.MasterMob("Channel_2_Contestant")
        f.content.mobs.append(mob_a)
        f.content.mobs.append(mob_b)
        for m in (mob_a, mob_b):
            slot = m.create_sound_slot(edit_rate=48000)
            slot.segment.length = 48000


def _write_chain_aaf(path: Path) -> None:
    """
    Three-hop CompositionMob -> MasterMob -> SourceMob chain so chain-walk
    tests have a known shape to bite on. The SourceMob's slot is the
    natural essence terminal (Sequence with no SourceClip).

    Each slot carries an explicit PhysicalTrackNumber=7 so chain-walk
    can be asserted to surface it (it's a slot Property accessed via
    properties() iteration, not a Python attribute).
    """
    with aaf2.open(str(path), "w") as f:
        src_mob = f.create.SourceMob("SrcEPWD0209_HOSTA")
        f.content.mobs.append(src_mob)
        src_mob.descriptor = f.create.ImportDescriptor()
        src_slot = src_mob.create_sound_slot(edit_rate=48000)
        src_slot["PhysicalTrackNumber"].value = 7

        master_mob = f.create.MasterMob("MstHostA")
        f.content.mobs.append(master_mob)
        m_slot = master_mob.create_sound_slot(edit_rate=48000)
        m_slot["PhysicalTrackNumber"].value = 7
        m_clip = f.create.SourceClip(
            start=0, length=48000,
            mob_id=src_mob.mob_id, slot_id=src_slot.slot_id,
        )
        m_slot.segment = m_clip

        comp_mob = f.create.CompositionMob("PW_213_HOSTA ISO")
        f.content.mobs.append(comp_mob)
        c_slot = comp_mob.create_sound_slot(edit_rate=48000)
        c_slot["PhysicalTrackNumber"].value = 7
        c_clip = f.create.SourceClip(
            start=0, length=48000,
            mob_id=master_mob.mob_id, slot_id=m_slot.slot_id,
        )
        c_slot.segment = c_clip


def _write_cycle_aaf(path: Path) -> None:
    """Two SourceMobs whose slots reference each other (cycle)."""
    with aaf2.open(str(path), "w") as f:
        a = f.create.SourceMob("A")
        b = f.create.SourceMob("B")
        f.content.mobs.append(a)
        f.content.mobs.append(b)
        a.descriptor = f.create.ImportDescriptor()
        b.descriptor = f.create.ImportDescriptor()
        a_slot = a.create_sound_slot(edit_rate=48000)
        b_slot = b.create_sound_slot(edit_rate=48000)
        # A's slot points at B's slot
        a_slot.segment = f.create.SourceClip(
            start=0, length=48000, mob_id=b.mob_id, slot_id=b_slot.slot_id,
        )
        # B's slot points back at A's slot
        b_slot.segment = f.create.SourceClip(
            start=0, length=48000, mob_id=a.mob_id, slot_id=a_slot.slot_id,
        )


def _write_multi_track_aaf(path: Path) -> None:
    """
    A CompositionMob with two audio tracks (different PhysicalTrackNumbers)
    and one video track. The first audio track has two SourceClip
    components on its Sequence so list_clips has a real cursor to advance.
    Each audio chain ends at a SourceMob with a distinct PTN so the
    operator-layer mic-identity recovery can be asserted per clip.
    """
    with aaf2.open(str(path), "w") as f:
        # Three SourceMobs (recorder identities) with distinct PTNs.
        sources = []
        for i, name in enumerate(("SrcA", "SrcB", "SrcC"), start=1):
            sm = f.create.SourceMob(name)
            f.content.mobs.append(sm)
            sm.descriptor = f.create.ImportDescriptor()
            s = sm.create_sound_slot(edit_rate=48000)
            s["PhysicalTrackNumber"].value = i
            sources.append((sm, s))

        # One MasterMob per source, one slot each pointing at the SourceMob.
        masters = []
        for (sm, s_slot), name in zip(sources, ("MstA", "MstB", "MstC")):
            mm = f.create.MasterMob(name)
            f.content.mobs.append(mm)
            mslot = mm.create_sound_slot(edit_rate=48000)
            mslot["PhysicalTrackNumber"].value = s_slot["PhysicalTrackNumber"].value
            mslot.segment = f.create.SourceClip(
                start=0, length=24000,
                mob_id=sm.mob_id, slot_id=s_slot.slot_id,
            )
            masters.append((mm, mslot))

        # CompositionMob: two audio slots (PTN 1 and 2) and one video slot.
        comp = f.create.CompositionMob("MultiTrackComp")
        f.content.mobs.append(comp)

        # Audio slot 1 (PTN=1) with two clips on its Sequence.
        a1 = comp.create_sound_slot(edit_rate=48000)
        a1["PhysicalTrackNumber"].value = 1
        for mm, mslot in (masters[0], masters[1]):
            a1.segment.components.append(
                f.create.SourceClip(
                    start=0, length=24000,
                    mob_id=mm.mob_id, slot_id=mslot.slot_id,
                )
            )

        # Audio slot 2 (PTN=2) with one clip.
        a2 = comp.create_sound_slot(edit_rate=48000)
        a2["PhysicalTrackNumber"].value = 2
        a2.segment.components.append(
            f.create.SourceClip(
                start=0, length=48000,
                mob_id=masters[2][0].mob_id, slot_id=masters[2][1].slot_id,
            )
        )

        # Video slot — no PTN set, no clips. Operator ordinal falls back
        # to slot_id.
        v = comp.create_picture_slot(edit_rate=25)
        v.segment.length = 100


def _write_broken_ref_aaf(path: Path) -> None:
    """A MasterMob whose SourceClip references a MobID that's not in the file."""
    from aaf2.mobid import MobID
    with aaf2.open(str(path), "w") as f:
        master = f.create.MasterMob("BrokenRef")
        f.content.mobs.append(master)
        slot = master.create_sound_slot(edit_rate=48000)
        # Generate a random MobID that won't be added to f.content.mobs
        ghost_id = MobID.new()
        slot.segment = f.create.SourceClip(
            start=0, length=48000, mob_id=ghost_id, slot_id=1,
        )


@pytest.fixture(scope="session")
def fixture_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("aafbrowser_fixtures")


@pytest.fixture(scope="session")
def minimal_aaf(fixture_dir: Path) -> Path:
    p = fixture_dir / "minimal.aaf"
    _write_minimal_aaf(p)
    return p


@pytest.fixture(scope="session")
def two_mob_aaf(fixture_dir: Path) -> Path:
    p = fixture_dir / "two_mob.aaf"
    _write_two_mob_aaf(p)
    return p


@pytest.fixture(scope="session")
def chain_aaf(fixture_dir: Path) -> Path:
    p = fixture_dir / "chain.aaf"
    _write_chain_aaf(p)
    return p


@pytest.fixture(scope="session")
def multi_track_aaf(fixture_dir: Path) -> Path:
    p = fixture_dir / "multi_track.aaf"
    _write_multi_track_aaf(p)
    return p


@pytest.fixture(scope="session")
def cycle_aaf(fixture_dir: Path) -> Path:
    p = fixture_dir / "cycle.aaf"
    _write_cycle_aaf(p)
    return p


@pytest.fixture(scope="session")
def broken_ref_aaf(fixture_dir: Path) -> Path:
    p = fixture_dir / "broken_ref.aaf"
    _write_broken_ref_aaf(p)
    return p


@pytest.fixture
def minimal_aaf_copy(minimal_aaf: Path, tmp_path: Path) -> Path:
    """
    Per-test copy so any (theoretical, prohibited) write would not leak
    across tests. Read-only round-trip tests use this to hash.
    """
    dst = tmp_path / "minimal_copy.aaf"
    shutil.copy2(minimal_aaf, dst)
    return dst
