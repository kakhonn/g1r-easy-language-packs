-- Pack reading: file formats, pack.json, defaults and broken input.
-- Synthetic Gen 1 records only; no ROM data.
local H = dofile(arg[1] .. "/tests/lua/harness.lua")
local eq, ok, S = H.eq, H.ok, H.Strings

local function dataset()
  return {
    strings = {},
    text = { _PalletTownSignText = "PALLET TOWN\nShades of your\vjourney await!",
             _PikachuDexEntry = "Original entry." },
    pokemon = { PIKACHU = { id = "PIKACHU", name = "PIKACHU",
      dexEntry = { kind = "MOUSE", text = "_PikachuDexEntry", heightFt = 1, heightIn = 4, weight = 13 } } },
    moves = { TACKLE = { id = "TACKLE", name = "TACKLE" } },
    items = { POTION = { id = "POTION", name = "POTION" } },
    trainers = {}, statuses = {},
  }
end

-- the sample pack is offered but never chosen on its own
do
  local data = dataset()
  local loaded, _, loader = H.boot(data)
  eq(loaded, true, "mod loads through the engine loader")
  eq(data.moves.TACKLE.name, "TACKLE", "example pack not applied by default")
  local schema = loader.optionSchemas[H.ID]
  eq(schema and schema[1].choices[2][2], "_example", "example pack is offered")
  eq(schema and schema[1].default, "off", "default is English when only the example exists")
end

-- the sample pack: CSV named after its kind + sectioned JSON
do
  local data = dataset()
  H.boot(data, { language = "_example" })
  eq(data.moves.TACKLE.name, "BROADSIDE", "json moves section")
  eq(data.items.POTION.name, "GROG", "json items section")
  eq(data.pokemon.PIKACHU.name, "SPARKY", "json species section")
  eq(data.text._PalletTownSignText, "PALLET PORT\nShades of yer\vfirst voyage", "dialogue label")
  eq(S("%s wants\nto fight!", "BROCK"), "BROCK be wantin'\nto scrap!", "csv \\n escape")
  eq(S("No! There's no\nrunning from a\vtrainer battle!"), "Belay that! No\nfleein' a\vcaptain's duel!", "csv \\v escape")
  eq(S("%s grew\nto level %d!", "PIKA", 12), "PIKA be growin'\nto level 12! Arr!", "two directives")
end

-- every input shape, inheritance, defaults
local extra = {
  ["packs/es/pack.json"] = '{"name":"Español","label":"ESPANOL","font":"pixel","extends":""}',
  ["packs/es/all.csv"] = "\239\187\191kind;key;english;text\r\n"
    .. "strings;\"%s wants\\nto fight!\";x;\"¡%s quiere\nluchar!\"\r\n"
    .. "moves;TACKLE;;PLACAJE\r\n"
    .. "moves;NOT_A_MOVE;;NADA\r\n"
    .. "items;POTION;;\r\n"
    .. "# comment;;;\r\n"
    .. "dex_kind;PIKACHU;;RATÓN\r\n",
  ["packs/es/dex_text.tsv"] = "key\ttext\nPIKACHU\tTexto de \\fprueba\n",
  ["packs/es/sub/rows.json"] = '[{"kind":"items","key":"POTION","text":"POCIÓN"},'
    .. '{"kind":"species","key":"PIKACHU","text":"PIKACHU\\u00e9"}]',
  ["packs/es/_drafts/ignored.csv"] = "kind,key,text\nmoves,TACKLE,IGNORED\n",
  ["packs/es_mx/pack.json"] = '{"name":"Español MX","label":"ESPANOL MX","extends":"es"}',
  ["packs/es_mx/moves.json"] = '{"TACKLE":"TACLEADA"}',
}
do
  local data = dataset()
  local _, _, loader = H.boot(data, { extra = extra })
  eq(loader.optionSchemas[H.ID][1].default, "es", "first real pack auto-selected")
  eq(data.moves.TACKLE.name, "PLACAJE", "kind column, semicolons; _drafts folder skipped")
  eq(S("%s wants\nto fight!", "BROCK"), "¡BROCK quiere\nluchar!", "quoted multi-line cell, BOM, CRLF")
  eq(data.items.POTION.name, "POCIÓN", "empty cell ignored; nested JSON rows applied")
  eq(data.pokemon.PIKACHU.name, "PIKACHUé", "\\u escape decoded")
  eq(data.pokemon.PIKACHU.dexEntry.kind, "RATÓN", "dex_kind")
  eq(data.pokemon.PIKACHU.dexEntry.text, "_PikachuDexEntry", "Gen 1 entry keeps its label")
  eq(data.text._PikachuDexEntry, "Texto de \fprueba", "Gen 1 entry text written through the label")
  eq(data.moves.NOT_A_MOVE, nil, "unknown id is not created")
  ok(data.font and data.font.ttf ~= nil, "font: pixel registers the ttf")
end
do
  local data = dataset()
  H.boot(data, { extra = extra, language = "es_mx" })
  eq(data.moves.TACKLE.name, "TACLEADA", "variant overrides its base")
  eq(data.items.POTION.name, "POCIÓN", "variant inherits its base")
end
do
  local data = dataset()
  H.boot(data, { extra = extra, language = "off" })
  eq(data.moves.TACKLE.name, "TACKLE", "off = English")
end

-- game scoping and version pins
do
  local entry = "Original entry."
  local pack = {
    ["packs/g/pack.json"] = '{"name":"g"}',
    ["packs/g/rows.csv"] = table.concat({
      "kind,key,game,text",
      "species,PIKACHU,,ANY",
      "species,PIKACHU,gen1,GEN1",
      "species,PIKACHU,gen2,GEN2",
      "dex_text,PIKACHU#" .. H.hash(entry) .. ",gen1,PINNED",
      "dex_text,PIKACHU#deadbeef,gen1,OTHER-VERSION",
      "dialogue,#" .. H.hash("PALLET TOWN\nShades of your\vjourney await!") .. ",gen1,BY-HASH",
      "moves,TACKLE,gen9,BAD-GAME",
    }, "\n") .. "\n",
  }
  local data = dataset()
  H.boot(data, { extra = pack, language = "g" })
  eq(data.pokemon.PIKACHU.name, "GEN1", "a row scoped to this game beats an unscoped one")
  eq(data.text._PikachuDexEntry, "PINNED", "the version pin that matches applies; the other is skipped")
  eq(data.text._PalletTownSignText, "BY-HASH", "#hash dialogue matches by English")
  eq(data.moves.TACKLE.name, "TACKLE", "an unknown game value rejects the row")
end

-- broken input never takes the game down
do
  local bad = {
    ["packs/bad/pack.json"] = '{"name":"bad"}',
    ["packs/bad/moves.json"] = '{"TACKLE": "X",, }',
    ["packs/bad/x.csv"] = 'key,text\nA,"unterminated',
    ["packs/bad/items.csv"] = 'key,text\nPOTION,OK\n',
  }
  local data = dataset()
  local loaded = H.boot(data, { extra = bad, language = "bad" })
  eq(loaded, true, "bad files still load")
  eq(data.moves.TACKLE.name, "TACKLE", "broken JSON skipped")
  eq(data.items.POTION.name, "OK", "good file in the same pack still applied")
end

-- a pack with no pack.json at all
do
  local data = dataset()
  H.boot(data, { extra = { ["packs/bare/moves.csv"] = "key,text\nTACKLE,BARE\n" }, language = "bare" })
  eq(data.moves.TACKLE.name, "BARE", "pack.json is optional")
end

-- files load sorted by their whole path, like lpk.py: "dex.csv" < "dex/a.csv"
-- because "." sorts before "/", even though the folder "dex" lists first
do
  local data = dataset()
  H.boot(data, { extra = {
    ["packs/o/dex.csv"] = "kind,key,text\nitems,POTION,FROM-ROOT\n",
    ["packs/o/dex/a.csv"] = "kind,key,text\nitems,POTION,FROM-FOLDER\n",
  }, language = "o" })
  eq(data.items.POTION.name, "FROM-FOLDER", "file order is by whole path, not folder by folder")
end

-- the hash matches published FNV-1a test values
eq(H.hash(""), "811c9dc5", "fnv1a('')")
eq(H.hash("a"), "e40c292c", "fnv1a('a')")
eq(H.hash("foobar"), "bf9cf968", "fnv1a('foobar')")

H.finish("packs")
