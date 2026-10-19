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


def quiet(fn, *a):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*a)


# ---- tests --------------------------------------------------------------

class Basics(unittest.TestCase):
    def test_fnv1a_vectors(self):
        self.assertEqual(lpk.fnv1a(""), "811c9dc5")
        self.assertEqual(lpk.fnv1a("a"), "e40c292c")
        self.assertEqual(lpk.fnv1a("foobar"), "bf9cf968")

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
        self.assertEqual(lpk.option_label("Bahasa Melayu"), "BAHASA MELAYU")
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

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.game = os.path.join(self.tmp, "game")
        make_install(self.game)
        self.out = os.path.join(self.tmp, "packs", "zz")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def split(self, *extra):
        return quiet(lpk.main, ["split", "--game", self.game, "-o", self.out, *extra])

    def path(self, name):
        """Where the organized layout puts one of split's sheets."""
        return os.path.join(self.out, *lpk_layout.sheet_path(name, "organized").split("/"))

    def sheet(self, name):
        return rows(self.path(name))

    def save(self, path, data, fieldnames=None):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames or lpk.SPLIT_HEADER)
            w.writeheader()
            w.writerows(data)


class Split(SplitCase):
    def test_writes_every_sheet_and_pack_json(self):
        self.assertEqual(self.split("--name", "Bahasa Melayu", "--author", "Tester", "--layout", "flat"), 0)
        for name, _ in lpk.SPLIT_FILES:
            self.assertTrue(os.path.isfile(os.path.join(self.out, name + ".csv")), name)
        meta = json.load(open(os.path.join(self.out, "pack.json"), encoding="utf-8"))
        self.assertEqual((meta["name"], meta["label"], meta["author"]), ("Bahasa Melayu", "BAHASA MELAYU", "Tester"))
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
        self.save(self.path("11_dex_gen1"), data)
        self.split("--name", "Second")
        kinds = {r["kind"]: r["text"] for r in self.sheet("11_dex_gen1") if r["kind"] == "dex_kind"}
        self.assertEqual(kinds["dex_kind"], "SPESIES BARU")
        orphans = rows(os.path.join(self.out, "_orphaned", "G1", "11_dex_gen1.csv"))
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
                r["text"] = "%s naik ke\\ntahap %d!"
            if r["english"] == "A SIGN":
                r["text"] = "PAPAN TANDA"
        self.save(self.path("01_red_blue"), data)
        self.assertEqual(quiet(lpk.main, ["check", self.out]), 0)
        dist = os.path.join(self.tmp, "dist")
        self.assertEqual(quiet(lpk.main, ["release", self.out, "-o", dist]), 0)
        released = rows(os.path.join(dist, "G1", "01_red_blue.csv"))
        self.assertEqual(len(released), 2, "untranslated rows are dropped")
        self.assertNotIn("english", released[0], "the ROM-text column is stripped")

    def test_check_catches_placeholder_mistakes(self):
        self.split()
        data = self.sheet("03_gold_silver")
        for r in data:
            if r["key"] == "AlreadyAsleepText" or "already asleep" in r["english"]:
                r["text"] = "{TARGET} tidur"  # {PROMPT} dropped
        self.save(self.path("03_gold_silver"), data)
        self.assertEqual(quiet(lpk.main, ["check", self.out]), 1)


class SplitLayout(SplitCase):
    """The organized layout, the manifest in _lpk/, and what a re-run keeps:
    translations wherever they are, rows the translator moved or deleted,
    files split no longer writes."""

    def manifest(self):
        with open(os.path.join(self.out, "_lpk", "split.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def keys_in(self, path):
        return [r["key"] for r in rows(path)]

    def translate(self, name, english, text):
        data = self.sheet(name)
        for r in data:
            if r["english"] == english:
                r["text"] = text
        self.save(self.path(name), data)

    def move(self, name, english, dest, fieldnames=None):
        """Move one row from a split sheet to `dest`, appending it there."""
        data = self.sheet(name)
        row = next(r for r in data if r["english"] == english)
        self.save(self.path(name), [r for r in data if r is not row])
        old = rows(dest) if os.path.isfile(dest) else []
        self.save(dest, old + [{k: row.get(k, "") for k in (fieldnames or lpk.SPLIT_HEADER)}], fieldnames)
        return row

    def test_organized_is_the_default(self):
        self.split()
        self.assertTrue(os.path.isfile(os.path.join(self.out, "G1", "01_red_blue.csv")))
        self.assertTrue(os.path.isfile(os.path.join(self.out, "G1", "Yellow", "02_yellow_exclusive.csv")))
        self.assertFalse(os.path.exists(os.path.join(self.out, "01_red_blue.csv")))
        self.assertFalse(os.path.exists(self.path("05_firered_leafgreen")), "empty sheets aren't written")
        self.assertEqual(self.manifest()["layout"], "organized")

    def test_upgrading_a_flat_pack_keeps_its_translations(self):
        self.split("--layout", "flat")
        shutil.rmtree(os.path.join(self.out, "_lpk"))          # as an older lpk.py left it
        flat = os.path.join(self.out, "01_red_blue.csv")
        data = rows(flat)
        for r in data:
            if r["english"] == "A SIGN":
                r["text"] = "PAPAN TANDA"
        self.save(flat, data)
        self.split()
        signs = [r["text"] for r in self.sheet("01_red_blue") if r["english"] == "A SIGN"]
        self.assertEqual(signs, ["PAPAN TANDA"])
        self.assertFalse(os.path.exists(flat), "the old sheet left the pack")
        kept = glob.glob(os.path.join(self.out, "_orphaned", "_retired", "*", "01_red_blue.csv"))
        self.assertEqual(len(kept), 1, "a retired sheet with translations is kept in _orphaned/_retired")
        self.assertFalse(os.path.exists(os.path.join(self.out, "02_yellow_exclusive.csv")))
        self.assertEqual(glob.glob(os.path.join(self.out, "_orphaned", "_retired", "*", "02_*")), [],
                         "an untranslated retired sheet is simply removed")
        self.assertEqual(quiet(lpk.main, ["check", self.out]), 0, "no key is defined twice")

    def test_moved_rows_stay_where_the_translator_put_them(self):
        self.split()
        self.translate("01_red_blue", "A SIGN", "PAPAN TANDA")
        mine = os.path.join(self.out, "G1", "mine.csv")
        self.move("01_red_blue", "A SIGN", mine)                       # into a file of their own
        self.move("01_red_blue", "TACKLE", self.path("08_names_gen1"))  # into another split file
        self.split()
        self.split()                                                   # and it stays that way
        rb = self.sheet("01_red_blue")
        self.assertNotIn("A SIGN", [r["english"] for r in rb])
        self.assertNotIn("TACKLE", [r["key"] for r in rb])
        self.assertEqual([(r["english"], r["text"]) for r in rows(mine)], [("A SIGN", "PAPAN TANDA")])
        self.assertIn("TACKLE", self.keys_in(self.path("08_names_gen1")))
        self.assertEqual(sorted(self.manifest()["pins"].values()), ["G1/08_names_gen1.csv", "G1/mine.csv"])

    def test_rows_in_your_own_file_get_fresh_english_and_nothing_else_changes(self):
        self.split()
        self.translate("01_red_blue", "TACKLE", "TERJANG")
        mine = os.path.join(self.out, "G1", "mine.csv")
        cols = lpk.SPLIT_HEADER + ["comment"]
        self.save(mine, [{"kind": "moves", "key": "MY_OWN", "game": "gen1", "english": "", "text": "SENDIRI",
                          "comment": "mine"}], cols)
        self.move("01_red_blue", "TACKLE", mine, cols)
        for v in ("red", "blue", "yellow"):                            # a game update renames the move
            write(os.path.join(self.game, v, "data", "generated", "moves.lua"),
                  {"TACKLE": {"id": "TACKLE", "name": "BODY SLAM"}})
        self.split()
        got = rows(mine)
        self.assertEqual([r["key"] for r in got], ["MY_OWN", "TACKLE"], "rows and order are kept")
        self.assertEqual(got[0]["comment"], "mine")
        self.assertEqual((got[1]["english"], got[1]["text"]), ("BODY SLAM", "TERJANG"))

    def test_deleted_rows_stay_deleted_until_restored(self):
        self.split()
        data = self.sheet("01_red_blue")
        self.save(self.path("01_red_blue"), [r for r in data if r["english"] != "A SIGN"])
        self.split()
        self.split()
        self.assertNotIn("A SIGN", [r["english"] for r in self.sheet("01_red_blue")])
        self.assertEqual(list(self.manifest()["deleted"].values()), [["G1/01_red_blue.csv"]])
        self.split("--restore", "G1\\01_red_blue.csv")
        self.assertIn("A SIGN", [r["english"] for r in self.sheet("01_red_blue")])
        self.assertEqual(self.manifest()["deleted"], {})

    def test_deleting_one_copy_of_a_key_split_writes_twice(self):
        gen3(os.path.join(self.game, "firered"))         # FRLG shares Ruby's line, so both sheets get it
        self.split()
        frlg, rs = self.path("05_firered_leafgreen"), self.path("06_ruby_sapphire")
        line = [r for r in rows(frlg) if r["kind"] == "dialogue"]
        self.assertEqual(len(line), 1)
        self.assertIn(line[0]["key"], self.keys_in(rs))
        self.save(frlg, [r for r in rows(frlg) if r["kind"] != "dialogue"])
        self.split()
        self.split()
        # (this small FRLG sheet had nothing else, so it's retired once its one row is gone)
        self.assertNotIn(line[0]["key"], self.keys_in(frlg) if os.path.isfile(frlg) else [],
                         "the deleted copy stays deleted")
        self.assertEqual(self.keys_in(rs).count(line[0]["key"]), 1, "the other copy is untouched")
        self.assertEqual(list(self.manifest()["deleted"].values()), [["G3/FRLG/05_firered_leafgreen.csv"]])

    def test_restore_all(self):
        self.split()
        os.remove(self.path("08_names_gen1"))                         # deleting a whole file
        self.split()
        self.assertFalse(os.path.exists(self.path("08_names_gen1")))
        self.split("--restore", "all")
        self.assertTrue(os.path.isfile(self.path("08_names_gen1")))

    def test_a_layout_change_moves_rows_but_not_the_translators(self):
        self.split()
        self.translate("11_dex_gen1", "NEW SPECIE", "SPESIES BARU")
        mine = os.path.join(self.out, "G1", "mine.csv")
        self.move("01_red_blue", "A SIGN", mine)
        new_paths = {"11_dex_gen1": "G1/Pokedex/11_dex_gen1.csv", "08_names_gen1": "G1/Names/08_names_gen1.csv",
                     "01_red_blue": "G1/Towns/01_red_blue.csv"}
        with mock.patch.dict(lpk_layout.ORGANIZED_PATHS, new_paths):
            self.split()
            kinds = [r["text"] for r in rows(os.path.join(self.out, "G1", "Pokedex", "11_dex_gen1.csv"))
                     if r["kind"] == "dex_kind"]
            self.assertTrue(os.path.isfile(os.path.join(self.out, "G1", "Names", "08_names_gen1.csv")))
            self.assertNotIn("A SIGN", [r["english"] for r in rows(os.path.join(self.out, "G1", "Towns",
                                                                                "01_red_blue.csv"))])
        self.assertEqual(kinds, ["SPESIES BARU"], "the translation followed the row")
        self.assertEqual([r["english"] for r in rows(mine)], ["A SIGN"], "a translator's move beats the layout")
        self.assertEqual(len(glob.glob(os.path.join(self.out, "_orphaned", "_retired", "*", "G1",
                                                    "11_dex_gen1.csv"))), 1)
        self.assertFalse(os.path.exists(os.path.join(self.out, "G1", "08_names_gen1.csv")))
        self.assertEqual(glob.glob(os.path.join(self.out, "_orphaned", "_retired", "*", "G1", "08_*")), [])

    def test_release_leaves_out_working_folders(self):
        self.split()
        data = self.sheet("11_dex_gen1")
        data.append({"kind": "moves", "key": "GONE", "game": "gen1", "english": "X", "text": "ORPHAN",
                     "versions": "", "notes": ""})
        self.save(self.path("11_dex_gen1"), data)
        self.split()
        self.assertTrue(os.path.isdir(os.path.join(self.out, "_orphaned")))
        dist = os.path.join(self.tmp, "dist")
        self.assertEqual(quiet(lpk.main, ["release", self.out, "-o", dist]), 0)
        self.assertFalse(os.path.exists(os.path.join(dist, "_orphaned")))
        self.assertFalse(os.path.exists(os.path.join(dist, "_lpk")))
        self.assertTrue(os.path.isfile(os.path.join(dist, "pack.json")))


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
        return {r["key"] for r in rows(os.path.join(self.out, "G1", "01_red_blue.csv")) if r["kind"] == "strings"}

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
