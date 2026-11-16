"""PRISM downloaded where the app is hosted (backend/services/prism_setup.py).

A fake archive served from a file:// URL stands for the release of PRISM."""

from __future__ import annotations

import hashlib
import io
import os
import stat
import tarfile

import pytest

from backend.services import prism_setup as ps


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(ps, "_state", {"status": None, "error": None})
    monkeypatch.setenv("CADL_EXPLORER_PRISM_DIR", str(tmp_path / "prism"))
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    monkeypatch.setattr(ps.shutil, "which",
                        lambda name, _which=ps.shutil.which: None if name == "prism"
                        else _which(name))
    return tmp_path


def _archive(tmp_path, evil=False):
    """A tar.gz like the release: one top directory with install.sh and bin/prism."""
    files = {
        "install.sh": "#!/bin/sh\necho installed > installed.txt\n",
        "bin/prism": "#!/bin/sh\necho 'PRISM version 4.11'\n",
    }
    if evil:
        files["../outside.txt"] = "x"
    path = tmp_path / "prism.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(f"prism-4.11-linux64-x86/{name}")
            info.size = len(data)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(data))
    return path


def _serve(monkeypatch, path, digest=None):
    monkeypatch.setattr(ps, "PRISM_URL", path.as_uri())
    monkeypatch.setattr(ps, "PRISM_SHA256",
                        digest or hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(ps, "supported", lambda: True)


def test_nothing_happens_away_from_the_cloud(fresh, monkeypatch):
    monkeypatch.delenv("CADL_EXPLORER_INSTALL_PRISM", raising=False)
    monkeypatch.setattr(ps, "_on_cloud", lambda: False)
    assert ps.ensure_prism(wait=True) is None
    assert not (fresh / "prism").exists()


def test_the_flag_turns_it_off_on_the_cloud(fresh, monkeypatch):
    monkeypatch.setattr(ps, "_on_cloud", lambda: True)
    monkeypatch.setenv("CADL_EXPLORER_INSTALL_PRISM", "0")
    assert not ps.wanted()
    monkeypatch.delenv("CADL_EXPLORER_INSTALL_PRISM")
    assert ps.wanted()


def test_the_archive_is_checked_installed_and_put_on_path(fresh, monkeypatch):
    monkeypatch.setenv("CADL_EXPLORER_INSTALL_PRISM", "1")
    _serve(monkeypatch, _archive(fresh))
    assert ps.ensure_prism(wait=True) == "ready", ps.status()
    target = fresh / "prism"
    assert (target / "installed.txt").exists()          # install.sh ran in the directory
    assert (target / ".installed").read_text() == "4.11"
    assert os.environ["PATH"].split(os.pathsep)[0] == str(target / "bin")
    assert os.stat(target / "bin" / "prism").st_mode & stat.S_IXUSR
    assert not (fresh / "prism-4.11.tar.gz").exists()   # the archive is removed

    # a later process finds the copy and only puts it on PATH
    monkeypatch.setattr(ps, "_state", {"status": None, "error": None})
    monkeypatch.setattr(ps, "PRISM_URL", "file:///nonexistent")
    assert ps.ensure_prism() == "ready"


def test_a_wrong_checksum_installs_nothing(fresh, monkeypatch):
    monkeypatch.setenv("CADL_EXPLORER_INSTALL_PRISM", "1")
    _serve(monkeypatch, _archive(fresh), digest="0" * 64)
    assert ps.ensure_prism(wait=True) == "failed"
    assert "SHA-256" in ps.status()["error"]
    assert not (fresh / "prism").exists()
    # it does not try again in the same process
    assert ps.ensure_prism(wait=True) == "failed"


def test_a_path_out_of_the_directory_is_not_unpacked(fresh, monkeypatch):
    monkeypatch.setenv("CADL_EXPLORER_INSTALL_PRISM", "1")
    _serve(monkeypatch, _archive(fresh, evil=True))
    assert ps.ensure_prism(wait=True) == "ready", ps.status()
    assert not (fresh / "outside.txt").exists()


def test_nothing_happens_without_java_or_on_another_platform(fresh, monkeypatch):
    monkeypatch.setenv("CADL_EXPLORER_INSTALL_PRISM", "1")
    monkeypatch.setattr(ps, "supported", lambda: False)
    assert ps.ensure_prism(wait=True) is None
    assert not (fresh / "prism").exists()
