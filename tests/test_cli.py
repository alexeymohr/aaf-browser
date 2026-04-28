"""CLI tests using click.testing.CliRunner."""
from __future__ import annotations

import json

import aaf2
from click.testing import CliRunner

from aafbrowser.cli.__main__ import cli


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
