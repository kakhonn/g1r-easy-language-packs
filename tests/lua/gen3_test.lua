-- Gen 3 (FireRed/LeafGreen/Ruby/Sapphire/Emerald): the text codec, and a
-- pack applied to synthetic game3 data through the engine's real loader.
-- No ROM data.
local H = dofile(arg[1] .. "/tests/lua/harness.lua")
local eq, ok = H.eq, H.ok
local G3 = dofile(H.MOD .. "/lib/g3text.lua")

-- ---- the codec ---------------------------------------------------------------
-- tests/test_lpk.py (Gen3Text) holds the same segments and expects the same
-- strings and hash from lpk.py's ir_source(): the dialogue keys depend on it
local IR = {
  { t = "text", s = "Hi " }, { t = "player" }, { t = "text", s = "! It's " }, { t = "rival" },
  { t = "nl" }, { t = "strvar", n = 2 }, { t = "scroll" }, { t = "ph", code = 7 }, { t = "para" },
  { t = "bph", code = 0x10 }, { t = "dynamic", n = 0 }, { t = "ext", cmd = 8, args = { 4 } },
  { t = "tag", tag = "{PKMN}" }, { t = "ph", code = 0x20 }, { t = "ph", code = 8 },
  { t = "eos" }, { t = "text", s = "after the end" },
}
local FRLG = "Hi {PLAYER}! It's {RIVAL}\n{STR_VAR_2}\v{VERSION}\f"
  .. "{B_DEF_NAME_WITH_PREFIX}{DYNAMIC_0}{EXT_08_4}{PKMN}{PH_20}{MAGMA}"
eq(G3.source(IR, "frlg"), FRLG, "source: every segment type (FireRed)")
eq(H.hash(FRLG), "4b914961", "source hash matches lpk.py's")
eq(H.hash(G3.source(IR, "rs")), "5956c3d8", "Ruby hash matches lpk.py's")
eq(H.hash(G3.source(IR, "rse")), "a22dbba0", "Emerald hash matches lpk.py's")
eq(G3.source(IR, "rs"):match("{B_[%w_]+}"), "{B_SCR_ACTIVE_NAME_WITH_PREFIX}", "Ruby numbers battle codes its own way")
eq(G3.source(IR, "rs"):sub(-11), "{EVIL_TEAM}", "Ruby names placeholder 8 EVIL_TEAM")
eq(G3.source(IR, "rse"):sub(-6), "{AQUA}", "Emerald names placeholder 8 AQUA")
eq(G3.source({ { t = "ph", code = 2, name = "" } }, "frlg"), "{STR_VAR_1}", "an empty name falls back to the code")

for _, d in ipairs({ "frlg", "rs", "rse" }) do
  local s = G3.source(IR, d)
  eq(G3.source(G3.parse(s, d), d), s, "parse undoes source (" .. d .. ")")
end
do
  local p = G3.parse("¡Hola, {PLAYER}!\n{B_DEF_NAME_WITH_PREFIX}{EXT_08_4}{PH_20}{nope!}{AQUA}", "rse")
  eq(p[1].s, "¡Hola, ", "text before a placeholder")
  eq(p[2].t, "player", "{PLAYER} -> player")
  eq(p[5].t .. ":" .. p[5].code, "bph:16", "battle code by name")
  eq(p[6].t .. ":" .. p[6].cmd .. ":" .. p[6].args[1], "ext:8:4", "control code with its argument")
  eq(p[7].t .. ":" .. p[7].code, "ph:32", "unnamed placeholder")
  eq(p[8].s, "{nope!}", "not a token: kept as text")
  eq(p[9].t .. ":" .. p[9].code .. ":" .. tostring(p[9].name), "ph:8:AQUA", "Emerald placeholders carry names")
  eq(p[#p].t, "eos", "ends like the engine's lines")
  eq(G3.parse("{AQUA}", "frlg")[1].name, nil, "FireRed placeholders carry codes only")
end

-- ---- a pack on synthetic FireRed data ----------------------------------------
local SIGN = { { t = "text", s = "PALLET TOWN" }, { t = "nl" }, { t = "text", s = "Shades of your journey" }, { t = "eos" } }
local GREET = { { t = "text", s = "Hi, " }, { t = "player" }, { t = "text", s = "!" }, { t = "para" },
                { t = "text", s = "Take this " }, { t = "strvar", n = 1 }, { t = "text", s = "." }, { t = "eos" } }
local function copy(t) return H.deepcopy(t) end

local reloadHook
local function dataset()
  return {
    strings = {},
    gen3Text = { PalletTown_Text_Sign = copy(SIGN), Route1_Text_Greet = copy(GREET),
                 Viridian_Text_SameSign = copy(SIGN) },
    gen3Pokemon = {
      _names = { [1] = "BULBASAUR", [25] = "PIKACHU", [277] = "TREECKO" },
      _moveNames = { [33] = "TACKLE" },
      speciesFromNational = function(nat) if nat == 252 then return 277 end return nat end,
      onReload = function(fn) reloadHook = fn end,
    },
    gen3Items = { _byId = { [13] = { name = "POTION" } }, ensureLoaded = function() end },
  }
end
package.loaded["src.core.game3.pokedex_data"] = {
  init = function() end,
  _entries = { [25] = { category = "MOUSE", description = "It keeps its tail raised.", description2 = "" } },
}

local function sheet(rows)
  return { ["packs/es/pack.json"] = '{"name":"Español"}',
           ["packs/es/G3/all.csv"] = "kind,key,game,text\n" .. table.concat(rows, "\n") .. "\n" }
end
local function key(ir, d) return "#" .. H.hash(G3.source(ir, d or "frlg")) end
local PACK = sheet({
  "dialogue," .. key(SIGN) .. ',gen3,"PUEBLO PALETA\\nTonos de tu viaje"',
  "dialogue," .. key(GREET) .. ',gen3,"¡Hola, {PLAYER}!\\fToma este {STR_VAR_1}."',
  "species,025,gen3,PIKACHÚ",
  "species,252,gen3,TREECKO-ES",
  "species,001,gen1,ONLY-GEN1",
  "moves,33,gen3,PLACAJE",
  "items,13,gen3,POCIÓN",
  "dex_kind,025,gen3,RATÓN",
  "dex_text,025,gen3,Mantiene la cola en alto.",
  "trainers,YOUNGSTER,gen3,JOVEN",
  'strings,"%s used\\n%s!",,"¡%s usó\\n%s!"',
})

do
  local data = dataset()
  local loaded, G, loader = H.boot(data, { gen = 3, extra = PACK, language = "es" })
  eq(loaded, true, "loads under Gen 3")
  local mods = loader.mods[H.ID]
  ok(mods and mods.enabled and not mods.failed, "the mod runs in FireRed")
  eq(G3.source(data.gen3Text.PalletTown_Text_Sign, "frlg"), "PUEBLO PALETA\nTonos de tu viaje",
    "dialogue by fingerprint")
  eq(G3.source(data.gen3Text.Viridian_Text_SameSign, "frlg"), "PUEBLO PALETA\nTonos de tu viaje",
    "every label with that English")
  eq(G3.source(data.gen3Text.Route1_Text_Greet, "frlg"), "¡Hola, {PLAYER}!\fToma este {STR_VAR_1}.",
    "placeholders and page breaks kept as segments")
  eq(data.gen3Text.Route1_Text_Greet[2].t, "player", "{PLAYER} is a live placeholder, not text")
  eq(data.gen3Pokemon._names[25], "PIKACHÚ", "species by national number")
  eq(data.gen3Pokemon._names[277], "TREECKO-ES", "national 252 is internal species 277")
  eq(data.gen3Pokemon._names[1], "BULBASAUR", "a gen1-scoped row stays out")
  eq(data.gen3Pokemon._moveNames[33], "PLACAJE", "move names")
  eq(data.gen3Items._byId[13].name, "POCIÓN", "item names")
  local entry = package.loaded["src.core.game3.pokedex_data"]._entries[25]
  eq(entry.category, "RATÓN", "Pokedex category")
  eq(entry.description, "Mantiene la cola en alto.", "Pokedex text")
  eq(data.strings["%s used\n%s!"], "¡%s usó\n%s!", "engine text through the strings catalog")

  -- species data reloaded by the engine: the new tables get the pack too
  data.gen3Pokemon._names = { [1] = "BULBASAUR", [25] = "PIKACHU", [277] = "TREECKO" }
  ok(type(reloadHook) == "function", "registered for species reloads")
  if reloadHook then reloadHook(data.gen3Pokemon) end
  eq(data.gen3Pokemon._names[25], "PIKACHÚ", "applied again after a reload")
end

-- ENGLISH puts every Gen 3 table back exactly
do
  local data = dataset()
  local dex = package.loaded["src.core.game3.pokedex_data"]
  dex._entries[25] = { category = "MOUSE", description = "It keeps its tail raised.", description2 = "" }
  local _, G = H.boot(data, { gen = 3, extra = PACK, language = "off" })
  local before, dexBefore = H.deepcopy(data), H.deepcopy(dex._entries)
  local hooks = G.mods.events
  hooks:emit("mod.options_changed", { mod = H.ID, key = "language", value = "es" })
  eq(data.gen3Pokemon._moveNames[33], "PLACAJE", "switched to the pack from the Mod Manager")
  hooks:emit("mod.options_changed", { mod = H.ID, key = "language", value = "off" })
  eq(H.diff(data, before), nil, "ENGLISH restores the Gen 3 data exactly")
  eq(H.diff(dex._entries, dexBefore), nil, "and the Pokedex entries")
end

-- Emerald: its own placeholder names, and the Pokedex screen's private copy
do
  local EM = { { t = "text", s = "Team " }, { t = "ph", code = 8, name = "AQUA" }, { t = "text", s = "!" }, { t = "eos" } }
  local reads = 0
  package.loaded["src.ui.game3.rse.mapsec"] = { readLua = function(rel)
    reads = reads + 1
    if rel == "pokemon/pokedex/entries.lua" then
      return { [25] = { category = "MOUSE", description = "Emerald text." } }
    end
    return { other = rel }
  end }
  local data = dataset()
  data.gen3Text = { Em_Text = copy(EM) }
  H.boot(data, { gen = 3, version = "emerald", language = "es", extra = sheet({
    "dialogue," .. key(EM, "rse") .. ",gen3,Equipo {AQUA}.",
    "dex_text,025,gen3,Texto de Esmeralda.",
  }) })
  eq(G3.source(data.gen3Text.Em_Text, "rse"), "Equipo {AQUA}.", "Emerald dialogue by its own fingerprint")
  local Mapsec = package.loaded["src.ui.game3.rse.mapsec"]
  local entries = Mapsec.readLua("pokemon/pokedex/entries.lua")
  eq(entries[25].description, "Texto de Esmeralda.", "the Pokedex screen gets translated entries")
  eq(Mapsec.readLua("x.lua").other, "x.lua", "other reads pass through")
end

H.finish("gen3")
