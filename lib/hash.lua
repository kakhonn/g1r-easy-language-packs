-- FNV-1a 32-bit over the string's bytes, as 8 lowercase hex digits.
--
-- A translation row keyed "PIKACHU#1a2b3c4d" applies only where the running
-- game's English for PIKACHU hashes to 1a2b3c4d, which is how one pack
-- carries Red/Blue's and Yellow's different Pokedex text side by side without
-- shipping the English itself.  tools/lpk.py computes the same hash.
local bit = bit
local band, bxor, lshift, tobit, tohex = bit.band, bit.bxor, bit.lshift, bit.tobit, bit.tohex

local function fnv1a(s)
  local h = tobit(0x811c9dc5)
  for i = 1, #s do
    h = bxor(h, s:byte(i))
    -- h * 16777619 mod 2^32, split so no intermediate exceeds 2^53:
    -- 16777619 = 2^24 + 403
    h = tobit(lshift(h, 24) + band(h, 0xffffffff) % 0x100000000 * 403)
  end
  return tohex(h, 8)
end

return { fnv1a = fnv1a }
