(function () {
  "use strict";
  var band = document.querySelector("[data-publication]");
  var button = document.querySelector("[data-refresh-evidence]");
  var note = document.querySelector("[data-refresh-state]");
  var busy = false;

  async function refresh() {
    if (!band || busy || document.hidden) return;
    busy = true;
    if (button) button.disabled = true;
    if (note) note.textContent = "Reading the latest checks...";
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, 8000);
    try {
      var response = await fetch("/api/v1/provenance", {cache: "no-store", signal: controller.signal});
      if (!response.ok) throw new Error("Status unavailable");
      var data = await response.json();
      ["chain", "gateway"].forEach(function (key) {
        var state = band.querySelector("[data-" + key + "-status]");
        var detail = band.querySelector("[data-" + key + "-detail]");
        state.textContent = data[key].status.charAt(0).toUpperCase() + data[key].status.slice(1);
        state.dataset.state = data[key].status;
        detail.textContent = data[key].detail;
      });
      if (note) note.textContent = "Status refreshed at " + new Date().toLocaleTimeString() + ". Reload to see new records.";
    } catch (error) {
      if (note) note.textContent = "Status could not be refreshed. Displayed checks may be stale. Try again.";
    } finally {
      clearTimeout(timer);
      busy = false;
      if (button) button.disabled = false;
    }
  }
  if (button) button.addEventListener("click", refresh);
  if (band) setInterval(refresh, 15000);

  var filter = document.querySelector("[data-registry-filter]");
  var list = document.querySelector("[data-repository-list]");
  if (filter && list) {
    filter.hidden = false;
    var input = filter.querySelector("input");
    input.addEventListener("input", function () {
      var query = input.value.trim().toLocaleLowerCase();
      var shown = 0;
      list.querySelectorAll(".ledger-row").forEach(function (row) {
        row.hidden = !row.querySelector(".ledger-name").textContent.toLocaleLowerCase().includes(query);
        if (!row.hidden) shown += 1;
      });
      filter.querySelector("[data-filter-count]").textContent = shown ? shown + " shown" : "No matching repositories";
    });
  }
})();
