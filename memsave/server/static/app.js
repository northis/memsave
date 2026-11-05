/* memsave frontend: gallery, URL state, search, tags, paste and lightbox. */
(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const GAP = 16;
  const PAGE = 40;
  const state = {
    items: [],
    q: "",
    tag: null,
    sort: "new",
    total: 0,
    loading: false,
    hasMore: true,
    seq: 0,
    imageId: null,
    lbItem: null,
    mode: "browse",
    semanticAvailable: true,
    theme: document.documentElement.dataset.theme || "light",
  };
  const pendingTimers = new Map();
  const jobTimers = new Map();
  let searchTimer = null;
  let resizeTimer = null;
  let dropDepth = 0;
  let lightboxTouchStart = null;

  const els = {
    search: $("#search"),
    searchClear: $("#search-clear"),
    sortButtons: Array.from(document.querySelectorAll(".sort-switch button")),
    themeBtn: $("#theme-btn"),
    themeIcon: $("#theme-icon"),
    build: $("#build"),
    buildVersion: $("#build-version"),
    buildCommit: $("#build-commit"),
    filter: $("#filter"),
    filterTag: $("#filter-tag"),
    clearFilter: $("#clear-filter"),
    stats: $("#stats"),
    searchMode: $("#search-mode"),
    masonry: $("#masonry"),
    sentinel: $("#sentinel"),
    loading: $("#loading"),
    empty: $("#empty"),
    emptyPick: $("#empty-pick"),
    fab: $("#upload-btn"),
    fileInput: $("#file-input"),
    drop: $("#drop"),
    lightbox: $("#lightbox"),
    lbImg: $("#lb-img"),
    lbPending: $("#lb-pending"),
    lbCategory: $("#lb-category"),
    lbStatus: $("#lb-status"),
    lbTitle: $("#lb-title"),
    lbDate: $("#lb-date"),
    lbDescription: $("#lb-description"),
    lbTags: $("#lb-tags"),
    lbTextWrap: $("#lb-text-wrap"),
    lbText: $("#lb-text"),
    lbSource: $("#lb-source"),
    lbDownload: $("#lb-download"),
    lbRetry: $("#lb-retry"),
    lbDelete: $("#lb-delete"),
    lbMeta: $("#lb-meta"),
    lbClose: $("#lb-close"),
    lbPrev: $("#lb-prev"),
    lbNext: $("#lb-next"),
    confirm: $("#confirm"),
    confirmCancel: $("#confirm-cancel"),
    confirmOk: $("#confirm-ok"),
    toasts: $("#toasts"),
  };

  const ICON_SUN = '<circle cx="12" cy="12" r="4"></circle><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"></path>';
  const ICON_MOON = '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>';
  const ICON_IMAGE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"></rect><circle cx="8.5" cy="9" r="1.5"></circle><path d="m3 16 4.5-4.5 4 4 3-2.5L21 18"></path></svg>';

  // ------------------------------------------------------------------ URL

  function parseUrl() {
    const params = new URLSearchParams(location.search);
    const image = Number(params.get("image") || 0);
    return {
      q: (params.get("q") || "").trim(),
      tag: params.get("tag") || null,
      sort: params.get("sort") === "old" ? "old" : "new",
      image: image > 0 ? image : null,
    };
  }

  function writeUrl(push) {
    const params = new URLSearchParams();
    if (state.q) params.set("q", state.q);
    if (state.tag) params.set("tag", state.tag);
    if (state.sort !== "new") params.set("sort", state.sort);
    if (state.imageId) params.set("image", String(state.imageId));
    const query = params.toString();
    const url = query ? `/?${query}` : "/";
    const method = push ? "pushState" : "replaceState";
    history[method]({ memsave: true }, "", url);
  }

  function syncControls() {
    els.search.value = state.q;
    els.searchClear.hidden = !state.q;
    els.filter.hidden = !state.tag;
    if (state.tag) els.filterTag.textContent = `#${state.tag}`;
    for (const button of els.sortButtons) {
      button.classList.toggle("active", button.dataset.sort === state.sort);
    }
    els.sortButtons.forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.sort === state.sort)));
  }

  // -------------------------------------------------------------- rendering

  function formatBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) return "0 Б";
    const units = ["Б", "КБ", "МБ", "ГБ"];
    let value = bytes;
    let index = 0;
    while (value >= 1024 && index < units.length - 1) {
      value /= 1024;
      index += 1;
    }
    return `${value >= 10 || index === 0 ? Math.round(value) : value.toFixed(1)} ${units[index]}`;
  }

  function fullDate(ts) {
    return new Date(ts * 1000).toLocaleString("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function relativeTime(ts) {
    const diff = ts * 1000 - Date.now();
    const abs = Math.abs(diff);
    if (abs < 45 * 1000) return "только что";
    const rtf = new Intl.RelativeTimeFormat("ru", { numeric: "auto" });
    if (abs < 3600 * 1000) return rtf.format(Math.round(diff / 60000), "minute");
    if (abs < 86400 * 1000) return rtf.format(Math.round(diff / 3600000), "hour");
    if (abs < 30 * 86400 * 1000) return rtf.format(Math.round(diff / 86400000), "day");
    if (abs < 365 * 86400 * 1000) return rtf.format(Math.round(diff / (30 * 86400000)), "month");
    return rtf.format(Math.round(diff / (365 * 86400000)), "year");
  }

  function makeWhen(ts) {
    const time = document.createElement("time");
    time.className = "when";
    time.dateTime = new Date(ts * 1000).toISOString();
    time.dataset.ts = String(ts);
    time.textContent = relativeTime(ts);
    time.title = fullDate(ts);
    return time;
  }

  function categoryOf(item) {
    return (item.tags || []).find((tag) => tag.kind === "category") || null;
  }

  function freeTags(item) {
    return (item.tags || []).filter((tag) => tag.kind !== "category");
  }

  function tagChip(tag, extraClass = "") {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `tag-chip ${extraClass}`.trim();
    button.textContent = tag.name;
    button.title = `Показать всё с тегом #${tag.name}`;
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      setTag(tag.name, true);
    });
    return button;
  }

  function createCard(item) {
    const card = document.createElement("article");
    card.className = "card";
    card.dataset.id = String(item.id);
    card.addEventListener("click", () => openLightbox(item.id, true));

    const media = document.createElement("button");
    media.type = "button";
    media.className = "card-media";
    media.setAttribute("aria-label", `Открыть картинку ${item.id}`);
    const thumb = item.thumb_url || (item.status === "ready" ? item.url : null);
    if (thumb) {
      const img = document.createElement("img");
      img.src = thumb;
      img.alt = item.category ? `${categoryOf(item)?.name || ""}` : "";
      img.loading = "lazy";
      img.decoding = "async";
      img.addEventListener("load", scheduleLayout);
      img.addEventListener("error", () => {
        img.replaceWith(placeholder());
        scheduleLayout();
      });
      media.appendChild(img);
    } else {
      media.appendChild(placeholder());
    }

    if (item.status === "pending") {
      const badge = document.createElement("span");
      badge.className = "status-badge";
      badge.innerHTML = '<span class="mini-spinner" aria-hidden="true"></span>AI…';
      badge.title = "Картинка сохранена; AI ещё размечает её";
      media.appendChild(badge);
    } else if (item.status === "failed") {
      const badge = document.createElement("span");
      badge.className = "status-badge failed";
      badge.textContent = item.ai_error ? "Без тегов" : "AI";
      badge.title = item.ai_error || "AI не смог разметить картинку";
      media.appendChild(badge);
    }
    card.appendChild(media);

    const info = document.createElement("div");
    info.className = "card-info";
    info.appendChild(makeWhen(item.saved_at));

    const tags = document.createElement("div");
    tags.className = "card-tags";
    const category = categoryOf(item);
    if (category) tags.appendChild(tagChip(category, "category"));
    const extras = freeTags(item);
    for (const tag of extras.slice(0, 2)) tags.appendChild(tagChip(tag));
    if (tags.children.length) info.appendChild(tags);
    card.appendChild(info);
    return card;
  }

  function placeholder() {
    const div = document.createElement("div");
    div.className = "thumb-placeholder";
    div.innerHTML = ICON_IMAGE;
    return div;
  }

  function render() {
    const fragment = document.createDocumentFragment();
    for (const item of state.items) fragment.appendChild(createCard(item));
    els.masonry.replaceChildren(fragment);
    els.empty.hidden = state.items.length > 0 || state.loading;
    els.loading.hidden = true;
    updateStats();
    scheduleLayout();
    ensurePendingPolling();
  }

  function updateStats() {
    let text = "";
    if (state.q) text = `Найдено: ${state.total}`;
    else if (state.tag) text = `Тег #${state.tag}: ${state.total}`;
    else text = `${state.total} ${plural(state.total, "картинка", "картинки", "картинок")}`;
    els.stats.textContent = text;

    let mode = "";
    if (state.q) {
      if (state.semanticAvailable === false) mode = "AI-поиск недоступен — текстовый поиск";
      else if (state.mode === "hybrid") mode = "AI-поиск";
      else mode = "Текстовый поиск";
    }
    els.searchMode.textContent = mode;
    els.searchMode.hidden = !mode;
  }

  function plural(number, one, few, many) {
    const mod10 = number % 10;
    const mod100 = number % 100;
    if (mod10 === 1 && mod100 !== 11) return one;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
    return many;
  }

  // --------------------------------------------------------------- masonry

  function layout() {
    const container = els.masonry;
    const width = container.clientWidth;
    if (!width) return;
    const minWidth = width < 600 ? 156 : 224;
    const columns = Math.max(2, Math.floor((width + GAP) / (minWidth + GAP)));
    const columnWidth = (width - GAP * (columns - 1)) / columns;
    const heights = new Array(columns).fill(0);
    const cards = Array.from(container.children);

    for (const card of cards) {
      let column = 0;
      for (let index = 1; index < columns; index += 1) {
        if (heights[index] < heights[column]) column = index;
      }
      const x = column * (columnWidth + GAP);
      const y = heights[column];
      card.style.width = `${columnWidth}px`;
      card.style.transform = `translate3d(${x}px, ${y}px, 0)`;
      heights[column] = y + card.offsetHeight + GAP;
    }
    const height = heights.length ? Math.max(...heights) - GAP : 0;
    container.style.height = `${Math.max(0, height)}px`;
  }

  let layoutFrame = 0;
  function scheduleLayout() {
    if (layoutFrame) return;
    layoutFrame = requestAnimationFrame(() => {
      layoutFrame = 0;
      layout();
    });
  }

  // ------------------------------------------------------------- data load

  async function load({ reset = false } = {}) {
    const seq = ++state.seq;
    if (reset) {
      state.items = [];
      state.total = 0;
      state.hasMore = true;
      state.mode = "browse";
      state.loading = true;
      els.empty.hidden = true;
      render();
    }
    state.loading = true;
    els.loading.hidden = state.items.length > 0;
    const params = new URLSearchParams({
      offset: String(state.items.length),
      limit: String(PAGE),
      sort: state.sort,
    });
    if (state.q) params.set("q", state.q);
    if (state.tag) params.set("tag", state.tag);

    try {
      const response = await fetch(`/api/images?${params}`, { credentials: "same-origin" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (seq !== state.seq) return;
      const incoming = Array.isArray(data.items) ? data.items : [];
      const known = new Set(state.items.map((item) => item.id));
      for (const item of incoming) {
        if (!known.has(item.id)) state.items.push(item);
      }
      state.total = Number(data.total || 0);
      state.hasMore = Boolean(data.has_more);
      state.mode = data.mode || "browse";
      state.semanticAvailable = data.semantic_available !== false;
      render();
    } catch (error) {
      if (seq === state.seq) {
        toast(`Не удалось загрузить архив: ${error.message}`, "error");
        els.empty.hidden = state.items.length > 0;
      }
    } finally {
      if (seq === state.seq) {
        state.loading = false;
        els.loading.hidden = true;
        els.empty.hidden = state.items.length > 0 || state.loading;
      }
    }
  }

  async function loadMore() {
    if (state.loading || !state.hasMore) return;
    await load({ reset: false });
  }

  // --------------------------------------------------------------- filters

  function closeLightboxForFilter() {
    if (!els.lightbox.hidden) closeLightbox(false);
  }

  function setTag(tag, push) {
    closeLightboxForFilter();
    state.tag = tag || null;
    state.q = els.search.value.trim();
    syncControls();
    writeUrl(Boolean(push));
    load({ reset: true });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function setSort(sort) {
    if (state.sort === sort) return;
    closeLightboxForFilter();
    state.sort = sort;
    syncControls();
    writeUrl(true);
    load({ reset: true });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function setQuery(query, push) {
    closeLightboxForFilter();
    state.q = query.trim();
    els.searchClear.hidden = !state.q;
    state.imageId = null;
    writeUrl(Boolean(push));
    load({ reset: true });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // -------------------------------------------------------------- lightbox

  function updateTimeElements() {
    document.querySelectorAll("time[data-ts]").forEach((element) => {
      const ts = Number(element.dataset.ts);
      if (ts) element.textContent = relativeTime(ts);
    });
    if (state.lbItem) els.lbDate.textContent = `${fullDate(state.lbItem.saved_at)} · ${relativeTime(state.lbItem.saved_at)}`;
  }

  async function openLightbox(id, push = false) {
    if (!id) return;
    try {
      const response = await fetch(`/api/images/${id}`, { credentials: "same-origin" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const item = await response.json();
      state.lbItem = item;
      state.imageId = item.id;
      els.lbImg.src = item.url || item.thumb_url || "";
      els.lbImg.alt = item.title || item.description || "Картинка";
      els.lbPending.hidden = item.status !== "pending";
      const category = categoryOf(item);
      els.lbCategory.textContent = category ? category.name : "";
      els.lbCategory.hidden = !category;
      els.lbStatus.textContent =
        item.status === "pending"
          ? "AI размечает…"
          : item.status === "failed"
            ? item.ai_error || "AI не смог разметить"
            : "";
      els.lbTitle.textContent = item.title || (category ? category.name : "Картинка");
      els.lbDate.textContent = `${fullDate(item.saved_at)} · ${relativeTime(item.saved_at)}`;
      els.lbDescription.textContent = item.description || "";
      els.lbDescription.hidden = !item.description;

      els.lbTags.replaceChildren();
      for (const tag of item.tags || []) {
        els.lbTags.appendChild(tagChip(tag, tag.kind === "category" ? "category" : ""));
      }
      if (!(item.tags || []).length) {
        const empty = document.createElement("span");
        empty.className = "lb-status";
        empty.textContent = item.status === "pending" ? "Теги появятся после AI" : "Тегов нет";
        els.lbTags.appendChild(empty);
      }

      const text = (item.text || "").trim();
      els.lbTextWrap.hidden = !text;
      els.lbText.textContent = text;

      const source = safeExternalUrl(item.source_url || item.image_url);
      els.lbSource.hidden = !source;
      if (source) {
        els.lbSource.href = source;
        els.lbSource.textContent = item.source_url ? "Источник" : "Оригинал";
      }
      els.lbDownload.href = item.url || "#";
      els.lbDownload.download = (item.url || "").split("/").pop() || "image";
      els.lbDownload.hidden = !item.url;
      els.lbRetry.hidden = item.status !== "failed";

      const meta = [];
      if (item.width && item.height) meta.push(`${item.width}×${item.height}`);
      if (item.size_bytes) meta.push(formatBytes(item.size_bytes));
      meta.push(`#${item.id}`);
      if (item.source) meta.push(item.source);
      if (item.ai_model) meta.push(item.ai_model);
      els.lbMeta.textContent = meta.join(" · ");

      els.lightbox.hidden = false;
      document.body.style.overflow = "hidden";
      els.lbClose.focus({ preventScroll: true });
      if (push) writeUrl(false);
    } catch (error) {
      toast(`Не удалось открыть картинку: ${error.message}`, "error");
    }
  }

  function closeLightbox(push = false) {
    els.lightbox.hidden = true;
    els.lbImg.removeAttribute("src");
    els.lbPending.hidden = true;
    state.lbItem = null;
    state.imageId = null;
    document.body.style.overflow = "";
    if (push) writeUrl(false);
  }

  function stepLightbox(delta) {
    if (!state.items.length) return;
    const index = state.items.findIndex((item) => item.id === state.imageId);
    let nextIndex;
    if (index < 0) nextIndex = 0;
    else nextIndex = (index + delta + state.items.length) % state.items.length;
    const next = state.items[nextIndex];
    if (next) openLightbox(next.id, false);
  }

  // ------------------------------------------------------------- uploading

  function safeExternalUrl(value) {
    if (!value) return null;
    try {
      const url = new URL(value, location.origin);
      return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
    } catch {
      return null;
    }
  }

  function toast(message, tone = "info", timeout = 4200) {
    const node = document.createElement("div");
    node.className = `toast ${tone}`.trim();
    node.textContent = message;
    els.toasts.appendChild(node);
    setTimeout(() => node.remove(), timeout);
  }

  function headersForFile(file, source) {
    return {
      "Content-Type": file.type || "application/octet-stream",
      "X-Memsave-Request": "1",
      "X-Memsave-Source": source,
      "X-Memsave-Filename": encodeURIComponent(file.name || "image"),
    };
  }

  function looksLikeImage(file) {
    if (!file) return false;
    if ((file.type || "").startsWith("image/")) return true;
    return /\.(jpe?g|png|webp|gif|heic|heif|avif|bmp|tiff?)$/i.test(file.name || "");
  }

  function absorbSavedImage(item) {
    if (state.q || state.tag || state.sort === "old") {
      load({ reset: true });
      return;
    }
    if (state.items.some((entry) => entry.id === item.id)) return;
    state.items.unshift(item);
    state.total += 1;
    render();
  }

  async function uploadFile(file, source = "upload") {
    if (!looksLikeImage(file)) {
      toast("Это не картинка", "error");
      return;
    }
    try {
      const response = await fetch("/api/images", {
        method: "POST",
        headers: headersForFile(file, source),
        body: file,
        credentials: "same-origin",
      });
      const contentType = response.headers.get("content-type") || "";
      if (!contentType.includes("application/json")) {
        throw new Error("сервер вернул не JSON — возможно, истекла сессия Cloudflare Access");
      }
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      if (data.duplicate) {
        toast("Такая картинка уже есть в архиве");
        return;
      }
      const item = data.image;
      absorbSavedImage(item);
      toast("Сохранено. AI размечает картинку…", "success");
      pollImage(item.id);
    } catch (error) {
      toast(`Не удалось сохранить: ${error.message}`, "error", 6500);
    }
  }

  async function uploadFiles(files, source = "upload") {
    const list = Array.from(files || []).filter((file) => looksLikeImage(file));
    if (!list.length) {
      toast("В буфере нет картинки", "error");
      return;
    }
    for (const file of list) {
      await uploadFile(file, source);
    }
  }

  function pollImage(id) {
    if (pendingTimers.has(id)) return;
    const tick = async () => {
      try {
        const response = await fetch(`/api/images/${id}`, { credentials: "same-origin" });
        if (response.ok) {
          const item = await response.json();
          const index = state.items.findIndex((entry) => entry.id === id);
          if (index >= 0) {
            state.items[index] = item;
            render();
          }
          if (state.lbItem && state.lbItem.id === id) {
            state.lbItem = item;
            if (!els.lightbox.hidden) openLightbox(id, false);
          }
          if (item.status === "pending") {
            pendingTimers.set(id, setTimeout(tick, 1800));
          } else {
            pendingTimers.delete(id);
          }
        } else {
          pendingTimers.delete(id);
        }
      } catch {
        pendingTimers.delete(id);
      }
    };
    pendingTimers.set(id, setTimeout(tick, 900));
  }

  function ensurePendingPolling() {
    for (const item of state.items) {
      if (item.status === "pending") pollImage(item.id);
    }
  }

  async function saveLink(url) {
    try {
      const response = await fetch("/api/links", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Memsave-Request": "1" },
        body: JSON.stringify({ url }),
        credentials: "same-origin",
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      toast("Ссылка принята, ищу картинку…");
      pollJob(data.job_id);
    } catch (error) {
      toast(`Не удалось принять ссылку: ${error.message}`, "error");
    }
  }

  function pollJob(id) {
    if (jobTimers.has(id)) return;
    const tick = async () => {
      try {
        const response = await fetch(`/api/jobs/${id}`, { credentials: "same-origin" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const job = await response.json();
        if (job.status === "done") {
          if (job.image) {
            const exists = state.items.some((item) => item.id === job.image.id);
            if (!exists) {
              absorbSavedImage(job.image);
              pollImage(job.image.id);
            }
            toast("Картинка из ссылки сохранена", "success");
          } else {
            toast("Ссылка обработана, но картинки в ней нет", "error", 6000);
          }
          jobTimers.delete(id);
          return;
        }
        if (job.status === "failed") {
          toast("В ссылке не нашлось картинки — ничего не добавлено", "error", 6000);
          jobTimers.delete(id);
          return;
        }
        jobTimers.set(id, setTimeout(tick, 1400));
      } catch (error) {
        toast(`Не удалось получить картинку: ${error.message}`, "error");
        jobTimers.delete(id);
      }
    };
    jobTimers.set(id, setTimeout(tick, 800));
  }

  // ------------------------------------------------------------- lightbox actions

  function askDelete() {
    if (!state.lbItem) return;
    els.confirm.hidden = false;
    els.confirmOk.focus();
  }

  function hideConfirm() {
    els.confirm.hidden = true;
  }

  async function deleteCurrent() {
    const id = state.lbItem && state.lbItem.id;
    if (!id) return;
    hideConfirm();
    try {
      const response = await fetch(`/api/images/${id}`, {
        method: "DELETE",
        headers: { "X-Memsave-Request": "1" },
        credentials: "same-origin",
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || `HTTP ${response.status}`);
      }
      state.items = state.items.filter((item) => item.id !== id);
      state.total = Math.max(0, state.total - 1);
      closeLightbox(true);
      render();
      toast("Картинка удалена");
    } catch (error) {
      toast(`Не удалось удалить: ${error.message}`, "error");
    }
  }

  async function retryCurrent() {
    if (!state.lbItem) return;
    const id = state.lbItem.id;
    try {
      const response = await fetch(`/api/images/${id}/retry`, {
        method: "POST",
        headers: { "X-Memsave-Request": "1" },
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const index = state.items.findIndex((item) => item.id === id);
      if (index >= 0) {
        state.items[index].status = "pending";
        state.items[index].ai_error = null;
        render();
      }
      els.lbStatus.textContent = "AI размечает…";
      els.lbPending.hidden = false;
      els.lbRetry.hidden = true;
      toast("Распознавание запущено");
      pollImage(id);
    } catch (error) {
      toast(`Не удалось запустить AI: ${error.message}`, "error");
    }
  }

  // ------------------------------------------------------------------- theme

  function applyTheme(theme) {
    state.theme = theme;
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("memsave_theme", theme);
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = theme === "dark" ? "#0f1712" : "#eef8ea";
    els.themeIcon.innerHTML = theme === "dark" ? ICON_MOON : ICON_SUN;
    els.themeBtn.title = theme === "dark" ? "Светлая тема" : "Тёмная тема";
  }

  // ------------------------------------------------------------------ version

  async function loadVersion() {
    try {
      const response = await fetch("/api/version", { credentials: "same-origin" });
      if (!response.ok) return;
      const info = await response.json();
      els.build.hidden = false;
      if (info.tag_url) {
        els.buildVersion.href = info.tag_url;
        els.buildVersion.textContent = `v${info.version}`;
      } else {
        els.buildVersion.textContent = `v${info.version}`;
      }
      if (info.commit && info.commit_url) {
        els.buildCommit.hidden = false;
        els.buildCommit.href = info.commit_url;
        els.buildCommit.textContent = `${info.short || info.commit.slice(0, 7)}${info.dirty ? "*" : ""}`;
        els.buildCommit.title = info.commit_date || "";
      } else {
        els.buildCommit.hidden = true;
        $(".build-dot", els.build).hidden = true;
      }
    } catch {
      /* version is optional */
    }
  }

  // ------------------------------------------------------------------ events

  function bindEvents() {
    els.search.addEventListener("input", () => {
      const value = els.search.value;
      els.searchClear.hidden = !value;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => setQuery(value, false), 260);
    });
    els.search.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        clearTimeout(searchTimer);
        setQuery(els.search.value, true);
      } else if (event.key === "Escape") {
        els.search.value = "";
        setQuery("", false);
      }
    });
    els.searchClear.addEventListener("click", () => {
      els.search.value = "";
      els.search.focus();
      setQuery("", false);
    });
    for (const button of els.sortButtons) {
      button.addEventListener("click", () => setSort(button.dataset.sort));
    }
    els.themeBtn.addEventListener("click", () => applyTheme(state.theme === "dark" ? "light" : "dark"));
    els.clearFilter.addEventListener("click", () => setTag(null, true));
    els.filterTag.addEventListener("click", () => {
      if (state.tag) setTag(state.tag, true);
    });

    els.fab.addEventListener("click", () => els.fileInput.click());
    els.emptyPick.addEventListener("click", () => els.fileInput.click());
    els.fileInput.addEventListener("change", () => {
      uploadFiles(els.fileInput.files, "upload");
      els.fileInput.value = "";
    });

    document.addEventListener("paste", (event) => {
      const target = event.target;
      if (target instanceof HTMLElement && (target.tagName === "INPUT" || target.tagName === "TEXTAREA")) {
        return;
      }
      const files = [];
      for (const item of event.clipboardData?.items || []) {
        if (item.kind === "file" && item.type.startsWith("image/")) {
          const file = item.getAsFile();
          if (file) files.push(file);
        }
      }
      if (files.length) {
        event.preventDefault();
        uploadFiles(files, "paste");
        return;
      }
      const text = event.clipboardData?.getData("text/plain") || "";
      const match = text.match(/https?:\/\/[^\s<>"']+/i);
      if (match) {
        event.preventDefault();
        saveLink(match[0].replace(/[.,);\]}>"']+$/, ""));
      }
    });

    window.addEventListener("dragenter", (event) => {
      if (!event.dataTransfer || !Array.from(event.dataTransfer.types).includes("Files")) return;
      event.preventDefault();
      dropDepth += 1;
      els.drop.hidden = false;
    });
    window.addEventListener("dragover", (event) => {
      if (!event.dataTransfer || !Array.from(event.dataTransfer.types).includes("Files")) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    });
    window.addEventListener("dragleave", (event) => {
      if (!event.dataTransfer || !Array.from(event.dataTransfer.types).includes("Files")) return;
      dropDepth = Math.max(0, dropDepth - 1);
      if (dropDepth === 0) els.drop.hidden = true;
    });
    window.addEventListener("drop", (event) => {
      event.preventDefault();
      dropDepth = 0;
      els.drop.hidden = true;
      if (event.dataTransfer?.files?.length) uploadFiles(event.dataTransfer.files, "upload");
    });

    els.lightbox.addEventListener("click", (event) => {
      if (event.target.closest("[data-close]")) closeLightbox(true);
    });
    els.lbClose.addEventListener("click", () => closeLightbox(true));
    els.lbPrev.addEventListener("click", () => stepLightbox(-1));
    els.lbNext.addEventListener("click", () => stepLightbox(1));
    els.lbDelete.addEventListener("click", askDelete);
    els.lbRetry.addEventListener("click", retryCurrent);
    els.confirmCancel.addEventListener("click", hideConfirm);
    els.confirmOk.addEventListener("click", deleteCurrent);
    els.confirm.addEventListener("click", (event) => {
      if (event.target === els.confirm) hideConfirm();
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        if (!els.confirm.hidden) hideConfirm();
        else if (!els.lightbox.hidden) closeLightbox(true);
      } else if (!els.lightbox.hidden && event.key === "ArrowLeft") {
        stepLightbox(-1);
      } else if (!els.lightbox.hidden && event.key === "ArrowRight") {
        stepLightbox(1);
      }
    });

    els.lbImg.addEventListener("touchstart", (event) => {
      const touch = event.changedTouches[0];
      lightboxTouchStart = { x: touch.clientX, y: touch.clientY };
    }, { passive: true });
    els.lbImg.addEventListener("touchend", (event) => {
      if (!lightboxTouchStart) return;
      const touch = event.changedTouches[0];
      const dx = touch.clientX - lightboxTouchStart.x;
      const dy = touch.clientY - lightboxTouchStart.y;
      lightboxTouchStart = null;
      if (Math.abs(dx) > 55 && Math.abs(dx) > Math.abs(dy)) stepLightbox(dx > 0 ? -1 : 1);
    }, { passive: true });

    window.addEventListener("popstate", () => {
      const next = parseUrl();
      const changed = next.q !== state.q || next.tag !== state.tag || next.sort !== state.sort;
      state.q = next.q;
      state.tag = next.tag;
      state.sort = next.sort;
      syncControls();
      if (next.image && next.image !== state.imageId) {
        openLightbox(next.image, false);
      } else if (!next.image && state.imageId) {
        closeLightbox(false);
      }
      if (changed) load({ reset: true });
    });

    window.addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(scheduleLayout, 120);
    });

    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) loadMore();
    }, { rootMargin: "600px 0px" });
    observer.observe(els.sentinel);

    setInterval(updateTimeElements, 60000);
  }

  // -------------------------------------------------------------------- init

  function begin() {
    const initial = parseUrl();
    state.q = initial.q;
    state.tag = initial.tag;
    state.sort = initial.sort;
    state.imageId = initial.image;
    syncControls();
    applyTheme(document.documentElement.dataset.theme || "light");
    bindEvents();
    loadVersion();
    load({ reset: true }).then(() => {
      if (initial.image) openLightbox(initial.image, false);
    });
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => {});
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", begin);
  } else {
    begin();
  }
})();
