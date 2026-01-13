let countdownInterval = null;

function scrollToDashboard() {
  document.getElementById("dashboard").scrollIntoView({ behavior: "smooth" });
}

function setApiKey() {
  const tier = document.getElementById("apiTier").value;
  document.getElementById("apiKey").value = tier;
}

async function callAPI() {
  const apiKey = document.getElementById("apiKey").value;

  const result = document.getElementById("result");
  const badge = document.getElementById("statusBadge");
  const progressContainer = document.getElementById("progressContainer");
  const progressBar = document.getElementById("progressBar");
  const resetTimer = document.getElementById("resetTimer");

  const response = await fetch("/protected", {
    headers: {
      "X-API-KEY": apiKey
    }
  });

  const data = await response.json();

  const limit = response.headers.get("x-ratelimit-limit");
  const remaining = response.headers.get("x-ratelimit-remaining");
  const resetAt = response.headers.get("x-ratelimit-reset");

  badge.classList.remove("hidden", "success", "error");
  badge.classList.add(response.status === 200 ? "success" : "error");
  badge.innerText = `Status: ${response.status}`;

  result.innerHTML = `
    <strong>Message:</strong> ${data.message || data.detail}<br />
    <strong>Remaining:</strong> ${remaining}/${limit}
  `;

  progressContainer.classList.remove("hidden");
  const percent = (remaining / limit) * 100;
  progressBar.style.width = percent + "%";

  if (resetAt) {
    resetTimer.classList.remove("hidden");
    startCountdown(resetAt);
  }
}

function startCountdown(resetTimestamp) {
  const countdownEl = document.getElementById("countdown");

  if (countdownInterval) {
    clearInterval(countdownInterval);
  }

  countdownInterval = setInterval(() => {
    const now = Math.floor(Date.now() / 1000);
    const remaining = resetTimestamp - now;

    if (remaining <= 0) {
      countdownEl.innerText = "0";
      clearInterval(countdownInterval);
      return;
    }

    countdownEl.innerText = remaining;
  }, 1000);
}
