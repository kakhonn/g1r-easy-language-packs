-- Gen 3 script text: the engine keeps each line as a list of segments (its
-- "IR": text, nl, scroll, para, player, strvar, ph, bph, ext, ...).  A sheet
-- holds the line as one string instead, and fingerprints it, so both
-- directions are needed:
--
--   G3.source(ir, dialect)  -> the line as lpk.py writes it: \n new line,
--                              \v scrolling line, \f new page, placeholders in
--                              pokeemerald's {BRACE} names.  MUST match
--                              lpk.py's ir_source() exactly: the dialogue
--                              keys are its hash.
--   G3.parse(text, dialect) -> a translation back into segments the engine
--                              draws, placeholders filled in live.
--
-- dialect: "frlg" (FireRed/LeafGreen), "rs" (Ruby/Sapphire), "rse" (Emerald);
-- they number their placeholders differently.
local G3 = {}

local B_TXT = {
  [0x00] = "B_BUFF1", [0x01] = "B_BUFF2", [0x02] = "B_COPY_VAR_1", [0x03] = "B_COPY_VAR_2",
  [0x04] = "B_COPY_VAR_3", [0x05] = "B_PLAYER_MON1_NAME", [0x06] = "B_OPPONENT_MON1_NAME",
  [0x07] = "B_PLAYER_MON2_NAME", [0x08] = "B_OPPONENT_MON2_NAME",
  [0x09] = "B_LINK_PLAYER_MON1_NAME", [0x0A] = "B_LINK_OPPONENT_MON1_NAME",
  [0x0B] = "B_LINK_PLAYER_MON2_NAME", [0x0C] = "B_LINK_OPPONENT_MON2_NAME",
  [0x0D] = "B_ATK_NAME_WITH_PREFIX_MON1", [0x0E] = "B_ATK_PARTNER_NAME",
  [0x0F] = "B_ATK_NAME_WITH_PREFIX", [0x10] = "B_DEF_NAME_WITH_PREFIX",
  [0x11] = "B_EFF_NAME_WITH_PREFIX", [0x12] = "B_ACTIVE_NAME_WITH_PREFIX",
  [0x13] = "B_SCR_ACTIVE_NAME_WITH_PREFIX", [0x14] = "B_CURRENT_MOVE", [0x15] = "B_LAST_MOVE",
  [0x16] = "B_LAST_ITEM", [0x17] = "B_LAST_ABILITY", [0x18] = "B_ATK_ABILITY",
  [0x19] = "B_DEF_ABILITY", [0x1A] = "B_SCR_ACTIVE_ABILITY", [0x1B] = "B_EFF_ABILITY",
  [0x1C] = "B_TRAINER1_CLASS", [0x1D] = "B_TRAINER1_NAME", [0x1E] = "B_LINK_PLAYER_NAME",
  [0x1F] = "B_LINK_PARTNER_NAME", [0x20] = "B_LINK_OPPONENT1_NAME",
  [0x21] = "B_LINK_OPPONENT2_NAME", [0x22] = "B_LINK_SCR_TRAINER_NAME",
  [0x23] = "B_PLAYER_NAME", [0x24] = "B_TRAINER1_LOSE_TEXT", [0x25] = "B_TRAINER1_WIN_TEXT",
  [0x26] = "B_26", [0x27] = "B_PC_CREATOR_NAME", [0x28] = "B_ATK_PREFIX1",
  [0x29] = "B_DEF_PREFIX1", [0x2A] = "B_ATK_PREFIX2", [0x2B] = "B_DEF_PREFIX2",
  [0x2C] = "B_ATK_PREFIX3", [0x2D] = "B_DEF_PREFIX3", [0x2E] = "B_TRAINER2_LOSE_TEXT",
  [0x2F] = "B_TRAINER2_WIN_TEXT", [0x30] = "B_BUFF3",
}
-- Emerald renumbers from 0x2E; Ruby/Sapphire lack the COPY_VAR and LINK_ ones
local B_TXT_RSE = {}
for code, name in pairs(B_TXT) do if code < 0x2E then B_TXT_RSE[code] = name end end
B_TXT_RSE[0x2E], B_TXT_RSE[0x2F] = "B_TRAINER2_CLASS", "B_TRAINER2_NAME"
B_TXT_RSE[0x30], B_TXT_RSE[0x31] = "B_TRAINER2_LOSE_TEXT", "B_TRAINER2_WIN_TEXT"
B_TXT_RSE[0x32], B_TXT_RSE[0x33], B_TXT_RSE[0x34] = "B_PARTNER_CLASS", "B_PARTNER_NAME", "B_BUFF3"
local B_TXT_RS = { [0] = "B_BUFF1", [1] = "B_BUFF2" }
for c = 0x02, 0x21 do B_TXT_RS[c] = B_TXT[c + 3] end
B_TXT_RS[0x22] = "B_26"
for c = 0x23, 0x29 do B_TXT_RS[c] = B_TXT[c + 4] end
B_TXT_RS[0x2A] = "B_BUFF3"

local function list(first, ...)
  local t = { [0] = first }
  for i, v in ipairs({ ... }) do t[i] = v end
  return t
end
local PH_COMMON = { "UNKNOWN", "PLAYER", "STR_VAR_1", "STR_VAR_2", "STR_VAR_3", "KUN", "RIVAL", "VERSION" }
local function ph(...)
  local all = {}
  for _, v in ipairs(PH_COMMON) do all[#all + 1] = v end
  for _, v in ipairs({ ... }) do all[#all + 1] = v end
  return list((table.unpack or unpack)(all))
end

G3.DIALECTS = {
  frlg = { b = B_TXT, ph = ph("MAGMA", "AQUA", "MAXIE", "ARCHIE", "GROUDON", "KYOGRE") },
  rse = { b = B_TXT_RSE, ph = ph("AQUA", "MAGMA", "ARCHIE", "MAXIE", "KYOGRE", "GROUDON"), named = true },
  rs = { b = B_TXT_RS, ph = ph("EVIL_TEAM", "GOOD_TEAM", "EVIL_LEADER", "GOOD_LEADER",
                               "EVIL_LEGENDARY", "GOOD_LEGENDARY"), named = true },
}
for _, d in pairs(G3.DIALECTS) do
  d.bCode, d.phCode = {}, {}
  for code, name in pairs(d.b) do d.bCode[name] = code end
  for code, name in pairs(d.ph) do d.phCode[name] = code end
end

G3.DIALECT_OF = { firered = "frlg", leafgreen = "frlg", ruby = "rs", sapphire = "rs", emerald = "rse" }

local function dialect(name)
  return G3.DIALECTS[name] or G3.DIALECTS.frlg
end

local fmt = string.format

-- lpk.py ir_source(), segment for segment
function G3.source(ir, dialectName)
  local d = dialect(dialectName)
  local out = {}
  for _, seg in ipairs(type(ir) == "table" and ir or {}) do
    if type(seg) == "table" then
      local t = seg.t
      if t == "eos" then break end
      if t == "nl" then out[#out + 1] = "\n"
      elseif t == "scroll" then out[#out + 1] = "\v"
      elseif t == "para" then out[#out + 1] = "\f"
      elseif t == "text" then out[#out + 1] = seg.s or ""
      elseif t == "tag" then out[#out + 1] = seg.tag or ""
      elseif t == "strvar" then out[#out + 1] = fmt("{STR_VAR_%d}", seg.n or 0)
      elseif t == "player" or t == "rival" then out[#out + 1] = "{" .. t:upper() .. "}"
      elseif t == "ph" then
        local code = seg.code
        local name = seg.name
        if name == "" then name = nil end -- falsy in Python too
        if not name and type(code) == "number" and code >= 0 then name = d.ph[code] end
        out[#out + 1] = "{" .. (name or fmt("PH_%02X", code or 0)) .. "}"
      elseif t == "bph" then
        local code = seg.code or 0
        out[#out + 1] = "{" .. (d.b[code] or ("B_" .. tostring(code))) .. "}"
      elseif t == "dynamic" then out[#out + 1] = fmt("{DYNAMIC_%d}", seg.n or 0)
      elseif t == "ext" then
        local cmd = seg.cmd or 0
        cmd = type(cmd) == "number" and fmt("%02X", cmd) or tostring(cmd)
        local args = {}
        for _, a in ipairs(type(seg.args) == "table" and seg.args or {}) do
          args[#args + 1] = "_" .. tostring(a)
        end
        out[#out + 1] = "{EXT_" .. cmd .. table.concat(args) .. "}"
      else
        out[#out + 1] = "{" .. tostring(t) .. "}"
      end
    end
  end
  return table.concat(out)
end

-- one {NAME} token -> a segment
local function token(name, d)
  if name == "PLAYER" then return { t = "player" } end
  if name == "RIVAL" then return { t = "rival" } end
  local n = name:match("^STR_VAR_(%d+)$")
  if n then return { t = "strvar", n = tonumber(n) } end
  n = name:match("^DYNAMIC_(%d+)$")
  if n then return { t = "dynamic", n = tonumber(n) } end
  local cmd, rest = name:match("^EXT_(%x%x)(.*)$")
  if cmd and (rest == "" or rest:match("^[_%d]+$")) then
    local args = {}
    for a in rest:gmatch("_(%d+)") do args[#args + 1] = tonumber(a) end
    return { t = "ext", cmd = tonumber(cmd, 16), args = args }
  end
  if d.bCode[name] then return { t = "bph", code = d.bCode[name] } end
  n = name:match("^B_(%d+)$")
  if n then return { t = "bph", code = tonumber(n) } end
  if d.phCode[name] then
    -- the engine names placeholders only in the Ruby/Sapphire/Emerald dialects
    return { t = "ph", code = d.phCode[name], name = d.named and name or nil }
  end
  n = name:match("^PH_(%x%x)$")
  if n then return { t = "ph", code = tonumber(n, 16) } end
  return { t = "tag", tag = "{" .. name .. "}" }
end

-- A translation (as the sheet holds it, escapes already decoded) -> IR
function G3.parse(text, dialectName)
  local d = dialect(dialectName)
  local out, buf = {}, {}
  local function flush()
    if #buf > 0 then
      out[#out + 1] = { t = "text", s = table.concat(buf) }
      buf = {}
    end
  end
  local i, n = 1, #text
  while i <= n do
    local c = text:sub(i, i)
    if c == "\n" then flush() out[#out + 1] = { t = "nl" }
    elseif c == "\v" then flush() out[#out + 1] = { t = "scroll" }
    elseif c == "\f" then flush() out[#out + 1] = { t = "para" }
    elseif c == "\r" then -- dropped
    elseif c == "{" then
      local j = text:find("}", i + 1, true)
      local name = j and text:sub(i + 1, j - 1)
      if name and name:match("^[%w_]+$") then
        flush()
        out[#out + 1] = token(name, d)
        i = j
      else
        buf[#buf + 1] = c
      end
    else
      buf[#buf + 1] = c
    end
    i = i + 1
  end
  flush()
  out[#out + 1] = { t = "eos" }
  return out
end

return G3
