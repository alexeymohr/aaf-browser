"""CLI tests using click.testing.CliRunner."""
from __future__ import annotations

import json

import aaf2
from click.testing import CliRunner

from aafbrowser.cli.__main__ import cli  # noqa: F401


def _run(*args):
    runner = CliRunner()
    result = runner.invoke(cli, list(args), catch_exceptions=False)
    return result


def test_help():
    res = _run("--help")
    assert res.exit_code == 0
    assert "aafbrowser" in res.output


def test_tree_runs(minimal_aaf):
    res = _run("tree", str(minimal_aaf))
    assert res.exit_code == 0, res.output
    assert "ContentStorage" in res.output
    assert "MasterMob" in res.output
    assert "FixtureMob" in res.output


def test_dump_default_human(minimal_aaf):
    res = _run("dump", str(minimal_aaf))
    assert res.exit_code == 0
    assert "AAF object graph" in res.output
    assert "CFB tree" in res.output
    assert "sha256:" in res.output


def test_dump_json_is_valid_json(minimal_aaf):
    res = _run("dump", str(minimal_aaf), "--json")
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["aaf"]["class"] == "ContentStorage"
    assert payload["cfb"]["_type"] == "cfb_storage"
    assert "sha256" in payload


def test_dump_object_only_skips_cfb(minimal_aaf):
    res = _run("dump", str(minimal_aaf), "--json", "--object-only")
    assert res.exit_code == 0
    payload = json.loads(res.output)
    assert payload["aaf"] is not None
    assert payload["cfb"] is None


def test_dump_cfb_only_skips_aaf(minimal_aaf):
    res = _run("dump", str(minimal_aaf), "--json", "--cfb-only")
    assert res.exit_code == 0
    payload = json.loads(res.output)
    assert payload["aaf"] is None
    assert payload["cfb"] is not None


def test_dump_object_only_and_cfb_only_conflict(minimal_aaf):
    res = _run("dump", str(minimal_aaf), "--object-only", "--cfb-only")
    assert res.exit_code != 0
    assert "mutually exclusive" in res.output


def test_cfb_default_excludes_metadict(minimal_aaf):
    res = _run("cfb", str(minimal_aaf))
    assert res.exit_code == 0
    assert "filtered" in res.output


def test_cfb_include_metadict(minimal_aaf):
    res = _run("cfb", str(minimal_aaf), "--include-metadict")
    assert res.exit_code == 0
    assert "ClassDefinitions" in res.output


def test_cfb_show_bytes_hex_view(minimal_aaf):
    # First find a stream path we can target
    with aaf2.open(str(minimal_aaf), "r") as f:
        target = None
        for _, _, streams in f.cfb.walk():
            for st in streams:
                if st.byte_size >= 16:
                    target = st.path()
                    break
            if target:
                break
    res = _run("cfb", str(minimal_aaf), "--show-bytes", target, "--length", "16")
    assert res.exit_code == 0, res.output
    assert "stream:" in res.output
    # First hex line begins with offset
    assert "00000000  " in res.output


def test_inspect_by_mob_id(minimal_aaf):
    with aaf2.open(str(minimal_aaf), "r") as f:
        urn = str(next(iter(f.content.mobs)).mob_id)
    res = _run("inspect", str(minimal_aaf), "--mob-id", urn)
    assert res.exit_code == 0
    assert "MasterMob" in res.output
    assert "FixtureMob" in res.output


def test_inspect_by_path(minimal_aaf):
    with aaf2.open(str(minimal_aaf), "r") as f:
        urn = str(next(iter(f.content.mobs)).mob_id)
    res = _run("inspect", str(minimal_aaf), "--path", f"Mobs/{urn}/Slots/0/Segment")
    assert res.exit_code == 0
    assert "Sequence" in res.output


def test_inspect_unknown_mob_id_errors(minimal_aaf):
    res = _run("inspect", str(minimal_aaf), "--mob-id", "definitely-not-a-mob-id")
    assert res.exit_code != 0
    assert "not found" in res.output.lower()


def test_inspect_requires_one_arg(minimal_aaf):
    res = _run("inspect", str(minimal_aaf))
    assert res.exit_code != 0
    assert "Provide" in res.output


def test_find_returns_match_for_known_value(two_mob_aaf):
    res = _run("find", str(two_mob_aaf), "--pattern", "Channel_1_Host")
    assert res.exit_code == 0, res.output
    assert "Channel_1_Host" in res.output
    assert "MasterMob" in res.output


def test_find_layer_cfb_only_does_not_match_aaf_value(two_mob_aaf):
    res = _run(
        "find", str(two_mob_aaf),
        "--pattern", "Channel_1_Host", "--layer", "cfb",
    )
    assert res.exit_code == 0
    assert "no matches" in res.output


def test_find_invalid_regex_errors(minimal_aaf):
    res = _run("find", str(minimal_aaf), "--pattern", "(unbalanced")
    assert res.exit_code != 0
    assert "invalid regex" in res.output


def test_find_json_output_lines_are_json(two_mob_aaf):
    res = _run("find", str(two_mob_aaf), "--pattern", "Channel", "--json")
    assert res.exit_code == 0
    lines = [ln for ln in res.output.strip().splitlines() if ln]
    assert lines  # at least one match
    for line in lines:
        obj = json.loads(line)
        assert obj["layer"] in ("aaf", "cfb")
        assert "path" in obj


# --- walk subcommand ---


def _comp_mob_urn(aaf_path: str) -> str:
    with aaf2.open(str(aaf_path), "r") as f:
        for m in f.content.mobs:
            if type(m).__name__ == "CompositionMob":
                return str(m.mob_id)
    raise AssertionError("no CompositionMob")


def test_walk_human_output(chain_aaf):
    urn = _comp_mob_urn(chain_aaf)
    res = _run("walk", str(chain_aaf), "--mob-id", urn)
    assert res.exit_code == 0, res.output
    assert "CompositionMob" in res.output
    assert "MasterMob" in res.output
    assert "SourceMob" in res.output
    assert "terminal: essence" in res.output


def test_walk_json_output(chain_aaf):
    urn = _comp_mob_urn(chain_aaf)
    res = _run("walk", str(chain_aaf), "--mob-id", urn, "--json")
    assert res.exit_code == 0
    j = json.loads(res.output)
    assert j["start"]["class"] == "CompositionMob"
    assert len(j["hops"]) == 3
    assert j["hops"][-1]["terminal"] is True
    assert j["hops"][-1]["terminal_reason"] == "essence"


def test_walk_unknown_mob_id_errors(chain_aaf):
    res = _run("walk", str(chain_aaf), "--mob-id", "definitely-not-a-mob")
    assert res.exit_code != 0
    assert "not found" in res.output.lower()


def test_walk_requires_one_of_mob_id_or_path(chain_aaf):
    res = _run("walk", str(chain_aaf))
    assert res.exit_code != 0
    assert "Provide" in res.output


def test_walk_max_hops_truncates(chain_aaf):
    urn = _comp_mob_urn(chain_aaf)
    res = _run("walk", str(chain_aaf), "--mob-id", urn, "--max-hops", "1", "--json")
    assert res.exit_code == 0
    j = json.loads(res.output)
    assert j["hops"][-1]["terminal_reason"] == "max_hops_reached"


# --- find --class ---


def test_find_class_filter_excludes_other_classes(chain_aaf):
    res = _run(
        "find", str(chain_aaf),
        "--pattern", "MstHostA",
        "--class", "SourceMob",
        "--layer", "aaf",
    )
    assert res.exit_code == 0
    assert "no matches" in res.output


def test_find_class_filter_includes_match(chain_aaf):
    res = _run(
        "find", str(chain_aaf),
        "--pattern", "PW_213_HOSTA",
        "--class", "CompositionMob",
        "--layer", "aaf",
    )
    assert res.exit_code == 0
    assert "PW_213_HOSTA" in res.output


def test_find_class_filter_repeatable(chain_aaf):
    res = _run(
        "find", str(chain_aaf),
        "--pattern", "(?i)host",
        "--class", "CompositionMob",
        "--class", "MasterMob",
        "--layer", "aaf",
        "--json",
    )
    assert res.exit_code == 0
    classnames = {
        json.loads(line)["classname"]
        for line in res.output.strip().splitlines()
        if line
    }
    assert "SourceMob" not in classnames


# ---------- Phase 9: operator-layer CLI commands ----------


def test_session_human(chain_aaf):
    res = _run("session", str(chain_aaf))
    assert res.exit_code == 0, res.output
    assert "composition:" in res.output
    assert "tracks:" in res.output
    assert "clips:" in res.output


def test_session_json(chain_aaf):
    res = _run("session", str(chain_aaf), "--json")
    assert res.exit_code == 0
    j = json.loads(res.output)
    assert j["session"]["_type"] == "operator_session_summary"
    assert "audio_track_count" in j["session"]
    assert j["sha256"]


def test_tracks_human(multi_track_aaf):
    res = _run("tracks", str(multi_track_aaf))
    assert res.exit_code == 0, res.output
    # PT-style A1/A2/V1 ordinals + named tracks
    assert "A1" in res.output
    assert "V1" in res.output
    assert "audio" in res.output
    assert "video" in res.output


def test_tracks_json(multi_track_aaf):
    res = _run("tracks", str(multi_track_aaf), "--json")
    j = json.loads(res.output)
    assert "tracks" in j
    kinds = [t["kind"] for t in j["tracks"]]
    assert kinds == ["audio", "audio", "video"]


def test_clips_human(multi_track_aaf):
    res = _run("clips", str(multi_track_aaf), "--slot", "1")
    assert res.exit_code == 0
    assert "slot 1:" in res.output
    assert "SrcA" in res.output  # mic identity in [brackets]


def test_clips_json(multi_track_aaf):
    res = _run("clips", str(multi_track_aaf), "--slot", "1", "--json")
    j = json.loads(res.output)
    assert j["slot_id"] == 1
    assert all(c["_type"] == "operator_clip" for c in j["clips"])


def test_clips_unknown_slot_errors(multi_track_aaf):
    res = _run("clips", str(multi_track_aaf), "--slot", "99")
    assert res.exit_code != 0
    assert "no slot" in res.output.lower()


def test_sources_human(combiner_aaf):
    res = _run("sources", str(combiner_aaf))
    assert res.exit_code == 0
    assert "CombSrcA" in res.output
    assert "used=" in res.output


def test_sources_json(combiner_aaf):
    res = _run("sources", str(combiner_aaf), "--json")
    j = json.loads(res.output)
    assert j["sources"]
    names = {s["name"] for s in j["sources"] if s.get("name")}
    assert {"CombSrcA", "CombSrcB", "CombSrcC"}.issubset(names)


def test_sources_unused_flag(combiner_aaf):
    """combiner_aaf may have unused-by-comp source mobs (CombMidMaster,
    CombMidComp). Default skips unused (use_count=0), --unused includes
    them."""
    default = _run("sources", str(combiner_aaf), "--json")
    with_unused = _run("sources", str(combiner_aaf), "--json", "--unused")
    j_def = json.loads(default.output)
    j_all = json.loads(with_unused.output)
    assert j_all["total"] >= j_def["total"]


def test_premiere_stereo_split_clips_show_pan(premiere_stereo_split_aaf):
    """Phase 8 path through the CLI: track-row pan_channel + clip
    recovery_method should be visible."""
    tres = _run("tracks", str(premiere_stereo_split_aaf))
    assert tres.exit_code == 0
    # The L/R pan pill is rendered as "[L]" / "[R]" in the CLI
    assert "[L]" in tres.output
    assert "[R]" in tres.output
    cres = _run("clips", str(premiere_stereo_split_aaf), "--slot", "1", "--json")
    j = json.loads(cres.output)
    assert j["clips"][0]["recovery_method"].startswith("premiere_stereo_split")
