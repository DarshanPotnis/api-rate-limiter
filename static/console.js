// LLM Gateway console. Plain JavaScript with no build step.
//
// Everything that comes from the server is written with textContent: the page never builds
// HTML from strings. Countdowns start from the durations the gateway sends (reset headers,
// cooldowns) and tick on performance.now(), never by comparing this machine's clock with
// the server's.

const LOG_SIZE = 25;
const STATUS_EVERY_MS = 2000;
const MODELS_EVERY_MS = 10000;

const $ = (id) => document.getElementById(id);
const form = $("request-form");
const tierSelect = $("tier");
const modelSelect = $("model");
const maxTokensInput = $("max-tokens");
const promptInput = $("prompt");
const sendButton = $("send");
const burstForm = $("burst-form");
const burstButton = $("burst-send");

const limitsByKey = new Map(); // API key -> { requests, tokens } from that tier's latest response
const log = [];
let tokenBudgetKind = "token_bucket";
let health = { receivedAt: 0, models: [], cooldownCells: [] };
let lastAriaUpdate = 0;

// ---------------------------------------------------------------------------------------
// Small helpers

function el(tag, { className, text, title } = {}, ...children) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  if (title) node.title = title;
  node.append(...children);
  return node;
}

function tierName(key) {
  const option = [...tierSelect.options].find((o) => o.value === key);
  return option ? option.dataset.name : key;
}

/** Parse the gateway's durations: "250ms", "17.525s", "1m0s". Returns milliseconds or null. */
function parseDuration(text) {
  if (!text) return null;
  const millis = /^(\d+(?:\.\d+)?)ms$/.exec(text);
  if (millis) return Number(millis[1]);
  const parts = /^(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?$/.exec(text);
  if (!parts || (parts[1] === undefined && parts[2] === undefined)) return null;
  return (Number(parts[1] ?? 0) * 60 + Number(parts[2] ?? 0)) * 1000;
}

function numberHeader(headers, name) {
  const value = headers.get(name);
  return value === null || value === "" || Number.isNaN(Number(value)) ? null : Number(value);
}

function formatWait(ms) {
  if (ms <= 0) return "now";
  const seconds = ms / 1000;
  if (seconds < 10) return `${seconds.toFixed(1)} s`;
  if (seconds < 60) return `${Math.ceil(seconds)} s`;
  const whole = Math.ceil(seconds);
  return `${Math.floor(whole / 60)} min ${whole % 60} s`;
}

const formatCount = (n) => Math.floor(n).toLocaleString("en-US");

// ---------------------------------------------------------------------------------------
// Talking to the gateway

/** Send one chat completion and describe what happened, whatever happened. */
async function send(request) {
  const started = performance.now();
  let response;
  try {
    response = await fetch("/v1/chat/completions", {
      method: "POST",
      headers: { Authorization: `Bearer ${request.key}`, "Content-Type": "application/json" },
      body: JSON.stringify(request.body),
    });
  } catch (error) {
    const outcome = {
      request,
      status: null,
      ok: false,
      latencyMs: performance.now() - started,
      json: null,
      provider: null,
      retryAfter: null,
      error: { message: `Could not reach the gateway (${error.message}).`, type: "network", code: null },
    };
    addToLog(outcome);
    return outcome;
  }

  const receivedAt = performance.now();
  const text = await response.text();
  let json = null;
  try {
    json = JSON.parse(text);
  } catch {
    json = null;
  }
  recordLimits(request.key, response.headers, receivedAt);

  const outcome = {
    request,
    status: response.status,
    ok: response.ok && json !== null,
    latencyMs: receivedAt - started,
    json,
    provider: response.headers.get("x-gateway-provider"),
    retryAfter: response.headers.get("retry-after"),
    error: null,
  };
  if (!outcome.ok) {
    const apiError = json && typeof json === "object" ? json.error : null;
    outcome.error = apiError
      ? { message: String(apiError.message ?? ""), type: apiError.type ?? null, code: apiError.code ?? null }
      : {
          message:
            json === null
              ? `The gateway answered HTTP ${response.status} with a body that is not JSON.`
              : `The gateway answered HTTP ${response.status}.`,
          type: null,
          code: null,
        };
  }
  addToLog(outcome);
  return outcome;
}

function currentRequest() {
  return {
    key: tierSelect.value,
    tier: tierName(tierSelect.value),
    model: modelSelect.value,
    body: {
      model: modelSelect.value,
      messages: [{ role: "user", content: promptInput.value }],
      max_tokens: Number(maxTokensInput.value),
    },
  };
}

/** How a request ended, in a few words: "ok", "requests" or "tokens" (429s), or "error". */
function outcomeKind(outcome) {
  if (outcome.ok) return "ok";
  if (outcome.status === 429 && outcome.error?.type === "requests") return "requests";
  if (outcome.status === 429 && outcome.error?.type === "tokens") return "tokens";
  return "error";
}

/** The status alone: "200", "429 requests", "429 tokens", "503 model_unavailable"… */
function statusLabel(outcome) {
  const kind = outcomeKind(outcome);
  if (kind === "ok") return String(outcome.status);
  if (kind === "requests") return "429 requests";
  if (kind === "tokens") return "429 tokens";
  if (outcome.status === null) return "network error";
  return `${outcome.status} ${outcome.error?.code ?? outcome.error?.type ?? "error"}`;
}

/** For burst chips, which have no separate answered-by column: "200 mock" on success. */
function outcomeLabel(outcome) {
  return outcomeKind(outcome) === "ok" ? `${statusLabel(outcome)} ${outcome.provider ?? ""}`.trim() : statusLabel(outcome);
}

// ---------------------------------------------------------------------------------------
// Live limits

function recordLimits(key, headers, receivedAt) {
  const entry = limitsByKey.get(key) ?? { requests: null, tokens: null };

  const requestLimit = numberHeader(headers, "x-ratelimit-limit-requests");
  const requestsLeft = numberHeader(headers, "x-ratelimit-remaining-requests");
  if (requestLimit !== null && requestsLeft !== null) {
    // Responses to a burst arrive out of order: a success decided while slots were left can
    // arrive after the 429s decided later. The count can only rise once the oldest request
    // leaves the window, so a response reporting more slots left is stale until then.
    const current = entry.requests;
    const slotFreed = current?.nextSlotAt != null && receivedAt >= current.nextSlotAt;
    if (!current || requestsLeft <= current.remaining || slotFreed) {
      const nextSlotMs = parseDuration(headers.get("x-ratelimit-reset-requests"));
      entry.requests = {
        limit: requestLimit,
        remaining: requestsLeft,
        nextSlotAt: nextSlotMs === null ? null : receivedAt + nextSlotMs,
      };
    }
  }

  const tokenLimit = numberHeader(headers, "x-ratelimit-limit-tokens");
  const tokensLeft = numberHeader(headers, "x-ratelimit-remaining-tokens");
  const untilFullMs = parseDuration(headers.get("x-ratelimit-reset-tokens"));
  if (tokenLimit !== null && tokensLeft !== null && untilFullMs !== null) {
    entry.tokens = {
      limit: tokenLimit,
      level: tokensLeft,
      at: receivedAt,
      fullAt: receivedAt + untilFullMs,
      // A per-minute budget refills its whole limit in a minute. (Deriving the rate from the
      // headers instead suffers from their rounding when only a few tokens are missing.)
      ratePerMs: tokenLimit / 60_000,
    };
  }
  limitsByKey.set(key, entry);
}

function tokenLevel(tokens, now) {
  if (tokenBudgetKind === "fixed_window") return now >= tokens.fullAt ? tokens.limit : tokens.level;
  return Math.min(tokens.limit, tokens.level + tokens.ratePerMs * (now - tokens.at));
}

function setGauge(fill, meter, value, max, valueText, updateAria) {
  const fraction = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0;
  fill.style.width = `${(fraction * 100).toFixed(2)}%`;
  fill.classList.toggle("low", fraction > 0 && fraction < 0.2);
  fill.classList.toggle("empty", fraction === 0);
  if (updateAria) {
    meter.setAttribute("aria-valuemax", String(max));
    meter.setAttribute("aria-valuenow", String(Math.floor(value)));
    meter.setAttribute("aria-valuetext", valueText);
  }
}

function renderLimits(now, updateAria) {
  const entry = limitsByKey.get(tierSelect.value);

  const requests = entry?.requests;
  if (requests) {
    const text = `${formatCount(requests.remaining)} of ${formatCount(requests.limit)} left`;
    $("requests-value").textContent = text;
    setGauge($("requests-fill"), $("requests-gauge"), requests.remaining, requests.limit, text, updateAria);
    if (requests.nextSlotAt !== null && requests.nextSlotAt > now) {
      $("requests-note").textContent = `The oldest request leaves the window in ${formatWait(requests.nextSlotAt - now)}.`;
    } else if (requests.remaining < requests.limit) {
      $("requests-note").textContent = "At least one slot has freed since; the next response shows the new count.";
    } else {
      $("requests-note").textContent = "Every request slot is free.";
    }
  } else {
    $("requests-value").textContent = "not seen yet";
    setGauge($("requests-fill"), $("requests-gauge"), 0, 0, "not seen yet", updateAria);
    $("requests-note").textContent = "Send a request as this tier to see its limits.";
  }

  const tokens = entry?.tokens;
  if (tokens) {
    const level = tokenLevel(tokens, now);
    const text = `${formatCount(level)} of ${formatCount(tokens.limit)} left`;
    $("tokens-value").textContent = text;
    setGauge($("tokens-fill"), $("tokens-gauge"), level, tokens.limit, text, updateAria);
    const untilFull = tokens.fullAt - now;
    if (untilFull <= 0) {
      $("tokens-note").textContent = "The budget is full.";
    } else if (tokenBudgetKind === "fixed_window") {
      $("tokens-note").textContent = `Fixed window: the budget resets in ${formatWait(untilFull)}.`;
    } else {
      const perSecond = Math.round(tokens.ratePerMs * 1000).toLocaleString("en-US");
      $("tokens-note").textContent = `Refilling ${perSecond} tokens a second; full in ${formatWait(untilFull)}.`;
    }
  } else {
    $("tokens-value").textContent = "not seen yet";
    setGauge($("tokens-fill"), $("tokens-gauge"), 0, 0, "not seen yet", updateAria);
    $("tokens-note").textContent = "The gauge refills between requests at the rate the gateway reports.";
  }
}

// ---------------------------------------------------------------------------------------
// Playground

function showResult(outcome) {
  const result = $("result");
  if (outcome.ok) {
    const json = outcome.json;
    const choice = json.choices?.[0];
    const usage = json.usage ?? {};
    const answered =
      outcome.provider && outcome.provider !== json.model ? `${json.model} (via ${outcome.provider})` : json.model;
    const details = el("dl", { className: "meta" });
    for (const [term, value] of [
      ["Answered by", answered],
      ["Finish reason", choice?.finish_reason ?? "unknown"],
      ["Usage", `${usage.prompt_tokens} prompt + ${usage.completion_tokens} completion = ${usage.total_tokens} tokens`],
      ["Latency", `${Math.round(outcome.latencyMs)} ms`],
    ]) {
      details.append(el("dt", { text: term }), el("dd", { text: value }));
    }
    result.replaceChildren(el("pre", { className: "reply", text: choice?.message?.content ?? "" }), details);
    return;
  }

  const heading = [outcome.status === null ? "No response" : `HTTP ${outcome.status}`];
  if (outcome.error?.type && outcome.error.type !== "network") heading.push(outcome.error.type);
  if (outcome.error?.code) heading.push(outcome.error.code);
  const box = el(
    "div",
    { className: "error-box" },
    el("p", { className: "error-title", text: heading.join(" · ") }),
    el("p", { text: outcome.error?.message ?? "" }),
  );
  if (outcome.retryAfter) box.append(el("p", { className: "hint", text: `Retry after ${outcome.retryAfter} s.` }));
  result.replaceChildren(box);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!form.reportValidity()) return;
  sendButton.disabled = true;
  sendButton.textContent = "Sending…";
  try {
    showResult(await send(currentRequest()));
  } finally {
    sendButton.disabled = false;
    sendButton.textContent = "Send";
  }
  refreshStatus();
});

promptInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    form.requestSubmit();
  }
});

tierSelect.addEventListener("change", () => {
  $("limits-tier").textContent = tierName(tierSelect.value);
  renderLimits(performance.now(), true);
});

// ---------------------------------------------------------------------------------------
// Burst

burstForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!burstForm.reportValidity() || !form.reportValidity()) return;
  const count = Math.max(1, Math.min(50, Number($("burst-count").value)));
  const request = currentRequest();
  burstButton.disabled = true;
  $("burst-results").replaceChildren();
  $("burst-summary").textContent = `Sending ${count} requests at once as ${request.tier}…`;
  try {
    const outcomes = await Promise.all(Array.from({ length: count }, () => send(request)));
    const tally = { ok: 0, requests: 0, tokens: 0, error: 0 };
    const chips = outcomes.map((outcome, index) => {
      const kind = outcomeKind(outcome);
      tally[kind] += 1;
      const detail = outcome.error ? outcome.error.message : `${Math.round(outcome.latencyMs)} ms`;
      return el("li", { className: `chip ${kind}`, text: `#${index + 1} ${outcomeLabel(outcome)}`, title: detail });
    });
    $("burst-results").replaceChildren(...chips);
    const parts = [
      `${tally.ok} succeeded`,
      `${tally.requests} refused for requests`,
      `${tally.tokens} refused for tokens`,
    ];
    if (tally.error) parts.push(`${tally.error} other errors`);
    $("burst-summary").textContent = `${count} sent at once as ${request.tier}: ${parts.join(", ")}.`;
  } finally {
    burstButton.disabled = false;
  }
  refreshStatus();
});

// ---------------------------------------------------------------------------------------
// Model health

const STATE_TEXT = { closed: "closed", open: "open", half_open: "half-open" };

async function refreshStatus() {
  try {
    const response = await fetch("/status");
    const body = await response.json();
    tokenBudgetKind = body.token_budget;
    const cooldownCells = [];
    const rows = body.models.map((model) => {
      const state = model.breaker ?? "none";
      const cooldown = el("td");
      cooldownCells.push({ cell: cooldown, model });
      return el(
        "tr",
        {},
        el("td", { text: model.id }),
        el("td", { text: model.provider }),
        el("td", {}, el("span", { className: `state ${state}`, text: STATE_TEXT[state] ?? "no breaker" })),
        cooldown,
      );
    });
    health = { receivedAt: performance.now(), models: body.models, cooldownCells };
    $("health-rows").replaceChildren(...rows);
    renderCooldowns(performance.now());
    const strategy = body.fallback_strategy === "circuit_breaker" ? "circuit breaker" : "sequential, no breakers";
    const budget = body.token_budget === "token_bucket" ? "token bucket" : "fixed window";
    $("health-note").textContent = `Fallback: ${strategy}. Token budget: ${budget}. Refreshed every 2 s.`;
  } catch (error) {
    $("health-note").textContent = `Could not load /status (${error.message}).`;
  }
}

function renderCooldowns(now) {
  for (const { cell, model } of health.cooldownCells) {
    let text = "–";
    if (model.breaker === "open") {
      const left = model.cooldown_remaining_seconds * 1000 - (now - health.receivedAt);
      text = left > 0 ? formatWait(left) : "trial next";
    } else if (model.breaker === "half_open") {
      text = "trial next";
    }
    cell.textContent = text;
  }
}

// ---------------------------------------------------------------------------------------
// Models and the request log

async function loadModels() {
  try {
    const response = await fetch("/v1/models", { headers: { Authorization: `Bearer ${tierSelect.value}` } });
    const body = await response.json();
    if (!response.ok) throw new Error(body?.error?.message ?? `HTTP ${response.status}`);
    const ids = body.data.map((model) => model.id);
    const selected = ids.includes(modelSelect.value) ? modelSelect.value : ids[0];
    modelSelect.replaceChildren(...ids.map((id) => el("option", { text: id })));
    modelSelect.value = selected;
    $("model-note").textContent = "";
  } catch (error) {
    $("model-note").textContent = `Could not load models (${error.message}).`;
  }
}

function answeredBy(outcome) {
  const model = outcome.json.model;
  return outcome.provider && outcome.provider !== model ? `${model} (${outcome.provider})` : model;
}

function addToLog(outcome) {
  log.unshift({
    time: new Date().toLocaleTimeString([], { hour12: false }),
    tier: outcome.request.tier,
    model: outcome.request.model,
    answered: outcome.ok ? answeredBy(outcome) : "–",
    status: statusLabel(outcome),
    kind: outcomeKind(outcome),
    latency: `${Math.round(outcome.latencyMs)} ms`,
    tokens: outcome.ok && outcome.json.usage ? formatCount(outcome.json.usage.total_tokens) : "–",
  });
  log.length = Math.min(log.length, LOG_SIZE);
  const statusClass = { ok: "status-ok", requests: "status-requests", tokens: "status-tokens", error: "status-error" };
  $("log-rows").replaceChildren(
    ...log.map((entry) =>
      el(
        "tr",
        {},
        el("td", { text: entry.time }),
        el("td", { text: entry.tier }),
        el("td", { text: entry.model }),
        el("td", { text: entry.answered }),
        el("td", { className: statusClass[entry.kind], text: entry.status }),
        el("td", { className: "number", text: entry.latency }),
        el("td", { className: "number", text: entry.tokens }),
      ),
    ),
  );
  $("log-empty").hidden = log.length > 0;
}

// ---------------------------------------------------------------------------------------
// The clock that drives gauges and countdowns

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

function tick() {
  const now = performance.now();
  const updateAria = now - lastAriaUpdate > 1000;
  if (updateAria) lastAriaUpdate = now;
  renderLimits(now, updateAria);
  renderCooldowns(now);
  if (reducedMotion.matches) {
    setTimeout(tick, 1000);
  } else {
    requestAnimationFrame(tick);
  }
}

setInterval(() => {
  if (document.visibilityState === "visible") refreshStatus();
}, STATUS_EVERY_MS);
setInterval(() => {
  if (document.visibilityState === "visible") loadModels();
}, MODELS_EVERY_MS);

loadModels();
refreshStatus();
tick();
