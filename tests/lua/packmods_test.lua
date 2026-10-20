-- Pack mods: language packs installed as their own mods (lpk.py release),
-- registering with this one through mod.exports.registerPack.
-- Synthetic Gen 1 records only; no ROM data.
local H = dofile(arg[1] .. "/tests/lua/harness.lua")
local eq, ok = H.eq, H.ok

local function dataset()
  return {
    strings = {},
    text = { _PalletTownSignText = "PALLET TOWN\nShades of your\vjourney await!" },
    pokemon = { PIKACHU = { id = "PIKACHU", name = "PIKACHU" } },
    moves = { TACKLE = { id = "TACKLE", name = "TACKLE" } },
    items = { POTION = { id = "POTION", name = "POTION" } },
    trainers = {}, statuses = {},
  }
end

local function choiceIds(loader)
  local out = {}
  for _, c in ipairs(loader.optionSchemas[H.ID][1].choices) do out[#out + 1] = c[2] end
  return table.concat(out, " ")
end

local ES = H.packMod({
  ["pack.json"] = '{"name":"Español","label":"ESPANOL"}',
  ["G1/moves.csv"] = "key,english,text\nTACKLE,TACKLE,PLACAJE\n",
  ["G1/items.csv"] = "key,text\nPOTION,POCIÓN\n",
}, { id = "elp_es" })

-- a pack mod is offered, auto-selected and applied
do
  local data = dataset()
  local loaded, G, loader = H.boot(data, { mods = { elp_es = ES } })
  eq(loaded, true, "loads with a pack mod installed")
  ok(loader.mods.elp_es and loader.mods.elp_es.enabled and not loader.mods.elp_es.failed,
    "the pack mod itself loads")
  eq(choiceIds(loader), "off _example es", "pack mod's language is in the choices (Mod Manager too)")
  eq(loader.optionSchemas[H.ID][1].default, "es", "a pack mod's language auto-selects")
  eq(data.moves.TACKLE.name, "PLACAJE", "pack mod applied (file in a subfolder)")
  eq(data.items.POTION.name, "POCIÓN", "every file of the pack mod read")
  local row = H.optionRow(loader, G)
  eq(row and row.value(G), "ESPANOL", "OPTIONS row shows the pack's label")
end

-- switching to a pack mod and back restores English exactly
do
  local data = dataset()
  local _, G, loader = H.boot(data, { mods = { elp_es = ES }, language = "off" })
  local before = H.deepcopy(data)
  local row = H.optionRow(loader, G)
  ok(H.stepTo(row, G, "ESPANOL"), "row steps to the pack mod's language")
  eq(data.moves.TACKLE.name, "PLACAJE", "applied after switching")
  ok(H.stepTo(row, G, "ENGLISH"), "row steps back to ENGLISH")
  eq(H.diff(data, before), nil, "ENGLISH restores the data exactly")
end

-- the translator's packs/ folder beats the released pack mod with the same id
do
  local data = dataset()
  local _, _, loader = H.boot(data, { mods = { elp_es = ES }, language = "es", extra = {
    ["packs/es/pack.json"] = '{"name":"Español (working copy)","label":"ESPANOL WIP"}',
    ["packs/es/moves.csv"] = "key,text\nTACKLE,PLACAJE NUEVO\n",
  } })
  eq(choiceIds(loader), "off _example es", "one choice for the id")
  eq(loader.optionSchemas[H.ID][1].choices[3][1], "ESPANOL WIP", "the folder's label is used")
  eq(data.moves.TACKLE.name, "PLACAJE NUEVO", "packs/es wins over elp_es")
  eq(data.items.POTION.name, "POTION", "nothing from the pack mod mixed in")
end

-- extends works between a folder and a pack mod, both ways
do
  local data = dataset()
  H.boot(data, { mods = { elp_es = ES }, language = "es_mx", extra = {
    ["packs/es_mx/pack.json"] = '{"name":"Español MX","extends":"es"}',
    ["packs/es_mx/moves.csv"] = "key,text\nTACKLE,TACLEADA\n",
  } })
  eq(data.moves.TACKLE.name, "TACLEADA", "folder variant overrides its pack-mod base")
  eq(data.items.POTION.name, "POCIÓN", "folder variant inherits from a pack mod")
end
do
  local MX = H.packMod({
    ["pack.json"] = '{"name":"Español MX","extends":"es"}',
    ["moves.csv"] = "key,text\nTACKLE,TACLEADA\n",
  }, { id = "elp_es_mx" })
  local data = dataset()
  H.boot(data, { mods = { elp_es_mx = MX }, language = "es_mx", extra = {
    ["packs/es/items.csv"] = "key,text\nPOTION,POCIÓN\n",
  } })
  eq(data.moves.TACKLE.name, "TACLEADA", "pack-mod variant applied")
  eq(data.items.POTION.name, "POCIÓN", "pack-mod variant inherits from a folder")
end

-- a pack mod's own font file resolves inside that mod
do
  local FONT = H.packMod({
    ["pack.json"] = '{"name":"Font","font":{"file":"f.ttf","size":15}}',
    ["f.ttf"] = "not really a font",
    ["moves.csv"] = "key,text\nTACKLE,FONTED\n",
  }, { id = "elp_font" })
  local data = dataset()
  H.boot(data, { mods = { elp_font = FONT }, language = "font" })
  eq(data.moves.TACKLE.name, "FONTED", "font pack applied")
  eq(data.font.ttf and data.font.ttf.file, "mods/elp_font/pack/f.ttf", "font path is in the pack mod")
end

-- a pack mod for a newer Easy Language Packs doesn't load, and nothing breaks
do
  local data = dataset()
  local loaded, _, loader = H.boot(data, { language = "es",
    mods = { elp_es = H.packMod({ ["moves.csv"] = "key,text\nTACKLE,X\n" },
                                { id = "elp_es", needs = "^9.0.0" }) } })
  ok(loader.mods.elp_es == nil or not loader.mods.elp_es.enabled or loader.mods.elp_es.failed
    or loader.exports.elp_es == nil, "the engine refuses the pack mod")
  eq(choiceIds(loader), "off _example", "its language isn't offered")
  eq(data.moves.TACKLE.name, "TACKLE", "a saved choice with no pack falls back to English")
  ok(loaded ~= nil, "Easy Language Packs still boots")
end

-- registerPack refuses bad registrations without failing either mod
do
  local bad = {
    ["manifest.json"] = '{"id":"elp_bad","name":"bad","version":"0.1.0","api":2,"entry":"main.lua",'
      .. '"profile":"content","category":"UI","games":["gen1","gen2"],'
      .. '"game_version":">=0.0.0-0 <2.0.0","dependencies":["easy_language_packs"]}',
    ["main.lua"] = [[
return function(mod)
  local reg = mod.find("easy_language_packs").exports.registerPack
  mod.exports.results = {
    reg({ api = 2, id = "x", read = print, list = print, info = print }),
    reg({ api = 1, id = "../x", read = print, list = print, info = print }),
    reg({ api = 1, id = "off", read = print, list = print, info = print }),
    reg({ api = 1, id = "nofs" }),
    reg("nonsense"),
  }
end]],
  }
  local data = dataset()
  local loaded, _, loader = H.boot(data, { mods = { elp_bad = bad } })
  local results = loader.exports.elp_bad and loader.exports.elp_bad.results or {}
  local accepted = 0
  for i = 1, 5 do if results[i] ~= false then accepted = accepted + 1 end end
  eq(accepted, 0, "every bad registration returns false")
  eq(choiceIds(loader), "off _example", "nothing bad was offered")
  eq(loaded, true, "both mods still load")
end

H.finish("packmods")
