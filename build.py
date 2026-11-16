"""Build the Windows app: dist/ElevenIndex.exe, one file with Python, pywebview and the program's files inside, and
dist/ElevenIndex-FM27-views-and-filters.zip, FM's folders to unzip into Documents/Sports Interactive/Football Manager 27.

    python build.py

Needs PyInstaller and pywebview (python -m pip install pyinstaller pywebview). The .exe keeps its data (settings,
saves, pages) in Documents\\ElevenIndex, so it can be moved or replaced without losing anything.
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
FM_FOLDERS = ["views", "player search filters", "staff search filters"]  # in the zip, as FM names its folders
FILES = ["template.html", "charts.html", "theme.css", "logo.svg", "docs/ElevenIndex.ico", "docs/ca_costs_fm27.csv", "weights"]  # what score.py reads from the program folder (APP)


def main():
    try:
        import PyInstaller.__main__
    except ImportError:
        sys.exit("PyInstaller isn't installed: python -m pip install pyinstaller")
    os.chdir(HERE)
    args = [
        "app.py",
        "--name", "ElevenIndex",
        "--icon", "docs/ElevenIndex.ico",  # the XI mark, in Explorer, the taskbar and the window
        "--onefile",       # a single .exe to download
        "--windowed",      # no console window behind the app
        "--noconfirm",     # replace an earlier build
        "--clean",
        # --add-data's second half is the folder to put it in: a file's own folder ("." or docs), a folder's own name
        *[arg for f in FILES for arg in ("--add-data", f"{f}{os.pathsep}{f if (HERE / f).is_dir() else Path(f).parent.as_posix()}")],
    ]
    PyInstaller.__main__.run(args)
    exe = HERE / "dist" / "ElevenIndex.exe"
    print(f"\nBuilt {exe} ({exe.stat().st_size / 1e6:.0f} MB)" if exe.exists() else "\nThe build failed: see above.")


def fm_zip():
    """dist/ElevenIndex-FM27-views-and-filters.zip: the views and search filters in FM's own folders, so unzipping it
    into Documents/Sports Interactive/Football Manager 27 puts every file where FM looks for it."""
    import zipfile
    out = HERE / "dist" / "ElevenIndex-FM27-views-and-filters.zip"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for folder in FM_FOLDERS:
            for f in sorted((HERE / folder).glob("*.fmf")):
                z.write(f, f"{folder}/{f.name}")
    print(f"Built {out} ({sum(1 for _ in zipfile.ZipFile(out).namelist())} files)")


if __name__ == "__main__":
    fm_zip()
    main()
