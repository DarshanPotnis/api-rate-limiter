-- Sliding-log rate limiter: admit a request only if fewer than `limit` requests
-- were admitted for this key in the last `window_ms` milliseconds.
--
-- Redis runs the whole script without interleaving other commands, so the count
-- check and the insert below cannot be split by a concurrent request.
--
-- KEYS[1]  sorted set of admitted requests for one client, scored by admission time (ms)
-- ARGV[1]  limit: maximum requests per window (>= 1)
-- ARGV[2]  window_ms: window length in milliseconds (>= 1)
-- ARGV[3]  random suffix that makes this request's member unique
--
-- Returns {allowed (1 or 0), remaining, reset_ms, now_ms}, where reset_ms is when the
-- oldest request in the window ages out, i.e. when the next slot frees up.

local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])

-- The Redis server clock, so every app instance agrees on "now".
local time = redis.call('TIME')
local now_ms = tonumber(time[1]) * 1000 + math.floor(tonumber(time[2]) / 1000)

redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - window_ms)

local count = redis.call('ZCARD', key)
local allowed = 0
if count < limit then
  redis.call('ZADD', key, now_ms, now_ms .. '-' .. ARGV[3])
  redis.call('PEXPIRE', key, window_ms)
  count = count + 1
  allowed = 1
end

-- The set cannot be empty here: limit >= 1, so either this request was just added
-- or `limit` earlier requests are still inside the window.
local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
local reset_ms = tonumber(oldest[2]) + window_ms

return {allowed, math.max(limit - count, 0), reset_ms, now_ms}
