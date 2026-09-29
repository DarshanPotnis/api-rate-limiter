-- Fixed-window token budget, reserve step: add `tokens` to this window's usage only if
-- the total stays within `limit`. Redis runs the script without interleaving other
-- commands, so concurrent reservations cannot both fit into the same remaining room.
--
-- KEYS[1]  hash for one caller: `window` = start of the window it counts (ms),
--          `used` = tokens reserved or settled in that window
-- ARGV[1]  limit: tokens allowed per window
-- ARGV[2]  window_ms: window length in milliseconds
-- ARGV[3]  tokens to reserve
--
-- Returns {allowed (1 or 0), remaining, window_start_ms, reset_ms, now_ms}. A refused
-- reservation writes nothing.

local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local tokens = tonumber(ARGV[3])

-- The Redis server clock, so every app instance agrees on which window is current.
local time = redis.call('TIME')
local now_ms = tonumber(time[1]) * 1000 + math.floor(tonumber(time[2]) / 1000)
local window_start = now_ms - (now_ms % window_ms)
local reset_ms = window_start + window_ms

-- A hash left over from an earlier window counts as empty.
local stored = redis.call('HMGET', key, 'window', 'used')
local used = 0
if tonumber(stored[1]) == window_start then
  used = tonumber(stored[2])
end

if used + tokens > limit then
  return {0, math.max(limit - used, 0), window_start, reset_ms, now_ms}
end

redis.call('HSET', key, 'window', window_start, 'used', used + tokens)
redis.call('PEXPIREAT', key, reset_ms)
return {1, limit - used - tokens, window_start, reset_ms, now_ms}
