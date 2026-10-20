#!/usr/bin/env python3
"""lpk.py: pack-making tool for Easy Language Packs (G1R Deluxe / Gen1Recomp).

Standard library only.  Three commands:

  template  Build a fill-in sheet (CSV or JSON) listing every translatable
            string: the engine's own text (read from the game folder's src/)
            and, if your imported ROM cache is found, every dialogue line and
            species / move / item / trainer / Pokedex name.
            --merge carries an existing pack's translations into a fresh
            template after a game update.

  check     Parse a pack the same way the mod does and report problems:
            bad files, unknown kinds or ids, %s/%d mismatches, lines that
            are longer than the English and may overflow the text box,
            and how much is translated.

  release   Package a pack as its own mod (elp_<id>-<version>.zip) that
            players import with MODS > Import mod .zip, with the English /
            notes columns and untranslated rows stripped.  The English for
            dialogue and names comes out of the ROM, so it should not be
            shipped.  --folder writes a plain pack folder instead.

Examples (Windows):
  py lpk.py template --game "C:\\Games\\gen1recomp" -o es.csv
  py lpk.py check ..\\packs\\es --game "C:\\Games\\gen1recomp"
  py lpk.py release ..\\packs\\es -o dist
"""

import argparse
import collections
import csv
import io
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lpk_layout  # noqa: E402  (where split puts each row; lives next to this file)

# --------------------------------------------------------------- kinds
# Must match lib/pack.lua.
KINDS = {
    "dialogue": ["dialogue", "text", "script"],
    "strings": ["strings", "engine", "ui"],
    "species": ["species", "pokemon", "species_names", "pokemon_names"],
    "moves": ["moves", "move", "move_names"],
    "items": ["items", "item", "item_names"],
    "trainers": ["trainers", "trainer", "trainer_names", "trainer_classes"],
    "statuses": ["statuses", "status", "status_labels"],
    "status_hud": ["status_hud"],
    "dex_kind": ["dex_kind", "dex", "dex_category", "dex_species"],
    "dex_text": ["dex_text", "dex_entry"],
    "dex_text2": ["dex_text2"],
}
ALIAS = {a: k for k, names in KINDS.items() for a in names}
KIND_ORDER = list(KINDS)
KEY_COLUMNS = ("key", "id", "label", "source")
TEXT_COLUMNS = ("text", "translation", "value", "target")
REFERENCE_COLUMNS = ("english", "notes", "note", "comment", "comments", "context", "versions")


def kind_of(name):
    return ALIAS.get((name or "").strip().lower())


# --------------------------------------------------------------- escapes
# Spreadsheet cells use backslash escapes for the game's control characters:
#   \n new line, \v scrolling line (pokered `cont`), \f new page (`para`)
_CSV_UNESC = {"n": "\n", "v": "\v", "f": "\f", "t": "\t", "r": "\r", "\\": "\\"}


def csv_unescape(s):
    return re.sub(r"\\(.)", lambda m: _CSV_UNESC.get(m.group(1), "\\" + m.group(1)), s)


def csv_escape(s):
    return (s.replace("\\", "\\\\").replace("\n", "\\n").replace("\v", "\\v")
             .replace("\f", "\\f").replace("\t", "\\t").replace("\r", "\\r"))


# --------------------------------------------------------------- Lua literals
# The imported ROM cache and the engine's Strings("...") call sites are Lua.
# This reads the subset build_data.py writes: `return <table>` of strings,
# numbers, booleans, nil and nested tables.

_LUA_ESC = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f",
            "v": "\v", "\\": "\\", '"': '"', "'": "'", "\n": "\n"}


def lua_unquote(lit):
    """Decode one quoted Lua string literal (with its quotes) to text."""
    body, out, i = lit[1:-1], [], 0
    raw = bytearray()

    def flush():
        if raw:
            out.append(raw.decode("utf-8", "replace"))
            raw.clear()

    while i < len(body):
        c = body[i]
        if c != "\\":
            flush()
            out.append(c)
            i += 1
            continue
        n = body[i + 1] if i + 1 < len(body) else ""
        if n.isdigit():
            m = re.match(r"\d{1,3}", body[i + 1:])
            raw.append(int(m.group(0)) & 0xFF)
            i += 1 + len(m.group(0))
        elif n == "x":
            raw.append(int(body[i + 2:i + 4], 16))
            i += 4
        elif n == "u":
            m = re.match(r"\{([0-9a-fA-F]+)\}", body[i + 2:])
            flush()
            out.append(chr(int(m.group(1), 16)))
            i += 2 + len(m.group(0))
        elif n == "z":
            i += 2
            while i < len(body) and body[i].isspace():
                i += 1
        else:
            flush()
            out.append(_LUA_ESC.get(n, n))
            i += 2
    flush()
    return "".join(out)


class LuaReader:
    TOKEN = re.compile(r"""
        (?P<ws>\s+|--\[(?P<eq>=*)\[.*?\](?P=eq)\]|--[^\n]*)
      | (?P<str>"(?:[^"\\\n]|\\.|\\\n)*"|'(?:[^'\\\n]|\\.|\\\n)*')
      | (?P<long>\[(?P<leq>=*)\[.*?\](?P=leq)\])
      | (?P<num>0[xX][0-9a-fA-F]+|-?\d+\.?\d*(?:[eE][-+]?\d+)?)
      | (?P<name>[A-Za-z_]\w*)
      | (?P<sym>[{}\[\]=,;()\-.:#<>~*/+%^])
    """, re.S | re.X)

    def __init__(self, text):
        self.toks = []
        pos = 0
        while pos < len(text):
            m = self.TOKEN.match(text, pos)
            if not m:
                raise ValueError(f"cannot parse Lua near {text[pos:pos + 30]!r}")
            pos = m.end()
            if m.group("ws") is not None:
                continue
            if m.group("str") is not None:
                self.toks.append(("v", lua_unquote(m.group("str"))))
            elif m.group("long") is not None:
                s = m.group("long")
                k = len(m.group("leq")) + 2
                body = s[k:-k]
                self.toks.append(("v", body[1:] if body.startswith("\n") else body))
            elif m.group("num") is not None:
                s = m.group("num")
                self.toks.append(("v", int(s, 16) if s.lower().startswith("0x")
                                  else (float(s) if any(c in s for c in ".eE") else int(s))))
            elif m.group("name") is not None:
                self.toks.append(("n", m.group("name")))
            else:
                self.toks.append(("s", m.group("sym")))
        self.i = 0

    def peek(self, k=0):
        return self.toks[self.i + k] if self.i + k < len(self.toks) else (None, None)

    def take(self):
        t = self.peek()
        self.i += 1
        return t

    def expect(self, kind, val=None):
        t = self.take()
        if t[0] != kind or (val is not None and t[1] != val):
            raise ValueError(f"expected {val or kind}, got {t[1]!r}")
        return t

    def value(self):
        kind, val = self.take()
        if kind == "v":
            return val
        if kind == "n":
            if val in ("true", "false"):
                return val == "true"
            if val == "nil":
                return None
            raise ValueError(f"unsupported Lua expression '{val}'")
        if (kind, val) == ("s", "-"):
            return -self.value()
        if (kind, val) == ("s", "{"):
            return self.table()
        raise ValueError(f"unexpected {val!r}")

    def table(self):
        out, n = {}, 0
        while True:
            t = self.peek()
            if t == ("s", "}"):
                self.take()
                return out
            if t == ("s", "["):
                self.take()
                k = self.value()
                self.expect("s", "]")
                self.expect("s", "=")
                out[k] = self.value()
            elif t[0] == "n" and self.peek(1) == ("s", "="):
                self.take()
                self.take()
                out[t[1]] = self.value()
            else:
                n += 1
                out[n] = self.value()
            if self.peek()[1] in (",", ";"):
                self.take()

    @classmethod
    def load(cls, text):
        r = cls(text)
        r.expect("n", "return")
        return r.value()

    @classmethod
    def load_assignments(cls, text):
        """Tables from either `return { k = {...} }` or the chunked form the
        Gen 3 extractor writes (`T["k"] = {...}` inside functions)."""
        r = cls(text)
        out = {}
        toks = r.toks
        i = 0
        while i < len(toks):
            if toks[i] == ("n", "return") and i + 1 < len(toks) and toks[i + 1] == ("s", "{"):
                r.i = i + 2
                val = r.table()
                if isinstance(val, dict):
                    out.update(val)
                i = r.i
                continue
            if (toks[i][0] == "n" and i + 5 < len(toks) and toks[i + 1] == ("s", "[")
                    and toks[i + 2][0] == "v" and toks[i + 3] == ("s", "]")
                    and toks[i + 4] == ("s", "=") and toks[i + 5] == ("s", "{")):
                r.i = i + 6
                out[toks[i + 2][1]] = r.table()
                i = r.i
                continue
            i += 1
        return out


# --------------------------------------------------------------- game sources

STRINGS_CALL = re.compile(  # same pattern tools/modkit.py harvests with
    r'\bStrings(?:\.source)?\('
    r'\s*("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')\s*(?:,|\))')
# A Strings(...) call whose second argument is a quoted context.
STRINGS_CONTEXT = re.compile(
    r'\bStrings\(\s*("(?:[^"\\]|\\.)*")\s*,\s*("(?:[^"\\]|\\.)*")\s*\)')


# Source paths whose text only ever shows in Gold/Silver/Crystal or the GBA
# games.  A Red/Blue/Yellow pack doesn't need them; --all-games keeps them.
OTHER_GAMES = re.compile(
    r"(^|/)(gen2|gen3|game3|g3u|gba|crystal|emerald|frlg|rse|gold|silver)(/|$)"
    r"|(^|/)[^/]*(Gen2|Gen3|Game3|Crystal|Emerald|FireRed|LeafGreen)[^/]*\.lua$", re.I)


# The launcher (ROM import, settings, online lobby) runs before any mod's
# code, so Easy Language Packs cannot translate it.
LAUNCHER = re.compile(r"^import/")


def engine_strings(game, all_games=False):
    """[(source, "path:line")] for every Strings("...") literal in src/."""
    src = os.path.join(game, "src")
    if not os.path.isdir(src):
        return None
    out, seen = [], set()
    for root, dirs, names in os.walk(src):
        dirs.sort()
        for name in sorted(names):
            if not name.endswith(".lua"):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, game).replace(os.sep, "/")
            if not all_games and (OTHER_GAMES.search(rel[len("src/"):])
                                  or LAUNCHER.search(rel[len("src/"):])):
                continue
            with open(path, encoding="utf-8", errors="replace") as fh:
                body = fh.read()
            for m in STRINGS_CALL.finditer(body):
                try:
                    text = lua_unquote(m.group(1))
                except Exception:
                    continue
                if text in seen:
                    continue
                seen.add(text)
                out.append((text, f"{rel}:{body.count(chr(10), 0, m.start()) + 1}"))
    return out


def status_labels(game):
    path = os.path.join(game, "src", "battle", "Status.lua")
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        body = fh.read()
    return re.findall(r'id = "(\w+)", label = "([^"]*)"', body)


def cache_candidates(explicit=None, game=None):
    if explicit:
        return [explicit]
    roots = []
    # portable installs (portable.txt beside the exe) keep the ROM cache in
    # the game folder itself: <game>/red/data/generated
    if game:
        roots.append(game)
    ident = os.environ.get("POKEPORT_IDENTITY") or "pokemon-love2d"

    def add_all(parent):
        # the default identity first, then every other save folder under it:
        # release builds and forks don't all use "pokemon-love2d"
        roots.append(os.path.join(parent, ident))
        try:
            for name in sorted(os.listdir(parent)):
                path = os.path.join(parent, name)
                if name != ident and os.path.isdir(path):
                    roots.append(path)
        except OSError:
            pass

    # A release build is a fused executable, which LOVE gives a save folder
    # named after the identity alone (%APPDATA%\pokemon-love2d); a source run
    # through love.exe nests it under LOVE\ instead.
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        roots.append(os.path.join(os.environ["APPDATA"], ident))
        add_all(os.path.join(os.environ["APPDATA"], "LOVE"))
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
        roots.append(os.path.join(base, ident))
        add_all(os.path.join(base, "LOVE"))
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        roots.append(os.path.join(base, ident))
        add_all(os.path.join(base, "love"))
    return roots


def imports_root(explicit=None, game=None):
    """The folder holding the imported games (red/, gold/, firered/, ...):
    --cache, else the game folder if a portable install keeps them there,
    else the first user data folder that has any."""
    if explicit:
        return explicit
    for root in cache_candidates(None, game):
        if any(os.path.isdir(os.path.join(root, v, "data", "generated")) for v in GEN_OF):
            return root
    return game


def find_cache(root, version):
    """The data/generated directory for red/blue/yellow under a save root."""
    for v in ([version] if version else ["red", "blue", "yellow", ""]):
        d = os.path.join(root, v, "data", "generated") if v else os.path.join(root, "data", "generated")
        if os.path.isfile(os.path.join(d, "text.lua")):
            return d
    # a directory that already IS data/generated (or a fixture set)
    if os.path.isfile(os.path.join(root, "text.lua")):
        return root
    return None


def load_cache(gen_dir):
    data = {}
    for name in ("text", "pokemon", "moves", "items", "trainers"):
        path = os.path.join(gen_dir, name + ".lua")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            try:
                data[name] = LuaReader.load(fh.read())
            except ValueError as err:
                print(f"lpk: warning: could not read {path}: {err}", file=sys.stderr)
    return data


# ---- the engine's source (for its menu and battle text) -----------------
# Release builds don't ship src/, so when the game folder has none, lpk.py
# downloads that release's "Source code (zip)" from GitHub once, keeps only
# src/**/*.lua, and caches it per version.

ENGINE_REPO = "bryanthaboi/gen1recomp"
# overridable for tests (a local server standing in for GitHub)
GITHUB_API = os.environ.get("LPK_GITHUB_API", "https://api.github.com")
GITHUB_WEB = os.environ.get("LPK_GITHUB_WEB", "https://github.com")


def engine_cache_root():
    if os.environ.get("LPK_CACHE"):
        return os.environ["LPK_CACHE"]
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        base = os.environ["LOCALAPPDATA"]
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "easy_language_packs", "engine")


def _http(url):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "easy-language-packs-lpk",
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=60)


def latest_engine_tag():
    with _http(f"{GITHUB_API}/repos/{ENGINE_REPO}/releases/latest") as resp:
        return json.load(resp)["tag_name"]


def fetch_engine_source(tag, refresh=False):
    """Folder holding src/ for engine release `tag` (e.g. v0.3.89),
    downloading it on first use.  Returns None on failure."""
    import tempfile
    import zipfile
    dest = os.path.join(engine_cache_root(), tag)
    marker = os.path.join(dest, "src", "core", "Strings.lua")
    if os.path.isfile(marker) and not refresh:
        print(f"lpk: engine source {tag}: using the copy in {dest}", file=sys.stderr)
        return dest
    url = f"{GITHUB_WEB}/{ENGINE_REPO}/archive/refs/tags/{tag}.zip"
    print(f"lpk: no src/ in the game folder; downloading G1R's source for {tag} "
          f"(one time, cached in {dest})", file=sys.stderr)
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    try:
        with _http(url) as resp, tmp:
            total = int(resp.headers.get("Content-Length") or 0)
            got, shown = 0, -1
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                tmp.write(chunk)
                got += len(chunk)
                step = got * 10 // total if total else got >> 23
                if step != shown:
                    shown = step
                    size = f"{got / 1e6:.0f} of {total / 1e6:.0f} MB" if total else f"{got / 1e6:.0f} MB"
                    print(f"lpk:   {size}", file=sys.stderr)
        staging = dest + ".partial"
        if os.path.isdir(staging):
            shutil.rmtree(staging)
        kept = 0
        with zipfile.ZipFile(tmp.name) as zf:
            for info in zf.infolist():
                # archive paths look like gen1recomp-0.3.89/src/core/Strings.lua
                parts = info.filename.split("/", 1)
                if len(parts) != 2 or not parts[1].startswith("src/") or not parts[1].endswith(".lua"):
                    continue
                rel = os.path.normpath(parts[1])
                if rel.startswith("..") or os.path.isabs(rel):
                    continue  # never write outside the cache
                out = os.path.join(staging, rel)
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with zf.open(info) as src, open(out, "wb") as fh:
                    shutil.copyfileobj(src, fh)
                kept += 1
        if not os.path.isfile(os.path.join(staging, "src", "core", "Strings.lua")):
            print(f"lpk: the download for {tag} doesn't look like G1R's source; engine text skipped",
                  file=sys.stderr)
            shutil.rmtree(staging, ignore_errors=True)
            return None
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        os.replace(staging, dest)
        print(f"lpk: engine source {tag}: {kept} files", file=sys.stderr)
        return dest
    except Exception as err:  # network, disk, bad zip: report and carry on without
        print(f"lpk: couldn't download G1R's source for {tag} ({err}); engine menu/battle "
              "text skipped. Try again, or pass --engine-source <folder with src>.", file=sys.stderr)
        return None
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def engine_source(args):
    """Folder whose src/ holds the engine's Lua: --engine-source, else the
    game folder's own src/, else a downloaded (and cached) release."""
    given = getattr(args, "engine_source", None)
    if given:
        if not os.path.isfile(os.path.join(given, "src", "core", "Strings.lua")):
            print(f"lpk: {given} has no src/core/Strings.lua; engine text skipped", file=sys.stderr)
            return None
        return given
    game = getattr(args, "game", None)
    if game and os.path.isdir(os.path.join(game, "src")):
        return game
    if getattr(args, "no_download", False):
        print("lpk: no src/ in the game folder and --no-download given; engine text skipped",
              file=sys.stderr)
        return None
    tag = getattr(args, "engine_version", None)
    if tag:
        tag = tag if tag.startswith("v") else "v" + tag
    else:
        try:
            tag = latest_engine_tag()
        except Exception as err:
            print(f"lpk: couldn't look up G1R's latest release ({err}); pass --engine-version "
                  "X.Y.Z, or --engine-source <folder with src>", file=sys.stderr)
            return None
        print(f"lpk: matching engine text to G1R {tag}, the latest release. If you play a "
              "different version, pass --engine-version X.Y.Z", file=sys.stderr)
    return fetch_engine_source(tag, getattr(args, "refresh_engine", False))


def reference_rows(game=None, cache=None, version=None, quiet=False, all_games=False, source=None):
    """Every translatable (kind, key, english, note) the sources give us.
    `source` is the folder holding the engine's src/ (see engine_source)."""
    rows = []
    if source:
        strings = engine_strings(source, all_games)
        if strings is None:
            print(f"lpk: no src/ folder in {source}; engine strings skipped", file=sys.stderr)
        else:
            for text, where in strings:
                rows.append(("strings", text, text, where))
            for sid, label in status_labels(source):
                rows.append(("statuses", sid, label, ""))
    gen_dir = None
    searched = cache_candidates(cache, game)
    for root in searched:
        gen_dir = find_cache(root, version)
        if gen_dir:
            break
    if gen_dir:
        if not quiet:
            print(f"lpk: imported data from {gen_dir}", file=sys.stderr)
        d = load_cache(gen_dir)
        for key, text in sorted((d.get("text") or {}).items()):
            if isinstance(text, str) and text:
                rows.append(("dialogue", key, text, ""))
        for reg, kind in (("pokemon", "species"), ("moves", "moves"),
                          ("items", "items"), ("trainers", "trainers")):
            for key, rec in sorted((d.get(reg) or {}).items(), key=lambda kv: str(kv[0])):
                if isinstance(rec, dict) and isinstance(rec.get("name"), str):
                    rows.append((kind, key, rec["name"], ""))
        texts = d.get("text") or {}
        for key, rec in sorted((d.get("pokemon") or {}).items()):
            dex = isinstance(rec, dict) and rec.get("dexEntry")
            if isinstance(dex, dict):
                for field, kind in (("kind", "dex_kind"), ("text", "dex_text"), ("text2", "dex_text2")):
                    value = dex.get(field)
                    # Gen 1 entry text is a label into the text table
                    if field != "kind" and isinstance(texts.get(value), str):
                        value = texts[value]
                    if isinstance(value, str) and value:
                        rows.append((kind, key, value, ""))
        # the entries' text labels are covered by the dex rows above
        dex_labels = {rec["dexEntry"]["text"] for rec in (d.get("pokemon") or {}).values()
                      if isinstance(rec, dict) and isinstance(rec.get("dexEntry"), dict)
                      and isinstance(rec["dexEntry"].get("text"), str)}
        rows = [r for r in rows if not (r[0] == "dialogue" and r[1] in dex_labels)]
    elif not quiet:
        print("lpk: no imported ROM cache found (dialogue and names skipped); "
              "run the game once with your ROM, or pass --cache. Looked in:", file=sys.stderr)
        for root in searched[:12]:
            print("   ", root, file=sys.stderr)
    return rows


# --------------------------------------------------------------- reading packs

def _sniff(first_line):
    best, best_n = ",", -1
    for d in (",", ";", "\t"):
        n, quoted = 0, False
        for c in first_line:
            if c == '"':
                quoted = not quoted
            elif c == d and not quoted:
                n += 1
        if n > best_n:
            best, best_n = d, n
    return best


def _column(row, names):
    for n in names:
        if n in row:
            return row[n]
    return None


def _stem(path):
    return os.path.splitext(os.path.basename(path))[0].lower()


def parse_games(value):
    """None = every game, a frozenset of gen1/gen2/gen3, or False if invalid."""
    if not value or not str(value).strip():
        return None
    words = [w for w in re.split(r"[\s,;/|]+", str(value).lower()) if w]
    if any(w in ("all", "any") for w in words):
        return None
    if not all(w in ("gen1", "gen2", "gen3") for w in words):
        return False
    return frozenset(words)


def read_csv_file(path, shown, out, problems):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        body = fh.read()
    delim = "\t" if path.lower().endswith(".tsv") else _sniff(body.split("\n", 1)[0])
    reader = csv.reader(io.StringIO(body), delimiter=delim)
    try:
        records = list(reader)
    except csv.Error as err:
        problems.append(f"{shown}: {err}")
        return
    if not records:
        return
    header = [h.strip().lower() for h in records[0]]
    file_kind = kind_of(_stem(path))
    if "kind" not in header and not file_kind:
        problems.append(f"{shown}: no 'kind' column, and the file name is not a kind")
        return
    if not any(h in header for h in KEY_COLUMNS) or not any(h in header for h in TEXT_COLUMNS):
        problems.append(f"{shown}: needs a 'key' column and a 'text' column (header: {', '.join(header)})")
        return
    line = 1
    for rec in records[1:]:
        line += 1  # approximate for multi-line cells; good enough for messages
        if rec == [] or rec == [""]:
            continue
        row = {h: (rec[i] if i < len(rec) else "") for i, h in enumerate(header) if h}
        raw_kind = row.get("kind")
        key, text = _column(row, KEY_COLUMNS), _column(row, TEXT_COLUMNS)
        where = f"{shown}:{line}"
        if (raw_kind or "").lstrip().startswith("#") or (
                (key or "").startswith("#") and not re.fullmatch(r"#[0-9a-fA-F]{8}", key)):
            continue
        english = row.get("english")
        if text:
            kind = kind_of(raw_kind) if raw_kind else file_kind
            if not kind:
                problems.append(f"{where}: unknown kind '{raw_kind}'")
            elif not key:
                problems.append(f"{where}: empty key")
            else:
                game = parse_games(row.get("game"))
                if game is False:
                    problems.append(f"{where}: unknown game '{row.get('game')}' (use gen1, gen2 or gen3)")
                    continue
                out.append({"kind": kind, "key": csv_unescape(key), "text": csv_unescape(text),
                            "where": where, "game": game,
                            "english": csv_unescape(english) if english else None})


_JSON_V = re.compile(r'(?<!\\)((?:\\\\)*)\\v')


def read_json_file(path, shown, out, problems):
    with open(path, encoding="utf-8-sig") as fh:
        body = fh.read()
    try:
        doc = json.loads(_JSON_V.sub(r"\1\\u000b", body))
    except json.JSONDecodeError as err:
        problems.append(f"{shown}: line {err.lineno} col {err.colno}: {err.msg}")
        return

    def ignorable(k):
        return isinstance(k, str) and (k.startswith("//") or k.startswith("_comment")
                                       or k in ("$schema", "game"))

    file_game = parse_games(doc.get("game")) if isinstance(doc, dict) else None
    if file_game is False:
        problems.append(f"{shown}: unknown game '{doc.get('game')}'")
        return

    def add_map(kind, m, section):
        for key in sorted(m):
            if ignorable(key):
                continue
            text = m[key]
            where = f"{shown}: {section}.{key}"
            if isinstance(text, str):
                if text:
                    out.append({"kind": kind, "key": key, "text": text, "where": where,
                                "english": None, "game": file_game})
            elif text not in ({}, None):
                problems.append(f"{where}: value must be a string")

    if isinstance(doc, list):
        for i, row in enumerate(doc, 1):
            where = f"{shown}: [{i}]"
            if not isinstance(row, dict):
                problems.append(f"{where}: each row must be an object")
                continue
            kind = kind_of(row.get("kind")) or kind_of(_stem(path))
            key, text = _column(row, KEY_COLUMNS), _column(row, TEXT_COLUMNS)
            if isinstance(text, str) and text:
                if not kind:
                    problems.append(f"{where}: unknown kind '{row.get('kind')}'")
                elif not isinstance(key, str) or not key:
                    problems.append(f"{where}: missing key")
                else:
                    g = parse_games(row.get("game"))
                    if g is False:
                        problems.append(f"{where}: unknown game '{row.get('game')}'")
                        continue
                    out.append({"kind": kind, "key": key, "text": text, "where": where,
                                "english": row.get("english"), "game": g})
        return
    if not isinstance(doc, dict):
        problems.append(f"{shown}: top level must be an object or a list")
        return
    keys = [k for k in doc if not ignorable(k)]
    if keys and all(kind_of(k) and isinstance(doc[k], dict) for k in keys):
        for section in sorted(keys):
            add_map(kind_of(section), doc[section], section)
        return
    kind = kind_of(_stem(path))
    if not kind:
        problems.append(f"{shown}: not sectioned by kind, and the file name is not a kind")
        return
    add_map(kind, doc, _stem(path))


def read_pack(packs_dir, pack_id, seen=None):
    seen = seen or set()
    entries, problems = [], []
    if pack_id in seen:
        return entries, {}, [f"pack '{pack_id}': extends itself in a loop"]
    seen.add(pack_id)
    d = os.path.join(packs_dir, pack_id)
    if not os.path.isdir(d):
        return entries, {}, [f"pack '{pack_id}' not found in {packs_dir}"]
    meta = {}
    if os.path.isfile(os.path.join(d, "pack.json")):
        try:
            with open(os.path.join(d, "pack.json"), encoding="utf-8-sig") as fh:
                meta = json.load(fh)
        except json.JSONDecodeError as err:
            problems.append(f"{pack_id}/pack.json: {err}")
    bases = meta.get("extends")
    for base in ([bases] if isinstance(bases, str) else (bases or [])):
        if not isinstance(base, str) or not base.strip():
            continue  # a blank "extends" (as the template ships) means no base pack
        e, _, p = read_pack(packs_dir, base, seen)
        entries += e
        problems += p
    files = meta.get("files")
    if not isinstance(files, list):
        files = []
        for root, dirs, names in os.walk(d):
            dirs[:] = sorted(x for x in dirs if not x.startswith((".", "_")))
            for name in sorted(names):
                rel = os.path.relpath(os.path.join(root, name), d).replace(os.sep, "/")
                if rel.lower() == "pack.json":
                    continue
                if name.lower().endswith((".json", ".csv", ".tsv")):
                    files.append(rel)
        files.sort()
    for rel in files:
        path, shown = os.path.join(d, rel), f"{pack_id}/{rel}"
        if not os.path.isfile(path):
            problems.append(f"{shown}: cannot read")
        elif rel.lower().endswith(".json"):
            read_json_file(path, shown, entries, problems)
        else:
            read_csv_file(path, shown, entries, problems)
    return entries, meta, problems


# --------------------------------------------------------------- checks

def directives(s):
    """(count, numbered?) of printf directives, as src/core/Strings.lua counts them."""
    n = 0
    for m in re.finditer(r"%(.)", s):
        if m.group(1) != "%":
            n += 1
    return n


def positional_ok(text, wants):
    if not re.search(r"%\d+\$", text):
        return None
    plain = re.sub(r"%%", "", text)
    specs = re.findall(r"%(\d+\$)?[-+ #0]*\d*\.?\d*[a-zA-Z]", plain)
    if any(s == "" for s in specs):
        return False  # numbered and plain mixed
    return all(1 <= int(s[:-1]) <= wants for s in specs)


_TOKEN = re.compile(r"\{[^{}]*\}|%\d*\$?[-+ #0]*\d*\.?\d*[a-zA-Z]")


def visible_lines(s):
    return [len(_TOKEN.sub("", line)) for line in re.split(r"[\n\v\f]", s)]


def cmd_check(args):
    packs_dir, pack_id = os.path.split(os.path.abspath(args.pack.rstrip("/\\")))
    entries, meta, problems = read_pack(packs_dir, pack_id)
    ref = {}
    if args.game or args.cache:
        for kind, key, english, _ in reference_rows(args.game, args.cache, args.version, quiet=True,
                                                     source=engine_source(args),
                                                     all_games=True):
            ref[(kind, key)] = english
    have_ref = bool(ref)
    kinds_in_ref = {k for k, _ in ref}
    warnings = []
    final = {}
    for e in entries:
        k = (e["kind"], e["key"], e.get("game"))
        prev = final.get(k)
        if prev and prev["text"] != e["text"]:
            warnings.append(f"{e['where']}: also translated differently at {prev['where']}; "
                            "this one wins (later files override earlier ones)")
        final[k] = e
    for (kind, key, game), e in final.items():
        hashed = re.search(r"#[0-9a-fA-F]{8}$", key)
        if hashed and e.get("english") and fnv1a(e["english"]) != hashed.group(0)[1:].lower():
            warnings.append(f"{e['where']}: the english column no longer matches the key's "
                            "fingerprint (edited English?) - the key decides what is translated")
        english = e.get("english") or (None if hashed else ref.get((kind, key)))
        if kind == "strings" and english is None and not have_ref:
            english = key.split("|", 1)[-1]  # engine strings are keyed by their English
        if (have_ref and kind in kinds_in_ref and (kind, key) not in ref and not hashed
                and (game is None or "gen1" in game)):
            # strings may carry a "context|source" key
            if not (kind == "strings" and ("strings", key.split("|", 1)[-1]) in ref):
                warnings.append(f"{e['where']}: '{key}' is not a known {kind} key in this game version")
        if english and kind in ("strings", "dialogue"):
            wants = directives(english)
            pos = positional_ok(e["text"], wants)
            if pos is False:
                problems.append(f"{e['where']}: numbered %N$ directives are wrong for "
                                f"{wants} argument(s); the game will draw the English")
            elif pos is None and directives(e["text"]) != wants:
                problems.append(f"{e['where']}: has {directives(e['text'])} %-directives, the English has "
                                f"{wants}; the game will draw the English instead")
        if english and kind in ("dialogue", "strings", "dex_text", "dex_text2"):
            want = sorted(re.findall(r"\{[^{}]*\}|<[A-Z]+>", english))
            got = sorted(re.findall(r"\{[^{}]*\}|<[A-Z]+>", e["text"]))
            if want != got:
                miss = sorted(set(want) - set(got))
                extra = sorted(set(got) - set(want))
                problems.append(f"{e['where']}: control codes differ from the English"
                                + (f"; missing {' '.join(miss)}" if miss else "")
                                + (f"; unexpected {' '.join(extra)}" if extra else "")
                                + (" (count differs)" if not miss and not extra else ""))
        if english and kind in ("dialogue", "strings"):
            limit = max([18] + visible_lines(english))
            long_ = [n for n in visible_lines(e["text"]) if n > limit]
            if long_:
                warnings.append(f"{e['where']}: a line is {max(long_)} glyphs; the box fits "
                                f"{limit}. Break it with \\n, or \\v to scroll")
        if kind in ("species", "moves", "items", "trainers") and english:
            if len(e["text"]) > max(len(english), 10 if kind == "species" else 12):
                warnings.append(f"{e['where']}: '{e['text']}' is longer than names usually fit "
                                f"({len(e['text'])} chars; English '{english}' is {len(english)})")
    for p in problems:
        print("ERROR  ", p)
    for w in warnings:
        print("warn   ", w)
    counts = {}
    for (kind, _key, _game) in final:
        counts[kind] = counts.get(kind, 0) + 1
    print()
    print(f"pack '{pack_id}' ({meta.get('name', pack_id)}): {len(final)} translated strings")
    for kind in KIND_ORDER:
        if kind in counts or (have_ref and kind in kinds_in_ref):
            total = sum(1 for k, _ in ref if k == kind)
            line = f"  {kind:<11}{counts.get(kind, 0):6}"
            if total:
                line += f" / {total:<6} ({100 * counts.get(kind, 0) // total}%)"
            print(line)
    print(f"{len(problems)} error(s), {len(warnings)} warning(s)")
    return 1 if problems else 0


# --------------------------------------------------------------- template

def cmd_template(args):
    rows = reference_rows(args.game, args.cache, args.version, source=engine_source(args),
                          all_games=args.all_games)
    if not rows:
        print("lpk: nothing found; pass --game (the game folder with src/) and/or --cache",
              file=sys.stderr)
        return 1
    done = {}
    if args.merge:
        packs_dir, pack_id = os.path.split(os.path.abspath(args.merge.rstrip("/\\")))
        entries, _, problems = read_pack(packs_dir, pack_id)
        for p in problems:
            print("lpk: merge:", p, file=sys.stderr)
        for e in entries:
            done[(e["kind"], e["key"])] = e["text"]
    rows.sort(key=lambda r: (KIND_ORDER.index(r[0]), r[1] if r[0] != "strings" else ""))
    out = args.output
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    used = set()
    if out.lower().endswith(".json"):
        doc = {"_comment": "Fill in the values. Empty = keep English. The English is in the "
                           "matching _english section; delete those before you share the file."}
        for kind, key, english, _ in rows:
            doc.setdefault(kind, {})[key] = done.get((kind, key), "")
            doc.setdefault("_comment_english_" + kind, {})[key] = english
            used.add((kind, key))
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    else:
        delim = "\t" if out.lower().endswith(".tsv") else (";" if args.semicolon else ",")
        # utf-8-sig so Excel opens accents correctly
        with open(out, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh, delimiter=delim, quoting=csv.QUOTE_MINIMAL)
            w.writerow(["kind", "key", "english", "text", "notes"])
            for kind, key, english, note in rows:
                w.writerow([kind, csv_escape(key), csv_escape(english),
                            csv_escape(done.get((kind, key), "")), note])
                used.add((kind, key))
    per = {}
    for kind, *_ in rows:
        per[kind] = per.get(kind, 0) + 1
    print("  " + ", ".join(f"{k} {per[k]}" for k in KIND_ORDER if k in per))
    orphans = [k for k in done if k not in used]
    print(f"wrote {os.path.abspath(out)}: {len(rows)} strings"
          + (f", {len(done) - len(orphans)} already translated" if done else ""))
    if orphans:
        print(f"lpk: {len(orphans)} translations in the merged pack have no matching key any more "
              "(the English changed in a game update):", file=sys.stderr)
        for kind, key in sorted(orphans)[:20]:
            print(f"   {kind}: {key!r}", file=sys.stderr)
    print("note: the english column holds text from your ROM. Use `lpk.py release` "
          "to strip it before sharing a pack.")
    return 0


# --------------------------------------------------------------- split
# One CSV per game family, plus per-generation name and Pokedex sheets.
#
# Dialogue rows are keyed "#<hash>" -- a fingerprint of the English line --
# and the mod applies them to every line in the running game with that exact
# English.  That is what lets Gold/Silver's translations carry into Crystal
# (same text, different ROM addresses) and lets the "exclusive" sheets list
# only what the base games don't already have.  Pokedex and name rows use the
# game's id; when versions disagree on the English for one id, each variant
# gets its own "ID#<hash>" row, which applies only to the version it came from.

def fnv1a(text):
    h = 0x811C9DC5
    for b in text.encode("utf-8"):
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return "%08x" % h


# ---- Gen 3 script text -> readable source
B_TXT = {0x00: "B_BUFF1", 0x01: "B_BUFF2", 0x02: "B_COPY_VAR_1", 0x03: "B_COPY_VAR_2",
         0x04: "B_COPY_VAR_3", 0x05: "B_PLAYER_MON1_NAME", 0x06: "B_OPPONENT_MON1_NAME",
         0x07: "B_PLAYER_MON2_NAME", 0x08: "B_OPPONENT_MON2_NAME",
         0x09: "B_LINK_PLAYER_MON1_NAME", 0x0A: "B_LINK_OPPONENT_MON1_NAME",
         0x0B: "B_LINK_PLAYER_MON2_NAME", 0x0C: "B_LINK_OPPONENT_MON2_NAME",
         0x0D: "B_ATK_NAME_WITH_PREFIX_MON1", 0x0E: "B_ATK_PARTNER_NAME",
         0x0F: "B_ATK_NAME_WITH_PREFIX", 0x10: "B_DEF_NAME_WITH_PREFIX",
         0x11: "B_EFF_NAME_WITH_PREFIX", 0x12: "B_ACTIVE_NAME_WITH_PREFIX",
         0x13: "B_SCR_ACTIVE_NAME_WITH_PREFIX", 0x14: "B_CURRENT_MOVE", 0x15: "B_LAST_MOVE",
         0x16: "B_LAST_ITEM", 0x17: "B_LAST_ABILITY", 0x18: "B_ATK_ABILITY",
         0x19: "B_DEF_ABILITY", 0x1A: "B_SCR_ACTIVE_ABILITY", 0x1B: "B_EFF_ABILITY",
         0x1C: "B_TRAINER1_CLASS", 0x1D: "B_TRAINER1_NAME", 0x1E: "B_LINK_PLAYER_NAME",
         0x1F: "B_LINK_PARTNER_NAME", 0x20: "B_LINK_OPPONENT1_NAME",
         0x21: "B_LINK_OPPONENT2_NAME", 0x22: "B_LINK_SCR_TRAINER_NAME",
         0x23: "B_PLAYER_NAME", 0x24: "B_TRAINER1_LOSE_TEXT", 0x25: "B_TRAINER1_WIN_TEXT",
         0x26: "B_26", 0x27: "B_PC_CREATOR_NAME", 0x28: "B_ATK_PREFIX1",
         0x29: "B_DEF_PREFIX1", 0x2A: "B_ATK_PREFIX2", 0x2B: "B_DEF_PREFIX2",
         0x2C: "B_ATK_PREFIX3", 0x2D: "B_DEF_PREFIX3", 0x2E: "B_TRAINER2_LOSE_TEXT",
         0x2F: "B_TRAINER2_WIN_TEXT", 0x30: "B_BUFF3"}
B_TXT_RSE = {c: n for c, n in B_TXT.items() if c < 0x2E}
B_TXT_RSE.update({0x2E: "B_TRAINER2_CLASS", 0x2F: "B_TRAINER2_NAME", 0x30: "B_TRAINER2_LOSE_TEXT",
                  0x31: "B_TRAINER2_WIN_TEXT", 0x32: "B_PARTNER_CLASS", 0x33: "B_PARTNER_NAME",
                  0x34: "B_BUFF3"})
B_TXT_RS = {0: "B_BUFF1", 1: "B_BUFF2"}
B_TXT_RS.update({c: B_TXT[c + 3] for c in range(0x02, 0x22)})
B_TXT_RS[0x22] = "B_26"
B_TXT_RS.update({c: B_TXT[c + 4] for c in range(0x23, 0x2A)})
B_TXT_RS[0x2A] = "B_BUFF3"
PH_COMMON = ["UNKNOWN", "PLAYER", "STR_VAR_1", "STR_VAR_2", "STR_VAR_3", "KUN", "RIVAL", "VERSION"]
DIALECTS = {
    "frlg": {"b": B_TXT, "ph": PH_COMMON + ["MAGMA", "AQUA", "MAXIE", "ARCHIE", "GROUDON", "KYOGRE"]},
    "rse": {"b": B_TXT_RSE, "ph": PH_COMMON + ["AQUA", "MAGMA", "ARCHIE", "MAXIE", "KYOGRE", "GROUDON"]},
    "rs": {"b": B_TXT_RS, "ph": PH_COMMON + ["EVIL_TEAM", "GOOD_TEAM", "EVIL_LEADER", "GOOD_LEADER",
                                            "EVIL_LEGENDARY", "GOOD_LEGENDARY"]},
}


def _seq(t):
    if isinstance(t, dict):
        return [t[k] for k in sorted(k for k in t if isinstance(k, int))]
    return list(t or [])


def ir_source(ir, dialect):
    """A Gen 3 text IR list as one string: \n line, \v scroll, \f page,
    placeholders in pokeemerald's {BRACE} names."""
    d = DIALECTS[dialect]
    out = []
    for seg in _seq(ir):
        if not isinstance(seg, dict):
            continue
        t = seg.get("t")
        if t == "eos":
            break
        if t == "nl":
            out.append("\n")
        elif t == "scroll":
            out.append("\v")
        elif t == "para":
            out.append("\f")
        elif t == "text":
            out.append(seg.get("s", ""))
        elif t == "tag":
            out.append(seg.get("tag", ""))
        elif t == "strvar":
            out.append("{STR_VAR_%d}" % seg.get("n", 0))
        elif t in ("player", "rival"):
            out.append("{%s}" % t.upper())
        elif t == "ph":
            code = seg.get("code")
            name = seg.get("name") or (d["ph"][code] if isinstance(code, int) and code < len(d["ph"]) else None)
            out.append("{%s}" % (name or "PH_%02X" % (code or 0)))
        elif t == "bph":
            code = seg.get("code", 0)
            out.append("{%s}" % d["b"].get(code, "B_%s" % code))
        elif t == "dynamic":
            out.append("{DYNAMIC_%d}" % seg.get("n", 0))
        elif t == "ext":
            cmd = seg.get("cmd", 0)
            cmd = "%02X" % cmd if isinstance(cmd, int) else str(cmd)
            args = "".join("_%s" % a for a in _seq(seg.get("args")))
            out.append("{EXT_%s%s}" % (cmd, args))
        else:
            out.append("{%s}" % t)
    return "".join(out)


# ---- per-version loaders: {(kind, key): english}, plus dialogue {english: [labels]}

GEN_OF = {"red": 1, "blue": 1, "yellow": 1, "gold": 2, "silver": 2, "crystal": 2,
          "firered": 3, "leafgreen": 3, "ruby": 3, "sapphire": 3, "emerald": 3}
DIALECT_OF = {"firered": "frlg", "leafgreen": "frlg", "ruby": "rs", "sapphire": "rs", "emerald": "rse"}


def _read(path, assignments=False):
    with open(path, encoding="utf-8") as fh:
        body = fh.read()
    return LuaReader.load_assignments(body) if assignments else LuaReader.load(body)


def _maybe(path, **kw):
    if not os.path.isfile(path):
        return None
    try:
        return _read(path, **kw)
    except ValueError as err:
        print(f"lpk: warning: could not read {path}: {err}", file=sys.stderr)
        return None


def load_version(root, version):
    """Everything translatable in one imported game, or None if not imported."""
    base = os.path.join(root, version, "data", "generated")
    gen = GEN_OF[version]
    keyed, lines, dex, names = {}, {}, {}, {}

    def line(text, label):
        if isinstance(text, str) and text.strip():
            lines.setdefault(text, []).append(str(label))

    if gen in (1, 2):
        text = _maybe(os.path.join(base, "text.lua"))
        if text is None:
            return None
        pokemon = _maybe(os.path.join(base, "pokemon.lua")) or {}
        for sid, rec in pokemon.items():
            if isinstance(rec, dict) and isinstance(rec.get("name"), str):
                names[sid] = (rec["name"], rec.get("dex") or rec.get("index"))
        dex_labels = set()
        if gen == 1:
            # Gen 1 keeps the entry's words in the text table: dexEntry.text
            # is a label (_MewDexEntry) the Pokedex screen looks up there.
            for sid, rec in pokemon.items():
                e = isinstance(rec, dict) and rec.get("dexEntry")
                if isinstance(e, dict):
                    if isinstance(e.get("kind"), str) and e["kind"]:
                        dex[("dex_kind", sid)] = e["kind"]
                    label = e.get("text")
                    words = text.get(label) if isinstance(label, str) else None
                    if isinstance(words, str) and words:
                        dex[("dex_text", sid)] = words
                        dex_labels.add(label)
        # dialogue, minus the Pokedex entries (those live in the dex sheet)
        for k, v in text.items():
            if k not in dex_labels:
                line(v, k)
        for k, v in (_maybe(os.path.join(base, "rom_text.lua")) or {}).items():
            line(v, k)
        if gen == 1:
            pass
        else:
            entries = (_maybe(os.path.join(base, "pokedex.lua")) or {}).get("entries") or {}
            for sid, e in entries.items():
                if isinstance(e, dict):
                    for field, kind in (("kind", "dex_kind"), ("text", "dex_text"), ("text2", "dex_text2")):
                        if isinstance(e.get(field), str) and e[field]:
                            dex[(kind, sid)] = e[field]
        for reg, kind in (("moves", "moves"), ("items", "items")):
            for k, rec in (_maybe(os.path.join(base, reg + ".lua")) or {}).items():
                if isinstance(rec, dict) and isinstance(rec.get("name"), str) and rec["name"]:
                    keyed[(kind, k)] = rec["name"]
        trainers = _maybe(os.path.join(base, "trainers.lua")) or {}
        classes = trainers.get("classes") if gen == 2 else trainers
        for k, rec in (classes or {}).items():
            if isinstance(rec, dict) and isinstance(rec.get("name"), str) and rec["name"]:
                keyed[("trainers", k)] = rec["name"]
    else:
        g = os.path.join(base, "gba")
        text = _maybe(os.path.join(g, "scripts", "text.lua"), assignments=True)
        if text is None:
            return None
        dialect = DIALECT_OF[version]
        for label, ir in text.items():
            line(ir_source(ir, dialect), label)
        national = {}
        nat_path = os.path.join(g, "pokemon", "national.lua")
        if os.path.isfile(nat_path):
            with open(nat_path, encoding="utf-8") as fh:
                for a, b in re.findall(r"M\.toNational\[(\d+)\] = (\d+)", fh.read()):
                    national[int(a)] = int(b)
        for idx, name in (_maybe(os.path.join(g, "pokemon", "names.lua")) or {}).items():
            nat = national.get(idx, idx if idx <= 251 else None)
            if isinstance(name, str) and nat and name.strip("? ") and idx > 0:
                names["%03d" % nat] = (name, nat)
        for nat, e in (_maybe(os.path.join(g, "pokemon", "pokedex", "entries.lua")) or {}).items():
            if isinstance(e, dict) and isinstance(nat, int) and nat > 0:
                for field, kind in (("category", "dex_kind"), ("description", "dex_text"),
                                    ("description2", "dex_text2")):
                    if isinstance(e.get(field), str) and e[field]:
                        dex[(kind, "%03d" % nat)] = e[field]
        for idx, name in (_maybe(os.path.join(g, "pokemon", "move_names.lua")) or {}).items():
            if isinstance(name, str) and idx and name.strip("-"):
                keyed[("moves", str(idx))] = name
        items = (_maybe(os.path.join(g, "items", "pack.lua")) or {}).get("items") or {}
        for idx, rec in items.items():
            if idx and isinstance(rec, dict) and isinstance(rec.get("name"), str) and rec["name"].strip("?"):
                keyed[("items", str(idx))] = rec["name"]
    return {"keyed": keyed, "lines": lines, "dex": dex, "names": names, "gen": gen}


def _variants(datas, field):
    """(kind, key) -> {english: [versions]} across versions."""
    out = {}
    for v, d in datas.items():
        for k, english in d[field].items():
            out.setdefault(k, {}).setdefault(english, []).append(v)
    return out


def keyed_rows(datas, field, base, game):
    """Rows for id-keyed things (names, moves, items, dex).  One plain row when
    every version agrees; otherwise one ID#hash row per English variant.
    Returns {"base": rows, "ext": rows} -- ext holds variants only the
    non-base versions have."""
    out = {"base": [], "ext": []}
    for (kind, key), variants in sorted(_variants(datas, field).items(),
                                       key=lambda kv: (KIND_ORDER.index(kv[0][0]), _natural(kv[0][1]))):
        split = len(variants) > 1
        for english, versions in variants.items():
            k = f"{key}#{fnv1a(english)}" if split else key
            dest = "base" if any(v in base for v in versions) else "ext"
            out[dest].append([kind, k, game, english, "", " ".join(versions), ""])
    return out


def _natural(key):
    return (0, int(key), "") if str(key).isdigit() else (1, 0, str(key))


def dialogue_rows(datas, base, game, exclude=()):
    out = {"base": [], "ext": []}
    seen = {}
    for v, d in datas.items():
        for english, labels in d["lines"].items():
            seen.setdefault(english, [set(), []])
            seen[english][0].add(v)
            seen[english][1] += labels
    for english in sorted(seen, key=lambda e: sorted(seen[e][1])[0]):
        if english in exclude:
            continue
        versions, labels = seen[english]
        dest = "base" if versions & set(base) else "ext"
        uniq = sorted(set(labels))
        note = ", ".join(uniq[:3]) + (f" (+{len(uniq) - 3})" if len(uniq) > 3 else "")
        out[dest].append(["dialogue", "#" + fnv1a(english), game, english, "",
                          " ".join(v for v in datas if v in versions), note])
    return out


# which engine-text file a source path belongs to
def engine_home(rel):
    p = rel[len("src/"):] if rel.startswith("src/") else rel
    if LAUNCHER.search(p):
        return None
    low = p.lower()
    if "emerald" in low:
        return "emerald"
    if re.search(r"(^|/)(rse|rs)(/|_)|ruby|sapphire|hoenn", low):
        return "rs"
    if re.search(r"(^|/)(game3|gen3|g3u|gba|frlg)(/|$)|game3|gen3|firered|leafgreen", low):
        return "frlg"
    if "crystal" in low:
        return "crystal"
    if re.search(r"(^|/)gen2(/|$)|gen2|game2|gold|silver", low):
        return "gs"
    if re.search(r"yellow|pikachu", low):
        return "yellow"
    return "rb"


SPLIT_FILES = [
    ("01_red_blue", "Red / Blue"), ("02_yellow_exclusive", "Yellow: text not in Red/Blue"),
    ("03_gold_silver", "Gold / Silver"), ("04_crystal_exclusive", "Crystal: text not in Gold/Silver"),
    ("05_firered_leafgreen", "FireRed / LeafGreen"), ("06_ruby_sapphire", "Ruby / Sapphire"),
    ("07_emerald_exclusive", "Emerald: text not in Ruby/Sapphire"),
    ("08_names_gen1", "Gen 1 Pokemon names"), ("09_names_gen2", "Gen 2 Pokemon names"),
    ("10_names_gen3", "Gen 3 Pokemon names"),
    ("11_dex_gen1", "Gen 1 Pokedex (Red/Blue/Yellow)"), ("12_dex_gen2", "Gen 2 Pokedex (Gold/Silver/Crystal)"),
    ("13_dex_gen3", "Gen 3 Pokedex (Ruby/Sapphire/Emerald, FireRed/LeafGreen)"),
]
SPLIT_HEADER = ["kind", "key", "game", "english", "text", "versions", "notes"]


# --------------------------------------------------------------- split: writing
# split rewrites the files it owns and leaves the rest of the pack alone.  It
# remembers what it wrote in a manifest so a re-run can tell a translator's
# changes from its own: a row found somewhere else was moved there (and stays),
# a row found nowhere was deleted (and stays deleted until --restore).  The
# manifest sits in a "_" folder, which the mod and lpk.py never load.

MANIFEST_PATH = "_lpk/split.json"
RETIRED_DIR = "_orphaned/_retired"


def _key_id(kind, key, game):
    """A row's identity in the manifest: kind, key (as written in the CSV), game."""
    return "\t".join((kind, key, game))


def _rel(path, root):
    return os.path.relpath(path, root).replace(os.sep, "/")


def _cell(rec, i):
    return rec[i] if i is not None and i < len(rec) else ""


class _Table:
    """One CSV/TSV file of the pack, kept as raw records so it can be
    rewritten without disturbing what split doesn't own."""

    def __init__(self, path):
        with open(path, encoding="utf-8-sig", newline="") as fh:
            body = fh.read()
        self.delim = "\t" if path.lower().endswith(".tsv") else _sniff(body.split("\n", 1)[0])
        recs = list(csv.reader(io.StringIO(body), delimiter=self.delim))
        self.header = recs[0] if recs else []
        self.recs = recs[1:]
        cols = [h.strip().lower() for h in self.header]
        col = lambda *names: next((cols.index(n) for n in names if n in cols), None)
        self.k, self.key, self.g = col("kind"), col("key"), col("game")
        self.text = col(*TEXT_COLUMNS)
        self.cols = cols

    def ids(self):
        """(record index, key id) for every row naming a kind and key."""
        if self.k is None or self.key is None:
            return []
        return [(i, _key_id(_cell(r, self.k), _cell(r, self.key), _cell(r, self.g)))
                for i, r in enumerate(self.recs)]

    def translated(self):
        return [i for i, r in enumerate(self.recs) if _cell(r, self.text).strip()]


def _scan_pack(out_dir):
    """{relative path: _Table} for every CSV/TSV the pack loads, and
    {key id: [relative paths holding it]}.  Like the mod, folders starting
    with _ or . are skipped."""
    tables, where = {}, collections.defaultdict(list)
    for root, dirs, names in os.walk(out_dir):
        dirs[:] = sorted(d for d in dirs if not d.startswith(("_", ".")))
        for name in sorted(names):
            if name.lower().endswith((".csv", ".tsv")):
                path = os.path.join(root, name)
                rel = _rel(path, out_dir)
                t = tables[rel] = _Table(path)
                for _, kid in t.ids():
                    if rel not in where[kid]:
                        where[kid].append(rel)
    return tables, where


class _Translations:
    """Every translation already in the pack, wherever it is.  Matches on
    kind + key + game; a translation keyed by a plain id also fills that id's
    version-pinned rows (ID#hash), and vice versa when there's exactly one."""

    def __init__(self, tables):
        self.entries = []          # (relative path, record index, kind, key, game, text)
        self.exact, self.by_base, self.used = {}, {}, set()
        for rel in sorted(tables):
            t = tables[rel]
            if t.k is None or t.key is None or t.text is None:
                continue
            for i in t.translated():
                r = t.recs[i]
                e = (rel, i, _cell(r, t.k), _cell(r, t.key), _cell(r, t.g), _cell(r, t.text))
                self.entries.append(e)
                self.exact.setdefault(e[2:5], e)
                base = re.sub(r"#[0-9a-fA-F]{8}$", "", e[3])
                self.by_base.setdefault((e[2], base, e[4]), []).append(e)

    def take(self, kind, key, game):
        """The translation for a row (key as written in the CSV), or ""."""
        hit = self.exact.get((kind, key, game))
        if hit is None:
            base = re.sub(r"#[0-9a-fA-F]{8}$", "", key)
            cands = self.by_base.get((kind, base, game), [])
            plain = [c for c in cands if c[3] == base]
            if plain:
                hit = plain[0]
            elif len(cands) == 1 and key == base:
                hit = cands[0]
        if hit is None:
            return ""
        # every copy of the same key counts as carried, not orphaned
        for e in self.by_base.get((hit[2], re.sub(r"#[0-9a-fA-F]{8}$", "", hit[3]), hit[4]), []):
            if e[3] == hit[3] and e[5] == hit[5]:
                self.used.add(e[:2])
        self.used.add(hit[:2])
        return hit[5]

    def unused(self, rels):
        """Translations in the given files that no row took."""
        return [e for e in self.entries if e[0] in rels and e[:2] not in self.used]


def _load_manifest(out_dir, tables):
    """The manifest, or for a pack split wrote before manifests existed, one
    rebuilt from the flat sheets (01_ ... 13_) in the pack root."""
    path = os.path.join(out_dir, *MANIFEST_PATH.split("/"))
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            m = json.load(fh)
        return {"files": m.get("files", {}), "pins": m.get("pins", {}), "deleted": m.get("deleted", {}),
                "legacy": False}
    files = {}
    for name, _ in SPLIT_FILES:
        rel = name + ".csv"
        if rel in tables:
            files[rel] = [kid for _, kid in tables[rel].ids()]
    return {"files": files, "pins": {}, "deleted": {}, "legacy": bool(files)}


def _write_rows(path, rows, delim):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=delim)
        w.writerow(SPLIT_HEADER)
        for r in rows:
            w.writerow([r[0], csv_escape(r[1]), r[2], csv_escape(r[3]), r[4], r[5], r[6]])


def _save_orphans(out_dir, rel, entries, tables, delim):
    """Append orphaned translations to _orphaned/<rel> (never overwrite)."""
    path = os.path.join(out_dir, "_orphaned", *rel.split("/"))
    old = _Table(path) if os.path.isfile(path) else None
    seen = {tuple(r[:5]) for r in (old.recs if old else [])}
    rows = list(old.recs) if old else []
    for (_, i, kind, key, game, text) in entries:
        t = tables[rel]
        rec = t.recs[i]
        col = lambda name: _cell(rec, t.cols.index(name)) if name in t.cols else ""
        row = [kind, key, game, col("english"), text, col("versions"), col("notes")]
        if tuple(row[:5]) not in seen:
            seen.add(tuple(row[:5]))
            rows.append(row)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=delim)
        w.writerow(SPLIT_HEADER)
        w.writerows(rows)


def _refresh_in_place(path, table, fresh):
    """Update English/versions/notes of split's rows in a translator's own
    file; every other cell, row and the row order stay as they are."""
    changed = False
    for i, kid in table.ids():
        row = fresh.get(kid)
        if row is None:
            continue
        rec = table.recs[i]
        for name, value in (("english", csv_escape(row[3])), ("versions", row[5]), ("notes", row[6])):
            if name in table.cols:
                c = table.cols.index(name)
                while len(rec) <= c:
                    rec.append("")
                if rec[c] != value:
                    rec[c] = value
                    changed = True
    if changed:
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh, delimiter=table.delim)
            w.writerow(table.header)
            w.writerows(table.recs)
    return changed


def write_split(out_dir, planned, delim, layout, restore=None, titles=None, today=None):
    """Write split's files into a pack, keeping every translation.

    planned: {relative path: rows} from lpk_layout.organize (rows are
    [kind, key, game, english, text, versions, notes], unescaped).
    Returns a report dict (counts and lists) for the caller to print."""
    import datetime
    tables, where = _scan_pack(out_dir)
    man = _load_manifest(out_dir, tables)
    trans = _Translations(tables)
    last = collections.defaultdict(set)        # key id -> files split wrote it to
    for rel, kids in man["files"].items():
        for kid in kids:
            last[kid].add(rel)
    pins = dict(man["pins"])
    # key id -> files it was deleted from (a key split writes twice can lose either copy)
    deleted = {kid: set([v] if isinstance(v, str) else v) for kid, v in man["deleted"].items()}
    owned_before = set(man["files"])
    rep = collections.Counter()
    lists = collections.defaultdict(list)

    if restore:
        want = restore.replace("\\", "/").strip("/")
        for kid, rels in deleted.items():
            back = {r for r in rels if want == "all" or r == want}
            rep["restored"] += len(back)
            rels -= back

    own = collections.OrderedDict((rel, []) for rel in planned)    # canonical rows per file
    moved_in = collections.defaultdict(list)                       # translator-moved rows per file
    new_pins, new_deleted, dupes = {}, collections.defaultdict(set), set()
    copies = collections.Counter(_key_id(r[0], csv_escape(r[1]), r[2])
                                 for rows in planned.values() for r in rows)
    for canon, rows in planned.items():
        for row in rows:
            kid = _key_id(row[0], csv_escape(row[1]), row[2])
            found = where.get(kid, [])
            mine = last.get(kid, set())
            # places split didn't put this row: where a translator put it
            theirs = [f for f in found if f not in mine]
            pin = pins.get(kid)
            if len(found) > copies[kid]:
                dupes.add(kid)
            if canon in deleted.get(kid, ()):
                # back only if the translator added it again: in this file, or
                # somewhere split doesn't put it (not split's other copy)
                back = [f for f in found if f == canon or f not in mine]
                if not back:
                    new_deleted[kid].add(canon)
                    rep["kept_deleted"] += 1
                    continue
                rep["undeleted"] += 1
                dest = canon if canon in back else pin if pin in back else back[0]
            elif pin and pin in found:
                dest = pin
            elif canon in mine:                 # split wrote it here last time
                if canon in found:
                    dest = canon
                elif theirs:
                    dest = theirs[0]            # the translator moved it
                else:
                    new_deleted[kid].add(canon)  # the translator deleted it
                    rep["newly_deleted"] += 1
                    continue
            elif any(f in mine for f in found):
                dest = canon                    # the layout changed: follow it
                rep["relocated"] += 1
            elif theirs:
                dest = theirs[0]
            elif mine:
                new_deleted[kid].add(canon)
                rep["newly_deleted"] += 1
                continue
            else:
                dest = canon                    # new text
            if dest == canon:
                own[canon].append(row)
            else:
                new_pins[kid] = dest
                moved_in[dest].append(row)
                rep["pinned"] += 1

    # fill in translations
    for rows in list(own.values()) + list(moved_in.values()):
        for row in rows:
            text = trans.take(row[0], csv_escape(row[1]), row[2])
            if text:
                row[4] = text
                rep["kept"] += 1

    # which files split writes now: its canonical files with rows (every
    # planned file in the flat layout), plus files it owned before that a
    # translator moved rows into
    write = collections.OrderedDict()
    for rel, rows in own.items():
        if rows or layout == "flat" or moved_in.get(rel):
            write[rel] = rows + moved_in.get(rel, [])
    foreign = {}
    for rel, rows in moved_in.items():
        if rel in write:
            continue
        if rel in owned_before:
            write[rel] = rows
        else:
            foreign[rel] = rows

    # orphans: translations in files split rewrites or retires that no row took
    rewritten = set(write) | owned_before
    orphans = collections.defaultdict(list)
    for e in trans.unused({rel for rel in rewritten if rel in tables}):
        orphans[e[0]].append(e)
    for rel, entries in orphans.items():
        _save_orphans(out_dir, rel, entries, tables, delim)
        rep["orphaned"] += len(entries)

    # retire files split owned that it no longer writes
    stamp = (today or datetime.date.today()).isoformat()
    for rel in sorted(owned_before - set(write)):
        path = os.path.join(out_dir, *rel.split("/"))
        if not os.path.isfile(path):
            continue
        if tables.get(rel) and tables[rel].translated():
            dest = os.path.join(out_dir, *RETIRED_DIR.split("/"), stamp, *rel.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(path, dest)
            lists["retired_kept"].append(rel)
        else:
            os.remove(path)
            lists["retired_removed"].append(rel)
        d = os.path.dirname(path)
        while os.path.normpath(d) != os.path.normpath(out_dir) and os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
            d = os.path.dirname(d)

    # write split's files, refresh rows it placed in translators' files
    for rel, rows in write.items():
        _write_rows(os.path.join(out_dir, *rel.split("/")), rows, delim)
    fresh_by_file = {rel: {_key_id(r[0], csv_escape(r[1]), r[2]): r for r in rows} for rel, rows in foreign.items()}
    for rel, fresh in fresh_by_file.items():
        if _refresh_in_place(os.path.join(out_dir, *rel.split("/")), tables[rel], fresh):
            lists["refreshed"].append(rel)

    manifest = {
        "format": 1,
        "layout": layout,
        "files": {rel: [_key_id(r[0], csv_escape(r[1]), r[2]) for r in rows] for rel, rows in write.items()},
        "pins": dict(sorted(new_pins.items())),
        "deleted": {kid: sorted(rels) for kid, rels in sorted(new_deleted.items())},
    }
    mpath = os.path.join(out_dir, *MANIFEST_PATH.split("/"))
    os.makedirs(os.path.dirname(mpath), exist_ok=True)
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
        fh.write("\n")

    rep["duplicates"] = len(dupes)
    return {"counts": rep, "lists": lists, "written": write, "foreign": foreign, "legacy": man["legacy"]}


def option_label(name):
    """What the LANGUAGE row shows: the options menu draws with the game's
    own font, so plain A-Z, digits and spaces only, kept short."""
    import unicodedata
    folded = unicodedata.normalize("NFKD", name or "")
    folded = "".join(c for c in folded if not unicodedata.combining(c)).upper()
    folded = re.sub(r"[^A-Z0-9 ]+", " ", folded)
    folded = re.sub(r" +", " ", folded).strip()
    # the Gen 1 value column fits 16 glyphs (longer scrolls when selected);
    # trim at a word break rather than mid-word
    if len(folded) > 16:
        cut = folded[:17].rsplit(" ", 1)[0]
        folded = cut if cut else folded[:16]
    return folded or "MY LANGUAGE"


def pack_json_template(name, label, author):
    """pack.json for a new project.  Every setting is present with a neutral
    value and a _comment_ line beside it; the mod ignores _comment_ keys and
    blank values."""
    return {
        "name": name,
        "_comment_name": "The language's own name, e.g. \"Español\". Shown in the log.",
        "label": label,
        "_comment_label": "What OPTIONS > LANGUAGE shows. Plain A-Z, digits and spaces only "
                          "(that menu uses the game's original font). Up to 16 fit on "
                          "Red/Blue/Yellow; Gold/Silver/Crystal's column is narrower.",
        "author": author,
        "_comment_author": "Credit for the translation.",
        "version": "0.1.0",
        "_comment_version": "Raise it each time you share an update: `lpk.py release` names the "
                            "pack mod after it, and players' games compare it.",
        "mod_name": "",
        "_comment_mod_name": "The name `lpk.py release` gives your pack in the Mod Manager. "
                             "Leave \"\" for \"<name> (ELP generated)\".",
        "font": "",
        "_comment_font": "Leave \"\" for the game's original font (A-Z, no accents). "
                         "\"pixel\" switches to the bundled font with accents, Cyrillic, kana and CJK. "
                         "Or point at your own TTF in this folder: "
                         "{\"file\": \"myfont.ttf\", \"size\": 15}",
        "extends": "",
        "_comment_extends": "Another pack's folder name to build on (e.g. \"es\" for an es_mx "
                            "pack that only changes a few lines). Leave \"\" for none.",
        "_comment_files": "Every .csv/.tsv/.json in this folder and its subfolders loads, in order of "
                          "its path (G1/... before G2/...); a later file wins when two define the same "
                          "key. Folders starting with _ (such as _orphaned) are ignored. Move or delete "
                          "rows freely: `lpk.py split` remembers where you put them.",
    }


def cmd_split(args):
    game = args.game
    root = imports_root(args.cache, game)
    if root != game:
        print(f"lpk: reading imported games from {root}", file=sys.stderr)
    datas = {}
    for v in GEN_OF:
        d = load_version(root, v)
        if d is None:
            print(f"lpk: {v}: not imported (no data under {os.path.join(root, v)}) - skipped",
                  file=sys.stderr)
        else:
            datas[v] = d
            print(f"lpk: {v}: {len(d['lines'])} lines, {len(d['names'])} names, "
                  f"{len(d['dex'])} dex fields", file=sys.stderr)
    if not datas:
        print("lpk: no imported games found in the game folder or your user data folder "
              "(%APPDATA%\\pokemon-love2d); import them in the game first, or pass --cache",
              file=sys.stderr)
        return 1

    def pick(*vs):
        return {v: datas[v] for v in vs if v in datas}

    files = {name: [] for name, _ in SPLIT_FILES}

    # engine-authored text, by source path (all games: split sorts them itself)
    source = engine_source(args)
    engine = (engine_strings(source, all_games=True) if source else None) or []
    home_file = {"rb": "01_red_blue", "yellow": "02_yellow_exclusive", "gs": "03_gold_silver",
                 "crystal": "04_crystal_exclusive", "frlg": "05_firered_leafgreen",
                 "rs": "06_ruby_sapphire", "emerald": "07_emerald_exclusive"}
    home_game = {"rb": "", "yellow": "", "gs": "gen2", "crystal": "gen2",
                 "frlg": "gen3", "rs": "gen3", "emerald": "gen3"}
    shared_engine = set()
    placed = {}
    for text, where in engine:
        home = engine_home(where)
        if home is None:
            continue
        placed.setdefault(text, (home, where))
    for text, (home, where) in placed.items():
        if home in ("rb", "yellow"):
            shared_engine.add(text)
    for text, (home, where) in sorted(placed.items(), key=lambda kv: kv[1][1]):
        # a gen2/gen3 line already in the shared (unscoped) set is covered there
        if home not in ("rb", "yellow") and text in shared_engine:
            continue
        files[home_file[home]].append(["strings", text, home_game[home], text, "", "", where])
    if source:
        for sid, label in status_labels(source):
            files["01_red_blue"].append(["statuses", sid, "gen1", label, "", "", ""])

    # script text and id-keyed names, per family
    families = [
        (("red", "blue"), ("yellow",), "gen1", "01_red_blue", "02_yellow_exclusive"),
        (("gold", "silver"), ("crystal",), "gen2", "03_gold_silver", "04_crystal_exclusive"),
        (("firered", "leafgreen"), (), "gen3", "05_firered_leafgreen", None),
        (("ruby", "sapphire"), ("emerald",), "gen3", "06_ruby_sapphire", "07_emerald_exclusive"),
    ]
    for base, ext, gen, base_file, ext_file in families:
        group = pick(*(base + ext))
        if not group:
            continue
        base_present = [v for v in base if v in group] or list(group)[:1]
        d_rows = dialogue_rows(group, base_present, gen)
        k_rows = keyed_rows(group, "keyed", base_present, gen)
        files[base_file] += d_rows["base"] + k_rows["base"]
        if ext_file:
            files[ext_file] += d_rows["ext"] + k_rows["ext"]

    for gen, vs, name_file, dex_file in (
            ("gen1", ("red", "blue", "yellow"), "08_names_gen1", "11_dex_gen1"),
            ("gen2", ("gold", "silver", "crystal"), "09_names_gen2", "12_dex_gen2"),
            ("gen3", ("ruby", "sapphire", "emerald", "firered", "leafgreen"), "10_names_gen3", "13_dex_gen3")):
        group = pick(*vs)
        if not group:
            continue
        # names: id -> (name, national #)
        flat = {v: {"n": {("species", sid): nm for sid, (nm, _n) in d["names"].items()}}
                for v, d in group.items()}
        nat = {}
        for d in group.values():
            for sid, (_nm, n) in d["names"].items():
                nat[sid] = n
        rows = keyed_rows(flat, "n", list(group), gen)["base"]
        for r in rows:
            r[6] = f"#{nat.get(r[1].split('#')[0]) or ''}"
        rows.sort(key=lambda r: (nat.get(r[1].split("#")[0]) or 9999, r[1]))
        files[name_file] = rows
        drows = keyed_rows(group, "dex", list(group), gen)["base"]
        name_of = {}
        for d in group.values():
            for sid, (nm, _n) in d["names"].items():
                name_of[sid] = nm
        for r in drows:
            r[6] = name_of.get(r[1].split("#")[0], "")
        drows.sort(key=lambda r: (nat.get(r[1].split("#")[0]) or 9999, KIND_ORDER.index(r[0]), r[1]))
        files[dex_file] = drows

    out_dir = args.output
    os.makedirs(out_dir, exist_ok=True)
    delim = ";" if args.semicolon else ","
    print()

    # pack.json: written once.  A re-run after a game update refreshes the
    # sheets but never overwrites settings the translator has filled in.
    pj = os.path.join(out_dir, "pack.json")
    folder = os.path.basename(os.path.normpath(os.path.abspath(out_dir)))
    name = args.name or folder
    if os.path.exists(pj) and not args.force_pack_json:
        print("  pack.json          kept (already there; --force-pack-json replaces it)")
    else:
        label = option_label(args.label or name)
        with open(pj, "w", encoding="utf-8") as fh:
            json.dump(pack_json_template(name, label, args.author or ""), fh,
                      ensure_ascii=False, indent=2)
            fh.write("\n")
        print(f"  pack.json          name \"{name}\", label \"{label}\" - edit to taste")
    layout = args.layout
    planned = lpk_layout.organize(collections.OrderedDict((n, files[n]) for n, _ in SPLIT_FILES), layout,
                                  root=root)
    title_of = {lpk_layout.sheet_path(n, layout): t for n, t in SPLIT_FILES}
    rep = write_split(out_dir, planned, delim, layout, restore=args.restore)
    c, lists = rep["counts"], rep["lists"]
    width = max([len(r) for r in rep["written"]] + [20])
    for rel, rows in rep["written"].items():
        per = collections.Counter(r[0] for r in rows)
        detail = ", ".join(f"{k} {per[k]}" for k in KIND_ORDER if k in per)
        kept = sum(1 for r in rows if r[4])
        title = title_of.get(rel, "")
        print(f"  {rel:<{width}}  {len(rows):6}  {title}" + (f"  ({detail})" if detail else "")
              + (f"  [{kept} translated]" if kept else ""))
    print(f"\nwrote {os.path.abspath(out_dir)} ({layout} layout)")
    if rep["legacy"]:
        print("note: upgraded a pack made by an older lpk.py; split now tracks its files in "
              f"{MANIFEST_PATH}")
    if c["kept"]:
        print(f"kept {c['kept']} existing translations")
    if c["relocated"]:
        print(f"moved {c['relocated']} rows to their new files")
    if c["pinned"]:
        print(f"kept {c['pinned']} rows where you moved them")
    if lists["refreshed"]:
        print("refreshed the English of split's rows in your own files: " + ", ".join(lists["refreshed"]))
    if c["newly_deleted"] or c["kept_deleted"]:
        print(f"left out {c['newly_deleted'] + c['kept_deleted']} rows you deleted"
              + (f" ({c['newly_deleted']} newly)" if c["newly_deleted"] else "")
              + "; `--restore PATH` or `--restore all` brings them back")
    if c["restored"]:
        print(f"restored {c['restored']} deleted rows")
    if c["undeleted"]:
        print(f"{c['undeleted']} rows you deleted earlier are back (you added them again)")
    if c["duplicates"]:
        print(f"note: {c['duplicates']} rows appear in more than one file; `lpk.py check` lists them")
    if lists["retired_kept"]:
        print(f"note: {len(lists['retired_kept'])} files split no longer writes held translations; "
              f"their translations moved to the new files and the old files are in "
              f"{os.path.join(out_dir, *RETIRED_DIR.split('/'))}: " + ", ".join(lists["retired_kept"]))
    if lists["retired_removed"]:
        print(f"removed {len(lists['retired_removed'])} untranslated files split no longer writes: "
              + ", ".join(lists["retired_removed"]))
    if c["orphaned"]:
        print(f"note: {c['orphaned']} translations no longer match any row (the English changed "
              f"in a game update); they're saved in "
              f"{os.path.join(out_dir, '_orphaned')} (the mod ignores that folder)")
    print("note: the english column holds text from your ROMs. Use `lpk.py release` "
          "to strip it before sharing a pack.")
    print("note: Gen 3 rows are ready to translate; the mod applies them once it supports "
          "FireRed/LeafGreen/Ruby/Sapphire/Emerald.")
    return 0


# --------------------------------------------------------------- release

# A released pack is a mod of its own (elp_<pack id>) that depends on this
# one, so updating Easy Language Packs never touches it.  Its main.lua is
# tools/pack_mod_main.lua, which hands the pack to registerPack (pack api 1,
# added in 1.2.0).
LOADER_ID = "easy_language_packs"
LOADER_REPO = "kakhonn/g1r-easy-language-packs"
PACK_API_SINCE = "1.2.0"
PACK_MOD_PREFIX = "elp_"
PACK_MOD_MAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pack_mod_main.lua")
_MOD_ID_RE = re.compile(r"^[a-z0-9_-]+$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.+-]*)?$")


def _loader_manifest():
    """This mod's manifest (lpk.py ships in its tools/), for the games and
    engine range a pack mod should match; defaults when lpk.py runs alone."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "manifest.json")
    try:
        with open(path, encoding="utf-8-sig") as fh:
            m = json.load(fh)
    except (OSError, ValueError):
        m = {}
    if m.get("id") != LOADER_ID:
        m = {}
    return {"games": m.get("games") or ["gen1", "gen2"],
            "game_version": m.get("game_version") or ">=0.0.0-0 <2.0.0"}


def pack_mod_manifest(pack_id, meta, github=None):
    name = meta.get("name") if isinstance(meta.get("name"), str) and meta.get("name").strip() else pack_id
    mod_name = meta.get("mod_name")
    if not (isinstance(mod_name, str) and mod_name.strip()):
        mod_name = f"{name} (ELP generated)"
    author = meta.get("author") if isinstance(meta.get("author"), str) else ""
    loader = _loader_manifest()
    manifest = {
        "id": PACK_MOD_PREFIX + pack_id,
        "name": mod_name.strip(),
        "version": meta["version"],
        "api": 2,
        "entry": "main.lua",
        "profile": "content",
        "category": "UI",
        "games": loader["games"],
        "game_version": loader["game_version"],
        "dependencies": [f"{LOADER_ID}@^{PACK_API_SINCE}#{LOADER_REPO}"],
        "description": f"{name} for Easy Language Packs" + (f", by {author.strip()}" if author.strip() else "")
                       + ". Made with lpk.py; pick it under OPTIONS > LANGUAGE.",
    }
    if github:
        manifest["github"] = github
    return manifest


def _strip_pack(src, dst):
    """Copy a pack without its reference columns, untranslated rows and
    working folders; returns how many data files were stripped."""
    stripped = 0
    for root, dirs, names in os.walk(src):
        # _orphaned/, _lpk/ and the like are working files the mod never loads
        dirs[:] = [d for d in dirs if not d.startswith((".", "_"))]
        for name in names:
            s = os.path.join(root, name)
            d = os.path.join(dst, os.path.relpath(s, src))
            os.makedirs(os.path.dirname(d), exist_ok=True)
            low = name.lower()
            if low.endswith((".csv", ".tsv")):
                with open(s, encoding="utf-8-sig", newline="") as fh:
                    body = fh.read()
                delim = "\t" if low.endswith(".tsv") else _sniff(body.split("\n", 1)[0])
                recs = list(csv.reader(io.StringIO(body), delimiter=delim))
                if not recs:
                    shutil.copy2(s, d)
                    continue
                header = [h.strip().lower() for h in recs[0]]
                keep = [i for i, h in enumerate(header) if h not in REFERENCE_COLUMNS]
                text_i = next((i for i, h in enumerate(header) if h in TEXT_COLUMNS), None)
                with open(d, "w", encoding="utf-8-sig", newline="") as fh:
                    w = csv.writer(fh, delimiter=delim)
                    w.writerow([recs[0][i] for i in keep])
                    for rec in recs[1:]:
                        # untranslated rows carry nothing worth shipping
                        if text_i is not None and (text_i >= len(rec) or rec[text_i] == ""):
                            continue
                        w.writerow([rec[i] if i < len(rec) else "" for i in keep])
                stripped += 1
            elif low.endswith(".json") and low != "pack.json":
                with open(s, encoding="utf-8-sig") as fh:
                    raw = fh.read()
                doc = json.loads(_JSON_V.sub(r"\1\\u000b", raw))
                if isinstance(doc, dict):
                    doc = {k: ({kk: vv for kk, vv in v.items() if vv != ""} if isinstance(v, dict) else v)
                           for k, v in doc.items() if not k.startswith("_comment")}
                elif isinstance(doc, list):
                    doc = [{k: v for k, v in r.items() if k not in REFERENCE_COLUMNS}
                           for r in doc if isinstance(r, dict) and r.get("text")]
                with open(d, "w", encoding="utf-8") as fh:
                    json.dump(doc, fh, ensure_ascii=False, indent=2)
                    fh.write("\n")
                stripped += 1
            else:
                shutil.copy2(s, d)
    return stripped


def cmd_release(args):
    src = os.path.abspath(args.pack.rstrip("/\\"))
    if not os.path.isdir(src):
        print(f"lpk: {src} is not a pack folder", file=sys.stderr)
        return 2
    if args.folder:
        dst = os.path.abspath(args.output)
        if os.path.exists(dst):
            if not args.force:
                print(f"lpk: {dst} exists (use --force)", file=sys.stderr)
                return 2
            shutil.rmtree(dst)
        stripped = _strip_pack(src, dst)
        print(f"wrote {dst} ({stripped} data files stripped of reference columns and empty rows)")
        return 0

    pack_id = args.id or os.path.basename(src)
    if not _MOD_ID_RE.match(pack_id):
        print(f"lpk: '{pack_id}' can't be part of a mod id (use lowercase a-z, 0-9, _ and -); "
              "pass --id, e.g. --id es", file=sys.stderr)
        return 2
    try:
        with open(os.path.join(src, "pack.json"), encoding="utf-8-sig") as fh:
            meta = json.load(fh)
    except FileNotFoundError:
        meta = {}
    except ValueError as e:
        print(f"lpk: pack.json: {e}", file=sys.stderr)
        return 2
    if not isinstance(meta, dict):
        meta = {}
    version = meta.get("version")
    if not (isinstance(version, str) and _VERSION_RE.match(version.strip())):
        print('lpk: set "version" in pack.json (e.g. "0.1.0"); the pack mod is versioned by it',
              file=sys.stderr)
        return 2
    meta["version"] = version.strip()
    manifest = pack_mod_manifest(pack_id, meta, args.github)
    mod_id = manifest["id"]

    out = os.path.abspath(args.output)
    zip_path = out if out.lower().endswith(".zip") else os.path.join(out, f"{mod_id}-{meta['version']}.zip")
    if os.path.exists(zip_path) and not args.force:
        print(f"lpk: {zip_path} exists (use --force, or raise \"version\" in pack.json)", file=sys.stderr)
        return 2
    with open(PACK_MOD_MAIN, encoding="utf-8") as fh:
        main_lua = fh.read()
    with tempfile.TemporaryDirectory() as tmp:
        stage = os.path.join(tmp, mod_id)
        stripped = _strip_pack(src, os.path.join(stage, "pack"))
        with open(os.path.join(stage, "manifest.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        with open(os.path.join(stage, "main.lua"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(main_lua)
        os.makedirs(os.path.dirname(zip_path), exist_ok=True)
        # one top folder, as the game's Import mod .zip expects
        with zipfile.ZipFile(zip_path + ".tmp", "w", zipfile.ZIP_DEFLATED) as z:
            for root, dirs, names in os.walk(stage):
                dirs.sort()
                for name in sorted(names):
                    full = os.path.join(root, name)
                    z.write(full, os.path.relpath(full, tmp).replace(os.sep, "/"))
        os.replace(zip_path + ".tmp", zip_path)
    print(f"wrote {zip_path}: mod {mod_id} {meta['version']} \"{manifest['name']}\" "
          f"({stripped} data files stripped of reference columns and empty rows)")
    print("players import it in the game: MODS > Import mod .zip "
          "(it needs Easy Language Packs " + PACK_API_SINCE + " or newer)")
    return 0


def main(argv=None):
    # a pack's name can be any script; output redirected to a file on Windows
    # uses the ANSI code page, which would otherwise crash on it
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def engine_args(sp):
        g = sp.add_argument_group("engine text (menus, battle messages)")
        g.add_argument("--engine-version", metavar="X.Y.Z",
                       help="G1R release to take engine text from when the game folder has no "
                            "src/ (default: the latest release)")
        g.add_argument("--engine-source", metavar="DIR",
                       help="a folder containing G1R's src/ (e.g. an unzipped Source code download)")
        g.add_argument("--no-download", action="store_true",
                       help="never download G1R's source; skip engine text if src/ is missing")
        g.add_argument("--refresh-engine", action="store_true",
                       help="download G1R's source again even if a cached copy exists")

    def sources(sp):
        sp.add_argument("--game", help="game folder (engine text comes from its src/ when "
                        "present, otherwise from a downloaded copy)")
        sp.add_argument("--cache", help="folder holding the imported ROM data (default: the "
                        "--game folder for portable installs, then %%APPDATA%%\\pokemon-love2d, "
                        "then %%APPDATA%%\\LOVE\\*)")
        sp.add_argument("--version", choices=["red", "blue", "yellow"],
                        help="which imported game to read (default: first found)")
        engine_args(sp)

    t = sub.add_parser("template", help="build a fill-in CSV/JSON of every string")
    sources(t)
    t.add_argument("-o", "--output", required=True, help="output .csv, .tsv or .json")
    t.add_argument("--merge", help="existing pack folder whose translations to carry over")
    t.add_argument("--semicolon", action="store_true", help="use ; as the CSV delimiter")
    t.add_argument("--all-games", action="store_true",
                   help="also include launcher text and engine text only used by "
                        "Gold/Silver/Crystal and the GBA games")

    sp = sub.add_parser("split", help="one CSV per game family + per-gen names and Pokedex")
    sp.add_argument("--game", required=True, help="game folder (for portable installs it holds "
                    "the red/, gold/, firered/... data folders)")
    engine_args(sp)
    sp.add_argument("--cache", help="folder holding red/, gold/, ... (default: the game folder "
                    "on a portable install, else your user data folder, %%APPDATA%%\\pokemon-love2d)")
    sp.add_argument("-o", "--output", required=True, help="output folder for the CSVs")
    sp.add_argument("--semicolon", action="store_true", help="use ; as the CSV delimiter")
    sp.add_argument("--name", help="language name for pack.json (default: the output folder's name)")
    sp.add_argument("--label", help="LANGUAGE menu label for pack.json (default: from --name, A-Z only)")
    sp.add_argument("--author", help="credit line for pack.json")
    sp.add_argument("--layout", choices=lpk_layout.LAYOUTS, default=lpk_layout.DEFAULT_LAYOUT,
                    help="organized: one folder per generation (default); flat: 13 sheets in the pack root")
    sp.add_argument("--restore", metavar="PATH|all",
                    help="bring back rows you deleted from PATH (relative to the pack), or from every file")
    sp.add_argument("--force-pack-json", action="store_true",
                    help="replace an existing pack.json with a fresh template")

    c = sub.add_parser("check", help="validate a pack folder")
    c.add_argument("pack", help="the pack folder (packs/<id>)")
    sources(c)

    r = sub.add_parser("release", help="make a shareable pack mod .zip (no English/notes columns)")
    r.add_argument("pack", help="the pack folder (packs/<id>)")
    r.add_argument("-o", "--output", required=True,
                   help="folder for elp_<id>-<version>.zip (or a .zip path); with --folder, the copy's folder")
    r.add_argument("--folder", action="store_true",
                   help="write a plain pack folder instead of a mod .zip")
    r.add_argument("--id", help="pack id, so the mod is elp_<id> (default: the pack folder's name)")
    r.add_argument("--github", metavar="OWNER/REPO",
                   help="your repository, for the game's update checks on your pack")
    r.add_argument("--force", action="store_true", help="replace an existing output")

    args = p.parse_args(argv)
    return {"template": cmd_template, "split": cmd_split, "check": cmd_check,
            "release": cmd_release}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
