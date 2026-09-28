-- Fixed-window token budget, settle step: once the real usage is known, add
-- `delta` = actual - reserved to the window the reservation was made in. A negative
-- delta refunds, a positive one charges; releasing a failed request is actual = 0.
-- If that window has already ended its hash has expired or been replaced, and there is
-- nothing to correct: the finished window no longer limits anyone.
--
-- KEYS[1]  the caller's budget hash (see fixed_window_reserve.lua)
-- ARGV[1]  limit: tokens allowed per window
-- ARGV[2]  window_ms: window length in milliseconds
-- ARGV[3]  window_start_ms returned by the reservation
-- ARGV[4]  delta in tokens
--
-- Returns {remaining, reset_ms, now_ms} for the window that is current now.

local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local reserved_window = tonumber(ARGV[3])
local delta = tonumber(ARGV[4])

local time = redis.call('TIME')
local now_ms = tonumber(time[1]) * 1000 + math.floor(tonumber(time[2]) / 1000)
local window_start = now_ms - (now_ms % window_ms)

local stored = redis.call('HMGET', key, 'window', 'used')
local stored_window = tonumber(stored[1])
local used = tonumber(stored[2])

if stored_window == reserved_window then
  used = redis.call('HINCRBY', key, 'used', delta)
end

-- Report the budget of the current window, which is empty unless the hash tracks it.
if stored_window ~= window_start then
  used = 0
end

return {math.max(limit - used, 0), window_start + window_ms, now_ms}
