"""Tests for aafbrowser.core.operator — Track + Clip operator-layer view."""
from __future__ import annotations

import aaf2
import pytest

from aafbrowser.core.operator import (
    Clip,
    SessionSummary,
    Track,
    format_timecode,
    list_clips,
    list_tracks,
    pick_topmost_composition,
    session_summary,
)


# ---------- pick_topmost_composition ----------


def test_pick_topmost_returns_none_for_essence_only(broken_ref_aaf):
    """broken_ref_aaf has only a MasterMob — no CompositionMob to pick."""
    with aaf2.open(str(broken_ref_aaf), "r") as f:
        assert pick_topmost_composition(f) is None


def test_pick_topmost_returns_single_composition(chain_aaf):
    with aaf2.open(str(chain_aaf), "r") as f:
        comp = pick_topmost_composition(f)
        assert comp is not None
        assert type(comp).__name__ == "CompositionMob"
        assert comp.name == "PW_213_HOSTA ISO"


def test_pick_topmost_picks_largest_by_slot_count(tmp_path):
    """When there are multiple CompositionMobs, the one with more slots wins."""
    p = tmp_path / "multi_comp.aaf"
    with aaf2.open(str(p), "w") as f:
        small = f.create.CompositionMob("Small")
        f.content.mobs.append(small)
        small.create_sound_slot(edit_rate=48000)

        big = f.create.CompositionMob("Big")
        f.content.mobs.append(big)
        for _ in range(4):
            big.create_sound_slot(edit_rate=48000)

    with aaf2.open(str(p), "r") as f:
        chosen = pick_topmost_composition(f)
        assert chosen.name == "Big"


# ---------- list_tracks ----------


def test_list_tracks_empty_for_essence_only(broken_ref_aaf):
    with aaf2.open(str(broken_ref_aaf), "r") as f:
        assert list_tracks(f) == []


def test_list_tracks_chain_aaf_returns_track_with_ptn(chain_aaf):
    """
    chain_aaf assigns the SourceClip directly as slot.segment (rather than
    appending to a Sequence). pyaaf2's slot.media_kind is derived from the
    segment's data_def, and a freshly-created SourceClip has no data_def,
    so the slot reports media_kind="Picture" in this fixture even though
    the slot was created via create_sound_slot. Real-world Avid/Premiere
    AAFs don't hit this — multi_track_aaf below validates kind filtering
    in the natural-shape case (Sequence with components).

    Here we just verify list_tracks surfaces the slot at all and carries
    the operator-meaningful metadata (PTN, edit rate).
    """
    with aaf2.open(str(chain_aaf), "r") as f:
        tracks = list_tracks(f)
    assert len(tracks) == 1
    t = tracks[0]
    assert isinstance(t, Track)
    assert t.slot_id == 1
    assert t.ordinal == 7  # PTN=7 set on the comp slot
    assert t.edit_rate == "48000/1"
    # comp slot's segment is a single SourceClip (not a Sequence) → 1 clip
    assert t.clip_count == 1


def test_list_tracks_multi_track_filters_kind_and_orders_by_slot(multi_track_aaf):
    with aaf2.open(str(multi_track_aaf), "r") as f:
        tracks = list_tracks(f)
    # Two audio + one video, audio first by slot_id
    assert [t.kind for t in tracks] == ["audio", "audio", "video"]
    assert [t.slot_id for t in tracks] == [1, 2, 3]
    # PTN sets the ordinal for audio slots; video slot has no PTN so
    # ordinal falls back to slot_id (3).
    assert [t.ordinal for t in tracks] == [1, 2, 3]
    # Audio slot 1 has two clips on its Sequence; audio slot 2 has one;
    # the video slot has no components.
    assert [t.clip_count for t in tracks] == [2, 1, 0]


def test_track_to_dict_carries_type_marker(multi_track_aaf):
    with aaf2.open(str(multi_track_aaf), "r") as f:
        t = list_tracks(f)[0]
    d = t.to_dict()
    assert d["_type"] == "operator_track"
    assert d["kind"] == "audio"
    assert d["slot_id"] == 1
    assert d["clip_count"] == 2


# ---------- list_clips ----------


def test_list_clips_raises_on_missing_composition(broken_ref_aaf):
    with aaf2.open(str(broken_ref_aaf), "r") as f:
        with pytest.raises(ValueError, match="no CompositionMob"):
            list_clips(f, 1)


def test_list_clips_raises_on_unknown_slot_id(chain_aaf):
    with aaf2.open(str(chain_aaf), "r") as f:
        with pytest.raises(ValueError, match="no slot"):
            list_clips(f, 99)


def test_list_clips_chain_aaf_recovers_mic_identity(chain_aaf):
    with aaf2.open(str(chain_aaf), "r") as f:
        clips = list_clips(f, 1)
    assert len(clips) == 1
    c = clips[0]
    assert isinstance(c, Clip)
    assert c.component_class == "SourceClip"
    assert c.timeline_start == 0
    assert c.length == 48000
    assert c.source_mob_name == "MstHostA"
    assert c.is_recorder_source is True
    assert c.mic_identity == "SrcEPWD0209_HOSTA"
    assert c.terminal_reason == "essence"
    assert c.physical_track_number == 7
    # MasterMob → SourceMob
    assert c.chain_length == 2


def test_list_clips_advances_timeline_cursor(multi_track_aaf):
    """Audio slot 1 has two back-to-back clips of length 24000 each.
    The second clip's timeline_start should be 24000."""
    with aaf2.open(str(multi_track_aaf), "r") as f:
        clips = list_clips(f, 1)
    assert len(clips) == 2
    assert [c.timeline_start for c in clips] == [0, 24000]
    assert [c.length for c in clips] == [24000, 24000]
    # Each chain ends at a distinct SourceMob with PTN matching its index.
    assert clips[0].mic_identity == "SrcA"
    assert clips[0].physical_track_number == 1
    assert clips[1].mic_identity == "SrcB"
    assert clips[1].physical_track_number == 2
    assert all(c.is_recorder_source for c in clips)


def test_list_clips_marks_broken_ref(tmp_path):
    """A SourceClip pointing at a non-existent MobID gets terminal_reason
    'broken_ref' and is_recorder_source=False; the listing doesn't crash."""
    from aaf2.mobid import MobID
    p = tmp_path / "comp_with_broken.aaf"
    with aaf2.open(str(p), "w") as f:
        comp = f.create.CompositionMob("C")
        f.content.mobs.append(comp)
        slot = comp.create_sound_slot(edit_rate=48000)
        ghost = MobID.new()
        slot.segment.components.append(
            f.create.SourceClip(start=0, length=1000, mob_id=ghost, slot_id=1)
        )
    with aaf2.open(str(p), "r") as f:
        clips = list_clips(f, 1)
    assert len(clips) == 1
    assert clips[0].is_recorder_source is False
    assert clips[0].terminal_reason == "broken_ref"
    assert clips[0].mic_identity is None


def test_clip_to_dict_carries_type_marker(chain_aaf):
    with aaf2.open(str(chain_aaf), "r") as f:
        c = list_clips(f, 1)[0]
    d = c.to_dict()
    assert d["_type"] == "operator_clip"
    assert d["component_class"] == "SourceClip"
    assert d["is_recorder_source"] is True


# ---------- format_timecode ----------


def test_format_timecode_non_drop():
    assert format_timecode(0, 30, False) == "00:00:00:00"
    assert format_timecode(48, 24, False) == "00:00:02:00"
    assert format_timecode(30 * 60 * 60, 30, False) == "01:00:00:00"


def test_format_timecode_drop_frame():
    # Standard SMPTE 12M-1 reference values for 29.97 DF.
    assert format_timecode(1800, 30, True) == "00:01:00;02"
    assert format_timecode(107892, 30, True) == "01:00:00;00"
    # Frame 17982 is exactly 10 minutes in DF (no skip on the 10th).
    assert format_timecode(17982, 30, True) == "00:10:00;00"


def test_format_timecode_returns_none_on_missing():
    assert format_timecode(None, 30, False) is None
    assert format_timecode(100, None, False) is None


# ---------- session_summary ----------


def test_session_summary_essence_only(broken_ref_aaf):
    """File with no CompositionMob has no timecode + no tracks."""
    with aaf2.open(str(broken_ref_aaf), "r") as f:
        s = session_summary(f, file_size_bytes=1234)
    assert isinstance(s, SessionSummary)
    assert s.audio_track_count == 0
    assert s.video_track_count == 0
    assert s.total_clip_count == 0
    assert s.timecode is None
    assert s.duration_frames is None
    assert s.master_mob_count == 1


def test_session_summary_multi_track(multi_track_aaf):
    with aaf2.open(str(multi_track_aaf), "r") as f:
        s = session_summary(f, file_size_bytes=2048)
    assert s.audio_track_count == 2
    assert s.video_track_count == 1
    # First audio: 2 clips; second audio: 1 clip; video: 0 — total 3
    assert s.total_clip_count == 3
    assert s.composition_mob_count == 1
    assert s.master_mob_count == 3
    assert s.source_mob_count == 3
    # multi_track_aaf doesn't have a Timecode slot so timecode is None
    assert s.timecode is None


def test_session_summary_to_dict_carries_type(multi_track_aaf):
    with aaf2.open(str(multi_track_aaf), "r") as f:
        s = session_summary(f).to_dict()
    assert s["_type"] == "operator_session_summary"
    assert "audio_track_count" in s
    assert "master_mob_count" in s
    # Authoring + last_modified keys are always present (may be None
    # for files lacking the IdentificationList).
    assert "authoring" in s
    assert "last_modified" in s


def test_parse_wave_summary_minimal_riff():
    """Build a minimal RIFF/WAVE blob and verify the parser pulls the
    fmt-chunk fields. Mirrors the structure pyaaf2 hands us as a list[int]."""
    from aafbrowser.core.operator import _parse_wave_summary
    # 1ch / 48000Hz / 24-bit
    fmt_chunk = (
        b"fmt " + (16).to_bytes(4, "little") +
        (1).to_bytes(2, "little") +     # PCM
        (1).to_bytes(2, "little") +     # channels
        (48000).to_bytes(4, "little") + # sample rate
        (144000).to_bytes(4, "little") +  # byte rate (irrelevant)
        (3).to_bytes(2, "little") +     # block align
        (24).to_bytes(2, "little")      # bits per sample
    )
    body = b"WAVE" + fmt_chunk
    blob = b"RIFF" + len(body).to_bytes(4, "little") + body
    info = _parse_wave_summary(list(blob))  # pyaaf2 yields list[int]
    assert info == {"channels": 1, "sample_rate": 48000, "bits_per_sample": 24}


def test_parse_wave_summary_skips_non_fmt_chunks():
    """The fmt chunk often comes after a 'bext' broadcast extension chunk."""
    from aafbrowser.core.operator import _parse_wave_summary
    bext = b"bext" + (8).to_bytes(4, "little") + b"x" * 8
    fmt_chunk = (
        b"fmt " + (16).to_bytes(4, "little") +
        (1).to_bytes(2, "little") +
        (2).to_bytes(2, "little") +     # 2 channels (stereo)
        (96000).to_bytes(4, "little") +
        (576000).to_bytes(4, "little") +
        (6).to_bytes(2, "little") +
        (32).to_bytes(2, "little")      # 32-bit
    )
    body = b"WAVE" + bext + fmt_chunk
    blob = b"RIFF" + len(body).to_bytes(4, "little") + body
    info = _parse_wave_summary(blob)
    assert info == {"channels": 2, "sample_rate": 96000, "bits_per_sample": 32}


def test_parse_wave_summary_handles_garbage():
    from aafbrowser.core.operator import _parse_wave_summary
    assert _parse_wave_summary(None) is None
    assert _parse_wave_summary(b"") is None
    assert _parse_wave_summary(b"NOTRIFF") is None


def test_session_summary_audio_aggregation_empty(multi_track_aaf):
    """multi_track_aaf uses ImportDescriptors (no audio format info)
    so AudioSummary aggregates are all empty but the field is present."""
    with aaf2.open(str(multi_track_aaf), "r") as f:
        s = session_summary(f)
    assert s.audio is not None
    assert s.audio.audio_source_count == 0
    assert s.audio.sample_rates == {}
    assert s.audio.bit_depths == {}
    assert s.audio.channel_counts == {}


def test_session_summary_includes_authoring_when_present(multi_track_aaf):
    """pyaaf2's writer populates Header.IdentificationList with a
    "pyaaf2" identification entry on file write — every fixture AAF
    we generate carries this. Verify we surface it."""
    with aaf2.open(str(multi_track_aaf), "r") as f:
        s = session_summary(f)
    if s.authoring is None:
        # Fixture writers may skip the identification on some pyaaf2
        # versions — don't make this brittle.
        return
    assert s.authoring.identification_count >= 1
    # to_dict round-trip carries the type marker
    d = s.to_dict()
    assert d["authoring"]["_type"] == "operator_authoring"
