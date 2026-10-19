-- RFC 4180 CSV reader in pure Lua 5.1.
--
-- * quoted fields may contain the delimiter, "" for a quote, and real line
--   breaks (what Excel / LibreOffice / Google Sheets write for Alt+Enter)
-- * CRLF or LF line endings, optional UTF-8 BOM
-- * delimiter: tab for .tsv, otherwise sniffed from the header line among
--   , ; and tab (Excel in many European locales saves with ;)
--
-- csv.parse(text, opts) -> { header = {...}, rows = { {field=..., _line=N}, ... } }
--                          | nil, "line N: message"

local csv = {}

local function sniff(text)
  local line = text:match("^[^\r\n]*") or ""
  local best, bestN = ",", -1
  for _, d in ipairs({ ",", ";", "\t" }) do
    local n, quoted = 0, false
    for i = 1, #line do
      local c = line:sub(i, i)
      if c == '"' then quoted = not quoted
      elseif c == d and not quoted then n = n + 1 end
    end
    if n > bestN then best, bestN = d, n end
  end
  return best
end

-- Returns a list of records, each a list of fields, plus each record's line.
local function records(text, delim)
  local out, lines = {}, {}
  local rec, field = {}, {}
  local i, n, line, recLine = 1, #text, 1, 1
  local quoted = false
  while i <= n do
    local c = text:sub(i, i)
    if quoted then
      if c == '"' then
        if text:sub(i + 1, i + 1) == '"' then
          field[#field + 1] = '"'
          i = i + 1
        else
          quoted = false
        end
      else
        if c == "\n" then line = line + 1 end
        -- normalise CRLF inside a quoted field to LF
        if not (c == "\r" and text:sub(i + 1, i + 1) == "\n") then
          field[#field + 1] = c
        end
      end
    elseif c == '"' and #field == 0 then
      quoted = true
    elseif c == delim then
      rec[#rec + 1] = table.concat(field)
      field = {}
    elseif c == "\r" or c == "\n" then
      if c == "\r" and text:sub(i + 1, i + 1) == "\n" then i = i + 1 end
      rec[#rec + 1] = table.concat(field)
      out[#out + 1], lines[#out + 1] = rec, recLine
      rec, field = {}, {}
      line = line + 1
      recLine = line
    else
      field[#field + 1] = c
    end
    i = i + 1
  end
  if quoted then return nil, ("line %d: unterminated quoted field"):format(recLine) end
  if #field > 0 or #rec > 0 then
    rec[#rec + 1] = table.concat(field)
    out[#out + 1], lines[#out + 1] = rec, recLine
  end
  return out, lines
end

function csv.parse(text, opts)
  opts = opts or {}
  if type(text) ~= "string" then return nil, "expected a string" end
  if text:sub(1, 3) == "\239\187\191" then text = text:sub(4) end
  local delim = opts.delimiter or sniff(text)
  local recs, lines = records(text, delim)
  if not recs then return nil, lines end
  if #recs == 0 then return { header = {}, rows = {} } end
  local header = {}
  for k, name in ipairs(recs[1]) do
    header[k] = name:lower():gsub("^%s+", ""):gsub("%s+$", "")
  end
  local rows = {}
  for r = 2, #recs do
    local rec = recs[r]
    -- skip blank lines (a lone empty field)
    if not (#rec == 1 and rec[1] == "") then
      local row = { _line = lines[r] }
      for k, name in ipairs(header) do
        if name ~= "" then row[name] = rec[k] or "" end
      end
      rows[#rows + 1] = row
    end
  end
  return { header = header, rows = rows, delimiter = delim }
end

return csv
