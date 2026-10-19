-- Minimal, strict JSON decoder in pure Lua 5.1 (the mod sandbox has no json
-- library).  Objects -> tables, arrays -> sequences, null -> json.null.
-- \uXXXX escapes (including surrogate pairs) decode to UTF-8.
--
-- json.decode(text) -> value | nil, "line N col M: message"

local json = {}
json.null = setmetatable({}, { __tostring = function() return "null" end })

local byte, sub, find, char = string.byte, string.sub, string.find, string.char
local concat, floor = table.concat, math.floor

local function utf8(cp)
  if cp < 0x80 then return char(cp) end
  if cp < 0x800 then
    return char(0xC0 + floor(cp / 0x40), 0x80 + cp % 0x40)
  end
  if cp < 0x10000 then
    return char(0xE0 + floor(cp / 0x1000), 0x80 + floor(cp / 0x40) % 0x40,
      0x80 + cp % 0x40)
  end
  return char(0xF0 + floor(cp / 0x40000), 0x80 + floor(cp / 0x1000) % 0x40,
    0x80 + floor(cp / 0x40) % 0x40, 0x80 + cp % 0x40)
end

local ESC = { ['"'] = '"', ["\\"] = "\\", ["/"] = "/", b = "\b", f = "\f",
              n = "\n", r = "\r", t = "\t",
              -- not standard JSON, accepted on purpose: the game's scrolling
              -- line break (pokered `cont`) is \v, and \u000b is a chore
              v = "\v" }

function json.decode(text)
  if type(text) ~= "string" then return nil, "expected a string" end
  local pos = 1
  -- tolerate a UTF-8 byte-order mark (Notepad and Excel like to add one)
  if sub(text, 1, 3) == "\239\187\191" then pos = 4 end

  local function fail(msg)
    local line, col = 1, 1
    for i = 1, pos - 1 do
      if byte(text, i) == 10 then line, col = line + 1, 1 else col = col + 1 end
    end
    error({ json = ("line %d col %d: %s"):format(line, col, msg) }, 0)
  end

  local function skip()
    pos = find(text, "[^ \t\r\n]", pos) or (#text + 1)
  end

  local value

  local function str()
    pos = pos + 1 -- opening quote
    local out = {}
    while true do
      local s, e = find(text, '["\\\1-\31%z]', pos)
      if not s then fail("unterminated string") end
      out[#out + 1] = sub(text, pos, s - 1)
      local c = sub(text, s, s)
      if c == '"' then pos = e + 1 break end
      if c ~= "\\" then pos = s fail("control character in string (use \\n)") end
      local esc = sub(text, s + 1, s + 1)
      if esc == "u" then
        local hex = sub(text, s + 2, s + 5)
        if not hex:match("^%x%x%x%x$") then pos = s fail("bad \\u escape") end
        local cp = tonumber(hex, 16)
        pos = s + 6
        if cp >= 0xD800 and cp <= 0xDBFF then
          local lo = sub(text, pos, pos + 5):match("^\\u(%x%x%x%x)$")
          lo = lo and tonumber(lo, 16)
          if lo and lo >= 0xDC00 and lo <= 0xDFFF then
            cp = 0x10000 + (cp - 0xD800) * 0x400 + (lo - 0xDC00)
            pos = pos + 6
          end
        end
        out[#out + 1] = utf8(cp)
      elseif ESC[esc] then
        out[#out + 1] = ESC[esc]
        pos = s + 2
      else
        pos = s
        fail("unknown escape \\" .. esc)
      end
    end
    return concat(out)
  end

  local function obj()
    pos = pos + 1
    local out = {}
    skip()
    if sub(text, pos, pos) == "}" then pos = pos + 1 return out end
    while true do
      skip()
      if sub(text, pos, pos) ~= '"' then fail("expected a string key") end
      local k = str()
      skip()
      if sub(text, pos, pos) ~= ":" then fail("expected ':'") end
      pos = pos + 1
      out[k] = value()
      skip()
      local c = sub(text, pos, pos)
      pos = pos + 1
      if c == "}" then return out end
      if c ~= "," then pos = pos - 1 fail("expected ',' or '}'") end
    end
  end

  local function arr()
    pos = pos + 1
    local out, n = {}, 0
    skip()
    if sub(text, pos, pos) == "]" then pos = pos + 1 return out end
    while true do
      n = n + 1
      out[n] = value()
      skip()
      local c = sub(text, pos, pos)
      pos = pos + 1
      if c == "]" then return out end
      if c ~= "," then pos = pos - 1 fail("expected ',' or ']'") end
    end
  end

  function value()
    skip()
    local c = sub(text, pos, pos)
    if c == "{" then return obj() end
    if c == "[" then return arr() end
    if c == '"' then return str() end
    if sub(text, pos, pos + 3) == "true" then pos = pos + 4 return true end
    if sub(text, pos, pos + 4) == "false" then pos = pos + 5 return false end
    if sub(text, pos, pos + 3) == "null" then pos = pos + 4 return json.null end
    local num = text:match("^-?%d+%.?%d*[eE]?[-+]?%d*", pos)
    if num and num ~= "" and tonumber(num) then
      pos = pos + #num
      return tonumber(num)
    end
    if c == "" then fail("unexpected end of file") end
    fail("unexpected character '" .. c .. "'")
  end

  local ok, result = pcall(function()
    local v = value()
    skip()
    if pos <= #text then fail("trailing characters after the document") end
    return v
  end)
  if ok then return result end
  if type(result) == "table" and result.json then return nil, result.json end
  return nil, tostring(result)
end

return json
