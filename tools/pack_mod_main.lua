-- A language pack for Easy Language Packs, made by `lpk.py release`.
-- The translation is in pack/. This file only hands it over: Easy Language
-- Packs can't read another mod's folder, so the pack lends it these readers.
-- The pack id is this mod's id without "elp_" (elp_es -> es).
return function(mod)
  local elp = mod.find("easy_language_packs")
  local register = elp and elp.exports and elp.exports.registerPack
  if type(register) ~= "function" then
    mod.log:warn("this language pack needs Easy Language Packs 1.2.0 or newer")
    return
  end
  register({
    api = 1,
    id = (mod.id:gsub("^elp_", "")),
    dir = "pack",
    mod = mod.id,
    read = function(path) return mod:read(path) end,
    list = function(path) return mod:list(path) end,
    info = function(path) return mod:info(path) end,
    assetPath = function(path) return mod.assets:path(path) end,
  })
end
