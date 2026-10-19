#!/usr/bin/env python3
"""Copy this mod into a game install for testing, without packaging.

    py deploy.py "K:\\path\\to\\gen1recomp-win64"

Writes into <game>/mods/easy_language_packs/.  Files from the repository
replace their installed copies; anything else already there (your packs,
packs/_example edits aside) is left alone.  Point it at the game folder of a
portable install, or at the save folder (%APPDATA%\\LOVE\\pokemon-love2d)
for a normal one.
"""
import os
import shutil
import subprocess
import sys

MOD_ID = "easy_language_packs"
ROOT = os.path.dirname(os.path.abspath(__file__))
# repository files that are tooling, not part of the mod
NOT_SHIPPED = (".github/", "tests/", "deploy.py", "CLAUDE.md", ".gitattributes", ".gitignore",
               "workflows-to-install/")


def mod_files():
    """What the release zip would contain: tracked files, minus tooling.
    Falls back to walking the folder when git isn't available."""
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        out = []
        for base, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
            rel = os.path.relpath(base, ROOT)
            if rel.startswith("packs") and rel not in ("packs", os.path.join("packs", "_example")):
                dirs[:] = []
                continue
            for name in files:
                out.append(os.path.normpath(os.path.join(rel, name)).replace(os.sep, "/"))
    return sorted(f for f in out if not f.startswith(NOT_SHIPPED))


def main(argv):
    if len(argv) != 1:
        print(__doc__.strip())
        return 2
    game = argv[0]
    if not os.path.isdir(game):
        print(f"deploy: {game} is not a folder")
        return 1
    dest = os.path.join(game, "mods", MOD_ID)
    files = mod_files()
    for rel in files:
        src = os.path.join(ROOT, rel)
        dst = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    print(f"deployed {len(files)} files to {dest}")
    old = os.path.join(game, "mods", "language_pack")
    if os.path.isdir(old):
        print(f"note: the earlier build is still installed at {old}; the game won't "
              "run both (they conflict). Move any packs you want from its packs\\ "
              "folder into the new one, then remove it.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
