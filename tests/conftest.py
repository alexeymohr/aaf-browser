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


@pytest.fixture
def minimal_aaf_copy(minimal_aaf: Path, tmp_path: Path) -> Path:
    """
    Per-test copy so any (theoretical, prohibited) write would not leak
    across tests. Read-only round-trip tests use this to hash.
    """
    dst = tmp_path / "minimal_copy.aaf"
    shutil.copy2(minimal_aaf, dst)
    return dst
