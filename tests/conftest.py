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


def _write_combiner_aaf(path: Path) -> None:
    """
    A CompositionMob with one audio slot containing two components:
      [0] A plain SourceClip pointing at MstC (control case).
      [1] An OperationGroup combining SourceClips → MstA + MstB
          (multi-input combiner — Phase 7's tree walk recursion target).

    Each SourceMob has a distinct PhysicalTrackNumber (1, 2, 3) so the
    chain-walk tests can assert per-input recovery on the combiner.

    Constructing OperationGroups in pyaaf2 requires registering an
    OperationDef in the dictionary first; that's done inline here.
    """
    from aaf2.auid import AUID

    with aaf2.open(str(path), "w") as f:
        # Custom 2-input audio mix OperationDef (synthetic AUID; doesn't
        # need to match a real AAF spec — only needs to be unique).
        od_auid = AUID("aaf12345-0000-0000-0000-000000000002")
        op_def = f.create.OperationDef(od_auid, "TestStereoMix",
                                        "2-input audio mix (test fixture)")
        op_def.media_kind = "Sound"
        op_def["NumberInputs"].value = 2
        f.dictionary.register_def(op_def)

        # Three SourceMobs, each on a distinct recorder PTN.
        sources = []
        for i, name in enumerate(("CombSrcA", "CombSrcB", "CombSrcC"), start=1):
            sm = f.create.SourceMob(name)
            f.content.mobs.append(sm)
            sm.descriptor = f.create.ImportDescriptor()
            s = sm.create_sound_slot(edit_rate=48000)
            s["PhysicalTrackNumber"].value = i
            sources.append((sm, s))

        # MasterMob per source.
        masters = []
        for (sm, s_slot), name in zip(sources, ("CombMstA", "CombMstB", "CombMstC")):
            mm = f.create.MasterMob(name)
            f.content.mobs.append(mm)
            mslot = mm.create_sound_slot(edit_rate=48000)
            mslot.segment.components.append(f.create.SourceClip(
                start=0, length=24000,
                mob_id=sm.mob_id, slot_id=s_slot.slot_id,
            ))
            masters.append((mm, mslot))

        # CompositionMob with two clips: a control SourceClip and a
        # multi-input OperationGroup at the top level (slot.segment is
        # a Sequence containing both).
        comp = f.create.CompositionMob("CombinerComp")
        f.content.mobs.append(comp)
        slot = comp.create_sound_slot(edit_rate=48000)
        slot["PhysicalTrackNumber"].value = 1

        # Component [0]: plain SourceClip → CombMstC
        slot.segment.components.append(f.create.SourceClip(
            start=0, length=24000,
            mob_id=masters[2][0].mob_id, slot_id=masters[2][1].slot_id,
        ))

        # Component [1]: OperationGroup combining CombMstA + CombMstB
        og = f.create.OperationGroup(op_def)
        og.length = 24000
        og["InputSegments"].value = [
            f.create.SourceClip(start=0, length=24000,
                                mob_id=masters[0][0].mob_id,
                                slot_id=masters[0][1].slot_id),
            f.create.SourceClip(start=0, length=24000,
                                mob_id=masters[1][0].mob_id,
                                slot_id=masters[1][1].slot_id),
        ]
        slot.segment.components.append(og)

        # Second comp mob: a chain that hits a multi-input OperationGroup
        # MID-CHAIN (not at the top level). Used to exercise walk_chain_tree's
        # in-loop branching: walking from this comp mob → goes through a
        # SourceClip → lands on a MasterMob whose slot's segment IS the
        # multi-input OG → chain forks.
        mid_master = f.create.MasterMob("CombMidMaster")
        f.content.mobs.append(mid_master)
        mid_slot = mid_master.create_sound_slot(edit_rate=48000)
        mid_combiner = f.create.OperationGroup(op_def)
        mid_combiner.length = 24000
        mid_combiner["InputSegments"].value = [
            f.create.SourceClip(start=0, length=24000,
                                mob_id=masters[0][0].mob_id,
                                slot_id=masters[0][1].slot_id),
            f.create.SourceClip(start=0, length=24000,
                                mob_id=masters[1][0].mob_id,
                                slot_id=masters[1][1].slot_id),
        ]
        mid_slot.segment = mid_combiner

        comp2 = f.create.CompositionMob("CombMidComp")
        f.content.mobs.append(comp2)
        comp2_slot = comp2.create_sound_slot(edit_rate=48000)
        comp2_slot["PhysicalTrackNumber"].value = 1
        comp2_slot.segment.components.append(f.create.SourceClip(
            start=0, length=24000,
            mob_id=mid_master.mob_id, slot_id=mid_slot.slot_id,
        ))


def _add_premiere_identification(f) -> None:
    """Append a Premiere Identification entry to the file's
    Header.IdentificationList. detect_authoring_kind reads the LAST
    entry, so this overrides pyaaf2's default 'PyAAF' identification."""
    import datetime
    from aaf2.auid import AUID
    ident = f.create.Identification()
    ident["CompanyName"].value = "Adobe Inc."
    ident["ProductName"].value = "Adobe Premiere Pro 24.0"
    ident["ProductVersionString"].value = "24.0.0"
    ident["ProductID"].value = AUID("11111111-2222-3333-4444-555555555555")
    ident["Date"].value = datetime.datetime(2026, 1, 1, 12, 0, 0)
    ident["GenerationAUID"].value = AUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    f.header["IdentificationList"].append(ident)


def _write_premiere_stereo_split_aaf(path: Path) -> None:
    """
    Synthetic Premiere AAF with the stereo-split pattern from
    docs/premiere-aaf-channel-recovery.md:

      CompositionMob "Premiere Stereo Comp"
        slot 1 (Sound): segment = OperationGroup "Mono Audio Pan"
          Parameters[0] = ConstantValue(AAFRational(0, 100M))   # Pan = 0.0 = LEFT
          InputSegments[0] = SourceClip → MasterMob "Audio 1_L"
        slot 2 (Sound): segment = OperationGroup "Mono Audio Pan"
          Parameters[0] = ConstantValue(AAFRational(100M, 100M))  # Pan = 1.0 = RIGHT
          InputSegments[0] = SourceClip → MasterMob "Audio 1_R"

    Authoring info marked as Adobe Premiere Pro.
    """
    from aaf2.auid import AUID
    from aaf2.rational import AAFRational

    with aaf2.open(str(path), "w") as f:
        _add_premiere_identification(f)

        # Register Mono Audio Pan OperationDef + a Pan parameter def.
        pan_op_auid = AUID("22222222-3333-4444-5555-666666666666")
        pan_def = f.create.OperationDef(pan_op_auid, "Mono Audio Pan",
                                         "Premiere stereo-split pan")
        pan_def.media_kind = "Sound"
        pan_def["NumberInputs"].value = 1
        f.dictionary.register_def(pan_def)

        pan_param_auid = AUID("33333333-4444-5555-6666-777777777777")
        pan_param_def = f.create.ParameterDef(
            pan_param_auid, "Pan", "pan position",
            f.dictionary.lookup_typedef("Rational"),
        )
        f.dictionary.register_def(pan_param_def)

        # SourceMob (one shared between L and R for fixture simplicity).
        sm = f.create.SourceMob("PremSrc")
        f.content.mobs.append(sm)
        sm.descriptor = f.create.ImportDescriptor()
        ss = sm.create_sound_slot(edit_rate=48000)

        def _master(name):
            mm = f.create.MasterMob(name)
            f.content.mobs.append(mm)
            ms = mm.create_sound_slot(edit_rate=48000)
            ms.segment.components.append(f.create.SourceClip(
                start=0, length=1000, mob_id=sm.mob_id, slot_id=ss.slot_id,
            ))
            return mm, ms

        mm_l, ms_l = _master("Audio 1_L")
        mm_r, ms_r = _master("Audio 1_R")

        comp = f.create.CompositionMob("Premiere Stereo Comp")
        f.content.mobs.append(comp)

        def _make_pan_slot(target_mm, target_ms, pan_value):
            slot = comp.create_sound_slot(edit_rate=48000)
            og = f.create.OperationGroup(pan_def)
            og.length = 1000
            og["InputSegments"].value = [
                f.create.SourceClip(
                    start=0, length=1000,
                    mob_id=target_mm.mob_id, slot_id=target_ms.slot_id,
                ),
            ]
            cv = f.create.ConstantValue(pan_param_def, pan_value)
            og["Parameters"].append(cv)
            slot.segment = og
            return slot

        _make_pan_slot(mm_l, ms_l, AAFRational(0, 100_000_000))           # LEFT
        _make_pan_slot(mm_r, ms_r, AAFRational(100_000_000, 100_000_000)) # RIGHT


def _write_premiere_polywav_aaf(path: Path) -> None:
    """
    Synthetic Premiere AAF mimicking the multichannel polywav import
    pattern: identical-metadata MasterMobs named "Audio N" with no
    Mono Audio Pan, no _L/_R suffix, and no PhysicalTrackNumber.
    The real-world signature is "channel identity destroyed at
    import" — surfaced via recovery_status="unrecoverable".
    """
    with aaf2.open(str(path), "w") as f:
        _add_premiere_identification(f)

        sm = f.create.SourceMob("PolySrc")
        f.content.mobs.append(sm)
        sm.descriptor = f.create.ImportDescriptor()
        ss = sm.create_sound_slot(edit_rate=48000)

        comp = f.create.CompositionMob("Premiere Polywav Comp")
        f.content.mobs.append(comp)
        for i in (1, 2):
            mm = f.create.MasterMob(f"Audio {i}")
            f.content.mobs.append(mm)
            ms = mm.create_sound_slot(edit_rate=48000)
            ms.segment.components.append(f.create.SourceClip(
                start=0, length=1000, mob_id=sm.mob_id, slot_id=ss.slot_id,
            ))
            comp_slot = comp.create_sound_slot(edit_rate=48000)
            comp_slot.segment.components.append(f.create.SourceClip(
                start=0, length=1000, mob_id=mm.mob_id, slot_id=ms.slot_id,
            ))


def _write_transition_aaf(path: Path) -> None:
    """
    A CompositionMob with one audio slot containing two SourceClips
    separated by a Transition. In AAF, a Transition represents the
    OVERLAP between adjacent clips, not extra timeline duration:
    the second clip's timeline_start is (clip1.length - transition.length),
    not (clip1.length + transition.length).

    Layout: [SourceClip A (len=1000), Transition (len=100), SourceClip B (len=1000)]
    Expected sequence timeline length: 1000 + 1000 - 100 = 1900
    Expected timeline_start of clip B: 900 (= 1000 - 100)
    """
    with aaf2.open(str(path), "w") as f:
        # One source mob to point both clips at.
        sm = f.create.SourceMob("XfadeSrc")
        f.content.mobs.append(sm)
        sm.descriptor = f.create.ImportDescriptor()
        s_slot = sm.create_sound_slot(edit_rate=48000)

        mm = f.create.MasterMob("XfadeMaster")
        f.content.mobs.append(mm)
        m_slot = mm.create_sound_slot(edit_rate=48000)
        m_slot.segment = f.create.SourceClip(
            start=0, length=2000, mob_id=sm.mob_id, slot_id=s_slot.slot_id,
        )

        comp = f.create.CompositionMob("XfadeComp")
        f.content.mobs.append(comp)
        a = comp.create_sound_slot(edit_rate=48000)
        a["PhysicalTrackNumber"].value = 1

        clip_a = f.create.SourceClip(
            start=0, length=1000, mob_id=mm.mob_id, slot_id=m_slot.slot_id,
        )
        # Transition needs an OperationGroup describing the effect; an
        # opaque dissolve is fine for testing offset math.
        try:
            op_def = f.dictionary.lookup_operationdef("MonoAudioDissolve")
        except Exception:
            op_def = f.create.OperationDef(
                aaf2.auid.AUID("0e24dd54-66cd-4f1a-b0a0-670ac3a7a0b3"),
                "MonoAudioDissolve",
                "Synthetic dissolve for transition tests.",
            )
            op_def["IsTimeWarp"].value = False
            op_def["DataDefinition"].value = f.dictionary.lookup_datadef("Sound")
            op_def["NumberInputs"].value = 2
            f.dictionary["OperationDefinitions"].append(op_def)
        og = f.create.OperationGroup(op_def, length=100)
        og["DataDefinition"].value = f.dictionary.lookup_datadef("Sound")
        transition = f.create.Transition(length=100)
        transition["OperationGroup"].value = og
        # CutPoint: 50 = midpoint of the 100-frame overlap
        transition["CutPoint"].value = 50

        clip_b = f.create.SourceClip(
            start=0, length=1000, mob_id=mm.mob_id, slot_id=m_slot.slot_id,
        )

        a.segment.components.append(clip_a)
        a.segment.components.append(transition)
        a.segment.components.append(clip_b)


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
def combiner_aaf(fixture_dir: Path) -> Path:
    p = fixture_dir / "combiner.aaf"
    _write_combiner_aaf(p)
    return p


@pytest.fixture(scope="session")
def premiere_stereo_split_aaf(fixture_dir: Path) -> Path:
    p = fixture_dir / "premiere_stereo_split.aaf"
    _write_premiere_stereo_split_aaf(p)
    return p


@pytest.fixture(scope="session")
def premiere_polywav_aaf(fixture_dir: Path) -> Path:
    p = fixture_dir / "premiere_polywav.aaf"
    _write_premiere_polywav_aaf(p)
    return p


@pytest.fixture(scope="session")
def cycle_aaf(fixture_dir: Path) -> Path:
    p = fixture_dir / "cycle.aaf"
    _write_cycle_aaf(p)
    return p


@pytest.fixture(scope="session")
def transition_aaf(fixture_dir: Path) -> Path:
    p = fixture_dir / "transition.aaf"
    _write_transition_aaf(p)
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
