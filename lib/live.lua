-- Applies a language pack straight onto the running game's data, and can
-- take it off again.
--
-- Every slot written (a table + field) remembers the value it had before the
-- first write.  Switching language restores all of them, then applies the
-- new pack, so a switch never stacks one translation on another and ENGLISH
-- puts back exactly what was there -- including other mods' own renames.
--
-- Where each kind lives at runtime (after the mod merge):
--   Gen 1  data.text, data.strings, data.pokemon[id].{name,dexEntry},
--          data.moves / items / trainers [id].name, data.statuses[id].label
--   Gen 2  data.gen2Text (script text) + data.text (label-keyed prose),
--          data.gen2Pokedex.entries[id], data.gen2Trainers.classes[id]
local Live = {}
Live.__index = Live

local function splitKey(key)
  local base, h = key:match("^(.-)#(%x%x%x%x%x%x%x%x)$")
  if base then return base, h:lower() end
  return key, nil
end
Live.splitKey = splitKey

function Live.new(data, gen, hash)
  local self = setmetatable({ data = data, gen = gen, hash = hash.fnv1a,
                              saved = {}, order = {}, index = nil }, Live)
  if type(data.strings) ~= "table" then data.strings = {} end
  return self
end

-- ---- slots --------------------------------------------------------------

-- the value before this mod touched it
function Live:original(tbl, field)
  local per = self.saved[tbl]
  if per and per[field] ~= nil then
    local v = per[field]
    if v == Live.NIL then return nil end
    return v
  end
  return tbl[field]
end
Live.NIL = {}

function Live:set(tbl, field, value)
  local per = self.saved[tbl]
  if not per then
    per = {}
    self.saved[tbl] = per
    self.order[#self.order + 1] = tbl
  end
  if per[field] == nil then
    local old = tbl[field]
    per[field] = old == nil and Live.NIL or old
  end
  tbl[field] = value
end

function Live:restore()
  for _, tbl in ipairs(self.order) do
    for field, old in pairs(self.saved[tbl]) do
      if old == Live.NIL then tbl[field] = nil else tbl[field] = old end
    end
  end
  self.saved, self.order = {}, {}
end

-- ---- where things are -----------------------------------------------------

function Live:dialogueTables()
  local d = self.data
  if self.gen == "gen2" then
    return { d.gen2Text, d.text }
  end
  return { d.text }
end

-- English line -> every (table, label) holding it, from the originals.
-- Built once, before anything is written.
function Live:hashIndex()
  if self.index then return self.index end
  local index = {}
  for _, tbl in ipairs(self:dialogueTables()) do
    if type(tbl) == "table" then
      for label in pairs(tbl) do
        local v = self:original(tbl, label)
        if type(v) == "string" and v ~= "" then
          local h = self.hash(v)
          local hits = index[h]
          if not hits then hits = {} index[h] = hits end
          hits[#hits + 1] = { tbl, label }
        end
      end
    end
  end
  self.index = index
  return index
end

local NAME_TABLE = {
  species = function(d) return d.pokemon end,
  moves = function(d) return d.moves end,
  items = function(d) return d.items end,
  statuses = function(d) return d.statuses end,
  status_hud = function(d) return d.statuses end,
}
local NAME_FIELD = { species = "name", moves = "name", items = "name", trainers = "name",
                     statuses = "label", status_hud = "hudLabel" }
local DEX_FIELD = { dex_kind = "kind", dex_text = "text", dex_text2 = "text2" }

function Live:record(kind, id)
  local d = self.data
  if kind == "trainers" then
    local t = self.gen == "gen2" and d.gen2Trainers and d.gen2Trainers.classes or d.trainers
    return t and t[id]
  end
  if DEX_FIELD[kind] then
    if self.gen == "gen2" then
      local dex = d.gen2Pokedex and d.gen2Pokedex.entries
      return dex and dex[id]
    end
    local mon = d.pokemon and d.pokemon[id]
    return mon and mon.dexEntry
  end
  local t = NAME_TABLE[kind] and NAME_TABLE[kind](d)
  return t and t[id]
end

-- ---- applying one entry ------------------------------------------------------

local OTHER = "English differs in this game version"
Live.OTHER = OTHER

function Live:apply(kind, key, text)
  local id, h = splitKey(key)
  if kind == "strings" then
    self:set(self.data.strings, key, text)
    return true
  end
  if kind == "dialogue" then
    if id == "" then
      local hits = h and self:hashIndex()[h]
      if not hits then return false, OTHER end
      for _, hit in ipairs(hits) do self:set(hit[1], hit[2], text) end
      return true
    end
    for _, tbl in ipairs(self:dialogueTables()) do
      if type(tbl) == "table" and self:original(tbl, id) ~= nil then
        local current = self:original(tbl, id)
        if h and (type(current) ~= "string" or self.hash(current) ~= h) then
          return false, OTHER
        end
        self:set(tbl, id, text)
        return true
      end
    end
    if h then return false, OTHER end
    return false, ("no dialogue label '%s' in this game"):format(id)
  end
  local rec = self:record(kind, id)
  if type(rec) ~= "table" then
    return false, ("unknown %s id '%s'"):format(kind, id)
  end
  local field = DEX_FIELD[kind] or NAME_FIELD[kind]
  if not field then return false, "unknown kind " .. tostring(kind) end
  -- Gen 1 Pokedex entries: dexEntry.text is a label (_MewDexEntry) and the
  -- screen reads data.text[label], so the words go there, not in the field
  if self.gen == "gen1" and kind == "dex_text" then
    local texts = self.data.text
    local label = self:original(rec, "text")
    if type(texts) == "table" and type(label) == "string"
        and type(self:original(texts, label)) == "string" then
      if h and self.hash(self:original(texts, label)) ~= h then return false, OTHER end
      self:set(texts, label, text)
      return true
    end
    -- no label to write through (another mod put literal text here): give
    -- the entry a label of its own
    if h then
      local current = self:original(rec, "text")
      if type(current) ~= "string" or self.hash(current) ~= h then return false, OTHER end
    end
    local own = "_easy_language_packs_dex_" .. id
    self:set(texts, own, text)
    self:set(rec, "text", own)
    return true
  end
  if h then
    local current = self:original(rec, field)
    if type(current) ~= "string" or self.hash(current) ~= h then return false, OTHER end
  end
  self:set(rec, field, text)
  return true
end

-- Applies a whole pack's entries.  Returns counts per kind, rows for other
-- versions, and a list of { where, reason } for rows that could not apply.
function Live:applyAll(entries)
  -- rows for another generation drop out; per kind+key+scope the last one wins
  local final, order = {}, {}
  for _, e in ipairs(entries) do
    if e.game == nil or e.game[self.gen] then
      local id = e.kind .. "\0" .. e.key .. "\0" .. (e.game and "scoped" or "")
      if not final[id] then order[#order + 1] = id end
      final[id] = e
    end
  end
  -- more specific rows go later so they win: scoped beats unscoped, and a
  -- version-pinned key beats a plain one
  local list = {}
  for i, id in ipairs(order) do
    local e = final[id]
    local _, h = splitKey(e.key)
    list[#list + 1] = { e = e, rank = (e.game and 2 or 0) + (h and 1 or 0), i = i }
  end
  table.sort(list, function(a, b)
    if a.rank ~= b.rank then return a.rank < b.rank end
    return a.i < b.i
  end)
  self:hashIndex() -- from the originals, before the first write
  local counts, other, failed = {}, 0, {}
  for _, item in ipairs(list) do
    local e = item.e
    local ok, applied, why = pcall(self.apply, self, e.kind, e.key, e.text)
    if ok and applied then
      counts[e.kind] = (counts[e.kind] or 0) + 1
    elseif ok and why == OTHER then
      other = other + 1
    else
      failed[#failed + 1] = { where = e.where, reason = tostring(ok and why or applied) }
    end
  end
  return counts, other, failed
end

return Live
