# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
"""Builds the RetroBard tester package: dist/RetroBard-<version>-windows.zip.

    uv run --extra dev python packaging/build.py

Steps: draw the app icon, run PyInstaller (one-folder, no console), add the
testers' read-me, the license files and third-party notices, check that no game,
secret or excluded library slipped in, and zip the folder.
"""

import fnmatch
import os
import shutil
import subprocess
import sys
import sysconfig
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WORK = HERE / "build"
DIST = ROOT / "dist"
LICENSES = HERE / "licenses"
# Copied next to RetroBard.exe
PROJECT_NOTICES = ("LICENSE", "NOTICE", "PRIVACY.md", "CHANGELOG.md")

# Games, disc images, saves and archives that must never be shipped
ROM_EXTENSIONS = frozenset({
    ".sfc", ".smc", ".fig", ".swc", ".nes", ".fds", ".gb", ".gbc", ".gba", ".n64", ".z64",
    ".v64", ".gen", ".smd", ".sms", ".gg", ".pce", ".iso", ".cue", ".chd", ".srm", ".sav",
    ".state", ".zip", ".7z", ".rar",
})
# Keys, tokens and cache files from a developer's own runs
SECRET_NAMES = frozenset({".env", "settings.env", "udp_token"})
SECRET_SUFFIXES = (".sqlite3", ".pcm", ".font.json.tmp")
# Left out of the build (see retrobard.spec): GPL-only Qt module and an unused video DLL
EXCLUDED_BINARY_PARTS = ("virtualkeyboard", "opencv_videoio_ffmpeg")


# Archives the build itself makes: PyInstaller's standard library and the tester zips
OWN_ARCHIVES = ("base_library.zip", "RetroBard-*-windows.zip")


def is_rom(path: Path) -> bool:
    if any(fnmatch.fnmatch(path.name, pattern) for pattern in OWN_ARCHIVES):
        return False
    return path.suffix.lower() in ROM_EXTENSIONS


def find_forbidden_files(folder: Path) -> list[Path]:
    """Files inside folder that must never be in a build: ROMs, secrets, cache files."""
    if not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        name = path.name.lower()
        if is_rom(path) or name in SECRET_NAMES or name.endswith(SECRET_SUFFIXES):
            found.append(path)
    return found


def find_excluded_binaries(folder: Path) -> list[Path]:
    """Files the spec should have dropped (Qt Virtual Keyboard, OpenCV's FFmpeg DLL)."""
    return [
        path
        for path in sorted(folder.rglob("*"))
        if path.is_file() and any(part in path.name.lower() for part in EXCLUDED_BINARY_PARTS)
    ]


# ------------------------------------------------------------- third-party notices

LICENSE_FILE_MARKERS = ("LICEN", "COPYING", "NOTICE")

# Parts that are not Python distributions of their own; texts in packaging/licenses
BUNDLED_COMPONENTS = [
    ("Qt 6 (through PySide6 and shiboken6)", "LGPL-3.0-only", "licenses/Qt"),
    ("SDL2, SDL2_image, SDL2_mixer, SDL2_ttf (in pygame)", "Zlib", "licenses/pygame-bundled"),
    ("FreeType (in pygame)", "FTL", "licenses/pygame-bundled"),
    ("libpng (in pygame)", "libpng-2.0", "licenses/pygame-bundled"),
    ("libjpeg (in pygame)", "IJG", "licenses/pygame-bundled"),
    ("libtiff (in pygame)", "libtiff", "licenses/pygame-bundled"),
    ("libwebp (in pygame)", "BSD-3-Clause", "licenses/pygame-bundled"),
    ("zlib (in pygame)", "Zlib", "licenses/pygame-bundled"),
    ("libogg, opus, opusfile (in pygame)", "BSD-3-Clause", "licenses/pygame-bundled"),
    ("libmodplug (in pygame)", "public domain", "licenses/pygame-bundled"),
    ("portmidi (in pygame)", "MIT", "licenses/pygame-bundled"),
    ("OpenSSL 3 (in Python)", "Apache-2.0", "licenses/OpenSSL"),
    ("Python", "PSF-2.0", "licenses/Python"),
    ("Mesa (opengl32sw.dll, in PySide6)", "MIT", "licenses/Mesa"),
]


def runtime_distributions(root: str = "retrobard") -> list:
    """The installed distributions root needs at run time, followed recursively."""
    from importlib.metadata import PackageNotFoundError, distribution

    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    found = {}
    pending = [(canonicalize_name(root), frozenset())]
    while pending:
        name, extras = pending.pop()
        if name in found:
            continue
        try:
            dist = distribution(name)
        except PackageNotFoundError:
            continue  # e.g. a Windows-only dependency on another system
        found[name] = dist
        for spec in dist.requires or []:
            req = Requirement(spec)
            if req.marker and not any(
                req.marker.evaluate({"extra": extra}) for extra in ("", *extras)
            ):
                continue
            pending.append((canonicalize_name(req.name), frozenset(req.extras)))
    found.pop(canonicalize_name(root), None)
    return [found[name] for name in sorted(found)]


def license_of(meta) -> str:
    if meta.get("License-Expression"):
        return meta["License-Expression"]
    text = (meta.get("License") or "").strip()
    if text:
        first = text.splitlines()[0].strip()
        return first if len(first) <= 80 else first[:77] + "..."
    classifiers = [
        c.split("::")[-1].strip()
        for c in meta.get_all("Classifier") or []
        if c.startswith("License ::")
    ]
    return ", ".join(classifiers) or "unknown"


def home_page_of(meta) -> str:
    if meta.get("Home-page"):
        return meta["Home-page"]
    urls = [url.split(",", 1) for url in meta.get_all("Project-URL") or [] if "," in url]
    for label, url in urls:
        if label.strip().lower() in ("homepage", "home", "source", "repository"):
            return url.strip()
    return urls[0][1].strip() if urls else ""


def copy_license_files(dist, target: Path) -> int:
    copied = 0
    for file in dist.files or []:
        name = file.name
        if not any(marker in name.upper() for marker in LICENSE_FILE_MARKERS):
            continue
        if name.endswith((".py", ".pyc", ".pyi")):
            continue
        source = Path(dist.locate_file(file))
        if not source.is_file():
            continue
        target.mkdir(parents=True, exist_ok=True)
        destination = target / name
        if destination.exists():
            destination = target / f"{file.parent.name}-{name}"
        shutil.copyfile(source, destination)
        copied += 1
    return copied


def write_qt_source(folder: Path) -> None:
    import PySide6
    from PySide6.QtCore import qVersion

    template = (LICENSES / "Qt" / "QT-SOURCE.txt").read_text(encoding="utf-8")
    text = template.format(qt_version=qVersion(), pyside_version=PySide6.__version__)
    (folder / "licenses" / "Qt" / "QT-SOURCE.txt").write_text(text, encoding="utf-8")


def write_third_party_notices(folder: Path) -> Path:
    """Writes THIRD-PARTY-NOTICES.txt and licenses/ into the build folder."""
    licenses = folder / "licenses"
    entries = []
    for dist in runtime_distributions():
        meta = dist.metadata
        name = meta["Name"]
        has_files = copy_license_files(dist, licenses / name)
        entry = [
            f"{name} {dist.version}",
            f"  License: {license_of(meta)}",
            f"  Home page: {home_page_of(meta) or '-'}",
        ]
        if has_files:
            entry.append(f"  License files: licenses/{name}/")
        entries.append("\n".join(entry))
    shutil.copytree(LICENSES, licenses, dirs_exist_ok=True)
    write_qt_source(folder)

    bundled = [
        f"{part}\n  License: {license}\n  License files: {where}/"
        for part, license, where in BUNDLED_COMPONENTS
    ]
    header = [
        "RetroBard third-party notices",
        "=============================",
        "",
        "RetroBard includes the components below. Their license texts are in the",
        "licenses folder next to this file.",
        "",
        "Python packages",
        "---------------",
    ]
    other = ["Other bundled components", "------------------------"]
    notices = folder / "THIRD-PARTY-NOTICES.txt"
    notices.write_text(
        "\n\n".join(["\n".join(header), *entries, "\n".join(other), *bundled]) + "\n",
        encoding="utf-8",
    )
    return notices


def copy_project_notices(folder: Path) -> None:
    """LICENSE, NOTICE, PRIVACY.md and CHANGELOG.md go next to RetroBard.exe."""
    for name in PROJECT_NOTICES:
        shutil.copyfile(ROOT / name, folder / name)


def refuse(message: str, paths: list[Path]) -> None:
    print(f"\n{message}", file=sys.stderr)
    for path in paths:
        print(f"  {path}", file=sys.stderr)
    raise SystemExit(1)


def version_info_text(version: str) -> str:
    """The .exe's Properties > Details, in PyInstaller's VSVersionInfo format."""
    from retrobard import about

    numbers = [int(part) for part in version.split(".")[:4]]
    numbers += [0] * (4 - len(numbers))
    dotted = ".".join(str(n) for n in numbers)
    exe = f"{about.APP_NAME}.exe"
    strings = {
        "CompanyName": about.AUTHOR,
        "FileDescription": about.APP_NAME,
        "FileVersion": dotted,
        "InternalName": exe,
        "LegalCopyright": f"© {about.COPYRIGHT_YEAR} {about.AUTHOR}",
        "OriginalFilename": exe,
        "ProductName": about.APP_NAME,
        "ProductVersion": dotted,
    }
    table = ",\n".join(
        f"          StringStruct({key!r}, {value!r})" for key, value in strings.items()
    )
    return (
        "VSVersionInfo(\n"
        "  ffi=FixedFileInfo(\n"
        f"    filevers={tuple(numbers)},\n"
        f"    prodvers={tuple(numbers)},\n"
        "    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)\n"
        "  ),\n"
        "  kids=[\n"
        "    StringFileInfo([\n"
        "      StringTable('040904B0', [\n"
        f"{table}\n"
        "      ])\n"
        "    ]),\n"
        "    VarFileInfo([VarStruct('Translation', [1033, 1200])])\n"
        "  ]\n"
        ")\n"
    )


def write_version_info(path: Path, version: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(version_info_text(version), encoding="utf-8")
    return path


def draw_icon(path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from retrobard.gui.main import make_icon

    app = QApplication.instance() or QApplication([])
    pixmap = make_icon().pixmap(256, 256)
    if not pixmap.save(str(path), "ICO"):
        raise SystemExit(f"Could not write the icon {path}")
    del app


def python_dll_dir() -> Path:
    """Where this Python keeps the DLLs its own modules need (OpenSSL, libffi...)."""
    return Path(sys.base_prefix) / "DLLs"


def pyinstaller_env() -> dict[str, str]:
    """The environment for PyInstaller, with Python's own DLLs found first.

    PyInstaller looks for a module's DLLs on PATH. A shell such as Git Bash puts
    its own copies first (Git's OpenSSL in mingw64/bin), and bundling those next
    to Python's _ssl makes the app crash on start ("DLL load failed while
    importing _ssl")."""
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join(
        [str(python_dll_dir()), sys.base_prefix, env.get("PATH", "")]
    )
    return env


def find_foreign_python_dlls(folder: Path, dll_dir: Path, packages_dir: Path) -> list[Path]:
    """Bundled DLLs named like one of Python's own whose contents match neither
    Python's copy nor one shipped inside an installed package (pygame brings its
    own zlib1.dll, for instance): a copy picked up from another program, which
    Python's modules may not be able to load."""
    found = []
    for path in sorted(folder.rglob("*.dll")):
        own = dll_dir / path.name
        if not own.is_file():
            continue
        data = path.read_bytes()
        sources = [own, *packages_dir.rglob(path.name)]
        if not any(source.read_bytes() == data for source in sources):
            found.append(path)
    return found


def run_pyinstaller() -> Path:
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller", str(HERE / "retrobard.spec"),
            "--noconfirm", "--clean",
            "--workpath", str(WORK / "pyinstaller"),
            "--distpath", str(DIST),
        ],
        check=True,
        cwd=ROOT,
        env=pyinstaller_env(),
    )
    return DIST / "RetroBard"


def make_zip(folder: Path, version: str) -> Path:
    archive = DIST / f"RetroBard-{version}-windows.zip"
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                zf.write(path, Path("RetroBard") / path.relative_to(folder))
    return archive


def main() -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from retrobard import __version__

    roms = [path for path in find_forbidden_files(DIST) if is_rom(path)]
    if roms:
        refuse("Build stopped: remove these game files from dist/ first.", roms)

    WORK.mkdir(parents=True, exist_ok=True)
    draw_icon(WORK / "retrobard.ico")
    write_version_info(WORK / "version_info.txt", __version__)
    folder = run_pyinstaller()
    excluded = find_excluded_binaries(folder)
    if excluded:
        refuse("Build stopped: these files must be left out of the build.", excluded)
    foreign = find_foreign_python_dlls(
        folder, python_dll_dir(), Path(sysconfig.get_paths()["purelib"])
    )
    if foreign:
        refuse(
            "Build stopped: these DLLs come from another program, not this Python, "
            "and would stop the app from starting. Build from a plain PowerShell window.",
            foreign,
        )
    readme = (HERE / "README-testers.txt").read_text(encoding="utf-8")
    (folder / "README.txt").write_text(readme.replace("{version}", __version__), encoding="utf-8")
    copy_project_notices(folder)
    write_third_party_notices(folder)
    forbidden = find_forbidden_files(folder)
    if forbidden:
        refuse("Build stopped, no zip written: these files must not be shipped.", forbidden)
    archive = make_zip(folder, __version__)
    size_mb = archive.stat().st_size / 1_000_000
    print(f"\nBuilt {folder}\nZipped {archive} ({size_mb:.0f} MB)")


if __name__ == "__main__":
    main()
    shutil.rmtree(WORK / "pyinstaller", ignore_errors=True)
