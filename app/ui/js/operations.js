/* How an operation (transfer or clean-up) is shown: title, route, date and the mosaic of
   squares that tell what happened. Used by Transfer and History. */
"use strict";

function opTitle(op) {
  return op.kind === "dedupe" ? t("op.dedupe_title", { name: op.source_name })
    : t("op.transfer_title", { src: op.source_name, dst: op.target_name });
}
function opRoute(op) {
  return op.kind === "dedupe" ? op.source_label : t("op.route", { src: op.source_label, dst: op.target_label });
}
function opSub(op) {
  if (op.kind === "dedupe") return t("op.removed", { n: op.removed });
  // The doubtful ones are already among the added: they're noted in parentheses, not counted separately
  const added = t("op.added", { n: op.added });
  const parts = [op.doubtful ? t("op.added_with_doubts", { added, doubtful: t("op.doubtful", { n: op.doubtful }) }) : added];
  if (op.already) parts.push(t("op.already", { n: op.already }));
  if (op.skipped) parts.push(t("op.skipped", { n: op.skipped }));
  if (op.not_found) parts.push(t("op.not_found", { n: op.not_found }));
  return parts.join(", ");
}
function opDate(op) {
  const d = new Date(op.created_at);
  return isNaN(d) ? op.created_at : d.toLocaleString(dateLocale(), { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
const emptyHistory = () => `<li class="empty"><b>${esc(t("history.empty_title"))}</b> ${esc(t("history.empty_text"))} <button class="btn ghost" data-go="transferir">${esc(t("history.empty_btn"))}</button></li>`;
/* ---- mosaics: one square per song, with its color; on hover it says which song it is ---- */

const LEGEND = [["ok", "ok", "--mint"], ["doubt", "doubt", "--peach"], ["miss", "miss", "--rose"],
                ["dup", "dup", "--sage"], ["rep", "rep", "--butter"], ["removed", "removed", "--lav"]];
/** The status name. In a dry run, what was found "would be added" (nothing was added). */
const statusLabel = (s, dry = false) => {
  if (s === "dudoso_omitido") return t(dry ? "legend.skipped_dry" : "legend.skipped");
  const key = CLASS[s] || "ok";
  return t(dry && key === "ok" ? "legend.ok_dry" : `legend.${key}`);
};

/** What each color means (once per screen, above the list). In the history a "?" may or may not
 *  have been added (depending on that transfer's checkbox): each square says which. */
function legendHTML() {
  return LEGEND.map(([key, cls, color]) =>
    `<span><i style="background:var(${color})"></i>${SYMBOL[cls]} ${esc(t(key === "doubt" ? "legend.doubt_any" : `legend.${key}`))}</span>`).join("");
}

function tileTitle(status, song, dry = false) {
  const label = statusLabel(status, dry);
  return song?.[0] ? t("tile.tooltip", { title: song[0], artist: song[1] || "?", status: label }) : label;
}

/** The small mosaic of a history operation (with "+N" if it has more songs than are drawn). */
function mosaicHTML(op) {
  const statuses = op.tiles || [], songsOf = op.tile_songs || [];
  const tiles = statuses.map((s, i) => `<i class="${CLASS[s] || "ok"}" title="${esc(tileTitle(s, songsOf[i]))}"></i>`).join("");
  const extra = (op.tiles_total || 0) - statuses.length;
  const more = extra > 0 ? `<span class="more" title="${esc(t("tiles.more_title", { n: extra }))}">${esc(t("tiles.more", { n: extra }))}</span>` : "";
  return `<span class="mosaic-wrap"><span class="mosaic small" role="img" aria-label="${esc(opSub(op))}">${tiles}</span>${more}</span>`;
}

function showLegend(id, visible) {
  const el = $(id);
  el.hidden = !visible;
  if (visible) el.innerHTML = legendHTML();
}
