-- Shared test harness: boots the mod through the engine's real mod loader
-- (sandbox, manifest validation, events, hooks) over an in-memory filesystem.
--
-- Run from the engine checkout's root (tests/run.py does this):
--   luajit <mod>/tests/lua/<name>_test.lua <mod>
package.path = "./?.lua;./?/init.lua;" .. package.path
if not _G.love then _G.love = require("tests.love_stub") end

local Loader = require("src.mods.Loader")
local Strings = require("src.core.Strings")
local SaveData = require("src.core.SaveData")

local H = {}
H.MOD = assert(arg[1], "usage: luajit <test> <mod root>")
H.ID = "easy_language_packs"
H.Strings = Strings

local pass, fail = 0, 0
function H.eq(got, want, msg)
  if got == want then
    pass = pass + 1
  else
    fail = fail + 1
    print("FAIL " .. msg .. "\n  got:  " .. tostring(got) .. "\n  want: " .. tostring(want))
  end
end
function H.ok(cond, msg) H.eq(cond and true or false, true, msg) end
function H.finish(name)
  print(("%s: %d passed, %d failed"):format(name, pass, fail))
  os.exit(fail == 0 and 0 or 1)
end

local function slurp(p)
  local f = assert(io.open(p, "rb"))
  local s = f:read("*a")
  f:close()
  return s
end
H.slurp = slurp

-- the mod as the game would see it: tracked mod files, no tooling
local SKIP = { ["tests/"] = true, [".github/"] = true, [".git/"] = true }
local function modFiles(extra, mods)
  local files = {}
  -- other installed mods (pack mods): { [mod id] = { [path] = body } }
  for id, tree in pairs(mods or {}) do
    for k, v in pairs(tree) do files["mods/" .. id .. "/" .. k] = v end
  end
  local cmd = ('cd "%s" && find . -type f'):format(H.MOD)
  for rel in io.popen(cmd):lines() do
    rel = rel:sub(3)
    local top = rel:match("^[^/]+/")
    local isPack = rel:match("^packs/") and not rel:match("^packs/_example/")
    if not (top and SKIP[top]) and not isPack and rel ~= "deploy.py"
        and not rel:match("__pycache__") then
      files["mods/" .. H.ID .. "/" .. rel] = slurp(H.MOD .. "/" .. rel)
    end
  end
  for k, v in pairs(extra or {}) do files["mods/" .. H.ID .. "/" .. k] = v end
  return files
end
H.modFiles = modFiles

local function memfs(files)
  return {
    read = function(path) return files[path] end,
    load = function(path) return load(files[path], path) end,
    getInfo = function(path)
      if files[path] then return { type = "file", size = #files[path] } end
      local prefix = path .. "/"
      for key in pairs(files) do
        if key:sub(1, #prefix) == prefix then return { type = "directory" } end
      end
    end,
    getDirectoryItems = function(path)
      local seen, items, prefix = {}, {}, path .. "/"
      for key in pairs(files) do
        if key:sub(1, #prefix) == prefix then
          local child = key:sub(#prefix + 1):match("^[^/]+")
          if child and not seen[child] then seen[child] = true items[#items + 1] = child end
        end
      end
      table.sort(items)
      return items
    end,
  }
end

-- Boot: load the mod, then fire game.ready the way Game.lua / Game2.lua do.
-- opts: gen (1|2), version, language (saved choice), extra (files added to
-- this mod, e.g. packs/), mods (other mods: { [id] = { [path] = body } })
function H.boot(data, opts)
  opts = opts or {}
  local real = SaveData.loadOptions
  SaveData.loadOptions = function()
    return { modOptions = opts.language and { [H.ID] = { language = opts.language } } or {} }
  end
  local loader = Loader.new({ fs = memfs(modFiles(opts.extra, opts.mods)), generation = opts.gen or 1,
                              version = opts.version or "red" })
  local G = { data = data, save = { options = { modOptions = {} } }, writes = 0 }
  G.writeOptions = function(self) self.writes = self.writes + 1 end
  G.mods = loader
  loader.game = G
  local ok = loader:load(data)
  SaveData.loadOptions = real
  data.font = data.font or {}
  if type(data.strings) ~= "table" then data.strings = {} end
  loader.events:emit("game.ready", { game = G })
  return ok, G, loader
end

-- the LANGUAGE row as the Options screen would build it
function H.optionRow(loader, G)
  local rows = loader.hooks:call("ui.options.rows", function(_, r) return r end, G,
    { { id = "textSpeed", label = "TEXT SPEED" }, { id = "mods", label = "MODS" } })
  for i, r in ipairs(rows) do
    if r.id == H.ID then return r, i, rows end
  end
end

function H.stepTo(row, G, value, dir)
  for _ = 1, 20 do
    if row.value(G) == value then return true end
    row.step(G, dir or 1)
  end
  return row.value(G) == value
end

function H.deepcopy(t, seen)
  if type(t) ~= "table" then return t end
  seen = seen or {}
  if seen[t] then return seen[t] end
  local o = {}
  seen[t] = o
  for k, v in pairs(t) do o[k] = H.deepcopy(v, seen) end
  return o
end

-- nil when equal, else the first differing path
function H.diff(a, b, path)
  path = path or "data"
  if type(a) ~= type(b) then return path .. ": " .. type(a) .. " vs " .. type(b) end
  if type(a) ~= "table" then
    if a ~= b then return ("%s: %q vs %q"):format(path, tostring(a), tostring(b)) end
    return nil
  end
  for k, v in pairs(a) do
    local d = H.diff(v, b[k], path .. "." .. tostring(k))
    if d then return d end
  end
  for k in pairs(b) do
    if a[k] == nil then return path .. "." .. tostring(k) .. ": extra" end
  end
end

-- A pack mod as `lpk.py release` writes it: its manifest, the generated
-- main.lua (tools/pack_mod_main.lua) and the pack under pack/.
-- files: { [path inside the pack] = body }; opts: version, needs, id
function H.packMod(files, opts)
  opts = opts or {}
  local manifest = ('{"id":"%s","name":"pack","version":"0.1.0","api":2,"entry":"main.lua",'
    .. '"profile":"content","category":"UI","games":["gen1","gen2"],'
    .. '"game_version":">=0.0.0-0 <2.0.0","dependencies":["%s@%s"]}')
    :format(opts.id or "elp_test", H.ID, opts.needs or "^1.2.0")
  local tree = { ["manifest.json"] = manifest,
                 ["main.lua"] = slurp(H.MOD .. "/tools/pack_mod_main.lua") }
  for k, v in pairs(files) do tree["pack/" .. k] = v end
  return tree
end

-- the mod's own hash, for building version-pinned keys in tests
function H.hash(s)
  local Hash = assert(loadfile(H.MOD .. "/lib/hash.lua"))()
  return Hash.fnv1a(s)
end

return H
