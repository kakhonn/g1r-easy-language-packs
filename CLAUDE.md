# CLAUDE.md: Easy Language Packs

Context for Claude Code sessions. This project was built in an earlier
claude.ai conversation; this file carries over what that conversation
learned. Read it before changing anything.

## What this is

A mod for **G1R Deluxe / Gen1Recomp** (https://github.com/bryanthaboi/gen1recomp,
a LÖVE2D recreation of Pokémon Red/Blue/Yellow, Gold/Silver/Crystal and the GBA
games). It replaces the game's text with translations from plain CSV/JSON
"language packs", switchable live from **OPTIONS > LANGUAGE**. It also ships
`tools/lpk.py`, a stdlib-only Python tool that generates the translation
sheets, checks packs and strips ROM text for release.

- Mod id `easy_language_packs`, name "Easy Language Packs", repo
  `kakhonn/g1r-easy-language-packs` (owner's handle: kakhonn). Version in
  `manifest.json`.
- Supported in-game: Gen 1 and Gen 2. Gen 3 sheets are generated, but the mod
  doesn't apply them yet (`manifest.json` games = gen1, gen2).
- The earlier build had id `language_pack`, and the manifest declares it as a
  conflict. Don't reuse that id.

## Layout

```
main.lua            entry: pack list, LANGUAGE option + OPTIONS row, applies at game.ready
lib/pack.lua        reads pack folders (CSV/TSV/JSON, pack.json, extends, game column)
lib/live.lua        applies a pack straight onto live game data; remembers originals; restore()
lib/hash.lua        FNV-1a 32-bit -> 8 hex digits (must match lpk.fnv1a)
lib/csv.lua, json.lua  pure-Lua parsers (the mod sandbox has neither)
tools/lpk.py        template / split / check / release (Python 3 stdlib only, runs on Windows)
tools/lpk_layout.py where split puts each row (the pack layout); imported by lpk.py
tools/pack_mod_main.lua  entry chunk of every pack mod `lpk.py release` writes
packs/_example/     the only pack allowed in the repo ("Pirate" sample, example: true)
tests/              ROM-free tests: tests/run.py, tests/lua/*_test.lua, tests/test_lpk.py
deploy.py           copy the mod into a game install for testing
.github/workflows   release.yml (from the engine's modkit) + tests.yml
```

`tests/`, `.github/`, `deploy.py`, `CLAUDE.md` and the git dotfiles are
`export-ignore` in `.gitattributes`, so the release zip holds only the mod.

## Hard rules

1. **Never commit ROM-derived text.** Every generated sheet's `english`
   column (and dialogue, names, Pokédex text generally) comes from the
   player's ROMs. `.gitignore` excludes `packs/*` except `_example`,
   `language_templates/`, `_orphaned/`, `dist/`. Tests use synthetic records
   or the engine's ROM-free `tests/fixture_data`, never real game data.
   Released packs go through `lpk.py release` (strips `english`/`notes`/
   `versions` and empty rows; fingerprinted keys keep no English).
2. **Keep the Lua and Python readers in lockstep**: kinds and aliases,
   CSV escapes (`\n \v \f \t \r \\`), the comment rule (kind starting `#`,
   or key starting `#` unless it's `#` + 8 hex), `game` parsing, blank
   `extends` ignored, JSON `\v` accepted, the FNV-1a hash, file load order
   (recursive, `_`/`.` folders skipped, sorted by whole relative path so
   `dex.csv` < `dex/a.csv`). `tests/test_lpk.py` and the Lua tests pin the
   hash to published FNV vectors and both check the file order.
3. **ENGLISH must restore the original data exactly.** `live.lua` records
   every slot before its first write; the live tests deep-compare all data
   after switching back. Any new kind must go through `Live:set`.
4. **README credit line is required** by G1R's GPL additional terms:
   "Based on the Pokemon Gen 1 Recompilation Project by BOIS CLUB GAMES, LLC
   (https://github.com/bryanthaboi/gen1recomp)". Project is GPL-3.0. The
   README also carries a "Made with Claude" disclosure; keep it accurate.
5. Run `py tests/run.py --engine <gen1recomp checkout>` before committing.
   Add a test for every behaviour change.
6. Don't push to `main` casually: **every push that touches the mod cuts a
   GitHub release** (see Releasing).

## How the mod works

**Boot and switching.** Entry chunk (`main.lua`) lists `packs/`, defines the
`language` option (ENGLISH = "off" + one choice per pack; default = first
pack whose pack.json lacks `"example": true` and whose id doesn't start
with `_`), wraps `ui.options.rows` to add a LANGUAGE row (Gen 1: before the
"MODS" row label; Gen 2: before "CANCEL"; row shape `{id,label,value,step}`
works on both screens) and `ui.naming.grid`. Real work happens on
**`game.ready`** (fires on both gens after the mod merge, Font.load and
Strings.load, before the title screen): `Live.new(game.data)` then
`switchTo(choice)`. Switching = `live:restore()`, apply the new pack, then
re-run `require("src.core.Strings").load(data)` and, if fonts changed,
`require("src.render.Font").load(data)`. Mod Manager changes arrive as the
`mod.options_changed` event and switch live too. Parsed packs are cached.
The mod declares `engine_internals` (Mod Manager shows "PATCHES ENGINE CODE")
because it edits live data and reloads engine modules; nothing is written to
disk except the option value in options.lua.

**Where packs come from.** Two places: folders in this mod's `packs/`, and
**pack mods** (`elp_<id>`, written by `lpk.py release`), which survive
updates of this mod because re-importing a zip wipes `mods/<id>/` (only
`baseroms/` is kept). Engine facts this rests on:
- A mod can only read its own folder.
- `mod.find(id).exports` may hold functions.
- Dependencies load first.
- `mods.loaded` fires after every entry chunk and right before the engine
  writes `mod_option_schemas.json` (the Mod Manager's copy).
- `mod.storage` (per save slot, unavailable before a save loads) and
  `mod.cache` (rebuildable data only) don't fit packs.

So:
- A pack mod's `main.lua` (`tools/pack_mod_main.lua`) calls
  `exports.registerPack{api=1, id, dir="pack", read, list, info,
  assetPath, mod}` with closures over its own `mod:read`/`mod:list`/
  `mod:info`/`mod.assets:path`. The pack id is the mod id minus `elp_`.
- `main.lua` rebuilds the choices on `mods.loaded`, refilling `choices` in
  place because the row closures hold it, and re-defines the option.
- A `packs/<id>` folder beats a pack mod with the same id (testing
  overrides release).
- `lib/pack.lua` reads every pack through a `locate(id) -> fs, dir`, so
  `extends` crosses sources.
- Font files resolve through the source's `assetPath`.

**Why not the content registries?** Registry overrides only apply at boot
(merged once), so they can't switch live. The first version used them; the
live approach replaced them. A side benefit: patching Gen 2 trainer records
through registries triggered full-record validation that could fail the
whole mod.

**Where text lives at runtime (after merge):**

| kind | Gen 1 | Gen 2 |
|---|---|---|
| dialogue | `data.text[label]` | `data.gen2Text[addr]` (script text, ROM-address keys like `03:4d44`) and `data.text[label]` (rom_text prose) |
| strings | `data.strings[english]` (engine's own text; Strings catalog, `context\|source` keys) | same |
| species/moves/items | `data.pokemon/moves/items[id].name` | same |
| trainers | `data.trainers[id].name` | `data.gen2Trainers.classes[id].name` |
| statuses | `data.statuses[id].label/hudLabel` (engine registers built-ins; don't pre-fill in tests) | n/a |
| dex_kind/dex_text/dex_text2 | `data.pokemon[id].dexEntry.kind`; **text is a label**: the screen reads `data.text[dexEntry.text]` (e.g. `_MewDexEntry`), so dex_text writes through the label | `data.gen2Pokedex.entries[id].kind/text/text2` (what the #DEX screen reads) |

**Keys and scoping.**
- `#<hash>` dialogue key = apply to every dialogue line in the running game
  whose English hashes to it (an index is built once from originals).
  `split` writes dialogue this way, which is what lets a Gold/Silver
  translation reach Crystal (same English, different addresses; only 3 of
  about 4,000 Crystal keys match Gold's) and makes "exclusive" sheets a set
  difference.
- `ID#<hash>` = version pin: applies only where that record's original
  English hashes to it. That's how one pack holds Red/Blue's and Yellow's
  Pokédex entries (all 151 differ) or Gold/Silver/Crystal's.
- `game` column/field: `gen1`/`gen2`/`gen3` (list or blank = all).
  Precedence (applied later = wins): unscoped plain < unscoped pinned <
  scoped plain < scoped pinned; within a rank, later files win (files load
  in path order; `01_`…`13_`).
- Unknown ids are skipped (never create half-records). A pin that doesn't
  match counts as "for other versions", not a warning.

## lpk.py

- `split --game DIR -o packs\<id> [--name --author --label --layout --restore]`:
  builds 13 sheets (01_red_blue, 02_yellow_exclusive, 03_gold_silver,
  04_crystal_exclusive, 05_firered_leafgreen, 06_ruby_sapphire,
  07_emerald_exclusive, 08–10_names_genN, 11–13_dex_genN), then
  `tools/lpk_layout.py` `organize()` decides each row's file. `flat` keeps
  the 13 in the root. `organized` (default) puts every row in a place or
  topic file under G1/G2/G3:
  - **Gen 1:** by script label against `G1_CODES` (map → T##/R##/D##; a few
    floors moved to where they are in the final game), then `G1_NONMAP` and
    `G1_SYSTEM` label families. Needs no game data.
  - **Gen 2:** traced from the imported `maps.lua` (events) → `scripts.lua` →
    text addresses, with the map's `landmarks.lua` name picking the file.
    There are map overrides, standard-label rules for lines many maps share,
    and phone banks for lines no map reaches.
  - **Gen 3:** a map-named label or the same tracing (`scripts/events.lua` →
    `scripts/scripts.lua`), with the map's `regionMapSectionId` (map_tree
    headers) → `RSE_SECTIONS` / `EMERALD_SECTIONS` / `FRLG_SECTIONS`. Keys
    both FRLG and RSE define become one row in G3/Shared/ (versions joined,
    FRLG notes appended).
  - **No data:** a version with no data falls back to `GENERATION_PATHS` (the
    whole sheet in its generation folder).
  - **Acceptance:** on the owner's install, organized output matches their
    hand-made reference pack byte for byte.

  Plus a `pack.json` template (written once; `--force-pack-json`).
- `write_split` owns the files it writes and nothing else, and records them in
  `_lpk/split.json` (per key: every file split wrote it to; pins; deletions).
  On a re-run:
  - Translations are carried pack-wide (exact kind+key+game, or plain id ↔
    version-pinned).
  - A row found where split didn't put it was **moved by the translator** and
    stays there. In a file the translator made, only english/versions/notes of
    those rows are refreshed in place.
  - A row split wrote that's now found nowhere was **deleted** and stays out
    until `--restore PATH|all`.
  - Owned files split no longer writes are **retired**: moved to
    `_orphaned/_retired/<date>/` if they held any translation, else removed.
  - Translations matching no row are appended to `_orphaned/<path>`.
  - A pack without a manifest but with root `NN_*.csv` sheets is an older
    flat pack: those sheets count as split's, so they're carried and retired.
- **The manifest must live in a `_` folder:** both readers load every non-root
  `.json` as data (dotfiles too) and skip only `_`/`.` folders. `release` skips
  `_` folders (`_orphaned`, `_lpk`).
- Engine text (menus, battles) = `Strings("...")` literals harvested from
  G1R's `src/`. **Release builds don't ship `src/`**: `engine_source()` uses
  `--engine-source`, else `<game>/src`, else downloads that release's source
  zip from GitHub (latest, or `--engine-version`), keeps `src/**/*.lua`,
  caches in `%LOCALAPPDATA%\easy_language_packs\engine\<tag>`. Untested
  against real GitHub (the old session's network blocked it); tested with
  a local stand-in server.
- Launcher text (`src/import/`) is excluded: the launcher runs before mods
  and only reads `lang/strings.lua`, so this mod can't translate it.
- Imported ROM data: portable installs keep it in the game folder
  (`<game>/red/data/generated/*.lua`, `gold/...`, `firered/data/generated/gba/...`);
  otherwise `%APPDATA%\LOVE\<identity>\`. Gen 1/2 files are `return {…}`;
  Gen 3 `scripts/text.lua` is a chunked `T["label"] = {IR…}` form
  (`LuaReader.load_assignments`), decoded by `ir_source` (`\n` line, `\v`
  scroll, `\f` page, `{PLAYER}`, `{STR_VAR_n}`, `{B_…}` per dialect
  frlg/rs/rse, `{EXT_cmd_args}`).
- `check` validates syntax, unknown kinds/ids, `%s/%d` arity (engine falls
  back to English on mismatch), `{CONTROL}` and `<NEXT>` codes, line widths
  (18 glyphs Gen 1/2), keys translated two different ways.
- `release PACK -o DIR` writes `DIR/elp_<id>-<version>.zip`:
  - **Contents:** one top folder holding `manifest.json`, `main.lua` and the
    stripped pack under `pack/`.
  - **Manifest:** the version comes from pack.json (required); the name is
    pack.json `mod_name`, else "<name> (ELP generated)"; games and
    game_version are copied from this mod's manifest; it depends on
    `easy_language_packs@^1.2.0#kakhonn/g1r-easy-language-packs`.
  - **Options:** `--id`, `--github`, `--force`; `--folder` writes the old
    plain stripped copy.
  - **Verified:** a released zip booted through the real Loader, and the
    importer's `LauncherMods.locateRoot` finds its folder.

## Testing

```
git clone https://github.com/bryanthaboi/gen1recomp ../gen1recomp
py tests/run.py --engine ../gen1recomp      # needs LuaJIT (or LUAJIT=path)
py tests/run.py --python-only               # just lpk.py
```

Lua tests run from the engine root and boot the mod through the engine's
real `src.mods.Loader` (sandbox, manifest validation, events, hooks) over an
in-memory fs (`tests/lua/harness.lua`: `H.boot(data, {gen, version,
language, extra, mods})`, `H.packMod`, `H.optionRow`, `H.diff`; `mods`
installs other mods beside this one). `--engine` must be a source
checkout: release builds have no `tests/`, and the harness needs the engine's
`tests/love_stub.lua`.

The mod tracks the latest G1R release, so test against a current engine and
fix the mod when the engine moves. Last full run: 135 Lua checks + 38 Python
tests, all passing, against engine `dev` 48a7139 (2026-10-20).

## Releasing

`release.yml` (generated by the engine's `modkit.py add-release-workflow`):
on push to `main` (ignoring `**.md`, `.github/**`, `tests/**`, `deploy.py`)
it packs a zip with `git archive` and publishes `vX.Y.Z`. Version = manifest
version if ahead of the newest tag, else newest tag's patch + 1. Commit
locally, push when a release is wanted. The manifest's `github` field
enables update checks in the game's launcher, which needs a **public** repo
(it starts private).

## Testing in a game install

- Re-importing a mod zip in-game **replaces the whole mod folder, `packs/`
  included** (pack mods are separate and survive). Use `deploy.py` for
  testing; keep pack folders backed up.
- LÖVE doesn't follow symlinks by default, so don't symlink the repo into
  `mods/`.
- Personal setup notes (where the game is installed, local packs, working
  style) go in a gitignored `CLAUDE.local.md`, not here.

## Roadmap (agreed ideas, not started)

1. **In-game play-test pass**: never done in a running game. Watch the Gen 2
   OPTIONS row, live pixel-font switch, Gen 2 trainer renames, naming grid.
2. **Gen 3 runtime support** (FireRed/LeafGreen, Ruby/Sapphire/Emerald):
   sheets already exist with `#hash` dialogue and `game=gen3`. Gen 3 text is
   label-keyed IR; engine routes `rom_text` to `gen3RomText` and translates
   via `Strings.translateLabel`/`RomText`. Note FRLG and RS sheets share ~2,200
   identical lines (same keys): `check` warns when they're translated
   differently.
3. **Title graphics**: an `images/` folder in packs. Gen 1 TitleState already
   supports a continuous `versionRibbon` (via `data.field.boot.title`, drawn
   centered as one piece: the hook is meant for translations); vanilla draws
   fragments at fixed x. Gen 2's title is one composed `title_screen.png`
   (Crystal's interlaced entrance is an effect on that one image; the gem is
   separate). Logo replacement opt-in via a pack.json flag
   (`replace_logo: false` default) plus a player-side toggle, and skip if
   another mod already replaced it. Live swap: find the TitleState on
   `game.stack.states`, replace its image fields, flush Assets.
4. Launcher translation (write `lang/strings.lua` from the active pack);
   completion % in the LANGUAGE row; translator workbook (xlsx with length
   checks); text-box preview page; `lpk.py draft` (local-model pre-fill,
   marked for review); untranslated-text audit (move/item descriptions,
   Gen 2 landmark names, type names, individual trainer names).
