/* =========================================================
   dashboard_app.js ― ダッシュボードの操作（済みチェック・絞り込み・折りたたみ・テーマ）
   * 外部ライブラリは使いません。
   * 状態はブラウザの localStorage に1つのキーで保存します。
   ========================================================= */
(function () {
  "use strict";

  // localStorage のキー。★変更しないこと★（変えると過去のチェック状態が読めなくなる）
  var STORAGE_KEY = "dailyInfoDashboard.state.v1";

  // ── 状態の読み書き（使えない環境でも落ちないよう try/catch で囲む） ──
  function loadState() {
    try {
      var raw = window.localStorage.getItem(STORAGE_KEY);
      var s = raw ? JSON.parse(raw) : {};
      s.done = s.done || {};           // 済みにした項目 { レコードID: 済みにした日 }
      s.folded = s.folded || {};       // 折りたたんだ区分 { 区分名: true }
      return s;
    } catch (e) {
      return { done: {}, folded: {} };
    }
  }
  function saveState() {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (e) {
      /* 保存できない環境ではそのページを開いている間だけ有効 */
    }
  }

  var state = loadState();
  var pageData = {};
  try { pageData = JSON.parse(document.getElementById("page-data").textContent); } catch (e) {}

  // ── 古い「済み」記録の掃除（60日より前のものは消して肥大化を防ぐ） ──
  (function pruneOld() {
    var limit = Date.now() - 60 * 24 * 60 * 60 * 1000;
    Object.keys(state.done).forEach(function (id) {
      var t = Date.parse(state.done[id]);
      if (!isNaN(t) && t < limit) delete state.done[id];
    });
  })();

  // ── テーマ（ライト／ダーク） ──
  var root = document.documentElement;
  if (state.theme) root.setAttribute("data-theme", state.theme);
  var themeBtn = document.getElementById("theme-toggle");
  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      var dark = root.getAttribute("data-theme") === "dark" ||
        (!root.getAttribute("data-theme") && window.matchMedia("(prefers-color-scheme: dark)").matches);
      state.theme = dark ? "light" : "dark";
      root.setAttribute("data-theme", state.theme);
      saveState();
    });
  }

  // ── 済みチェック ──
  var items = Array.prototype.slice.call(document.querySelectorAll(".item"));
  items.forEach(function (li) {
    var id = li.getAttribute("data-id");
    var box = li.querySelector(".done-box");
    if (!box) return;
    if (state.done[id]) { box.checked = true; li.classList.add("done"); }
    box.addEventListener("change", function () {
      if (box.checked) { state.done[id] = pageData.date || new Date().toISOString().slice(0, 10); li.classList.add("done"); }
      else { delete state.done[id]; li.classList.remove("done"); }
      saveState();
      applyFilters();
    });
  });

  // ── 区分の折りたたみ ──
  Array.prototype.forEach.call(document.querySelectorAll("section[data-section]"), function (sec) {
    var key = sec.getAttribute("data-section");
    var btn = sec.querySelector(".collapse");
    if (state.folded[key]) { sec.classList.add("folded"); btn.setAttribute("aria-expanded", "false"); }
    btn.addEventListener("click", function () {
      var folded = sec.classList.toggle("folded");
      btn.setAttribute("aria-expanded", folded ? "false" : "true");
      if (folded) state.folded[key] = true; else delete state.folded[key];
      saveState();
    });
  });

  // ── 絞り込み（キーワード・種類・カテゴリー・済みを隠す） ──
  var q = document.getElementById("q");
  var hideDone = document.getElementById("hide-done");
  var activeKinds = {};
  var activeTags = {};
  hideDone.checked = !!state.hideDone;

  function normalize(s) {
    // 全角英数を半角に、大文字を小文字にそろえる（HTML側の data-text と同じ考え方）
    return (s || "").normalize("NFKC").toLowerCase();
  }

  function applyFilters() {
    var words = normalize(q.value).split(/\s+/).filter(Boolean);
    var kinds = Object.keys(activeKinds);
    var tags = Object.keys(activeTags);
    items.forEach(function (li) {
      var text = li.getAttribute("data-text") || "";
      var show = words.every(function (w) { return text.indexOf(w) >= 0; });
      if (show && kinds.length) show = kinds.indexOf(li.getAttribute("data-kind")) >= 0;
      if (show && tags.length) {
        var t = (li.getAttribute("data-tags") || "").split("|");
        show = tags.some(function (x) { return t.indexOf(x) >= 0; });
      }
      if (show && hideDone.checked && li.classList.contains("done")) show = false;
      li.classList.toggle("hidden", !show);
    });
  }

  q.addEventListener("input", applyFilters);
  hideDone.addEventListener("change", function () { state.hideDone = hideDone.checked; saveState(); applyFilters(); });
  Array.prototype.forEach.call(document.querySelectorAll(".chip"), function (chip) {
    chip.addEventListener("click", function () {
      var kind = chip.getAttribute("data-filter-kind");
      var tag = chip.getAttribute("data-filter-tag");
      var bag = kind ? activeKinds : activeTags;
      var key = kind || tag;
      if (bag[key]) delete bag[key]; else bag[key] = true;
      chip.classList.toggle("on", !!bag[key]);
      applyFilters();
    });
  });

  // ── 見立ての根拠リンクを押したら、その項目が見えるようにする ──
  Array.prototype.forEach.call(document.querySelectorAll(".basis a"), function (a) {
    a.addEventListener("click", function () {
      var target = document.querySelector(a.getAttribute("href"));
      if (!target) return;
      var sec = target.closest("section");
      if (sec && sec.classList.contains("folded")) sec.querySelector(".collapse").click();
      target.classList.remove("hidden");
    });
  });

  saveState();
  applyFilters();
})();
