"""Build the Windows app: dist/FMAnalyst.exe, one file with Python, pywebview and the program's files inside.

    python build.py

Needs PyInstaller and pywebview (python -m pip install pyinstaller pywebview). The .exe keeps its data (settings,
saves, pages) in Documents\\FMAnalyst, so it can be moved or replaced without losing anything.
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
FILES = ["template.html", "charts.html", "theme.css", "logo.svg", "docs/FMAnalyst.ico", "weights"]  # what score.py reads from the program folder (APP)


def main():
    try:
        import PyInstaller.__main__
    except ImportError:
        sys.exit("PyInstaller isn't installed: python -m pip install pyinstaller")
    os.chdir(HERE)
    args = [
        "app.py",
        "--name", "FMAnalyst",
        "--icon", "docs/FMAnalyst.ico",  # the shield, in Explorer, the taskbar and the window
        "--onefile",       # a single .exe to download
        "--windowed",      # no console window behind the app
        "--noconfirm",     # replace an earlier build
        "--clean",
        # --add-data's second half is the folder to put it in: a file's own folder ("." or docs), a folder's own name
        *[arg for f in FILES for arg in ("--add-data", f"{f}{os.pathsep}{f if (HERE / f).is_dir() else Path(f).parent.as_posix()}")],
    ]
    PyInstaller.__main__.run(args)
    exe = HERE / "dist" / "FMAnalyst.exe"
    print(f"\nBuilt {exe} ({exe.stat().st_size / 1e6:.0f} MB)" if exe.exists() else "\nThe build failed: see above.")


if __name__ == "__main__":
    main()
