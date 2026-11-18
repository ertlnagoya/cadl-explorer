"""PRISM for `cadl norms-check` where the app is hosted.

Streamlit Community Cloud installs the Debian packages of `packages.txt`
(Java), but not PRISM. On the first run there, `ensure_prism()` downloads the
release archive of the PRISM project in a background thread, checks its
SHA-256 (the archive and checksum of the CI), unpacks it under the home
directory, runs its `install.sh` and puts its `bin` on PATH, where
`cadl.norms_check.find_checker()` finds it. Later runs of the same container
find the unpacked copy and only put it on PATH.

Elsewhere nothing happens unless CADL_EXPLORER_INSTALL_PRISM=1;
CADL_EXPLORER_INSTALL_PRISM=0 turns it off on the Cloud as well.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import tarfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

PRISM_VERSION = "4.11"
PRISM_URL = ("https://github.com/prismmodelchecker/prism/releases/download/"
             f"v{PRISM_VERSION}/prism-{PRISM_VERSION}-linux64-x86.tar.gz")
PRISM_SHA256 = "6d4677b6ab64daa16a8bb5ad05f79c46313ad67835cb8445ee770e7f57f611c8"
DOWNLOAD_TIMEOUT = 120      # seconds
INSTALL_TIMEOUT = 120
RETRY_AFTER = 600           # seconds after a failure before the next try

_lock = threading.Lock()
# status: None, "installing", "ready", "failed" or "unsupported"
_state = {"status": None, "error": None, "failed_at": 0.0}


def _on_cloud() -> bool:
    """Streamlit Community Cloud checks the repository out under /mount/src."""
    return str(Path(__file__).resolve()).startswith("/mount/src/")


def wanted() -> bool:
    flag = os.environ.get("CADL_EXPLORER_INSTALL_PRISM")
    if flag is not None:
        return flag == "1"
    return _on_cloud()


def unsupported_reason() -> Optional[str]:
    """Why PRISM cannot be installed here, or None: the archive is for Linux on
    x86-64, and PRISM runs on Java."""
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        return f"the archive of PRISM is for Linux on x86-64, not {platform.system()} on {platform.machine()}"
    if shutil.which("java") is None:
        return "there is no Java on PATH (packages.txt installs default-jre-headless)"
    return None


def supported() -> bool:
    return unsupported_reason() is None


def prism_dir() -> Path:
    default = Path.home() / ".cache" / "cadl-explorer" / f"prism-{PRISM_VERSION}"
    return Path(os.environ.get("CADL_EXPLORER_PRISM_DIR", default))


def _put_on_path(bin_dir: Path) -> None:
    path = os.environ.get("PATH", "")
    if str(bin_dir) not in path.split(os.pathsep):
        os.environ["PATH"] = str(bin_dir) + (os.pathsep + path if path else "")


def _install(target: Path) -> None:
    """Download, check, unpack and install PRISM into `target`. `install.sh`
    writes the absolute path of the directory into the scripts, so the
    archive is unpacked where it stays; a marker file says it is complete."""
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    archive = target.parent / f"prism-{PRISM_VERSION}.tar.gz"
    try:
        with urllib.request.urlopen(PRISM_URL, timeout=DOWNLOAD_TIMEOUT) as response, \
                open(archive, "wb") as out:
            shutil.copyfileobj(response, out)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != PRISM_SHA256:
            raise RuntimeError(f"the archive of PRISM has SHA-256 {digest}, "
                               f"expected {PRISM_SHA256}")
        with tarfile.open(archive) as tar:
            members = []
            for member in tar.getmembers():
                parts = Path(member.name).parts[1:]     # --strip-components=1
                if not parts or ".." in parts or member.name.startswith("/"):
                    continue
                if not (member.isfile() or member.isdir() or member.issym()):
                    continue
                member.name = str(Path(*parts))
                members.append(member)
            if hasattr(tarfile, "data_filter"):
                tar.extractall(target, members=members, filter="data")
            else:                                        # Python before 3.12
                tar.extractall(target, members=members)
        subprocess.run(["./install.sh", "silent"], cwd=target, check=True,
                       capture_output=True, timeout=INSTALL_TIMEOUT)
        (target / ".installed").write_text(PRISM_VERSION)
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise
    finally:
        archive.unlink(missing_ok=True)


def _run(target: Path) -> None:
    try:
        _install(target)
        _put_on_path(target / "bin")
        _state.update(status="ready", error=None)
    except Exception as exc:        # noqa: BLE001 - reported on the page
        _state.update(status="failed", error=str(exc) or type(exc).__name__,
                      failed_at=time.monotonic())


def ensure_prism(wait: bool = False) -> Optional[str]:
    """Make PRISM available if it is wanted here; returns the status (None when
    nothing is done, "installing", "ready", "failed", or "unsupported"). A failure
    is tried again after RETRY_AFTER seconds."""
    if shutil.which("prism") or not wanted():
        return _state["status"]
    target = prism_dir()
    with _lock:
        if (target / ".installed").exists():
            _put_on_path(target / "bin")
            _state.update(status="ready", error=None)
            return "ready"
        retry = (_state["status"] == "failed"
                 and time.monotonic() - _state.get("failed_at", 0.0) >= RETRY_AFTER)
        reason = unsupported_reason()
        if reason:
            _state.update(status="unsupported", error=reason)
            thread = None
        elif _state["status"] in ("installing", "failed") and not retry:
            thread = _state.get("thread")
        else:
            _state["status"] = "installing"
            thread = threading.Thread(target=_run, args=(target,), daemon=True,
                                      name="prism-setup")
            _state["thread"] = thread
            thread.start()
    if wait and thread is not None:
        thread.join()
    return _state["status"]


def status() -> dict:
    """{"status": ..., "error": ...} of the installation in this process."""
    return {"status": _state["status"], "error": _state["error"]}


def diagnostics() -> str:
    """One line on where the installation stands, for the page when PRISM is
    not found: the status, the directory, its marker file and `bin/prism`, and
    whether that `bin` is on PATH and found by `shutil.which`."""
    target = prism_dir()
    binary = target / "bin" / "prism"
    path = os.environ.get("PATH", "").split(os.pathsep)
    return "; ".join([
        f"status {_state['status']}",
        f"wanted {wanted()} (Cloud {_on_cloud()})",
        f"directory {target}",
        f"marker {(target / '.installed').exists()}",
        f"bin/prism {binary.exists()}, executable {os.access(binary, os.X_OK)}",
        f"bin on PATH {str(target / 'bin') in path}",
        f"which prism {shutil.which('prism')}",
        f"java {shutil.which('java')}",
    ])
