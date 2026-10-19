#!/usr/bin/env python3
"""Run every test.  ROM-free: the Lua tests boot the mod through the engine's
real mod loader with synthetic records, and the Python tests build their own
miniature "imported games" folder.

    py tests/run.py --engine ../gen1recomp

Needs LuaJIT on PATH (or set LUAJIT=path\\to\\luajit.exe) and a checkout of
https://github.com/bryanthaboi/gen1recomp for the Lua half.  --python-only
skips that half.
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_lua(engine):
    luajit = os.environ.get("LUAJIT") or shutil.which("luajit")
    if not luajit:
        print("tests: LuaJIT not found (install it or set LUAJIT); skipping the Lua tests")
        return False
    failed = False
    for test in sorted(glob.glob(os.path.join(ROOT, "tests", "lua", "*_test.lua"))):
        proc = subprocess.run([luajit, test, ROOT], cwd=engine, capture_output=True, text=True)
        out = (proc.stdout + proc.stderr).strip().splitlines()
        # the engine logs every mod load; keep failures and the summary line
        shown = [l for l in out if l.startswith(("FAIL", "  got", "  want")) or ": " in l and "passed" in l
                 or l.startswith(("luajit:", "lua:", "stack traceback"))]
        crashed = proc.returncode != 0 and not any("passed" in l for l in shown)
        # a crash before the summary: show the tail, which holds the error
        print("\n".join(out[-20:]) if crashed or not shown else "\n".join(shown))
        if proc.returncode != 0:
            failed = True
    return not failed


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--engine", help="path to a gen1recomp checkout")
    ap.add_argument("--python-only", action="store_true")
    args = ap.parse_args()

    ok = True
    if not args.python_only:
        engine = args.engine
        if not engine or not os.path.isfile(os.path.join(engine, "src", "mods", "Loader.lua")):
            print("tests: pass --engine <gen1recomp checkout> (or --python-only)")
            return 2
        ok = run_lua(os.path.abspath(engine)) and ok

    suite = unittest.defaultTestLoader.discover(os.path.join(ROOT, "tests"), pattern="test_*.py",
                                                top_level_dir=os.path.join(ROOT, "tests"))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    ok = result.wasSuccessful() and ok
    print("\nALL TESTS PASSED" if ok else "\nSOME TESTS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
