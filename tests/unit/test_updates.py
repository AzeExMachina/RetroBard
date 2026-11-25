# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import io
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import httpx
import pytest

from retrobard.app import updates
from retrobard.app.updates import UpdateCancelled, UpdateError

ZIP_URL = "https://github.com/x/releases/download/v9.0.0/RetroBard-9.0.0-windows.zip"
SUM_URL = ZIP_URL + ".sha256"


def make_zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buffer.getvalue()


APP_ZIP = make_zip({
    "RetroBard/RetroBard.exe": b"new exe",
    "RetroBard/_internal/lib.dll": b"new lib",
    "RetroBard/README.txt": b"read me",
})


def release_json(digest: str | None = None, with_sum: bool = False) -> dict:
    zip_asset = {
        "name": "RetroBard-9.0.0-windows.zip",
        "browser_download_url": ZIP_URL,
        "size": len(APP_ZIP),
    }
    if digest:
        zip_asset["digest"] = digest
    assets = [zip_asset]
    if with_sum:
        assets.append({"name": "RetroBard-9.0.0-windows.zip.sha256", "browser_download_url": SUM_URL})
    return {
        "tag_name": "v9.0.0",
        "body": "New voices.",
        "html_url": "https://github.com/x/releases/tag/v9.0.0",
        "assets": assets,
    }


def client_for(routes: dict[str, httpx.Response]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return routes.get(str(request.url), httpx.Response(404))

    return httpx.Client(transport=httpx.MockTransport(handler))


# ------------------------------------------------------------------------ versions


@pytest.mark.parametrize(
    ("text", "expected"),
    [("v0.2.3", (0, 2, 3)), ("1.10", (1, 10)), ("0.3.0-beta", None), ("", None), ("vx", None)],
)
def test_parse_version(text, expected):
    assert updates.parse_version(text) == expected


@pytest.mark.parametrize(
    ("latest", "current", "newer"),
    [
        ("0.2.3", "0.2.2", True),
        ("0.10.0", "0.9.9", True),
        ("0.2.2", "0.2.2", False),
        ("0.2", "0.2.0", False),
        ("0.2.1", "0.2.2", False),
        ("nonsense", "0.2.2", False),
    ],
)
def test_is_newer(latest, current, newer):
    assert updates.is_newer(latest, current) is newer


# ------------------------------------------------------------------------- checking


def test_parse_release_reads_the_zip_and_its_digest():
    sha = "a" * 64
    release = updates.parse_release(release_json(digest=f"sha256:{sha}", with_sum=True))
    assert release.version == "9.0.0"
    assert release.notes == "New voices."
    assert release.zip_url == ZIP_URL
    assert release.sha256 == sha
    assert release.checksum_url == SUM_URL
    assert release.can_download


def test_parse_release_without_a_windows_zip():
    data = release_json()
    data["assets"] = [{"name": "source.tar.gz", "browser_download_url": "x"}]
    release = updates.parse_release(data)
    assert not release.can_download


def test_fetch_latest_release():
    client = client_for({updates.LATEST_RELEASE_API: httpx.Response(200, json=release_json())})
    assert updates.fetch_latest_release(client).version == "9.0.0"


@pytest.mark.parametrize(
    ("status", "message"), [(404, "No release"), (403, "limiting"), (500, "500")]
)
def test_fetch_latest_release_errors(status, message):
    client = client_for({updates.LATEST_RELEASE_API: httpx.Response(status)})
    with pytest.raises(UpdateError, match=message):
        updates.fetch_latest_release(client)


def test_fetch_latest_release_offline():
    def handler(request):
        raise httpx.ConnectError("no network", request=request)

    with pytest.raises(UpdateError, match="Could not reach GitHub"):
        updates.fetch_latest_release(httpx.Client(transport=httpx.MockTransport(handler)))


# ------------------------------------------------------------------------ download


def test_download_checks_the_github_digest(tmp_path: Path):
    sha = hashlib.sha256(APP_ZIP).hexdigest()
    release = updates.parse_release(release_json(digest=f"sha256:{sha}"))
    seen = []
    client = client_for({ZIP_URL: httpx.Response(200, content=APP_ZIP)})
    path = updates.download_release(client, release, tmp_path, progress=lambda d, t: seen.append(d))
    assert path.read_bytes() == APP_ZIP
    assert seen[-1] == len(APP_ZIP)


def test_download_falls_back_to_the_checksum_file(tmp_path: Path):
    sha = hashlib.sha256(APP_ZIP).hexdigest()
    release = updates.parse_release(release_json(with_sum=True))
    client = client_for({
        ZIP_URL: httpx.Response(200, content=APP_ZIP),
        SUM_URL: httpx.Response(200, text=f"{sha}  RetroBard-9.0.0-windows.zip"),
    })
    assert updates.download_release(client, release, tmp_path).exists()


def test_download_refuses_a_damaged_file(tmp_path: Path):
    release = updates.parse_release(release_json(digest="sha256:" + "0" * 64))
    client = client_for({ZIP_URL: httpx.Response(200, content=APP_ZIP)})
    with pytest.raises(UpdateError, match="damaged"):
        updates.download_release(client, release, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_download_refuses_without_a_checksum(tmp_path: Path):
    release = updates.parse_release(release_json())
    client = client_for({ZIP_URL: httpx.Response(200, content=APP_ZIP)})
    with pytest.raises(UpdateError, match="no checksum"):
        updates.download_release(client, release, tmp_path)


def test_download_can_be_cancelled(tmp_path: Path):
    release = updates.parse_release(release_json(digest="sha256:" + "0" * 64))
    client = client_for({ZIP_URL: httpx.Response(200, content=APP_ZIP)})
    with pytest.raises(UpdateCancelled):
        updates.download_release(client, release, tmp_path, cancelled=lambda: True)
    assert list(tmp_path.iterdir()) == []


# ----------------------------------------------------------------------- unpacking


def write_zip(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def test_extract_update_next_to_the_app(tmp_path: Path):
    app = tmp_path / "games" / "RetroBard"
    app.mkdir(parents=True)
    new_app = updates.extract_update(write_zip(tmp_path / "u.zip", APP_ZIP), app, "9.0.0")
    assert new_app.parent == app.parent / ".RetroBard-update-9.0.0"
    assert (new_app / "RetroBard.exe").read_bytes() == b"new exe"


@pytest.mark.parametrize(
    "name", ["RetroBard/../evil.txt", "Other/RetroBard.exe", "/RetroBard/x", "RetroBard/C:x"]
)
def test_extract_update_refuses_files_outside_the_app(tmp_path: Path, name: str):
    app = tmp_path / "RetroBard"
    app.mkdir()
    data = make_zip({"RetroBard/RetroBard.exe": b"exe", name: b"bad"})
    with pytest.raises(UpdateError, match="unexpected file"):
        updates.extract_update(write_zip(tmp_path / "u.zip", data), app, "9.0.0")
    assert not updates.staging_dir(app, "9.0.0").exists()
    assert not (tmp_path / "evil.txt").exists()


def test_extract_update_needs_the_exe(tmp_path: Path):
    app = tmp_path / "RetroBard"
    app.mkdir()
    data = make_zip({"RetroBard/README.txt": b"x"})
    with pytest.raises(UpdateError, match="does not contain"):
        updates.extract_update(write_zip(tmp_path / "u.zip", data), app, "9.0.0")


def test_extract_update_refuses_a_broken_zip(tmp_path: Path):
    app = tmp_path / "RetroBard"
    app.mkdir()
    with pytest.raises(UpdateError, match="not a valid zip"):
        updates.extract_update(write_zip(tmp_path / "u.zip", b"not a zip"), app, "9.0.0")


# ------------------------------------------------------------------------ blockers


def test_update_blocker(tmp_path: Path):
    app = tmp_path / "RetroBard"
    app.mkdir()
    assert "git pull" in updates.update_blocker(app, frozen=False)
    assert "not found" in updates.update_blocker(app, frozen=True)
    (app / "RetroBard.exe").write_bytes(b"exe")
    assert updates.update_blocker(app, frozen=True) is None
    (app / ".itch").mkdir()
    assert "itch.io app" in updates.update_blocker(app, frozen=True)


# --------------------------------------------------------------------- swap script


def old_install(root: Path) -> Path:
    app = root / "RetroBard"
    (app / "_internal").mkdir(parents=True)
    (app / "RetroBard.exe").write_bytes(b"old exe")
    (app / "_internal" / "old.dll").write_bytes(b"old lib")
    (app / "my notes.txt").write_text("mine")  # something the user put there
    return app


def run_swap(tmp_path: Path, app: Path, new_app: Path) -> subprocess.CompletedProcess:
    script = tmp_path / "swap.ps1"
    script.write_text(updates.SWAP_SCRIPT, encoding="utf-8-sig")
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    command = updates.swap_command(
        script, pid=finished.pid, folder=app, new_app=new_app,
        log=tmp_path / "update.log", restart=False,
    )
    command.remove("-WindowStyle")
    command.remove("Hidden")
    return subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)


@pytest.mark.skipif(sys.platform != "win32" or not shutil.which("powershell.exe"),
                    reason="the swap script runs in Windows PowerShell")
def test_swap_script_installs_the_new_version(tmp_path: Path):
    app = old_install(tmp_path)
    new_app = updates.extract_update(write_zip(tmp_path / "u.zip", APP_ZIP), app, "9.0.0")
    result = run_swap(tmp_path, app, new_app)
    assert result.returncode == 0, result.stderr + (tmp_path / "update.log").read_text()
    assert (app / "RetroBard.exe").read_bytes() == b"new exe"
    assert (app / "_internal" / "lib.dll").exists()
    assert not (app / "_internal" / "old.dll").exists()
    assert (app / "my notes.txt").read_text() == "mine"
    assert not app.with_name("RetroBard.old").exists()
    assert not new_app.parent.exists()


@pytest.mark.skipif(sys.platform != "win32" or not shutil.which("powershell.exe"),
                    reason="the swap script runs in Windows PowerShell")
def test_swap_script_restores_the_old_version_on_failure(tmp_path: Path):
    app = old_install(tmp_path)
    missing = tmp_path / ".RetroBard-update-9.0.0" / "RetroBard"
    result = run_swap(tmp_path, app, missing)
    assert result.returncode == 1
    assert (app / "RetroBard.exe").read_bytes() == b"old exe"
    assert "Restored the previous version" in (tmp_path / "update.log").read_text()

