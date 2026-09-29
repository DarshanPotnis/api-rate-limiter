-- Learn one model's base prompt overhead: the tokens its chat template adds to every
-- request beyond characters / 4 of the messages and the per-message allowance.
-- Runs atomically, so gateway processes learning at the same time cannot overwrite
-- each other's updates.
--
-- KEYS[1]  hash for one model: overhead (float), samples (count)
-- ARGV[1]  overhead observed on one request
-- ARGV[2]  default overhead, in effect until the first sample is learned
-- ARGV[3]  fraction of the gap closed by a lower sample (0.1)
-- ARGV[4]  ms without samples after which the model starts again from the default
--
-- Returns {learned (1 or 0), overhead as a string}. Errs toward reserving too much:
-- a higher sample is adopted at once, a lower one only nudges the overhead down, and one
-- under half the current overhead is ignored, because a template's overhead does not
-- halve; such a count left out prompt tokens Ollama served from its cache.

local key = KEYS[1]
local observed = tonumber(ARGV[1])
local stored = redis.call('HMGET', key, 'overhead', 'samples')
local current = tonumber(stored[1]) or tonumber(ARGV[2])
local samples = tonumber(stored[2]) or 0

if observed < current / 2 then
  return {0, tostring(current)}
end

local overhead
if samples == 0 or observed > current then
  overhead = observed
else
  overhead = current - (current - observed) * tonumber(ARGV[3])
end

redis.call('HSET', key, 'overhead', tostring(overhead), 'samples', samples + 1)
redis.call('PEXPIRE', key, ARGV[4])
return {1, tostring(overhead)}
