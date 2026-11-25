/* History: list, filters, detail and download. */
"use strict";

/* ---------------------------------------------------------------- history */

async function renderHistory() {
  const kind = state.histKind || "";
  const ops = await call("history", null, kind || null) || [];
  $("#hist-actions").hidden = ops.length === 0;
  $("#export").title = t("history.download_title");
  showLegend("#hist-legend", ops.length > 0);
  $("#hist").innerHTML = ops.length ? ops.map(op => `
    <li data-op="${op.id}"><button class="row" aria-expanded="false">
      <span class="date">${esc(opDate(op))}</span>
      <span><div class="what" title="${esc(opTitle(op))}">${esc(opTitle(op))}</div><div class="sub">${esc(opRoute(op))}. ${esc(opSub(op))}</div></span>
      ${mosaicHTML(op)}</button>
      <div class="detail"><ul><li class="muted">${esc(t("common.loading"))}</li></ul></div></li>`).join("")
    : (kind ? `<li class="empty">${esc(t(kind === "dedupe" ? "history.empty_dedupe" : "history.empty_transfer"))}</li>` : emptyHistory());
}
$("#hist").addEventListener("click", async e => {
  const b = e.target.closest("button.row"); if (!b) return;
  const li = b.parentElement, open = li.classList.toggle("open"); b.setAttribute("aria-expanded", open);
  if (!open || li.dataset.loaded) return;
  const tracks = await call("history_detail", Number(li.dataset.op)) || [];
  li.dataset.loaded = "1";
  $(".detail ul", li).innerHTML = tracks.length ? tracks.map(x => {
    const cls = CLASS[x.status] || "ok";
    const to = x.match_title && x.status !== "no_encontrado" ? ` <span class="muted">→ ${esc(x.match_title)}${x.match_artist ? `, ${esc(x.match_artist)}` : ""}</span>` : "";
    return `<li><span class="badge ${cls}">${SYMBOL[cls]}</span><span><b>${esc(x.title)}</b> <span class="muted">${esc(x.artist)}</span>${to}</span></li>`;
  }).join("") : `<li class="muted">${esc(t("history.no_detail"))}</li>`;
});

$("#hist-filter").addEventListener("click", e => {
  const b = e.target.closest("[data-kind]"); if (!b) return;
  state.histKind = b.dataset.kind;
  $$("#hist-filter button").forEach(x => x.setAttribute("aria-pressed", x === b));
  renderHistory();
});
$("#export").addEventListener("click", async () => {
  const r = await call("export_history", state.histKind || null);
  if (!r || r.cancelled) return;
  $("#open-export").hidden = false;
  $("#export-note").hidden = false;
  $("#export-note").textContent = t("history.download_note", { n: r.operations, path: r.path });
  toast(t("history.download_toast", { name: r.name }));
});
$("#open-export").addEventListener("click", () => call("open_export"));
