-- Easy Language Packs
--
-- Replaces the game's text with translations read from JSON or CSV files.
-- Drop a folder into packs/ (see packs/_example and README.md), or install a
-- pack mod made by `lpk.py release`, then pick it under OPTIONS > LANGUAGE.
-- The switch takes effect immediately.
--
-- The pack is applied straight onto the live game data when the game becomes
-- ready (before the title screen) and again whenever the language changes;
-- lib/live.lua remembers every original so ENGLISH restores it exactly.
-- Engine-authored text goes through the engine's own Strings catalog, so a
-- translation whose %s / %d directives don't match the English falls back
-- to English instead of crashing a battle.
return function(mod)
  local PACKS = "packs"
  local compile = loadstring or load

  local function loadModule(path)
    local source, readError = mod:read(path)
    if not source then
      error(("easy_language_packs: cannot read %s (%s); reinstall the mod")
        :format(path, tostring(readError)), 0)
    end
    local chunk, compileError = compile(source, "@" .. path)
    if not chunk then error("easy_language_packs: " .. tostring(compileError), 0) end
    return chunk()
  end

  local libs = { json = loadModule("lib/json.lua"), csv = loadModule("lib/csv.lua") }
  local Pack = loadModule("lib/pack.lua")
  local Hash = loadModule("lib/hash.lua")
  local Live = loadModule("lib/live.lua")
  local GEN = "gen" .. tostring(mod.generation or 1)

  local fs = {
    read = function(path)
      local ok, body = pcall(mod.read, mod, path)
      if ok and type(body) == "string" then return body end
      return nil
    end,
    list = function(path)
      local ok, items = pcall(mod.list, mod, path)
      return ok and items or {}
    end,
    info = function(path)
      local ok, info = pcall(mod.info, mod, path)
      return ok and info or nil
    end,
  }

  -- ---- which languages exist ---------------------------------------------
  -- Packs live in this mod's packs/ folder or in pack mods (`lpk.py release`
  -- writes them), which survive updates of this mod.  A pack mod depends on
  -- this one, so it runs later and calls mod.exports.registerPack; the list
  -- is finished on mods.loaded, after every mod has run and before the
  -- engine saves the Mod Manager's copy of the options.
  local here = { fs = fs, assetPath = function(p) return mod.assets:path(p) end }
  local registered = {} -- { id=, dir=, source=, from= } in registration order
  local byId = {}       -- pack id -> { id=, label=, example=, dir=, source= }
  local choices, default = {}, "off"
  local cache = {}      -- parsed packs: re-selecting a language is instant
  local finished = false

  local function rebuild()
    byId, cache = {}, {}
    local all = {}
    for _, p in ipairs(Pack.list(fs, libs, PACKS)) do
      p.dir, p.source = PACKS .. "/" .. p.id, here
      byId[p.id] = p
      all[#all + 1] = p
    end
    for _, r in ipairs(registered) do
      local taken = byId[r.id]
      if taken and taken.source == here then
        -- the translator's working copy beats the version they released
        if finished then
          mod.log:info("language pack: packs/%s overrides the one in mod %s", r.id, r.from)
        end
      elseif taken then
        mod.log:warn("language pack: mod %s and mod %s both register '%s'; keeping %s",
          taken.from, r.from, r.id, taken.from)
      else
        local p = Pack.describe(r.source.fs, libs.json, r.dir, r.id)
        p.dir, p.source, p.from = r.dir, r.source, r.from
        byId[p.id] = p
        all[#all + 1] = p
      end
    end
    table.sort(all, function(a, b) return a.id < b.id end)
    -- refilled in place: the OPTIONS row's closures hold this table
    for i = #choices, 1, -1 do choices[i] = nil end
    choices[1] = { "ENGLISH", "off" }
    default = "off"
    for _, p in ipairs(all) do
      choices[#choices + 1] = { p.label, p.id }
      -- default to the first real pack, so a single-language install just
      -- works; packs marked "example": true never auto-select
      if default == "off" and not p.example and p.id:sub(1, 1) ~= "_" then default = p.id end
    end
    -- defined for the Mod Manager page too
    mod.options:define({
      { key = "language", type = "choice", label = "LANGUAGE",
        default = default, choices = choices },
    })
  end
  rebuild()

  -- registerPack{ api = 1, id =, dir =, read =, list =, info =, assetPath =, mod = }
  -- read/list/info/assetPath are the pack mod's own mod:read, mod:list,
  -- mod:info and mod.assets:path: a mod can only read its own folder.
  mod.exports.packApi = 1
  mod.exports.registerPack = function(spec)
    local from = type(spec) == "table" and tostring(spec.mod or "?") or "?"
    local function refuse(why)
      mod.log:warn("language pack from mod %s not registered: %s", from, why)
      return false, why
    end
    if type(spec) ~= "table" or spec.api ~= 1 then
      return refuse("needs pack api 1; update Easy Language Packs or re-release the pack")
    end
    local id = spec.id
    if type(id) ~= "string" or not id:match("^[%w_%-]+$") or id == "off" then
      return refuse("bad pack id " .. tostring(id))
    end
    for _, f in ipairs({ "read", "list", "info" }) do
      if type(spec[f]) ~= "function" then return refuse("missing " .. f) end
    end
    local function guarded(fn, want)
      return function(path)
        local ok, v = pcall(fn, path)
        if ok and type(v) == want then return v end
        return nil
      end
    end
    local list = guarded(spec.list, "table")
    local source = {
      fs = { read = guarded(spec.read, "string"), info = guarded(spec.info, "table"),
             list = function(path) return list(path) or {} end },
      assetPath = type(spec.assetPath) == "function" and guarded(spec.assetPath, "string") or nil,
    }
    local dir = type(spec.dir) == "string" and spec.dir ~= "" and spec.dir or "pack"
    registered[#registered + 1] = { id = id, dir = dir, source = source, from = from }
    if finished then rebuild() end
    return true
  end

  mod.events:on("mods.loaded", function()
    finished = true
    rebuild()
  end)

  local function isChoice(id)
    for _, c in ipairs(choices) do if c[2] == id then return true end end
    return false
  end

  local function locate(id)
    local p = byId[id]
    if p then return p.source.fs, p.dir end
  end

  local function readPack(id)
    local hit = cache[id]
    if hit then return hit end
    local entries, meta, problems = Pack.read(locate, libs, id)
    for _, p in ipairs(problems) do mod.log:warn("language pack: %s", p) end
    hit = { entries = entries, meta = meta or {} }
    cache[id] = hit
    return hit
  end

  -- ---- applying -----------------------------------------------------------
  local game, live, current = nil, nil, nil
  local naming = nil -- the active pack's name-entry grid, if any

  local function engine(name)
    local ok, m = pcall(require, name)
    return ok and m or nil
  end

  -- Fonts: "font": "pixel" uses the engine's bundled Plain Pixel TTF; an
  -- object registers the pack's own TTF; glyph_pages/charmap add 8x8 sheets.
  -- File paths resolve in the folder or mod the pack came from.
  local function applyFont(meta, p)
    local font = game.data.font
    if type(font) ~= "table" then return false end
    local function asset(file)
      local path = p.source.assetPath and p.source.assetPath(p.dir .. "/" .. file)
      if not path then mod.log:warn("language pack '%s': cannot locate %s", p.id, file) end
      return path
    end
    local touched = false
    local spec = meta.font
    if spec == "pixel" or spec == true then
      live:set(font, "ttf", {})
      touched = true
    elseif type(spec) == "table" then
      local ttf = {}
      for k, v in pairs(spec) do ttf[k] = v end
      if type(ttf.file) == "string" then ttf.file = asset(ttf.file) end
      live:set(font, "ttf", ttf)
      touched = true
    end
    if type(meta.glyph_pages) == "table" then
      for pageId, page in pairs(meta.glyph_pages) do
        local copy = {}
        for k, v in pairs(page) do copy[k] = v end
        if type(copy.image) == "string" then copy.image = asset(copy.image) end
        live:set(font, pageId, copy)
      end
      touched = true
    end
    if type(meta.charmap) == "table" then
      for seq, code in pairs(meta.charmap) do
        live:set(font, "charmap:" .. seq, { seq = seq, code = code })
      end
      touched = true
    end
    return touched
  end

  local fontTouched = false
  local function switchTo(id)
    if not (game and live) then return end
    if not isChoice(id) then id = "off" end
    if id == current then return end
    live:restore()
    naming = nil
    local summary = "English"
    local touchedNow = false
    if id ~= "off" then
      local pack = readPack(id)
      local counts, other, failed = live:applyAll(pack.entries)
      for _, f in ipairs(failed) do
        mod.log:warn("language pack: %s: skipped (%s)", f.where, f.reason)
      end
      touchedNow = applyFont(pack.meta, byId[id])
      if type(pack.meta.naming) == "table" and type(pack.meta.naming.upper) == "table" then
        naming = pack.meta.naming
      end
      local total, parts = 0, {}
      for kind, n in pairs(counts) do
        total = total + n
        parts[#parts + 1] = kind .. "=" .. n
      end
      table.sort(parts)
      summary = ("'%s': %d strings (%s), %d for other versions, %d skipped")
        :format(id, total, table.concat(parts, " "), other, #failed)
    end
    -- the engine's text catalog and font hold onto what they last loaded
    local Strings = engine("src.core.Strings")
    if Strings and Strings.load then pcall(Strings.load, game.data) end
    if touchedNow or fontTouched then
      local Font = engine("src.render.Font")
      if Font and Font.load then pcall(Font.load, game.data) end
    end
    fontTouched = touchedNow
    current = id
    mod.log:info("language pack on %s: %s", GEN, summary)
  end

  local function chosen()
    local v = mod.options:get("language")
    if v == nil then v = default end
    return v
  end

  -- game.ready: services up, nothing drawn yet (both generations)
  mod.events:on("game.ready", function(payload)
    game = payload and payload.game or mod.game
    if not (game and game.data) then
      mod.log:warn("language pack: no game data to translate")
      return
    end
    if not live then live = Live.new(game.data, GEN, Hash) end
    current = nil
    switchTo(chosen())
  end)

  -- a change made on the Mod Manager page applies right away too
  mod.events:on("mod.options_changed", function(payload)
    if payload and payload.mod == mod.id and payload.key == "language" then
      switchTo(payload.value)
    end
  end)

  -- ---- OPTIONS > LANGUAGE ----------------------------------------------------
  local function store(g, value)
    local options = g.save and g.save.options
    if options then
      options.modOptions = options.modOptions or {}
      options.modOptions[mod.id] = options.modOptions[mod.id] or {}
      options.modOptions[mod.id].language = value
    end
    -- keep mod.options:get in step until options.lua is read again
    local loader = g.mods
    if loader and loader.modOptions then
      loader.modOptions[mod.id] = loader.modOptions[mod.id] or {}
      loader.modOptions[mod.id].language = value
    end
    if g.writeOptions then pcall(g.writeOptions, g)
    elseif g.persistOptions then pcall(g.persistOptions, g) end
  end

  local function labelOf(id)
    for _, c in ipairs(choices) do if c[2] == id then return c[1] end end
    return "ENGLISH"
  end

  local function tr(text)
    local Strings = engine("src.core.Strings")
    if Strings then
      local ok, out = pcall(Strings, text)
      if ok and type(out) == "string" then return out end
    end
    return text
  end

  mod.hooks:wrap("ui.options.rows", function(next, g, rows)
    local out = next(g, rows)
    -- pack mods register after this chunk runs, so decide on every call
    if type(out) ~= "table" or #choices < 2 then return out end
    local row = {
      id = "easy_language_packs",
      label = tr("LANGUAGE"),
      value = function() return labelOf(current or chosen()) end,
      -- LEFT/RIGHT (or A) steps through ENGLISH and the installed packs
      step = function(g2, dir)
        local now = current or chosen()
        local i = 1
        for k, c in ipairs(choices) do if c[2] == now then i = k end end
        i = (i - 1 + (dir or 1)) % #choices + 1
        local id = choices[i][2]
        store(g2, id)
        switchTo(id)
        return true
      end,
    }
    -- Gold's OPTION screen has no MODS row; CANCEL is its last one
    local anchor = GEN == "gen2" and "CANCEL" or tr("MODS")
    return mod.ui.insertBefore(out, anchor, row)
  end)

  -- ---- name entry -------------------------------------------------------------
  mod.hooks:wrap("ui.naming.grid", function(next, base, ctx)
    local grid = next(base, ctx)
    if not naming then return grid end
    local want = ctx and ctx.lower and naming.lower or naming.upper
    return want or grid
  end)
end
