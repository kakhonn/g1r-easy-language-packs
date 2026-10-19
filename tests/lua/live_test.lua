-- Live switching: OPTIONS > LANGUAGE, the Mod Manager event, exact restore
-- to English, and where each kind of text lands in Gen 1 and Gen 2 data.
-- Synthetic records only; no ROM data.
local H = dofile(arg[1] .. "/tests/lua/harness.lua")
local eq, ok, S = H.eq, H.ok, H.Strings

local RB_ENTRY = "Red/Blue entry."
local Y_ENTRY = "Yellow entry."
local G_ENTRY = "Gold entry."
local C_ENTRY = "Crystal entry."
local SHARED = "Same words in\nboth games!{DONE}"

local function gen1(version)
  return {
    strings = {},
    text = {
      _SignText = "A SIGN",
      _PikachuDexEntry = version == "yellow" and Y_ENTRY or RB_ENTRY,
      [version == "yellow" and "_YellowOnlyText" or "_RedOnlyText"] = "only here",
    },
    pokemon = { PIKACHU = { id = "PIKACHU", name = "PIKACHU",
      dexEntry = { kind = "MOUSE", text = "_PikachuDexEntry", heightFt = 1, heightIn = 4, weight = 13 } } },
    moves = { TACKLE = { id = "TACKLE", name = "TACKLE" } },
    items = { POTION = { id = "POTION", name = "POTION" } },
    trainers = { OPP_YOUNGSTER = { id = "OPP_YOUNGSTER", name = "YOUNGSTER" } },
    statuses = {},  -- the engine registers its built-in statuses here
  }
end

local function gen2(version)
  return {
    strings = {},
    -- script text is keyed by ROM address, which differs between versions
    gen2Text = { [version == "crystal" and "12:4000" or "03:4d44"] = SHARED },
    text = { AlreadyAsleepText = "{TARGET}'s\nalready asleep!{PROMPT}" },
    pokemon = { PIKACHU = { id = "PIKACHU", name = "PIKACHU" } },
    gen2Pokedex = { entries = { PIKACHU = { kind = "MOUSE",
      text = version == "crystal" and C_ENTRY or G_ENTRY, text2 = "Page two." } } },
    moves = { THUNDERBOLT = { id = "THUNDERBOLT", name = "THUNDERBOLT" } },
    items = { BERRY = { id = "BERRY", name = "BERRY" } },
    gen2Trainers = { classes = { BEAUTY = { id = "BEAUTY", name = "BEAUTY" } } },
  }
end

local pack = {
  ["packs/t/pack.json"] = '{"name":"Test","label":"TEST"}',
  ["packs/t/rows.csv"] = table.concat({
    "kind,key,game,text",
    "strings,SAVE,,GUARDAR",
    "dialogue,_SignText,gen1,UN LETRERO",
    "species,PIKACHU,gen1,PIKA-1",
    "species,PIKACHU,gen2,PIKA-2",
    "moves,TACKLE,gen1,PLACAJE",
    "trainers,OPP_YOUNGSTER,gen1,JOVEN",
    "statuses,PSN,gen1,ENV",
    "dex_kind,PIKACHU,gen1,RATON",
    "dex_text,PIKACHU#" .. H.hash(RB_ENTRY) .. ",gen1,ENTRADA-RB",
    "dex_text,PIKACHU#" .. H.hash(Y_ENTRY) .. ",gen1,ENTRADA-Y",
    "dialogue,#" .. H.hash(SHARED) .. ",gen2,MISMAS PALABRAS{DONE}",
    "dialogue,AlreadyAsleepText,gen2,{TARGET}\\nya duerme{PROMPT}",
    "dex_text,PIKACHU#" .. H.hash(G_ENTRY) .. ",gen2,ENTRADA-ORO",
    "dex_text,PIKACHU#" .. H.hash(C_ENTRY) .. ",gen2,ENTRADA-CRISTAL",
    "dex_text2,PIKACHU,gen2,Pagina dos.",
    "moves,THUNDERBOLT,gen2,RAYO",
    "trainers,BEAUTY,gen2,BELLEZA",
  }, "\n") .. "\n",
}

local cases = {
  { gen = 1, version = "red", make = gen1, check = function(d)
      eq(d.text._SignText, "UN LETRERO", "red: dialogue label")
      eq(d.pokemon.PIKACHU.name, "PIKA-1", "red: species (gen1 row)")
      eq(d.moves.TACKLE.name, "PLACAJE", "red: move")
      eq(d.trainers.OPP_YOUNGSTER.name, "JOVEN", "red: trainer class")
      eq(d.statuses.PSN.label, "ENV", "red: status")
      eq(d.pokemon.PIKACHU.dexEntry.kind, "RATON", "red: dex category")
      eq(d.pokemon.PIKACHU.dexEntry.text, "_PikachuDexEntry", "red: entry keeps its label")
      eq(d.text._PikachuDexEntry, "ENTRADA-RB", "red: Red/Blue entry via its label")
    end },
  { gen = 1, version = "yellow", make = gen1, check = function(d)
      eq(d.text._PikachuDexEntry, "ENTRADA-Y", "yellow: Yellow's own entry")
    end },
  { gen = 2, version = "gold", make = gen2, check = function(d)
      eq(d.gen2Text["03:4d44"], "MISMAS PALABRAS{DONE}", "gold: script text by English")
      eq(d.text.AlreadyAsleepText, "{TARGET}\nya duerme{PROMPT}", "gold: label-keyed prose")
      eq(d.pokemon.PIKACHU.name, "PIKA-2", "gold: species (gen2 row)")
      eq(d.gen2Pokedex.entries.PIKACHU.text, "ENTRADA-ORO", "gold: Gold's entry")
      eq(d.gen2Pokedex.entries.PIKACHU.text2, "Pagina dos.", "gold: entry page two")
      eq(d.moves.THUNDERBOLT.name, "RAYO", "gold: move")
      eq(d.gen2Trainers.classes.BEAUTY.name, "BELLEZA", "gold: trainer class")
    end },
  { gen = 2, version = "crystal", make = gen2, check = function(d)
      eq(d.gen2Text["12:4000"], "MISMAS PALABRAS{DONE}", "crystal: same English at another address")
      eq(d.gen2Pokedex.entries.PIKACHU.text, "ENTRADA-CRISTAL", "crystal: Crystal's entry")
    end },
}

for _, case in ipairs(cases) do
  local data = case.make(case.version)
  local loaded, G, loader = H.boot(data, { gen = case.gen, version = case.version,
                                          language = "off", extra = pack })
  local tag = case.version .. ": "
  eq(loaded, true, tag .. "loads")
  local english = H.deepcopy(data)

  local row, pos, rows = H.optionRow(loader, G)
  ok(row, tag .. "LANGUAGE row added to OPTIONS")
  if case.gen == 1 then
    eq(rows[pos + 1] and rows[pos + 1].id, "mods", tag .. "row sits just above MODS")
  end
  eq(row.value(G), "ENGLISH", tag .. "starts in English")

  ok(H.stepTo(row, G, "TEST"), tag .. "stepping reaches the pack")
  eq(G.save.options.modOptions[H.ID].language, "t", tag .. "choice saved")
  ok(G.writes > 0, tag .. "options written")
  eq(S("SAVE"), "GUARDAR", tag .. "engine text")
  case.check(data)

  ok(H.stepTo(row, G, "ENGLISH", -1), tag .. "stepping back to ENGLISH")
  eq(H.diff(english, data), nil, tag .. "ENGLISH restores every original exactly")
  eq(S("SAVE"), "SAVE", tag .. "engine text back to English")

  loader.events:emit("mod.options_changed", { mod = H.ID, key = "language", value = "t" })
  eq(S("SAVE"), "GUARDAR", tag .. "a Mod Manager change applies live")
  loader.events:emit("mod.options_changed", { mod = H.ID, key = "language", value = "off" })
  eq(H.diff(english, data), nil, tag .. "and back again, still exact")
end

-- booting straight into a language applies before the title screen
do
  local data = gen1("red")
  H.boot(data, { language = "t", extra = pack })
  eq(data.text._SignText, "UN LETRERO", "saved language applies at game.ready")
end

-- a pack's name-entry grid replaces the letters only while it's active
do
  local grid = '{"name":"n","naming":{"upper":[["A","B"],["ED"]]}}'
  local data = gen1("red")
  local _, G, loader = H.boot(data, { language = "n", extra = { ["packs/n/pack.json"] = grid } })
  local base = { { "X" } }
  local got = loader.hooks:call("ui.naming.grid", function(b) return b end, base, { lower = false })
  eq(got and got[1] and got[1][1], "A", "naming grid from pack.json")
  loader.events:emit("mod.options_changed", { mod = H.ID, key = "language", value = "off" })
  got = loader.hooks:call("ui.naming.grid", function(b) return b end, base, { lower = false })
  eq(got, base, "English keeps the game's own grid")
end

H.finish("live")
