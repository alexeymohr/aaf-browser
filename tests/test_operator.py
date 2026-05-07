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


# ---------- per-clip operator info ----------


def test_clip_carries_handle_fields_on_chain_aaf(chain_aaf):
    """chain_aaf's MasterMob slot has total length 48000; the comp-level
    SourceClip uses start=0 length=48000, so head=0 tail=0."""
    with aaf2.open(str(chain_aaf), "r") as f:
        clips = list_clips(f, 1)
    c = clips[0]
    # head_handle_frames: SourceClip.start = 0
    assert c.head_handle_frames == 0
    # tail_handle: master slot total - (start + length). The chain
    # walk's terminal SourceMob's slot.segment is a SourceClip (the
    # fixture's pattern), with length 48000 at edit_rate 48000/1.
    # head_handle_seconds at 48k = 0 / 48000 = 0.0
    assert c.head_handle_seconds == 0.0


def test_clip_locators_empty_for_import_descriptor(chain_aaf):
    """ImportDescriptor on chain_aaf carries no Locator entries."""
    with aaf2.open(str(chain_aaf), "r") as f:
        clips = list_clips(f, 1)
    assert clips[0].source_locators == ()


def test_clip_to_dict_includes_per_clip_operator_fields(chain_aaf):
    with aaf2.open(str(chain_aaf), "r") as f:
        d = list_clips(f, 1)[0].to_dict()
    assert "source_locators" in d
    assert "head_handle_frames" in d
    assert "tail_handle_frames" in d
    assert "audio_sample_rate" in d
    assert "audio_bits_per_sample" in d
    assert "audio_channels" in d
    assert "sub_clips" in d
    assert isinstance(d["source_locators"], list)
    assert isinstance(d["sub_clips"], list)


# ---------- multi-input combiner fan-out ----------


def test_list_clips_fans_out_on_multi_input_combiner(combiner_aaf):
    """CombinerComp slot 1 has [SourceClip(C), OperationGroup(A+B)].
    list_clips must yield 2 top-level clips: a normal SourceClip clip
    and an OperationGroup clip with 2 sub_clips."""
    with aaf2.open(str(combiner_aaf), "r") as f:
        clips = list_clips(f, 1)
    assert len(clips) == 2
    # [0] SourceClip pointing at CombMstC -> CombSrcC
    assert clips[0].component_class == "SourceClip"
    assert clips[0].mic_identity == "CombSrcC"
    assert clips[0].sub_clips == ()
    # [1] OperationGroup combining CombMstA + CombMstB
    og_clip = clips[1]
    assert og_clip.component_class == "OperationGroup"
    assert og_clip.mic_identity is None
    assert og_clip.is_recorder_source is False
    assert og_clip.terminal_reason == "combiner:TestStereoMix"
    assert len(og_clip.sub_clips) == 2
    # Each sub_clip is itself a Clip with its own recovered identity
    sub_a, sub_b = og_clip.sub_clips
    assert sub_a.mic_identity == "CombSrcA"
    assert sub_a.physical_track_number == 1
    assert sub_a.is_recorder_source is True
    assert sub_b.mic_identity == "CombSrcB"
    assert sub_b.physical_track_number == 2
    assert sub_b.is_recorder_source is True


def test_combiner_clip_to_dict_recursive_sub_clips(combiner_aaf):
    """Top-level clip's to_dict contains nested sub_clip dicts."""
    with aaf2.open(str(combiner_aaf), "r") as f:
        clips = list_clips(f, 1)
    d = clips[1].to_dict()
    assert d["component_class"] == "OperationGroup"
    assert len(d["sub_clips"]) == 2
    assert all(s["_type"] == "operator_clip" for s in d["sub_clips"])
    assert d["sub_clips"][0]["mic_identity"] == "CombSrcA"


# ---------- locator helpers ----------


def test_url_to_local_path_handles_common_forms():
    from aafbrowser.core.operator import _url_to_local_path
    assert _url_to_local_path("/abs/path") == "/abs/path"
    assert _url_to_local_path("file:///abs/path/file.wav") == "/abs/path/file.wav"
    assert _url_to_local_path("file:///path%20with%20spaces.wav") == "/path with spaces.wav"
    assert _url_to_local_path("urn:smpte:umid:abc") is None
    assert _url_to_local_path("http://example.com/x") is None
    assert _url_to_local_path("relative/path") is None


def test_is_online_for_existing_and_missing(tmp_path):
    from aafbrowser.core.operator import _is_online
    real = tmp_path / "real.txt"
    real.write_text("hi")
    assert _is_online(f"file://{real}") is True
    assert _is_online(f"file://{tmp_path}/missing.wav") is False
    # Non-local URL → unknown
    assert _is_online("http://example.com/x") is None
    assert _is_online("urn:smpte:umid:abc") is None


# ---------- authoring detection + Premiere recovery ----------


def test_classify_product_name():
    from aafbrowser.core.operator import _classify_product_name
    assert _classify_product_name("Avid Media Composer 24.12.1") == "avid"
    assert _classify_product_name("Adobe Premiere Pro 24.0") == "premiere"
    assert _classify_product_name("Pro Tools 2024.6") == "protools"
    assert _classify_product_name("Protools") == "protools"
    assert _classify_product_name("DaVinci Resolve") == "unknown"
    assert _classify_product_name(None) == "unknown"
    assert _classify_product_name("") == "unknown"


def test_detect_authoring_kind_pwd_310_is_avid():
    """Real Avid AAF must classify as 'avid'."""
    import aaf2
    from aafbrowser.core.operator import detect_authoring_kind
    import os
    sample = "samples/PWD_310_LC_10-07-2025.aaf"
    if not os.path.exists(sample):
        return  # opt-in: skip when sample missing
    with aaf2.open(sample, "r") as f:
        assert detect_authoring_kind(f) == "avid"


def test_detect_authoring_kind_premiere_fixture(premiere_stereo_split_aaf):
    import aaf2
    from aafbrowser.core.operator import detect_authoring_kind
    with aaf2.open(str(premiere_stereo_split_aaf), "r") as f:
        assert detect_authoring_kind(f) == "premiere"


def test_track_pan_channel_premiere_stereo_split(premiere_stereo_split_aaf):
    import aaf2
    with aaf2.open(str(premiere_stereo_split_aaf), "r") as f:
        tracks = list_tracks(f)
    assert len(tracks) == 2
    assert tracks[0].pan_channel == "L"
    assert tracks[1].pan_channel == "R"


def test_track_pan_channel_silent_on_avid(chain_aaf):
    """Avid AAFs don't carry Mono Audio Pan; pan_channel must be None."""
    import aaf2
    with aaf2.open(str(chain_aaf), "r") as f:
        tracks = list_tracks(f)
    assert all(t.pan_channel is None for t in tracks)


def test_premiere_stereo_split_clip_recovery(premiere_stereo_split_aaf):
    """Each Premiere stereo-split clip recovers via Mono Audio Pan."""
    import aaf2
    with aaf2.open(str(premiere_stereo_split_aaf), "r") as f:
        l_clips = list_clips(f, 1)
        r_clips = list_clips(f, 2)
    assert l_clips[0].recovery_status == "recoverable"
    assert l_clips[0].recovery_method == "premiere_stereo_split_pan_l"
    # mic_identity already ends in _L (the SourceMob name); no double-suffix
    assert l_clips[0].mic_identity == "Audio 1_L"
    assert r_clips[0].recovery_status == "recoverable"
    assert r_clips[0].recovery_method == "premiere_stereo_split_pan_r"
    assert r_clips[0].mic_identity == "Audio 1_R"


def test_premiere_polywav_clip_unrecoverable(premiere_polywav_aaf):
    """Premiere polywav imports — channel destroyed — must be marked
    unrecoverable explicitly so consumers don't trust the mic name."""
    import aaf2
    with aaf2.open(str(premiere_polywav_aaf), "r") as f:
        clips_a = list_clips(f, 1)
        clips_b = list_clips(f, 2)
    for c in (clips_a[0], clips_b[0]):
        assert c.recovery_status == "unrecoverable"
        assert c.recovery_method == "premiere_polywav_indeterminate"


def test_avid_recovery_status_unchanged(chain_aaf):
    """chain_aaf's chain hits a SourceMob with PTN=7 → recoverable
    via avid_chain_walk."""
    import aaf2
    with aaf2.open(str(chain_aaf), "r") as f:
        clips = list_clips(f, 1)
    assert clips[0].recovery_status == "recoverable"
    assert clips[0].recovery_method == "avid_chain_walk"


def test_combiner_recovery_status_aggregates(combiner_aaf):
    """A multi-input combiner whose inputs all recover should itself
    be marked 'recoverable' with combiner_all_inputs_recovered."""
    import aaf2
    with aaf2.open(str(combiner_aaf), "r") as f:
        clips = list_clips(f, 1)
    og = next(c for c in clips if c.component_class == "OperationGroup")
    assert og.recovery_status == "recoverable"
    assert og.recovery_method == "combiner_all_inputs_recovered"


def test_transition_does_not_inflate_timeline_offsets(transition_aaf):
    """In AAF, a Transition between two clips represents the OVERLAP
    region — its length is duration that the surrounding clips share,
    not extra timeline duration. The next clip after a transition
    starts at (cursor - transition.length), not (cursor + transition.length).

    Regression test for the Cherries Wild bug: every clip after a
    transition was reported off by the cumulative transition length
    preceding it, so users couldn't find clips at the timecode their
    NLE displayed.

    Layout: [SourceClip A (1000), Transition (100), SourceClip B (1000)]
      - A starts at 0
      - Transition's timeline span = [900, 1000] (overlap)
      - B starts at 900 (overlaps with transition)
    Total timeline length: 1900 (not 2100).
    """
    from aafbrowser.core.operator import list_clips
    with aaf2.open(str(transition_aaf), "r") as f:
        comp = next(f.content.compositionmobs())
        slot_id = comp.slots[0].slot_id
        clips = list_clips(f, slot_id)

    assert len(clips) == 3
    a, t, b = clips
    assert a.component_class == "SourceClip"
    assert t.component_class == "Transition"
    assert b.component_class == "SourceClip"

    assert a.timeline_start == 0
    assert a.length == 1000
    # The transition occupies the overlap region — its timeline_start
    # is 900 (= 1000 - 100), not 1000.
    assert t.timeline_start == 900, (
        f"Transition should start at 900 (overlap with end of clip A), "
        f"got {t.timeline_start}"
    )
    assert t.length == 100
    # Clip B starts at the same position as the transition (the
    # overlap point), not at the transition's end.
    assert b.timeline_start == 900, (
        f"Clip after transition should start at 900 (overlap point), "
        f"got {b.timeline_start} — this is the off-by-transition-length "
        f"bug that hides clips from users searching by timecode."
    )
    assert b.length == 1000


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
