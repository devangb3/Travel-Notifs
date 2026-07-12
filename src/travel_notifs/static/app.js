const form = document.querySelector("#planner");
const results = document.querySelector("#results");
const sessionToken = crypto.randomUUID();
const telegramButton = document.querySelector("#connect-telegram");

document.querySelectorAll("[data-trip-toggle]").forEach((button) => {
  button.addEventListener("click", async () => {
    const panel = document.getElementById(button.getAttribute("aria-controls"));
    const opening = button.getAttribute("aria-expanded") !== "true";
    button.setAttribute("aria-expanded", String(opening));
    button.querySelector(".drawer-meta i").textContent = opening ? "−" : "+";

    if (!opening) {
      panel.hidden = true;
      return;
    }

    panel.hidden = false;
    if (panel.dataset.loaded === "true") return;
    panel.innerHTML = '<div class="drawer-loading">Loading trips…</div>';
    try {
      const response = await fetch(button.dataset.url);
      if (!response.ok) throw new Error("Could not load trips right now");
      panel.innerHTML = await response.text();
      panel.dataset.loaded = "true";
    } catch (error) {
      panel.innerHTML = `<div class="no-trips">${escapeHtml(error.message)}</div>`;
    }
  });
});

if (telegramButton) {
  telegramButton.addEventListener("click", async () => {
    const result = document.querySelector("#telegram-pairing");
    telegramButton.disabled = true;
    telegramButton.textContent = "Creating link…";
    try {
      const response = await fetch("/api/telegram/pairing", {method: "POST"});
      if (!response.ok) throw new Error("Could not connect Telegram right now");
      const data = await response.json();
      result.innerHTML = `<a href="${escapeHtml(data.url)}" target="_blank" rel="noopener">Open Telegram and press Start →</a><small>This link expires in 10 minutes.</small>`;
      telegramButton.hidden = true;
    } catch (error) {
      result.textContent = error.message;
      telegramButton.disabled = false;
      telegramButton.textContent = "Try again";
    }
  });
}

if (form) {
  const travelAt = form.elements.travel_at;
  const localNow = new Date(Date.now() + 30 * 60 * 1000);
  travelAt.value = new Date(localNow - localNow.getTimezoneOffset() * 60000)
    .toISOString().slice(0, 16);

  form.querySelector(".swap").addEventListener("click", () => {
    const origin = form.elements.origin.value;
    const originPlaceId = form.elements.origin_place_id.value;
    form.elements.origin.value = form.elements.destination.value;
    form.elements.origin_place_id.value = form.elements.destination_place_id.value;
    form.elements.destination.value = origin;
    form.elements.destination_place_id.value = originPlaceId;
  });

  setupAutocomplete("origin");
  setupAutocomplete("destination");

  form.elements.recurrence.forEach((radio) => radio.addEventListener("change", () => {
    form.querySelector(".weekdays").hidden = radio.value !== "weekly" || !radio.checked;
  }));

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = form.querySelector(".primary");
    submit.disabled = true;
    submit.querySelector("span").textContent = "Reading the board…";
    results.className = "loading-state";
    results.innerHTML = "<i></i><i></i><i></i>";

    const payload = {
      origin: form.elements.origin.value,
      destination: form.elements.destination.value,
      travel_at: new Date(form.elements.travel_at.value).toISOString(),
      timing_mode: form.elements.timing_mode.value,
      agency_id: form.elements.agency_id.value,
      origin_place_id: form.elements.origin_place_id.value,
      destination_place_id: form.elements.destination_place_id.value,
    };

    try {
      const response = await fetch("/api/plan", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error("Could not plan this trip");
      const data = await response.json();
      renderItineraries(data.itineraries, payload);
    } catch (error) {
      results.className = "error-state";
      results.textContent = error.message;
    } finally {
      submit.disabled = false;
      submit.querySelector("span").textContent = "Find trips";
    }
  });
}

function clock(iso) {
  return new Intl.DateTimeFormat([], {hour: "numeric", minute: "2-digit"}).format(new Date(iso));
}

function renderItineraries(itineraries, trip) {
  if (!itineraries.length) {
    results.className = "empty-state";
    results.innerHTML = `<div class="route-glyph">◉━━◉</div>
      <p>No transit trips found for this time.</p>
      <small>Try an earlier time or a different stop.</small>`;
    return;
  }

  results.className = "itineraries";
  results.innerHTML = itineraries.map((item, index) => {
    const transit = item.legs.find((leg) => leg.mode !== "WALK");
    return `<article class="itinerary" style="--delay:${index * 80}ms">
      <div class="run-time"><strong>${clock(item.start_time)}</strong><span>${clock(item.end_time)}</span></div>
      <div class="run-detail">
        <div><b class="route-pill">${transit?.route || "—"}</b> ${transit?.headsign || "Transit"}</div>
        <small>${item.duration_minutes} min · ${item.transfers ? `${item.transfers} transfer` : "direct"}</small>
      </div>
      <button type="button" data-itinerary="${item.id}">Monitor</button>
    </article>`;
  }).join("");

  results.querySelectorAll("button").forEach((button, index) => button.addEventListener("click", async () => {
    button.disabled = true;
    button.textContent = "Saving…";
    const recurrence = form.elements.recurrence.value;
    const weekdays = [...form.querySelectorAll('[name="weekdays"]:checked')].map((node) => node.value);
    const response = await fetch("/api/trips", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        ...trip,
        recurrence,
        weekdays,
        itinerary_id: button.dataset.itinerary,
        selected_itinerary: itineraries[index],
      }),
    });
    if (response.ok) window.location.reload();
    else {
      button.disabled = false;
      button.textContent = "Try again";
    }
  }));
}

function setupAutocomplete(name) {
  const input = form.elements[name];
  const placeId = form.elements[`${name}_place_id`];
  const list = input.parentElement.querySelector(".suggestions");
  let timer;

  input.addEventListener("input", () => {
    placeId.value = "";
    clearTimeout(timer);
    if (input.value.trim().length < 3) {
      list.replaceChildren();
      return;
    }
    timer = setTimeout(async () => {
      const params = new URLSearchParams({
        q: input.value.trim(),
        agency_id: form.elements.agency_id.value,
        session_token: sessionToken,
      });
      const response = await fetch(`/api/places/autocomplete?${params}`);
      if (!response.ok) return;
      const data = await response.json();
      list.innerHTML = data.suggestions.map((suggestion) =>
        `<button type="button" data-id="${escapeHtml(suggestion.place_id)}">${escapeHtml(suggestion.label)}</button>`
      ).join("");
      list.querySelectorAll("button").forEach((option) => option.addEventListener("click", () => {
        input.value = option.textContent;
        placeId.value = option.dataset.id;
        list.replaceChildren();
      }));
    }, 250);
  });

  input.addEventListener("blur", () => setTimeout(() => list.replaceChildren(), 160));
}

function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = value;
  return node.innerHTML;
}
