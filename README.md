# Easy Language Packs

Play Pokémon Red/Blue/Yellow and Gold/Silver/Crystal in your own language on
[G1R Deluxe / Gen1Recomp](https://github.com/bryanthaboi/gen1recomp).
Translations are plain **CSV or JSON files**, which you can edit in Excel,
LibreOffice or Google Sheets. Pick a language under **OPTIONS > LANGUAGE**
and it switches instantly. No restart, and no Lua to write.

> Based on the Pokemon Gen 1 Recompilation Project by BOIS CLUB GAMES, LLC
> (https://github.com/bryanthaboi/gen1recomp)

This is a vibe-coded project, written with Claude (Anthropic's AI). See
[Made with Claude](#made-with-claude) for what that means here.

- Anything a pack leaves out stays in English, so a half-finished pack still plays.
- A translation whose `%s` / `%d` placeholders don't match the English is drawn
  in English (with a log line) instead of crashing a battle.
- Numbered placeholders (`%2$s ... %1$s`) let you reorder names in a sentence.
- `tools/lpk.py` builds ready-to-translate spreadsheets from the games you've
  imported, checks your work, and strips ROM text before you share a pack.

## Install

1. Download the latest `easy_language_packs-X.Y.Z.zip` from
   [Releases](https://github.com/kakhonn/g1r-easy-language-packs/releases).
2. In the game: **MODS > Import mod .zip** and pick it.
3. Put a language pack folder in `mods/easy_language_packs/packs/`
   (e.g. `packs/es/`). On a portable install, `mods` is inside the game folder.
4. **OPTIONS > LANGUAGE**: press LEFT/RIGHT (or A) to cycle through ENGLISH and
   your packs. The same setting is also on the mod's page in the Mod Manager.

Re-importing or updating the mod replaces its folder, packs included. Keep
your packs backed up somewhere else.

The first pack that isn't marked `"example": true` is selected automatically,
so a build that ships one language works without touching options.
`packs/_example` is a small sample pack ("Pirate") that shows every file shape.

Things already on screen when you switch (an open menu, a text box mid-line)
keep the old wording until they're redrawn; close and reopen the menu.
Pokémon without a nickname show their species name in whichever language is
active. New folders dropped into `packs/` while the game is running need a
restart to appear in the list.

## A pack folder

```
packs/es/
  pack.json        optional: name, font, inheritance
  strings.csv      any number of .csv / .tsv / .json files, in any subfolders
  dialogue.json
  names.json
```

All `.csv`, `.tsv` and `.json` files are read in alphabetical order of their
path inside the pack, so `dex.csv` comes before `dex/more.csv` (subfolders
starting with `_` or `.` are skipped). When the same key appears twice, the
later file wins. To set the order yourself, list `"files"` in `pack.json`.

### pack.json

```json
{
  "name": "Español",
  "label": "ESPANOL",
  "font": "pixel",
  "extends": "es",
  "example": false
}
```

| field | meaning |
|---|---|
| `name` | display name |
| `label` | what the LANGUAGE option shows. Keep it plain A-Z, because the option menu draws with the vanilla font |
| `font` | omit to keep the vanilla glyphs (A-Z, a few symbols, no accents). `"pixel"` switches to the engine's bundled Plain Pixel TTF (Latin + diacritics, Cyrillic, kana, CJK). `{ "file": "myfont.ttf", "size": 15, "spacing": 0, "yOffset": -6, "bold": true }` uses your own TTF from the pack folder |
| `extends` | pack id (or list of ids) to inherit from. A regional variant (`es_mx` extends `es`) only needs the lines that differ |
| `files` | explicit file list/order, relative to the pack folder |
| `glyph_pages`, `charmap` | hand-drawn 8x8 glyph sheets, same shape as modkit's `lang/font.lua` / `lang/charmap.lua`; image paths are relative to the pack |
| `naming` | `{ "upper": [[...rows...]], "lower": [...] }` letter grid for the name-entry screen |
| `example` | `true` = never auto-select this pack |

## Kinds of text

Every entry has a **kind**, a **key**, and your **text**. Empty text means
"not translated yet", never "blank".

| kind | key | example key |
|---|---|---|
| `dialogue` | the script text label, **or** `#` + the English line's fingerprint (what `split` writes: applies to every line in the game with that exact English) | `_PalletTownSignText`, `#7d87deb6` |
| `strings` | the engine's English text itself (menus, battle messages, prompts). Prefix `context\|` for the few context-split ones | `%s wants\nto fight!` |
| `species` | species id | `PIKACHU` |
| `moves` | move id | `TACKLE` |
| `items` | item id | `POTION` |
| `trainers` | trainer class id (the template lists them) | |
| `statuses` / `status_hud` | `SLP` `PSN` `BRN` `FRZ` `PAR` | `PSN` |
| `dex_kind` / `dex_text` / `dex_text2` | species id (Pokédex category / entry text) | `PIKACHU` |

Aliases work too (`text`, `ui`, `pokemon`, `move`, `item_names`, …).
Unknown ids are skipped with a log warning rather than creating broken records.

### Which game a row applies to

- **`game` column** (optional): `gen1`, `gen2`, `gen3`, or several (`gen1 gen2`).
  Empty means every game. A row scoped to the running game beats an unscoped
  row for the same key. In JSON use `"game"` on a row, or at the top of a file.
- **Version fingerprints:** a key ending in `#` plus 8 hex digits
  (`PIKACHU#d377b2d3`) applies only where the running game's English for that
  key has that fingerprint. That's how one pack holds Gold's, Silver's and
  Crystal's different Pokédex entries for the same species. `split` writes
  these for you; the fingerprint is a hash, so no English ships with the pack.

### Control characters

| | meaning | in CSV | in JSON |
|---|---|---|---|
| new line | (pokered `line`) | `\n` or a real line break in the cell | `\n` |
| scrolling line | (pokered `cont`) | `\v` | `\v` (accepted even though standard JSON lacks it) |
| new page | (pokered `para`) | `\f` | `\f` |
| tokens | player/rival names etc. | `{PLAYER}` `{RIVAL}` … keep them as-is | same |

The text box fits **18 glyphs per line**. Your own line breaks are kept exactly.

## File formats

### CSV / TSV

Header row required. Columns are matched by name, in any order:

- `key` (or `id`, `label`, `source`)
- `text` (or `translation`, `value`, `target`)
- `kind`, optional if the file is named after a kind (`moves.csv`, `dialogue.tsv`)
- anything else (`english`, `notes`, …) is ignored

Delimiter is auto-detected (`,` `;` or tab; `.tsv` is always tab). Rows whose
kind starts with `#` are comments (so is a key starting with `#`, unless it's a
fingerprint like `#7d87deb6`). Save as UTF-8.

```csv
kind,key,english,text
strings,"%s wants\nto fight!","%s wants\nto fight!","¡%s quiere\nluchar!"
moves,TACKLE,TACKLE,PLACAJE
dialogue,_PalletTownSignText,...,"PUEBLO PALETA\nTonos de tu\vprimer viaje"
```

### JSON, three shapes

Sectioned by kind:

```json
{ "moves": { "TACKLE": "PLACAJE" }, "strings": { "SAVE": "GUARDAR" } }
```
A flat map in a file named after its kind, e.g. `moves.json`:

```json
{ "TACKLE": "PLACAJE", "EMBER": "ASCUAS" }
```
A list of rows:

```json
[ { "kind": "moves", "key": "TACKLE", "text": "PLACAJE" } ]
```

Keys starting with `_comment` or `//` are ignored.

## Tooling: `tools/lpk.py`

Plain Python 3, no installs. On Windows use `py tools\lpk.py ...`.

**Make a template** listing every translatable string with its English:

```
py tools\lpk.py template --game "C:\path\to\gen1recomp" -o es.csv
```

- `--game` is your game folder. The menu and battle text comes from G1R's own
  source code. Release builds don't include it, so the first time, `lpk.py`
  downloads that release's source from G1R's GitHub, keeps only the Lua files,
  and caches them in `%LOCALAPPDATA%\easy_language_packs\engine\`. It uses the
  latest release unless you pass `--engine-version 0.3.89` (match the version
  you play). `--engine-source DIR` uses a source folder you already have;
  `--no-download` skips that text instead.
- Dialogue and names come from your **imported ROM cache**, auto-detected at
  `%APPDATA%\LOVE\pokemon-love2d\red\data\generated` (run the game once with
  your ROM first). Use `--version blue|yellow` or `--cache DIR` to choose.
- `-o file.json` makes a JSON template instead. `--semicolon` writes `;` CSV for
  European-locale Excel.
- `--merge packs\es` carries existing translations into a fresh template after
  a game update, and lists the ones whose English changed.

Translate the `text` column, drop the file into `packs\es\`, done.

**Split by game and place** (what most translators want): every game you've
imported, one folder per generation and one sheet per place, so you can
translate a town, a route or a menu at a time:

```
py tools\lpk.py split --game "C:\path\to\gen1recomp" -o packs\zsm --name "Bahasa Melayu" --author "You"
```

```
packs\zsm\
  Engine\            menus, battle and option text of the game engine (every game)
  G1\                Red/Blue
    Towns\           T01_pallet.csv … T11_saffron.csv
    Routes\          R01.csv … R25.csv
    Dungeons\        D01_viridian_forest.csv … D16_seafoam_islands.csv
    System\          battle, items, PC, marts, Pokédex ratings, menus…
    Names\  Pokedex\ intro.csv  shared.csv
    Yellow\          only what Yellow adds, in the same folders
  G2\                Gold/Silver (Johto towns T20–T31, routes R26–R46, dungeons D17–D37),
    Crystal\         Kanto as in G1; Crystal's additions in Crystal\
  G3\
    FRLG\            FireRed/LeafGreen: Kanto as in G1, Sevii islands T41–T47, R411…, D411…
    RSE\             Ruby/Sapphire: towns T101–T107, cities C101–C109, routes R101–R134,
      Emerald\       underwater W124…, dungeons D101…; Emerald adds Frontier\ (F00–F07)
    Shared\          text FireRed/LeafGreen and Ruby/Sapphire/Emerald have in common
```

A place's sheet holds every line spoken there, and its buildings come along:
houses, shops and gyms go with their town, and gates go with their route. The
Pokédex sheets keep one row per distinct version text (the `versions` column says
which). Red/Blue and Yellow, for example, have different entries for every species.
Gen 2 and Gen 3 lines are placed by following the games' map scripts, so `split`
needs those games imported; a game it can't trace gets one sheet in its
generation folder instead. `--layout flat` writes the original 13 sheets
(`01_red_blue` … `13_dex_gen3`) straight in the pack folder.

`split` also writes a **`pack.json`** with every setting filled in neutrally
and a `_comment_…` line explaining each one (the mod ignores those). Set
`--name "Bahasa Melayu"`, `--author` and optionally `--label` (the
OPTIONS > LANGUAGE text; by default it's the name in plain A-Z). Point `-o`
straight at `packs\<your_id>` and the folder is a ready-to-use pack.

**Re-running `split`** after a game update refreshes the sheets and keeps
your work:

- every translation already in the pack, wherever it is (rows are matched by
  kind, key and game);
- rows you **moved** to another file stay there. In a file you made yourself,
  `split` only refreshes the English of those rows and leaves everything else
  in that file alone;
- rows you **deleted** stay deleted. `--restore G1\01_red_blue.csv` brings
  back what you deleted from one file, `--restore all` everything;
- your `pack.json` (`--force-pack-json` replaces it).

`split` keeps track of this in `_lpk\split.json` (don't edit it by hand).
A translation that no longer matches any row is saved to `_orphaned\`
instead of lost. A file `split` no longer writes (after a layout change, or when
upgrading a flat pack) is moved to `_orphaned\_retired\<date>\` if it held any
translation, once those translations are in their new files; untranslated ones
are simply removed. The mod and `release` ignore every folder that starts
with `_`.

Dialogue is matched by its English, so a Gold/Silver translation also applies
in Crystal wherever the line is identical, even though Crystal stores it at a
different address. The exclusive sheets hold only what that adds. Portable
installs need only `--game`; otherwise add `--cache "%APPDATA%\LOVE\pokemon-love2d"`.

**Check a pack** (syntax, unknown ids, `%s` mismatches, missing `{CONTROL}`
codes, overlong lines, the same key translated two ways, coverage):

```
py tools\lpk.py check packs\es --game "C:\path\to\gen1recomp"
```

**Release a pack:** the template's `english` column holds text from your ROM.
Don't ship it. This copies the pack with reference columns and untranslated
rows removed:

```
py tools\lpk.py release packs\es -o dist\es
```

## Notes

- Engine strings are keyed by their English. If an update rewords one, that
  entry quietly stops matching. Re-run `template --merge` after updates to spot them.
- The launcher (before you pick a game) reads only `lang/strings.lua` in a mod
  folder, so its menus stay English with this loader.
- Problems are logged, never fatal: a broken file is skipped and the rest of the pack still loads.
- The mod declares the `engine_internals` permission (the Mod Manager shows
  "PATCHES ENGINE CODE"): switching live means editing the running game's
  text tables and reloading its text catalog and font, which the content
  registries can only do at boot.
- Supported in-game: Red/Blue/Yellow and Gold/Silver/Crystal. The Gen 3 sheets
  (FireRed/LeafGreen, Ruby/Sapphire/Emerald) can be translated now, but the mod
  doesn't apply them in those games yet.
- Gen 2 text carries control codes like `{DONE}`, `{PROMPT}` and `<NEXT>` (a
  Pokédex line break). Keep every one; `check` flags any that go missing.

## Development

The repository is the mod: everything at the root except `tests/`,
`.github/` and `deploy.py` ships in the release zip.

```
tests/            ROM-free tests (Lua + Python)
deploy.py         copy the mod into a game install for testing
.github/workflows release.yml: zip + GitHub Release on every push to main
                  tests.yml:   run the tests on every push and pull request
```

**Try your changes in the game** without packaging:

```
py deploy.py "C:\path\to\gen1recomp-win64"
```

It copies the mod into `mods\easy_language_packs\`, leaving any packs you
have there untouched.

**Run the tests** (needs Python 3 and LuaJIT, plus a checkout of the engine;
no ROMs required):

```
git clone https://github.com/bryanthaboi/gen1recomp ../gen1recomp
py tests/run.py --engine ../gen1recomp
```

**Releasing:** bump `version` in `manifest.json` and push to `main`. The
release workflow packs the zip and publishes it; the game's launcher sees the
new version through the `github` field in the manifest.

**Never commit translation sheets with an `english` column** or anything from
`language_templates\`: that column is text from the ROMs. `.gitignore` keeps
every pack except `_example` out of the repository; share finished packs as
`lpk.py release` output.

## Made with Claude

Easy Language Packs was vibe-coded: it was built through conversation with
Claude, Anthropic's AI model.

- **kakhonn** came up with the idea, decided what it should do and how it
  should behave, tested it, and reviewed the results along the way.
- **Claude** wrote the code, the tests, `lpk.py` and this documentation. It
  also researched how G1R Deluxe handles text, fonts, mods and imported game
  data, by reading the engine's source code.

Every change was checked by the automated tests in `tests/`, which run the mod
through the engine's real mod loader. Much of it was also checked against
data imported from real cartridges. Even so, it hasn't had much play-testing
in the actual game yet, and AI-written code can be wrong in ways that look
right. If something doesn't work, please open an issue.

## License

GPL-3.0 (see `LICENSE`). Easy Language Packs is a mod for, and is based on,
the Pokemon Gen 1 Recompilation Project by BOIS CLUB GAMES, LLC
(https://github.com/bryanthaboi/gen1recomp).

Pokémon is © Nintendo / Creatures Inc. / GAME FREAK inc. This project is not
affiliated with or endorsed by them, and contains no game assets or ROM data.

