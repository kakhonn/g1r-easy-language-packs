"""Tests for tools/lpk.py.  Self-contained: a miniature "imported games" folder
is written from synthetic records, so neither the engine nor any ROM is needed.

    python3 -m unittest discover -s tests -p "test_*.py"
"""
import contextlib
import csv
import glob
import http.server
import threading
import zipfile
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import lpk  # noqa: E402
import lpk_layout  # noqa: E402


# ---- writing a fake imported cache --------------------------------------

def lua(v, indent=0):
    pad = "  " * indent
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return '"' + (v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
                      .replace("\f", "\\f").replace("\v", "\\v")) + '"'
    if isinstance(v, list):
        return "{\n" + "".join(f"{pad}  {lua(x, indent + 1)},\n" for x in v) + pad + "}"
    items = []
    for k, x in v.items():
        key = f"[{k}]" if isinstance(k, int) else (k if k.isidentifier() else f"[{lua(k)}]")
        items.append(f"{pad}  {key} = {lua(x, indent + 1)},\n")
    return "{\n" + "".join(items) + pad + "}"


def write(path, value, chunked=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        if chunked:  # the Gen 3 extractor's T["label"] = {...} form
            fh.write("local T = {}\ndo (function(T)\n")
            for k, x in value.items():
                fh.write(f'T[{lua(k)}] = {lua(x)}\n')
            fh.write("end)(T) end\nreturn T\n")
        else:
            fh.write("-- generated\nreturn " + lua(value) + "\n")


RB_DEX = "So rare that it\nis still said to\nbe a mirage."
Y_DEX = "When viewed\nthrough a micro-\nscope."
SHARED_G2 = "Same in both\ngames!{DONE}"


def gen1(base, yellow=False):
    g = os.path.join(base, "data", "generated")
    text = {"_SignText": "A SIGN", "_MewDexEntry": Y_DEX if yellow else RB_DEX}
    if yellow:
        text["_PikachuFollowText"] = "PIKACHU is happy!"
    write(os.path.join(g, "text.lua"), text)
    write(os.path.join(g, "pokemon.lua"), {"MEW": {"id": "MEW", "name": "MEW", "dex": 151, "dexEntry": {
        "kind": "NEW SPECIE", "text": "_MewDexEntry", "heightFt": 1, "heightIn": 4, "weight": 90}}})
    write(os.path.join(g, "moves.lua"), {"TACKLE": {"id": "TACKLE", "name": "TACKLE"}})
    write(os.path.join(g, "items.lua"), {"POTION": {"id": "POTION", "name": "POTION"}})
    write(os.path.join(g, "trainers.lua"), {"OPP_YOUNGSTER": {"id": "OPP_YOUNGSTER", "name": "YOUNGSTER"}})


def gen2(base, crystal=False):
    g = os.path.join(base, "data", "generated")
    text = {"generation": 2, ("12:4000" if crystal else "03:4d44"): SHARED_G2}
    if crystal:
        text["12:5000"] = "Crystal only!{DONE}"
    write(os.path.join(g, "text.lua"), text)
    write(os.path.join(g, "rom_text.lua"), {"AlreadyAsleepText": "{TARGET}'s\nalready asleep!{PROMPT}"})
    write(os.path.join(g, "pokemon.lua"), {"CHIKORITA": {"id": "CHIKORITA", "name": "CHIKORITA", "dex": 152}})
    write(os.path.join(g, "pokedex.lua"), {"entries": {"CHIKORITA": {
        "kind": "LEAF", "text": "Crystal words." if crystal else "Gold words.", "text2": "Page two."}}})
    write(os.path.join(g, "moves.lua"), {"RAZOR_LEAF": {"id": "RAZOR_LEAF", "name": "RAZOR LEAF"}})
    write(os.path.join(g, "items.lua"), {"BERRY": {"id": "BERRY", "name": "BERRY"}})
    write(os.path.join(g, "trainers.lua"), {"classes": {"BEAUTY": {"id": "BEAUTY", "name": "BEAUTY"}}})


def gen3(base, emerald=False):
    g = os.path.join(base, "data", "generated", "gba")
    ir = [{"t": "text", "s": "Hello, "}, {"t": "player"}, {"t": "nl"},
          {"t": "text", "s": "welcome!"}, {"t": "para"}, {"t": "strvar", "n": 1}, {"t": "eos"}]
    text = {"Town_Text_Hello": ir}
    if emerald:
        text["Frontier_Text_Hi"] = [{"t": "text", "s": "Battle Frontier!"}, {"t": "eos"}]
    write(os.path.join(g, "scripts", "text.lua"), text, chunked=True)
    write(os.path.join(g, "pokemon", "names.lua"), {0: "??????????", 1: "BULBASAUR"})
    write(os.path.join(g, "pokemon", "pokedex", "entries.lua"), {1: {
        "category": "SEED", "description": "Emerald seed." if emerald else "A seed.", "description2": "More."}})


def make_install(root):
    os.makedirs(os.path.join(root, "src", "ui"))
    with open(os.path.join(root, "src", "ui", "Menu.lua"), "w", encoding="utf-8") as fh:
        fh.write('local S = Strings("SAVE")\nlocal T = Strings("%s grew\\nto level %d!", a, b)\n')
    os.makedirs(os.path.join(root, "src", "import"))
    with open(os.path.join(root, "src", "import", "LauncherView.lua"), "w", encoding="utf-8") as fh:
        fh.write('local L = Strings("Launcher only")\n')
    gen1(os.path.join(root, "red"))
    gen1(os.path.join(root, "blue"))
    gen1(os.path.join(root, "yellow"), yellow=True)
    gen2(os.path.join(root, "gold"))
    gen2(os.path.join(root, "crystal"), crystal=True)
    gen3(os.path.join(root, "ruby"))
    gen3(os.path.join(root, "emerald"), emerald=True)


def rows(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def pack_rows(pack):
    """(relative path, row) for every CSV row the mod would load from a pack."""
    for root, dirs, names in os.walk(pack):
        dirs[:] = sorted(d for d in dirs if not d.startswith(("_", ".")))
        for name in sorted(names):
            if name.endswith(".csv"):
                path = os.path.join(root, name)
                for r in rows(path):
                    yield os.path.relpath(path, pack).replace(os.sep, "/"), r


def quiet(fn, *a):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*a)


# ---- tests --------------------------------------------------------------

class Basics(unittest.TestCase):
    def test_fnv1a_vectors(self):
        self.assertEqual(lpk.fnv1a(""), "811c9dc5")
        self.assertEqual(lpk.fnv1a("a"), "e40c292c")
        self.assertEqual(lpk.fnv1a("foobar"), "bf9cf968")

    def test_gen3_source_matches_the_mod(self):
        # tests/lua/gen3_test.lua holds the same segments and expects the same
        # strings and hashes from lib/g3text.lua: the dialogue keys depend on it
        ir = [{"t": "text", "s": "Hi "}, {"t": "player"}, {"t": "text", "s": "! It's "}, {"t": "rival"},
              {"t": "nl"}, {"t": "strvar", "n": 2}, {"t": "scroll"}, {"t": "ph", "code": 7}, {"t": "para"},
              {"t": "bph", "code": 0x10}, {"t": "dynamic", "n": 0}, {"t": "ext", "cmd": 8, "args": [4]},
              {"t": "tag", "tag": "{PKMN}"}, {"t": "ph", "code": 0x20}, {"t": "ph", "code": 8},
              {"t": "eos"}, {"t": "text", "s": "after the end"}]
        frlg = ("Hi {PLAYER}! It's {RIVAL}\n{STR_VAR_2}\v{VERSION}\f"
                "{B_DEF_NAME_WITH_PREFIX}{DYNAMIC_0}{EXT_08_4}{PKMN}{PH_20}{MAGMA}")
        self.assertEqual(lpk.ir_source(ir, "frlg"), frlg)
        self.assertEqual(lpk.fnv1a(frlg), "4b914961")
        self.assertEqual(lpk.fnv1a(lpk.ir_source(ir, "rs")), "5956c3d8")
        self.assertEqual(lpk.fnv1a(lpk.ir_source(ir, "rse")), "a22dbba0")
        self.assertEqual(lpk.ir_source([{"t": "ph", "code": 2, "name": ""}], "frlg"), "{STR_VAR_1}")

    def test_csv_escapes_round_trip(self):
        s = "line\nscroll\vpage\fback\\slash"
        self.assertEqual(lpk.csv_unescape(lpk.csv_escape(s)), s)
        self.assertEqual(lpk.csv_unescape("\\p stays"), "\\p stays")

    def test_lua_reader_both_shapes(self):
        self.assertEqual(lpk.LuaReader.load('return { a = "x\\n", [2] = { 1, 2 } }'),
                         {"a": "x\n", 2: {1: 1, 2: 2}})
        chunked = 'local T = {}\ndo (function(T)\nT["k"] = { s = "v" }\nend)(T) end\nreturn T\n'
        self.assertEqual(lpk.LuaReader.load_assignments(chunked), {"k": {"s": "v"}})

    def test_gen3_ir_source(self):
        ir = {1: {"t": "text", "s": "Hi "}, 2: {"t": "player"}, 3: {"t": "scroll"},
              4: {"t": "bph", "code": 0x0F}, 5: {"t": "para"}, 6: {"t": "eos"}, 7: {"t": "text", "s": "x"}}
        self.assertEqual(lpk.ir_source(ir, "frlg"), "Hi {PLAYER}\v{B_ATK_NAME_WITH_PREFIX}\f")

    def test_option_label(self):
        self.assertEqual(lpk.option_label("Español"), "ESPANOL")
        self.assertEqual(lpk.option_label("Português (Brasil)"), "PORTUGUES BRASIL")
        self.assertEqual(lpk.option_label("Español de México y Centroamérica"), "ESPANOL DE")
        self.assertEqual(lpk.option_label("日本語"), "MY LANGUAGE")

    def test_pack_files_load_in_whole_path_order(self):
        # must match lib/pack.lua (packs_test.lua checks the same pack)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        pack = os.path.join(tmp, "o")
        os.makedirs(os.path.join(pack, "dex"))
        with open(os.path.join(pack, "dex.csv"), "w", encoding="utf-8") as fh:
            fh.write("kind,key,text\nitems,POTION,FROM-ROOT\n")
        with open(os.path.join(pack, "dex", "a.csv"), "w", encoding="utf-8") as fh:
            fh.write("kind,key,text\nitems,POTION,FROM-FOLDER\n")
        entries, _, problems = lpk.read_pack(tmp, "o", set())
        self.assertEqual(problems, [])
        self.assertEqual([e["text"] for e in entries], ["FROM-ROOT", "FROM-FOLDER"])


class SplitCase(unittest.TestCase):
    """A synthetic install and a pack folder to split into."""
    layout = None            # None: split's default

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.game = os.path.join(self.tmp, "game")
        make_install(self.game)
        self.out = os.path.join(self.tmp, "packs", "zz")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def split(self, *extra):
        layout = ["--layout", self.layout] if self.layout and "--layout" not in extra else []
        return quiet(lpk.main, ["split", "--game", self.game, "-o", self.out, *layout, *extra])

    def path(self, rel):
        return os.path.join(self.out, *rel.split("/"))

    def sheet(self, name):
        return rows(self.path(name + ".csv"))

    def save(self, path, data, fieldnames=None):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames or lpk.SPLIT_HEADER)
            w.writeheader()
            w.writerows(data)

    def find(self, match, kind=None):
        """The pack file (relative path) holding the row whose english, key or
        first notes label is `match` (and whose kind is `kind`, if given)."""
        hits = [rel for rel, r in pack_rows(self.out)
                if match in (r["english"], r["key"], r["notes"].split(",")[0].strip())
                and kind in (None, r["kind"])]
        self.assertEqual(len(hits), 1, f"{match!r} in {hits}")
        return hits[0]


class Split(SplitCase):
    """What split extracts, on the flat layout (one sheet per game family)."""
    layout = "flat"

    def test_writes_every_sheet_and_pack_json(self):
        self.assertEqual(self.split("--name", "Español", "--author", "Tester"), 0)
        for name, _ in lpk.SPLIT_FILES:
            self.assertTrue(os.path.isfile(os.path.join(self.out, name + ".csv")), name)
        meta = json.load(open(os.path.join(self.out, "pack.json"), encoding="utf-8"))
        self.assertEqual((meta["name"], meta["label"], meta["author"]), ("Español", "ESPANOL", "Tester"))
        self.assertEqual((meta["font"], meta["extends"]), ("", ""))

    def test_engine_text_skips_the_launcher(self):
        self.split()
        keys = {r["key"] for r in self.sheet("01_red_blue") if r["kind"] == "strings"}
        self.assertIn("SAVE", keys)
        self.assertNotIn("Launcher only", keys)

    def test_yellow_sheet_holds_only_what_yellow_adds(self):
        self.split()
        y = self.sheet("02_yellow_exclusive")
        self.assertEqual([r["english"] for r in y if r["kind"] == "dialogue"], ["PIKACHU is happy!"])
        rb = [r["english"] for r in self.sheet("01_red_blue") if r["kind"] == "dialogue"]
        self.assertEqual(rb, ["A SIGN"], "Pokedex entries are not repeated as dialogue")

    def test_gen1_dex_shows_real_text_per_version(self):
        self.split()
        dex = {r["versions"]: r for r in self.sheet("11_dex_gen1") if r["kind"] == "dex_text"}
        self.assertEqual(set(dex), {"red blue", "yellow"})
        self.assertEqual(dex["red blue"]["key"], "MEW#" + lpk.fnv1a(RB_DEX))
        self.assertEqual(lpk.csv_unescape(dex["yellow"]["english"]), Y_DEX)

    def test_crystal_sheet_dedupes_by_english(self):
        self.split()
        crystal = [r["english"] for r in self.sheet("04_crystal_exclusive") if r["kind"] == "dialogue"]
        self.assertEqual(crystal, ["Crystal only!{DONE}"])
        gs = {r["key"] for r in self.sheet("03_gold_silver") if r["kind"] == "dialogue"}
        self.assertIn("#" + lpk.fnv1a(SHARED_G2), gs)

    def test_gen3_from_chunked_text(self):
        self.split()
        rs = [lpk.csv_unescape(r["english"]) for r in self.sheet("06_ruby_sapphire") if r["kind"] == "dialogue"]
        self.assertEqual(rs, ["Hello, {PLAYER}\nwelcome!\f{STR_VAR_1}"])
        em = [r["english"] for r in self.sheet("07_emerald_exclusive") if r["kind"] == "dialogue"]
        self.assertEqual(em, ["Battle Frontier!"])

    def test_rerun_keeps_translations_and_pack_json(self):
        self.split("--name", "First")
        data = self.sheet("11_dex_gen1")
        for r in data:
            if r["kind"] == "dex_kind":
                r["text"] = "SPESIES BARU"
        data.append({"kind": "moves", "key": "GONE", "game": "gen1", "english": "X", "text": "ORPHAN",
                     "versions": "", "notes": ""})
        self.save(self.path("11_dex_gen1.csv"), data)
        self.split("--name", "Second")
        kinds = {r["kind"]: r["text"] for r in self.sheet("11_dex_gen1") if r["kind"] == "dex_kind"}
        self.assertEqual(kinds["dex_kind"], "SPESIES BARU")
        orphans = rows(os.path.join(self.out, "_orphaned", "11_dex_gen1.csv"))
        self.assertEqual([o["text"] for o in orphans], ["ORPHAN"])
        meta = json.load(open(os.path.join(self.out, "pack.json"), encoding="utf-8"))
        self.assertEqual(meta["name"], "First", "an existing pack.json is kept")

    def test_plain_key_carries_into_version_rows(self):
        # a pack from before version-pinned keys: one plain-key row, no manifest yet
        os.makedirs(self.out)
        with open(os.path.join(self.out, "11_dex_gen1.csv"), "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(lpk.SPLIT_HEADER)
            w.writerow(["dex_text", "MEW", "gen1", "_MewDexEntry", "OLD-STYLE", "", ""])
        self.split()
        texts = [r["text"] for r in self.sheet("11_dex_gen1") if r["kind"] == "dex_text"]
        self.assertEqual(texts, ["OLD-STYLE", "OLD-STYLE"])

    def test_check_and_release(self):
        self.split()
        data = self.sheet("01_red_blue")
        for r in data:
            if r["key"] == "%s grew\\nto level %d!":
                r["text"] = "¡%s subió\\nal nivel %d!"
            if r["english"] == "A SIGN":
                r["text"] = "CARTEL"
        self.save(self.path("01_red_blue.csv"), data)
        self.assertEqual(quiet(lpk.main, ["check", self.out]), 0)
        dist = os.path.join(self.tmp, "dist")
        self.assertEqual(quiet(lpk.main, ["release", self.out, "-o", dist, "--folder"]), 0)
        released = rows(os.path.join(dist, "01_red_blue.csv"))
        self.assertEqual(len(released), 2, "untranslated rows are dropped")
        self.assertNotIn("english", released[0], "the ROM-text column is stripped")

    def test_check_catches_placeholder_mistakes(self):
        self.split()
        data = self.sheet("03_gold_silver")
        for r in data:
            if r["key"] == "AlreadyAsleepText" or "already asleep" in r["english"]:
                r["text"] = "{TARGET} tidur"  # {PROMPT} dropped
        self.save(self.path("03_gold_silver.csv"), data)
        self.assertEqual(quiet(lpk.main, ["check", self.out]), 1)


class SplitLayout(SplitCase):
    """The manifest in _lpk/ and what a re-run keeps: translations wherever they
    are, rows the translator moved or deleted, files split no longer writes."""

    def manifest(self):
        with open(os.path.join(self.out, "_lpk", "split.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def edit(self, rel, fn, fieldnames=None):
        data = rows(self.path(rel))
        self.save(self.path(rel), fn(data), fieldnames)

    def translate(self, match, text):
        def fn(data):
            for r in data:
                if match in (r["english"], r["key"]):
                    r["text"] = text
            return data
        self.edit(self.find(match), fn)

    def move(self, match, dest, fieldnames=None):
        """Move one row from wherever it is to `dest` (relative), appending it there."""
        src = self.find(match)
        data = rows(self.path(src))
        row = next(r for r in data if match in (r["english"], r["key"]))
        self.save(self.path(src), [r for r in data if r is not row])
        old = rows(self.path(dest)) if os.path.isfile(self.path(dest)) else []
        self.save(self.path(dest), old + [{k: row.get(k, "") for k in (fieldnames or lpk.SPLIT_HEADER)}],
                  fieldnames)
        return src

    def test_organized_is_the_default(self):
        self.split()
        self.assertEqual(self.manifest()["layout"], "organized")
        self.assertFalse(os.path.exists(self.path("01_red_blue.csv")))
        self.assertEqual(self.find("TACKLE"), "G1/Names/moves.csv")

    def test_upgrading_a_flat_pack_keeps_its_translations(self):
        self.split("--layout", "flat")
        shutil.rmtree(os.path.join(self.out, "_lpk"))          # as an older lpk.py left it
        self.translate("A SIGN", "CARTEL")
        self.split()
        rel = self.find("A SIGN")
        self.assertTrue(rel.startswith("G1/"))
        self.assertEqual([r["text"] for r in rows(self.path(rel)) if r["english"] == "A SIGN"], ["CARTEL"])
        self.assertFalse(os.path.exists(self.path("01_red_blue.csv")), "the old sheet left the pack")
        kept = glob.glob(os.path.join(self.out, "_orphaned", "_retired", "*", "01_red_blue.csv"))
        self.assertEqual(len(kept), 1, "a retired sheet with translations is kept in _orphaned/_retired")
        self.assertFalse(os.path.exists(self.path("02_yellow_exclusive.csv")))
        self.assertEqual(glob.glob(os.path.join(self.out, "_orphaned", "_retired", "*", "02_*")), [],
                         "an untranslated retired sheet is simply removed")
        self.assertEqual(quiet(lpk.main, ["check", self.out]), 0, "no key is defined twice")

    def test_moved_rows_stay_where_the_translator_put_them(self):
        self.split()
        self.translate("A SIGN", "CARTEL")
        self.move("A SIGN", "G1/mine.csv")                       # into a file of their own
        self.move("TACKLE", "G1/Names/species.csv")              # into another split file
        self.split()
        self.split()                                             # and it stays that way
        self.assertEqual(self.find("A SIGN"), "G1/mine.csv")
        self.assertEqual([(r["english"], r["text"]) for r in rows(self.path("G1/mine.csv"))],
                         [("A SIGN", "CARTEL")])
        self.assertEqual(self.find("TACKLE"), "G1/Names/species.csv")
        self.assertEqual(sorted(self.manifest()["pins"].values()), ["G1/Names/species.csv", "G1/mine.csv"])

    def test_rows_in_your_own_file_get_fresh_english_and_nothing_else_changes(self):
        self.split()
        self.translate("TACKLE", "TERJANG")
        cols = lpk.SPLIT_HEADER + ["comment"]
        self.save(self.path("G1/mine.csv"), [{"kind": "moves", "key": "MY_OWN", "game": "gen1", "english": "",
                                              "text": "SENDIRI", "comment": "mine"}], cols)
        self.move("TACKLE", "G1/mine.csv", cols)
        for v in ("red", "blue", "yellow"):                      # a game update renames the move
            write(os.path.join(self.game, v, "data", "generated", "moves.lua"),
                  {"TACKLE": {"id": "TACKLE", "name": "BODY SLAM"}})
        self.split()
        got = rows(self.path("G1/mine.csv"))
        self.assertEqual([r["key"] for r in got], ["MY_OWN", "TACKLE"], "rows and order are kept")
        self.assertEqual(got[0]["comment"], "mine")
        self.assertEqual((got[1]["english"], got[1]["text"]), ("BODY SLAM", "TERJANG"))

    def test_deleted_rows_stay_deleted_until_restored(self):
        self.split()
        rel = self.find("A SIGN")
        self.edit(rel, lambda data: [r for r in data if r["english"] != "A SIGN"])
        self.split()
        self.split()
        self.assertEqual([x for x, r in pack_rows(self.out) if r["english"] == "A SIGN"], [])
        self.assertEqual(list(self.manifest()["deleted"].values()), [[rel]])
        self.split("--restore", rel.replace("/", "\\"))
        self.assertEqual(self.find("A SIGN"), rel)
        self.assertEqual(self.manifest()["deleted"], {})

    def test_restore_all(self):
        self.split()
        os.remove(self.path("G1/Names/species.csv"))           # deleting a whole file
        self.split()
        self.assertFalse(os.path.exists(self.path("G1/Names/species.csv")))
        self.split("--restore", "all")
        self.assertTrue(os.path.isfile(self.path("G1/Names/species.csv")))

    def test_deleting_one_copy_of_a_key_split_writes_twice(self):
        # the flat layout writes a line FRLG and Ruby share in both sheets
        gen3(os.path.join(self.game, "firered"))
        self.split("--layout", "flat")
        frlg, rs = "05_firered_leafgreen", "06_ruby_sapphire"
        line = [r for r in self.sheet(frlg) if r["kind"] == "dialogue"]
        self.assertEqual(len(line), 1)
        self.assertIn(line[0]["key"], [r["key"] for r in self.sheet(rs)])
        self.edit(frlg + ".csv", lambda data: [r for r in data if r["kind"] != "dialogue"])
        self.split("--layout", "flat")
        self.split("--layout", "flat")
        self.assertNotIn(line[0]["key"], [r["key"] for r in self.sheet(frlg)], "the deleted copy stays deleted")
        self.assertEqual([r["key"] for r in self.sheet(rs)].count(line[0]["key"]), 1, "the other copy is untouched")
        self.assertEqual(list(self.manifest()["deleted"].values()), [["05_firered_leafgreen.csv"]])

    def test_a_layout_change_moves_rows_but_not_the_translators(self):
        self.split()
        self.translate("TACKLE", "TERJANG")
        self.move("A SIGN", "G1/mine.csv")
        with mock.patch.dict(lpk_layout.FOLDERS, {"names": "Nimi"}):
            self.split()
            self.assertEqual(self.find("TACKLE"), "G1/Nimi/moves.csv")
            self.assertEqual([r["text"] for r in rows(self.path("G1/Nimi/moves.csv"))], ["TERJANG"],
                             "the translation followed the row")
        self.assertEqual(self.find("A SIGN"), "G1/mine.csv", "a translator's move beats the layout")
        self.assertEqual(len(glob.glob(os.path.join(self.out, "_orphaned", "_retired", "*", "G1", "Names",
                                                    "moves.csv"))), 1, "the translated old file is kept")
        self.assertFalse(os.path.exists(self.path("G1/Names/species.csv")))
        self.assertEqual(glob.glob(os.path.join(self.out, "_orphaned", "_retired", "*", "G1", "Names",
                                                "species.csv")), [], "an untranslated old file is removed")

    def test_release_leaves_out_working_folders(self):
        self.split()
        self.edit("G1/Names/moves.csv", lambda data: data + [
            {"kind": "moves", "key": "GONE", "game": "gen1", "english": "X", "text": "ORPHAN",
             "versions": "", "notes": ""}])
        self.split()
        self.assertTrue(os.path.isdir(os.path.join(self.out, "_orphaned")))
        dist = os.path.join(self.tmp, "dist")
        self.assertEqual(quiet(lpk.main, ["release", self.out, "-o", dist, "--folder"]), 0)
        self.assertFalse(os.path.exists(os.path.join(dist, "_orphaned")))
        self.assertFalse(os.path.exists(os.path.join(dist, "_lpk")))
        self.assertTrue(os.path.isfile(os.path.join(dist, "pack.json")))


class Gen1Layout(SplitCase):
    """Gen 1 lines go by their script label: map codes, final-game places,
    families, Yellow's own folder."""

    def setUp(self):
        super().setUp()
        for v in ("red", "blue", "yellow"):
            text = {"_MewDexEntry": Y_DEX if v == "yellow" else RB_DEX,
                    "_PalletTownText1": "PALLET!", "_RockTunnel1FText1": "ROCKY", "_OakSpeechText1": "WELCOME",
                    "_CinnabarQuizQuestionsText1": "QUIZ", "_SignText": "A SIGN"}
            if v == "yellow":
                text["_CeruleanMelaniesHouseText1"] = "MELANIE"
            write(os.path.join(self.game, v, "data", "generated", "text.lua"), text)
        self.split()

    def test_places(self):
        self.assertEqual(self.find("PALLET!"), "G1/Towns/T01_pallet.csv")
        self.assertEqual(self.find("ROCKY"), "G1/Dungeons/D15_rock_tunnel.csv", "where it is in the final game")
        self.assertEqual(self.find("QUIZ"), "G1/Towns/T09_cinnabar.csv", "a line with no map name, known place")
        self.assertEqual(self.find("WELCOME"), "G1/intro.csv")
        self.assertEqual(self.find("MELANIE"), "G1/Yellow/Towns/T04_cerulean.csv")

    def test_everything_else(self):
        self.assertEqual(self.find("SAVE"), "Engine/menus.csv", "engine text applies to every game")
        self.assertEqual(self.find("TACKLE"), "G1/Names/moves.csv")
        self.assertEqual(self.find("MEW", kind="species"), "G1/Names/species.csv")
        self.assertEqual(self.find("MEW", kind="dex_kind"), "G1/Pokedex/categories.csv")
        self.assertEqual(self.find("MEW#" + lpk.fnv1a(RB_DEX)), "G1/Pokedex/entries.csv")
        self.assertEqual(self.find("MEW#" + lpk.fnv1a(Y_DEX)), "G1/Yellow/Pokedex/entries.csv")
        self.assertEqual(quiet(lpk.main, ["check", self.out]), 0)


class Gen2Layout(SplitCase):
    """Gen 2 lines are traced: map events -> scripts -> text, and the map's
    Town Map landmark picks the file."""

    def setUp(self):
        super().setUp()
        for v, addr, script, mid, landmark in (
                ("gold", "03:4d44", "03:4d30", "NEW_BARK_TOWN", (1, "NEW BARK TOWN")),
                ("crystal", "12:5000", "12:4ff0", "TEAM_ROCKET_BASE_B1F", (35, "MAHOGANY TOWN"))):
            g = os.path.join(self.game, v, "data", "generated")
            write(os.path.join(g, "landmarks.lua"), {"landmarks": {"LM": {"index": landmark[0], "name": landmark[1]}}})
            write(os.path.join(g, "maps.lua"), {mid: {"landmark": landmark[0],
                                                      "bgEvents": [{"scriptKey": script}]}})
            write(os.path.join(g, "scripts.lua"), {script: [{"op": "writetext", "text": addr}]})
        self.split()

    def test_traced_lines_land_in_their_place(self):
        self.assertEqual(self.find("#" + lpk.fnv1a(SHARED_G2)), "G2/Towns/T20_new_bark.csv")
        self.assertEqual(self.find("Crystal only!{DONE}"), "G2/Crystal/Dungeons/D27_team_rocket_base.csv",
                         "a map-level override beats the landmark (Mahogany)")

    def test_unreached_lines_go_by_label_and_the_rest_by_kind(self):
        self.assertEqual(self.find("AlreadyAsleepText"), "G2/System/battle.csv")
        self.assertEqual(self.find("RAZOR_LEAF"), "G2/Names/moves.csv")
        self.assertEqual(self.find("CHIKORITA", kind="species"), "G2/Names/species.csv")
        self.assertEqual(self.find("CHIKORITA#" + lpk.fnv1a("Crystal words.")), "G2/Crystal/Pokedex/entries.csv")


class Gen3Layout(SplitCase):
    """Gen 3: labels or traced scripts, region-map sections, Emerald's Frontier,
    and text FRLG and Ruby share kept once in G3/Shared."""

    def setUp(self):
        super().setUp()
        hello = [{"t": "text", "s": "Welcome to "}, {"t": "player"}, {"t": "eos"}]
        menu = [{"t": "text", "s": "Shared menu text"}, {"t": "eos"}]
        frlg_only = [{"t": "text", "s": "Pallet line"}, {"t": "eos"}]
        self.game3("ruby", {"LittlerootTown_Text_Hello": hello, "gText_Menu": menu},
                   [("LittlerootTown", 0)])
        self.game3("emerald", {"Frontier_Text_Hi": [{"t": "text", "s": "Battle Frontier!"}, {"t": "eos"}]},
                   [("BattleFrontier_OutsideWest", 58)])
        self.game3("firered", {"g3:08100000": frlg_only, "g3:08100100": menu}, [("PalletTown", 88)],
                   events={"FR_PALLET_TOWN": {"bgEvents": [{"scriptKey": "g3:08200000"}]}},
                   scripts={"g3:08200000": [{"op": "message", "value": "g3:08100000"}]})
        self.split()

    def game3(self, v, text, maps, events=None, scripts=None):
        g = os.path.join(self.game, v, "data", "generated", "gba")
        write(os.path.join(g, "scripts", "text.lua"), text, chunked=True)
        census = {"groups": [{"group": 0, "maps": [{"id": mid, "num": i, "slot": f"0_{i}"}
                                                   for i, (mid, _) in enumerate(maps)]}]}
        os.makedirs(os.path.join(g, "map_tree"), exist_ok=True)
        with open(os.path.join(g, "map_tree", "census.json"), "w", encoding="utf-8") as fh:
            json.dump(census, fh)
        for i, (mid, sec) in enumerate(maps):
            d = os.path.join(g, "map_tree", "maps", f"0_{i}")
            os.makedirs(d)
            with open(os.path.join(d, "header.json"), "w", encoding="utf-8") as fh:
                json.dump({"id": mid, "regionMapSectionId": sec}, fh)
        if events:
            write(os.path.join(g, "scripts", "events.lua"), events)
            write(os.path.join(g, "scripts", "scripts.lua"), scripts)

    def test_places(self):
        self.assertEqual(self.find("Welcome to {PLAYER}"), "G3/RSE/Towns/T101_littleroot.csv")
        self.assertEqual(self.find("Battle Frontier!"), "G3/RSE/Emerald/Frontier/F00_battle_frontier.csv")
        self.assertEqual(self.find("Pallet line"), "G3/FRLG/Towns/T01_pallet.csv", "traced through the scripts")

    def test_text_both_families_have_is_kept_once_in_shared(self):
        self.assertEqual(self.find("Shared menu text"), "G3/Shared/System/menus.csv")
        row = next(r for rel, r in pack_rows(self.out) if r["english"] == "Shared menu text")
        self.assertEqual(row["versions"], "firered ruby")
        self.assertEqual(row["notes"], "gText_Menu; FRLG: g3:08100100")
        self.assertEqual(self.find("BULBASAUR", kind="species"), "G3/Shared/Names/species.csv")
        self.assertEqual(self.find("001", kind="dex_kind"), "G3/RSE/Pokedex/categories.csv",
                         "only Ruby/Emerald's Pokedex is imported here, so the categories are RSE's")
        self.assertEqual(quiet(lpk.main, ["check", self.out]), 0)


class PackMod(unittest.TestCase):
    """`release` packages a pack as its own mod, elp_<id>-<version>.zip."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.pack = os.path.join(self.tmp, "packs", "es")
        self.dist = os.path.join(self.tmp, "dist")
        self.meta = {"name": "Español", "label": "ESPANOL", "author": "Ana", "version": "0.2.0",
                     "mod_name": "", "_comment_name": "ignored"}
        self.write_pack()

    def write_pack(self):
        os.makedirs(os.path.join(self.pack, "G1", "Names"), exist_ok=True)
        os.makedirs(os.path.join(self.pack, "_orphaned"), exist_ok=True)
        with open(os.path.join(self.pack, "pack.json"), "w", encoding="utf-8") as fh:
            json.dump(self.meta, fh, ensure_ascii=False)
        with open(os.path.join(self.pack, "G1", "Names", "moves.csv"), "w", encoding="utf-8", newline="") as fh:
            fh.write("kind,key,game,english,text,versions,notes\n"
                     "moves,TACKLE,gen1,TACKLE,PLACAJE,,\nmoves,EMBER,gen1,EMBER,,,\n")
        with open(os.path.join(self.pack, "_orphaned", "old.csv"), "w", encoding="utf-8") as fh:
            fh.write("kind,key,text\nmoves,GONE,X\n")

    def release(self, *extra):
        return quiet(lpk.main, ["release", self.pack, "-o", self.dist, *extra])

    def zipped(self, name="elp_es-0.2.0.zip"):
        with zipfile.ZipFile(os.path.join(self.dist, name)) as z:
            return {n: z.read(n) for n in z.namelist()}

    def test_zip_is_an_importable_mod(self):
        self.assertEqual(self.release(), 0)
        files = self.zipped()
        self.assertEqual({n.split("/")[0] for n in files}, {"elp_es"}, "one top folder")
        self.assertEqual(sorted(files), ["elp_es/main.lua", "elp_es/manifest.json",
                                         "elp_es/pack/G1/Names/moves.csv", "elp_es/pack/pack.json"],
                         "working folders like _orphaned stay out")
        m = json.loads(files["elp_es/manifest.json"])
        self.assertEqual((m["id"], m["version"], m["entry"], m["api"]), ("elp_es", "0.2.0", "main.lua", 2))
        self.assertEqual(m["name"], "Español (ELP generated)")
        self.assertEqual(m["dependencies"], ["easy_language_packs@^1.2.0#kakhonn/g1r-easy-language-packs"])
        with open(os.path.join(ROOT, "manifest.json"), encoding="utf-8") as fh:
            loader = json.load(fh)
        self.assertEqual(m["games"], loader["games"], "same games as Easy Language Packs")
        self.assertEqual(m["game_version"], loader["game_version"])
        self.assertIn("Ana", m["description"])
        self.assertNotIn("github", m)
        with open(os.path.join(ROOT, "tools", "pack_mod_main.lua"), "rb") as fh:
            self.assertEqual(files["elp_es/main.lua"].replace(b"\r\n", b"\n"),
                             fh.read().replace(b"\r\n", b"\n"))
        released = list(csv.DictReader(io.StringIO(files["elp_es/pack/G1/Names/moves.csv"].decode("utf-8-sig"))))
        self.assertEqual([r["key"] for r in released], ["TACKLE"], "untranslated rows are dropped")
        self.assertNotIn("english", released[0], "the ROM-text column is stripped")

    def test_mod_name_github_and_id(self):
        self.meta["mod_name"] = "Spanish by Ana"
        self.write_pack()
        self.assertEqual(self.release("--id", "es_ana", "--github", "ana/es-pack"), 0)
        m = json.loads(self.zipped("elp_es_ana-0.2.0.zip")["elp_es_ana/manifest.json"])
        self.assertEqual((m["id"], m["name"], m["github"]), ("elp_es_ana", "Spanish by Ana", "ana/es-pack"))

    def test_refusals(self):
        self.assertEqual(self.release(), 0)
        self.assertEqual(self.release(), 2, "an existing zip needs --force or a new version")
        self.assertEqual(self.release("--force"), 0)
        self.assertEqual(self.release("--id", "Español"), 2, "the id must be a valid mod id")
        del self.meta["version"]
        self.write_pack()
        self.assertEqual(self.release("--force"), 2, "pack.json needs a version")

    def test_split_template_has_mod_name(self):
        meta = lpk.pack_json_template("Español", "ESPANOL", "Ana")
        self.assertEqual(meta["mod_name"], "")
        self.assertEqual(lpk.pack_mod_manifest("es", meta)["name"], "Español (ELP generated)")


class EngineDownload(unittest.TestCase):
    """A release build has no src/: lpk.py fetches G1R's source zip once and
    caches it.  A local server stands in for GitHub."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.game = os.path.join(self.tmp, "game")
        make_install(self.game)
        shutil.rmtree(os.path.join(self.game, "src"))  # like a release build
        # the archive GitHub serves for a tag
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("gen1recomp-0.3.89/src/core/Strings.lua", "return {}\n")
            zf.writestr("gen1recomp-0.3.89/src/ui/Menu.lua", 'local S = Strings("CONTINUE")\n')
            zf.writestr("gen1recomp-0.3.89/src/battle/Status.lua",
                        'Status.RECORDS = { PSN = { id = "PSN", label = "PSN" } }\n')
            zf.writestr("gen1recomp-0.3.89/assets/big.png", "x" * 1000)
            zf.writestr("gen1recomp-0.3.89/src/../../escape.lua", "bad\n")
        self.zipdata = buf.getvalue()
        self.hits = []
        test = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                test.hits.append(self.path)
                if self.path.endswith("/releases/latest"):
                    body = json.dumps({"tag_name": "v0.3.89"}).encode()
                elif self.path.endswith("/archive/refs/tags/v0.3.89.zip"):
                    body = test.zipdata
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{self.server.server_port}"
        self.saved = (lpk.GITHUB_API, lpk.GITHUB_WEB, os.environ.get("LPK_CACHE"))
        lpk.GITHUB_API = lpk.GITHUB_WEB = base
        self.cache = os.path.join(self.tmp, "cache")
        os.environ["LPK_CACHE"] = self.cache
        self.out = os.path.join(self.tmp, "out")

    def tearDown(self):
        self.server.shutdown()
        lpk.GITHUB_API, lpk.GITHUB_WEB = self.saved[0], self.saved[1]
        if self.saved[2] is None:
            os.environ.pop("LPK_CACHE", None)
        else:
            os.environ["LPK_CACHE"] = self.saved[2]
        shutil.rmtree(self.tmp)

    def strings(self):
        return {r["key"] for _, r in pack_rows(self.out) if r["kind"] == "strings"}

    def test_downloads_once_and_caches(self):
        self.assertEqual(quiet(lpk.main, ["split", "--game", self.game, "-o", self.out]), 0)
        self.assertIn("CONTINUE", self.strings(), "engine text came from the downloaded source")
        self.assertTrue(os.path.isfile(os.path.join(self.cache, "v0.3.89", "src", "ui", "Menu.lua")))
        self.assertFalse(os.path.exists(os.path.join(self.cache, "v0.3.89", "assets")),
                         "only src/*.lua is kept")
        self.assertFalse(os.path.exists(os.path.join(self.cache, "escape.lua")),
                         "archive paths can't escape the cache")
        downloads = [h for h in self.hits if h.endswith(".zip")]
        self.assertEqual(len(downloads), 1)
        quiet(lpk.main, ["split", "--game", self.game, "-o", self.out, "--engine-version", "0.3.89"])
        self.assertEqual(len([h for h in self.hits if h.endswith(".zip")]), 1, "second run uses the cache")

    def test_no_download(self):
        quiet(lpk.main, ["split", "--game", self.game, "-o", self.out, "--no-download"])
        self.assertEqual(self.strings(), set())
        self.assertEqual(self.hits, [])

    def test_unknown_version_fails_softly(self):
        code = quiet(lpk.main, ["split", "--game", self.game, "-o", self.out, "--engine-version", "9.9.9"])
        self.assertEqual(code, 0, "the sheets are still written")
        self.assertEqual(self.strings(), set())


if __name__ == "__main__":
    unittest.main()
