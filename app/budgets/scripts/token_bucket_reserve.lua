-- Token bucket, reserve step. The bucket holds up to `capacity` tokens and refills
-- continuously at capacity / refill_ms tokens per millisecond. Refill is computed lazily
-- from the time elapsed since the last update, on the Redis clock, so nothing runs
-- between requests. Redis runs the script atomically, so concurrent reservations cannot
-- spend the same tokens.
--
-- KEYS[1]  hash for one caller: tokens (balance; negative while repaying an overcharge),
--          at (ms of the last update). A missing key is a full bucket.
-- ARGV[1]  capacity: the tier's tokens per minute
-- ARGV[2]  refill_ms: time to refill an empty bucket
-- ARGV[3]  tokens to reserve
--
-- Returns {allowed (1 or 0), remaining, full_at_ms, now_ms, retry_after_ms}. A refused
-- reservation writes nothing; retry_after_ms is how long until the balance covers it.

local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local rate = capacity / tonumber(ARGV[2])
local cost = tonumber(ARGV[3])

local time = redis.call('TIME')
local now_ms = tonumber(time[1]) * 1000 + math.floor(tonumber(time[2]) / 1000)

local balance = capacity
local stored = redis.call('HMGET', key, 'tokens', 'at')
if stored[1] then
  balance = math.min(capacity, tonumber(stored[1]) + (now_ms - tonumber(stored[2])) * rate)
end

local allowed = 0
local retry_after_ms = 0
if balance >= cost then
  allowed = 1
  balance = balance - cost
  local full_in_ms = math.ceil((capacity - balance) / rate)
  redis.call('HSET', key, 'tokens', tostring(balance), 'at', now_ms)
  -- Once the bucket would be full again the key is gone, which means the same thing.
  redis.call('PEXPIRE', key, math.max(full_in_ms, 1))
else
  retry_after_ms = math.ceil((cost - balance) / rate)
end

local full_at_ms = now_ms + math.ceil((capacity - balance) / rate)
return {allowed, math.floor(math.max(balance, 0)), full_at_ms, now_ms, retry_after_ms}
