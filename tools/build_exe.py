"""Build the Windows distribution folder.

    python tools/build_exe.py [--out DIR]

Produces a single self-contained folder (default: Desktop\\UnityGuard):

    UnityGuard.exe        graphical front-end (double-click this)
    runtime\\             runtime files for the GUI
    UnityGuard-CLI.exe    command line version, for scripts / CI
    cli_runtime\\         runtime files for the CLI
    使用说明.txt           short guide

Two deliberate choices:

* **onedir, not onefile** - the onefile build re-extracts itself into %TEMP% on
  every launch and hardens that folder's ACL; in folders with restrictive
  permissions (including anything created inside an agent sandbox) it dies with
  "Could not create temporary directory". A plain folder has no such step.
* **build outside the project** - the output is assembled in the system temp
  folder and copied to its destination, so the shipped files do not inherit the
  project folder's ACLs.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"

VERSION_INFO = """\
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={filevers},
    prodvers={prodvers},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          '080404B0',
          [
            StringStruct('CompanyName', 'UnityGuard'),
            StringStruct('FileDescription', '{description}'),
            StringStruct('FileVersion', '{version}'),
            StringStruct('InternalName', '{internal}'),
            StringStruct('LegalCopyright', 'UnityGuard'),
            StringStruct('OriginalFilename', '{internal}.exe'),
            StringStruct('ProductName', 'UnityGuard'),
            StringStruct('ProductVersion', '{version}')
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""


def build_one(name: str, script: str, *, windowed: bool, version: str,
              filevers: tuple, description: str, contents: str,
              work: Path, dist: Path) -> Path:
    version_file = work / f"version_{name}.txt"
    version_file.write_text(
        VERSION_INFO.format(filevers=filevers, prodvers=filevers, version=version,
                            description=description, internal=name),
        encoding="utf-8",
    )
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onedir",
        "--contents-directory", contents,
        "--console" if not windowed else "--windowed",
        "--name", name,
        "--version-file", str(version_file),
        "--distpath", str(dist),
        "--workpath", str(work / "work"),
        "--specpath", str(work),
        str(ROOT / script),
    ]
    print(f"\n>>> {name} ({'GUI' if windowed else 'CLI'})")
    completed = subprocess.run(command, cwd=ROOT)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)
    exe = dist / name / f"{name}.exe"
    if not exe.is_file():
        raise SystemExit(f"{exe} was not produced")
    return dist / name


def smoke_test(exe: Path, expected: str) -> None:
    completed = subprocess.run([str(exe), "--version"],
                               capture_output=True, text=True, timeout=180)
    output = (completed.stdout or "").strip()
    if completed.returncode != 0 or expected not in output:
        raise SystemExit(f"smoke test failed: rc={completed.returncode} "
                         f"out={output!r} err={(completed.stderr or '')[:200]}")
    print(f"冒烟测试通过：{output}")


def _is_running(folder: Path) -> bool:
    """A running exe keeps its DLLs locked, so the build cannot replace them."""
    try:
        import psutil  # type: ignore
    except ImportError:
        return False
    prefix = str(folder).lower()
    for process in psutil.process_iter(["exe"]):
        exe = (process.info.get("exe") or "").lower()
        if exe.startswith(prefix):
            return True
    return False


def _force_remove(path: Path) -> bool:
    """Delete a previous build, fixing ACLs first if needed.

    Files created by a restricted/sandboxed process can end up without DELETE
    rights for the current user, which makes rmtree fail with "access denied".
    """
    import os

    def attempt() -> bool:
        try:
            shutil.rmtree(path)
            return True
        except OSError:
            return False

    if attempt():
        return True
    user = os.environ.get("USERNAME", "")
    for command in (
        ["takeown", "/f", str(path), "/r", "/d", "Y"],
        ["icacls", str(path), "/grant", f"{user}:(OI)(CI)F", "/t", "/c"],
    ):
        subprocess.run(command, capture_output=True)
    return attempt()


def folder_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(Path.home() / "Desktop" / "UnityGuard"),
                        help="final folder (default: Desktop\\UnityGuard)")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))
    from unityguard import __version__

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("需要先安装：python -m pip install pyinstaller", file=sys.stderr)
        return 2

    parts = [int(piece) for piece in __version__.split(".")] + [0, 0, 0, 0]
    filevers = tuple(parts[:4])

    out = Path(args.out).expanduser()
    if out.exists() and _is_running(out):
        print(f"请先关闭正在运行的 UnityGuard（{out} 里的程序还在运行），再重新构建。",
              file=sys.stderr)
        return 2
    staged = Path(tempfile.mkdtemp(prefix="ug_dist_"))
    work = Path(tempfile.mkdtemp(prefix="ug_work_"))
    try:
        gui_dir = build_one("UnityGuard", "unityguard_gui.py", windowed=True,
                            version=__version__, filevers=filevers,
                            description="Unity 素材体检与安全整理（图形界面）",
                            contents="runtime", work=work, dist=staged)
        cli_dir = build_one("UnityGuard-CLI", "unity_asset_audit.py", windowed=False,
                            version=__version__, filevers=filevers,
                            description="Unity 素材体检与安全整理（命令行）",
                            contents="cli_runtime", work=work, dist=staged)
        smoke_test(cli_dir / "UnityGuard-CLI.exe", "UnityGuard")

        # Assemble the final folder: GUI at the top, CLI in a subfolder.
        if out.exists() and not _force_remove(out):
            # Never leave the user with a half-old folder: build next to it.
            out = out.with_name(out.name + "-new")
            if out.exists():
                _force_remove(out)
        out.mkdir(parents=True)
        for item in gui_dir.iterdir():
            if item.is_dir():
                shutil.copytree(item, out / item.name)
            else:
                shutil.copy2(item, out / item.name)
        shutil.copytree(cli_dir, out / "cli")

        guide = ROOT / "docs" / "使用说明.txt"
        if guide.is_file():
            text = guide.read_text(encoding="utf-8-sig")
            text = text.replace("UnityGuard 0.6.0", f"UnityGuard {__version__}")
            (out / "使用说明.txt").write_text(text, encoding="utf-8-sig")

        smoke_test(out / "cli" / "UnityGuard-CLI.exe", "UnityGuard")

        print("\n完成：")
        for item in sorted(out.iterdir()):
            if item.is_dir():
                print(f"  dir  {item.name:20} {folder_size(item) / 1024 / 1024:6.1f} MB")
            else:
                print(f"  file {item.name:20} {item.stat().st_size / 1024 / 1024:6.2f} MB")
        print(f"  合计 {folder_size(out) / 1024 / 1024:.1f} MB   目录：{out}")
        return 0
    finally:
        shutil.rmtree(staged, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
