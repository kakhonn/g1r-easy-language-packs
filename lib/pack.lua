-- Reads a language pack directory into a flat list of entries.
--
-- Pure logic: all filesystem access goes through the `fs` table handed in
-- ({ read = fn(path) -> string|nil, list = fn(dir) -> {names}, info =
-- fn(path) -> {type=...}|nil }), so the same code runs inside the mod
-- sandbox and in tests.
--
-- An entry is { kind = <canonical kind>, key = ..., text = ..., where = "file:line" }.

local Pack = {}

-- canonical kind -> accepted spellings (filename stems and `kind` column values)
Pack.KINDS = {
  dialogue   = { "dialogue", "text", "script" },
  strings    = { "strings", "engine", "ui" },
  species    = { "species", "pokemon", "species_names", "pokemon_names" },
  moves      = { "moves", "move", "move_names" },
  items      = { "items", "item", "item_names" },
  trainers   = { "trainers", "trainer", "trainer_names", "trainer_classes" },
  statuses   = { "statuses", "status", "status_labels" },
  status_hud = { "status_hud" },
  dex_kind   = { "dex_kind", "dex", "dex_category", "dex_species" },
  dex_text   = { "dex_text", "dex_entry" },
  dex_text2  = { "dex_text2" },
}

local ALIAS = {}
for canon, names in pairs(Pack.KINDS) do
  for _, n in ipairs(names) do ALIAS[n] = canon end
end

function Pack.kind(name)
  if type(name) ~= "string" then return nil end
  return ALIAS[name:lower():gsub("^%s+", ""):gsub("%s+$", "")]
end

-- `game` scopes a row to generations: "gen1", "gen2 gen3", "gen1,gen2".
-- Empty means every game.  Returns a set, nil for "all", or false + the bad
-- word when it does not parse.
local GAMES = { gen1 = true, gen2 = true, gen3 = true }
function Pack.games(value)
  if value == nil or value == "" then return nil end
  if type(value) ~= "string" then return false, tostring(value) end
  local set, any = {}, false
  for word in value:lower():gmatch("[^%s,;/|]+") do
    if word == "all" or word == "any" then return nil end
    if not GAMES[word] then return false, word end
    set[word], any = true, true
  end
  return any and set or nil
end

local KEY_COLUMNS = { "key", "id", "label", "source" }
local TEXT_COLUMNS = { "text", "translation", "value", "target" }

local function column(row, names)
  for _, n in ipairs(names) do
    if row[n] ~= nil then return row[n], n end
  end
end

-- Spreadsheet cells cannot hold the game's control characters directly, so
-- CSV/TSV cells use backslash escapes, the same ones the worksheets print:
--   \n new line   \v scrolling line (pokered `cont`)   \f new page (`para`)
--   \t tab        \\ a literal backslash
-- Anything else after a backslash is left exactly as written.
local CSV_ESC = { n = "\n", v = "\v", f = "\f", t = "\t", r = "\r", ["\\"] = "\\" }
function Pack.unescape(s)
  return (s:gsub("\\(.)", function(c) return CSV_ESC[c] or ("\\" .. c) end))
end

local function stem(file)
  return (file:match("([^/]+)%.[^.]+$") or file):lower()
end

local function ext(file)
  return (file:match("%.([^.]+)$") or ""):lower()
end

-- ---------------------------------------------------------------- CSV / TSV

local function fromCsv(Csv, body, file, out, problems)
  local parsed, err = Csv.parse(body, { delimiter = ext(file) == "tsv" and "\t" or nil })
  if not parsed then
    problems[#problems + 1] = file .. ": " .. err
    return
  end
  local fileKind = Pack.kind(stem(file))
  local hasKind = false
  for _, h in ipairs(parsed.header) do if h == "kind" then hasKind = true end end
  if not hasKind and not fileKind then
    problems[#problems + 1] = ("%s: no 'kind' column, and the file name is not a kind "
      .. "(name it e.g. dialogue.csv, or add a kind column)"):format(file)
    return
  end
  local probe = parsed.rows[1] or {}
  if #parsed.rows > 0 and (not column(probe, KEY_COLUMNS) or not column(probe, TEXT_COLUMNS)) then
    problems[#problems + 1] = ("%s: needs a 'key' column and a 'text' column (header was: %s)")
      :format(file, table.concat(parsed.header, ", "))
    return
  end
  for _, row in ipairs(parsed.rows) do
    local where = file .. ":" .. row._line
    local rawKind = hasKind and row.kind or nil
    local key = column(row, KEY_COLUMNS)
    local text = column(row, TEXT_COLUMNS)
    -- a row is a comment when its kind (or, with no kind column, its key)
    -- starts with "#" -- but "#1a2b3c4d" is a hash key, not a comment
    local hashKey = key and key:match("^#%x%x%x%x%x%x%x%x$")
    if (rawKind and rawKind:match("^%s*#")) or (key and key:match("^#") and not hashKey) then
      -- comment row
    elseif text ~= nil and text ~= "" then
      local kind = (rawKind and rawKind ~= "") and Pack.kind(rawKind) or fileKind
      if not kind then
        problems[#problems + 1] = ("%s: unknown kind '%s'"):format(where, tostring(rawKind))
      elseif not key or key == "" then
        problems[#problems + 1] = where .. ": empty key"
      else
        local game, bad = Pack.games(row.game)
        if game == false then
          problems[#problems + 1] = ("%s: unknown game '%s' (use gen1, gen2 or gen3)"):format(where, bad)
        else
          out[#out + 1] = { kind = kind, key = Pack.unescape(key), game = game,
                            text = Pack.unescape(text), where = where }
        end
      end
    end
  end
end

-- ---------------------------------------------------------------- JSON
--
-- Three shapes are accepted:
--   1. sectioned:  { "dialogue": { "_Label": "..." }, "moves": { "TACKLE": "..." } }
--   2. flat map in a file named after its kind:  moves.json = { "TACKLE": "..." }
--   3. a list of rows:  [ { "kind": "moves", "key": "TACKLE", "text": "..." } ]
-- Keys beginning with "_comment" or "//" are ignored everywhere.

local function ignorable(k)
  return type(k) == "string" and (k:sub(1, 2) == "//" or k:sub(1, 8) == "_comment"
    or k == "$schema" or k == "game")
end

local function sortedPairs(t)
  local keys = {}
  for k in pairs(t) do keys[#keys + 1] = k end
  table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
  local i = 0
  return function()
    i = i + 1
    local k = keys[i]
    if k ~= nil then return k, t[k] end
  end
end

local function addMap(kind, map, file, section, out, problems, game)
  for key, text in sortedPairs(map) do
    if not ignorable(key) then
      local where = ("%s: %s.%s"):format(file, section, tostring(key))
      if type(text) == "string" then
        if text ~= "" then
          out[#out + 1] = { kind = kind, key = key, text = text, where = where, game = game }
        end
      elseif type(text) ~= "table" or next(text) ~= nil then
        problems[#problems + 1] = where .. ": value must be a string"
      end
    end
  end
end

local function fromJson(Json, body, file, out, problems)
  local doc, err = Json.decode(body)
  if doc == nil then
    problems[#problems + 1] = file .. ": " .. tostring(err)
    return
  end
  if type(doc) ~= "table" then
    problems[#problems + 1] = file .. ": top level must be an object or a list"
    return
  end
  if doc[1] ~= nil then -- list of rows
    for i, row in ipairs(doc) do
      local where = ("%s: [%d]"):format(file, i)
      if type(row) ~= "table" then
        problems[#problems + 1] = where .. ": each row must be an object"
      else
        local kind = Pack.kind(row.kind) or Pack.kind(stem(file))
        local key, text = column(row, KEY_COLUMNS), column(row, TEXT_COLUMNS)
        if type(text) == "string" and text ~= "" then
          if not kind then
            problems[#problems + 1] = ("%s: unknown kind '%s'"):format(where, tostring(row.kind))
          elseif type(key) ~= "string" or key == "" then
            problems[#problems + 1] = where .. ": missing key"
          else
            local game, bad = Pack.games(row.game)
            if game == false then
              problems[#problems + 1] = ("%s: unknown game '%s'"):format(where, bad)
            else
              out[#out + 1] = { kind = kind, key = key, text = text, where = where, game = game }
            end
          end
        end
      end
    end
    return
  end
  -- sectioned if every non-comment top-level key is a kind holding an object
  local sectioned, any = true, false
  for k, v in pairs(doc) do
    if not ignorable(k) then
      any = true
      if not (Pack.kind(k) and type(v) == "table") then sectioned = false end
    end
  end
  -- a top-level "game": "gen2" scopes every entry in the file
  local game, bad = Pack.games(doc.game)
  if game == false then
    problems[#problems + 1] = ("%s: unknown game '%s'"):format(file, bad)
    return
  end
  if any and sectioned then
    for section, map in sortedPairs(doc) do
      if not ignorable(section) then
        addMap(Pack.kind(section), map, file, section, out, problems, game)
      end
    end
    return
  end
  local kind = Pack.kind(stem(file))
  if not kind then
    problems[#problems + 1] = ("%s: not sectioned by kind, and the file name is not a kind "
      .. "(use { \"dialogue\": {...}, \"moves\": {...} } or name it e.g. moves.json)"):format(file)
    return
  end
  addMap(kind, doc, file, stem(file), out, problems, game)
end

-- ---------------------------------------------------------------- packs

local DATA_EXT = { json = true, csv = true, tsv = true }

-- Every data file in a pack directory, recursively (unsorted: Pack.read sorts).
local function dataFiles(fs, dir, prefix, out)
  for _, name in ipairs(fs.list(dir) or {}) do
    local path = dir .. "/" .. name
    local info = fs.info(path)
    if info and info.type == "directory" then
      if name:sub(1, 1) ~= "." and name:sub(1, 1) ~= "_" then
        dataFiles(fs, path, prefix .. name .. "/", out)
      end
    elseif DATA_EXT[ext(name)] and not (prefix == "" and name:lower() == "pack.json") then
      out[#out + 1] = prefix .. name
    end
  end
  return out
end

-- Pack.meta(fs, Json, dir) -> metadata table (possibly empty), problems
function Pack.meta(fs, Json, dir)
  local body = fs.read(dir .. "/pack.json")
  if not body then return {}, {} end
  local meta, err = Json.decode(body)
  if type(meta) ~= "table" then
    return {}, { dir .. "/pack.json: " .. tostring(err or "must be an object") }
  end
  return meta, {}
end

-- Pack.describe(fs, Json, dir, id) -> { id=, label=, name=, example= }
-- What the LANGUAGE option shows for a pack: its label, else its name, else id.
function Pack.describe(fs, Json, dir, id)
  local meta = Pack.meta(fs, Json, dir)
  local label = type(meta.label) == "string" and meta.label
    or (type(meta.name) == "string" and meta.name) or id
  return { id = id, label = label, name = meta.name or id,
           example = meta.example == true, hidden = meta.hidden == true }
end

-- Pack.read(locate, libs, id) -> entries, meta, problems
-- locate(id) -> fs, dir of the pack with that id, or nil: a pack can live in
-- this mod's packs/ folder or in a pack mod, each read through its own fs.
-- Follows "extends" (a pack id or list of ids) so a regional variant only
-- needs the lines that differ; the base is read first, so the variant wins.
-- The base may live in the other place.
function Pack.read(locate, libs, id, seen)
  seen = seen or {}
  local entries, problems = {}, {}
  if seen[id] then
    return entries, {}, { ("pack '%s': extends itself in a loop"):format(id) }
  end
  seen[id] = true
  local fs, dir = locate(id)
  local info = fs and fs.info(dir)
  if not (info and info.type == "directory") then
    return entries, {}, { ("pack '%s' not found"):format(id) }
  end
  local meta, metaProblems = Pack.meta(fs, libs.json, dir)
  for _, p in ipairs(metaProblems) do problems[#problems + 1] = p end

  local bases = meta.extends
  if type(bases) == "string" then bases = { bases } end
  for _, base in ipairs(type(bases) == "table" and bases or {}) do
    -- a blank "extends" (as the template ships) means no base pack
    if type(base) == "string" and not base:match("^%s*$") then
      local e, _, p = Pack.read(locate, libs, base, seen)
      for _, x in ipairs(e) do entries[#entries + 1] = x end
      for _, x in ipairs(p) do problems[#problems + 1] = x end
    end
  end

  local files = meta.files
  if type(files) ~= "table" then
    -- sort whole paths, as lpk.py does: listing folder by folder would put
    -- "dex/a.csv" before "dex.csv"
    files = dataFiles(fs, dir, "", {})
    table.sort(files)
  end
  for _, rel in ipairs(files) do
    local path = dir .. "/" .. rel
    local body = fs.read(path)
    local shown = id .. "/" .. rel
    if not body then
      problems[#problems + 1] = shown .. ": cannot read"
    elseif ext(rel) == "json" then
      fromJson(libs.json, body, shown, entries, problems)
    else
      fromCsv(libs.csv, body, shown, entries, problems)
    end
  end
  return entries, meta, problems
end

-- Pack.list(fs, libs, packsDir) -> { Pack.describe(...), ... } sorted by id
function Pack.list(fs, libs, packsDir)
  local out = {}
  for _, name in ipairs(fs.list(packsDir) or {}) do
    local info = fs.info(packsDir .. "/" .. name)
    if info and info.type == "directory" and name:sub(1, 1) ~= "." then
      out[#out + 1] = Pack.describe(fs, libs.json, packsDir .. "/" .. name, name)
    end
  end
  table.sort(out, function(a, b) return a.id < b.id end)
  return out
end

return Pack
