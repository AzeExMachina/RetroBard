# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Checks GitHub for a newer RetroBard and installs it, only when the user asks.

Nothing here runs on its own: the "Check for updates" button calls it. The check
reads the latest GitHub release; installing downloads its Windows zip, checks the
SHA-256 that GitHub (or the release's .sha256 file) gives, and unpacks it next to
the app. The running app cannot replace its own files, so a small PowerShell script
waits for it to close, swaps the folders, and starts the new version. The user's
own files live in %APPDATA%\\RetroBard and are never touched.
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import httpx

from retrobard import __version__

GITHUB_REPO = "AzeExMachina/RetroBard"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"
ZIP_ROOT = "RetroBard"  # the folder inside the release zip (packaging/build.py)
EXE_NAME = "RetroBard.exe"
STAGING_PREFIX = ".RetroBard-update-"
VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CHUNK = 256 * 1024


class UpdateError(Exception):
    """Something the user should read: the check, download or unpacking failed."""


class UpdateCancelled(UpdateError):
    pass


def parse_version(text: str) -> tuple[int, ...] | None:
    """(0, 2, 3) for "v0.2.3" or "0.2.3"; None for anything else (pre-releases too)."""
    match = VERSION_RE.match(text.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(latest: str, current: str = __version__) -> bool:
    new, old = parse_version(latest), parse_version(current)
    if new is None or old is None:
        return False
    width = max(len(new), len(old))
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


@dataclass(frozen=True)
class Release:
    version: str
    notes: str
    page_url: str
    zip_name: str | None = None
    zip_url: str | None = None
    zip_size: int = 0
    sha256: str | None = None  # from GitHub's asset digest
    checksum_url: str | None = None  # the release's <zip>.sha256 file

    @property
    def can_download(self) -> bool:
        return self.zip_url is not None


def parse_release(data: dict) -> Release:
    """A Release from GitHub's JSON for one release."""
    tag = str(data.get("tag_name") or "")
    version = tag.removeprefix("v")
    zip_name = f"RetroBard-{version}-windows.zip"
    assets = {asset.get("name"): asset for asset in data.get("assets") or []}
    zip_asset = assets.get(zip_name)
    checksum_asset = assets.get(f"{zip_name}.sha256")
    sha256 = None
    if zip_asset is not None:
        digest = str(zip_asset.get("digest") or "")
        if digest.startswith("sha256:") and SHA256_RE.match(digest[7:].lower()):
            sha256 = digest[7:].lower()
    return Release(
        version=version,
        notes=str(data.get("body") or "").strip(),
        page_url=str(data.get("html_url") or RELEASES_PAGE),
        zip_name=zip_name if zip_asset is not None else None,
        zip_url=zip_asset.get("browser_download_url") if zip_asset is not None else None,
        zip_size=int(zip_asset.get("size") or 0) if zip_asset is not None else 0,
        sha256=sha256,
        checksum_url=(
            checksum_asset.get("browser_download_url") if checksum_asset is not None else None
        ),
    )


def make_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": f"RetroBard/{__version__}"},
        follow_redirects=True,
        timeout=httpx.Timeout(20.0, connect=10.0),
    )


def fetch_latest_release(client: httpx.Client) -> Release:
    try:
        response = client.get(
            LATEST_RELEASE_API,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
    except httpx.HTTPError as exc:
        raise UpdateError(f"Could not reach GitHub: {exc}") from exc
    if response.status_code == 404:
        raise UpdateError("No release has been published yet.")
    if response.status_code in (403, 429):
        raise UpdateError("GitHub is limiting requests right now. Try again in an hour.")
    if response.status_code != 200:
        raise UpdateError(f"GitHub answered with an error ({response.status_code}).")
    try:
        release = parse_release(response.json())
    except (ValueError, TypeError, AttributeError) as exc:
        raise UpdateError("GitHub sent an answer RetroBard does not understand.") from exc
    if parse_version(release.version) is None:
        raise UpdateError(f"The latest release has an unexpected version ({release.version}).")
    return release


def expected_sha256(client: httpx.Client, release: Release) -> str:
    """The zip's SHA-256: GitHub's digest, or the release's .sha256 file."""
    if release.sha256:
        return release.sha256
    if release.checksum_url:
        try:
            response = client.get(release.checksum_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise UpdateError(f"Could not download the checksum: {exc}") from exc
        token = response.text.strip().split()[0].lower() if response.text.strip() else ""
        if SHA256_RE.match(token):
            return token
    raise UpdateError("The release has no checksum, so the download cannot be verified.")


def download_release(
    client: httpx.Client,
    release: Release,
    folder: Path,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Path:
    """Downloads the release zip into folder and checks its SHA-256."""
    if not release.can_download:
        raise UpdateError("This release has no Windows download.")
    expected = expected_sha256(client, release)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / release.zip_name
    partial = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    done = 0
    try:
        with client.stream("GET", release.zip_url) as response, partial.open("wb") as out:
            if response.status_code != 200:
                raise UpdateError(f"The download failed ({response.status_code}).")
            total = int(response.headers.get("Content-Length") or release.zip_size or 0)
            for chunk in response.iter_bytes(CHUNK):
                if cancelled is not None and cancelled():
                    raise UpdateCancelled("Download cancelled.")
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
    except httpx.HTTPError as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"The download failed: {exc}") from exc
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    if digest.hexdigest() != expected:
        partial.unlink(missing_ok=True)
        raise UpdateError("The download is damaged (its checksum does not match). Try again.")
    partial.replace(target)
    return target


# --------------------------------------------------------------------- installing


def install_dir() -> Path:
    """The folder holding RetroBard.exe (the packaged app)."""
    return Path(sys.executable).resolve().parent


def managed_by_itch(folder: Path) -> bool:
    """True when the itch.io app installed this copy (it keeps a .itch folder there)."""
    return (folder / ".itch").is_dir()


def _writable(folder: Path) -> bool:
    probe = folder / f".retrobard-write-test-{os.getpid()}"
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        return False
    return True


def update_blocker(folder: Path, frozen: bool) -> str | None:
    """Why this copy cannot update itself, or None when it can."""
    if not frozen:
        return "You are running RetroBard from source: update it with git pull."
    if managed_by_itch(folder):
        return "This copy was installed by the itch.io app: update it there."
    if not (folder / EXE_NAME).is_file():
        return f"{EXE_NAME} was not found next to the app."
    if not (_writable(folder) and _writable(folder.parent)):
        return "RetroBard cannot write to its folder. Download the new version by hand."
    return None


def staging_dir(folder: Path, version: str) -> Path:
    return folder.parent / f"{STAGING_PREFIX}{version}"


def extract_update(zip_path: Path, folder: Path, version: str) -> Path:
    """Unpacks the release zip next to the app folder; returns the new app folder."""
    staging = staging_dir(folder, version)
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                parts = PurePosixPath(info.filename.replace("\\", "/")).parts
                if (
                    not parts
                    or parts[0] != ZIP_ROOT
                    or any(part in ("", "..") or ":" in part for part in parts)
                ):
                    raise UpdateError(f"The download contains an unexpected file: {info.filename}")
            zf.extractall(staging)
    except zipfile.BadZipFile as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise UpdateError("The download is not a valid zip file.") from exc
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    new_app = staging / ZIP_ROOT
    if not (new_app / EXE_NAME).is_file():
        shutil.rmtree(staging, ignore_errors=True)
        raise UpdateError(f"The download does not contain {EXE_NAME}.")
    return new_app


# Waits for RetroBard to close, swaps the folders (putting the old one back if that
# fails), keeps any files the user added next to the old RetroBard.exe, and starts
# the app again.
SWAP_SCRIPT = r"""
param(
    [int]$WaitPid,
    [string]$Install,
    [string]$Staged,
    [string]$Backup,
    [string]$StagingRoot,
    [string]$Log,
    [switch]$NoRestart
)
$ErrorActionPreference = 'Stop'

function Write-Log([string]$Message) {
    try { Add-Content -LiteralPath $Log -Value "$(Get-Date -Format s) $Message" } catch {}
}

function Invoke-Retry([scriptblock]$Action) {
    for ($i = 0; $i -lt 40; $i++) {
        try { & $Action; return } catch { Start-Sleep -Milliseconds 500 }
    }
    & $Action
}

$process = Get-Process -Id $WaitPid -ErrorAction SilentlyContinue
if ($process) {
    Write-Log "Waiting for RetroBard ($WaitPid) to close"
    if (-not $process.WaitForExit(60000)) {
        Write-Log "RetroBard did not close: update not installed"
        exit 1
    }
}

$moved = $false
$failed = $false
try {
    if (Test-Path -LiteralPath $Backup) { Remove-Item -LiteralPath $Backup -Recurse -Force }
    Invoke-Retry { Move-Item -LiteralPath $Install -Destination $Backup }
    $moved = $true
    Move-Item -LiteralPath $Staged -Destination $Install
    foreach ($item in Get-ChildItem -LiteralPath $Backup -Force) {
        $target = Join-Path $Install $item.Name
        if (-not (Test-Path -LiteralPath $target)) {
            Move-Item -LiteralPath $item.FullName -Destination $target
        }
    }
    Write-Log "Installed the new version in $Install"
} catch {
    $failed = $true
    Write-Log "Update failed: $_"
    if ($moved -and -not (Test-Path -LiteralPath (Join-Path $Install 'RetroBard.exe'))) {
        if (Test-Path -LiteralPath $Install) { Remove-Item -LiteralPath $Install -Recurse -Force }
        Move-Item -LiteralPath $Backup -Destination $Install
        Write-Log "Restored the previous version"
    }
}

if (-not $NoRestart) {
    try {
        Start-Process -FilePath (Join-Path $Install 'RetroBard.exe') -WorkingDirectory $Install
    } catch {
        Write-Log "Could not start RetroBard: $_"
    }
}
if (-not $failed) {
    Remove-Item -LiteralPath $Backup -Recurse -Force -ErrorAction SilentlyContinue
}
Remove-Item -LiteralPath $StagingRoot -Recurse -Force -ErrorAction SilentlyContinue
if ($failed) { exit 1 }
"""


def swap_command(
    script: Path,
    *,
    pid: int,
    folder: Path,
    new_app: Path,
    log: Path,
    restart: bool = True,
) -> list[str]:
    command = [
        "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-WindowStyle", "Hidden", "-File", str(script),
        "-WaitPid", str(pid),
        "-Install", str(folder),
        "-Staged", str(new_app),
        "-Backup", str(folder.with_name(folder.name + ".old")),
        "-StagingRoot", str(new_app.parent),
        "-Log", str(log),
    ]
    if not restart:
        command.append("-NoRestart")
    return command


def start_swap(folder: Path, new_app: Path, log: Path, temp_dir: Path) -> None:
    """Starts the swap script, detached; the caller must then quit the app."""
    temp_dir.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    script = temp_dir / "retrobard-update.ps1"
    script.write_text(SWAP_SCRIPT, encoding="utf-8-sig")
    command = swap_command(script, pid=os.getpid(), folder=folder, new_app=new_app, log=log)
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        # Outlive the app even when it runs inside a job object that would end it too
        subprocess.Popen(
            command, cwd=folder.parent, close_fds=True,
            creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB,
        )
    except OSError:
        subprocess.Popen(command, cwd=folder.parent, close_fds=True, creationflags=flags)


def discard_staged(new_app: Path) -> None:
    shutil.rmtree(new_app.parent, ignore_errors=True)
