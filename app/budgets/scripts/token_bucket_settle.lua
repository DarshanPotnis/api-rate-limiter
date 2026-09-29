-- Token bucket, settle step: once the real usage is known, apply delta = actual - reserved
-- to the refilled balance. A refund (negative delta) is capped at capacity; an overcharge
-- can take the balance below zero, and later refill pays that debt back before anything
-- new fits.
--
-- KEYS[1]  the caller's bucket (see token_bucket_reserve.lua)
-- ARGV[1]  capacity
-- ARGV[2]  refill_ms
-- ARGV[3]  delta in tokens
--
-- Returns {remaining, full_at_ms, now_ms}.

local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local rate = capacity / tonumber(ARGV[2])
local delta = tonumber(ARGV[3])

local time = redis.call('TIME')
local now_ms = tonumber(time[1]) * 1000 + math.floor(tonumber(time[2]) / 1000)

local balance = capacity
local stored = redis.call('HMGET', key, 'tokens', 'at')
if stored[1] then
  balance = math.min(capacity, tonumber(stored[1]) + (now_ms - tonumber(stored[2])) * rate)
end
balance = math.min(capacity, balance - delta)

local full_in_ms = math.ceil((capacity - balance) / rate)
redis.call('HSET', key, 'tokens', tostring(balance), 'at', now_ms)
redis.call('PEXPIRE', key, math.max(full_in_ms, 1))
return {math.floor(math.max(balance, 0)), now_ms + full_in_ms, now_ms}
