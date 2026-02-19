const state = {
  map: null,
  markersLayer: null,
  allLocated: [],
  allUnlocated: [],
  filtered: [],
  activeJobId: null,
  cacheEntries: [],
  selectedCacheIds: new Set(),
  loadedCacheIds: new Set(),
};

const el = {
  pathInput: document.getElementById("pathInput"),
  pickDirBtn: document.getElementById("pickDirBtn"),
  scanBtn: document.getElementById("scanBtn"),
  scanStatus: document.getElementById("scanStatus"),
  summaryText: document.getElementById("summaryText"),
  progressBarFill: document.querySelector("#progressBar span"),
  refreshCacheBtn: document.getElementById("refreshCacheBtn"),
  loadSelectedCacheBtn: document.getElementById("loadSelectedCacheBtn"),
  toggleSelectCacheBtn: document.getElementById("toggleSelectCacheBtn"),
  clearCacheBtn: document.getElementById("clearCacheBtn"),
  cacheSummary: document.getElementById("cacheSummary"),
  cacheList: document.getElementById("cacheList"),
  keywordInput: document.getElementById("keywordInput"),
  typeSelect: document.getElementById("typeSelect"),
  fromDate: document.getElementById("fromDate"),
  toDate: document.getElementById("toDate"),
  clusterList: document.getElementById("clusterList"),
  unlocatedList: document.getElementById("unlocatedList"),
  previewModal: document.getElementById("previewModal"),
  closeModal: document.getElementById("closeModal"),
  previewMedia: document.getElementById("previewMedia"),
  previewMeta: document.getElementById("previewMeta"),
};

function initMap() {
  state.map = L.map("map", {
    worldCopyJump: true,
    zoomControl: true,
  }).setView([24, 103], 3);

  const street = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap",
    maxZoom: 19,
  });

  const topo = L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenTopoMap",
    maxZoom: 17,
  });

  const carto = L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
    attribution: "&copy; OpenStreetMap & CARTO",
    maxZoom: 19,
  });

  street.addTo(state.map);
  L.control.layers({
    标准底图: street,
    地形底图: topo,
    简洁底图: carto,
  }).addTo(state.map);

  state.markersLayer = L.layerGroup().addTo(state.map);

  state.map.on("zoomend", () => {
    renderMapMarkers();
  });
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function formatDate(raw) {
  if (!raw) return "未知时间";
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return "未知时间";
  return d.toLocaleString();
}

function thumbnailUrl(id) {
  return `/api/thumbnail?id=${encodeURIComponent(id)}`;
}

function previewUrl(id) {
  return `/api/preview?id=${encodeURIComponent(id)}`;
}

function updateSummary() {
  const located = state.allLocated.length;
  const unlocated = state.allUnlocated.length;
  const total = located + unlocated;
  el.summaryText.textContent = `总计 ${total}，已定位 ${located}，未定位 ${unlocated}`;
}

function itemPassesFilter(item) {
  const keyword = el.keywordInput.value.trim().toLowerCase();
  const type = el.typeSelect.value;
  const fromDate = el.fromDate.value;
  const toDate = el.toDate.value;

  if (keyword && !item.name.toLowerCase().includes(keyword)) {
    return false;
  }
  if (type !== "all" && item.type !== type) {
    return false;
  }

  if (fromDate || toDate) {
    const t = item.captured_at ? new Date(item.captured_at).getTime() : NaN;
    if (Number.isNaN(t)) {
      return false;
    }
    if (fromDate) {
      const from = new Date(`${fromDate}T00:00:00`).getTime();
      if (t < from) return false;
    }
    if (toDate) {
      const to = new Date(`${toDate}T23:59:59`).getTime();
      if (t > to) return false;
    }
  }

  return true;
}

function applyFilters() {
  state.filtered = state.allLocated.filter(itemPassesFilter);
  renderMapMarkers();
  renderUnlocated();
}

function applyScanPayload(payload) {
  state.allLocated = payload.items || [];
  state.allUnlocated = payload.unlocated || [];
  updateSummary();
  applyFilters();

  if (state.allLocated.length) {
    const bounds = L.latLngBounds(state.allLocated.map(item => [item.lat, item.lon]));
    state.map.fitBounds(bounds.pad(0.15), { maxZoom: 9 });
  }
}

function buildClusters(items) {
  if (!items.length) return [];

  const map = state.map;
  const zoom = map.getZoom();
  const cellSize = zoom >= 11 ? 74 : zoom >= 8 ? 92 : zoom >= 5 ? 112 : 136;
  const buckets = new Map();

  for (const item of items) {
    const point = map.project([item.lat, item.lon], zoom);
    const key = `${Math.floor(point.x / cellSize)}:${Math.floor(point.y / cellSize)}`;
    if (!buckets.has(key)) {
      buckets.set(key, []);
    }
    buckets.get(key).push(item);
  }

  return Array.from(buckets.values()).map(clusterItems => {
    const lat = clusterItems.reduce((sum, item) => sum + item.lat, 0) / clusterItems.length;
    const lon = clusterItems.reduce((sum, item) => sum + item.lon, 0) / clusterItems.length;
    clusterItems.sort((a, b) => (b.captured_at || "").localeCompare(a.captured_at || ""));
    return { lat, lon, items: clusterItems };
  });
}

function renderClusterList(items) {
  el.clusterList.innerHTML = "";
  if (!items.length) {
    el.clusterList.innerHTML = '<div class="summary">点击地图缩略图分组后会显示在这里</div>';
    return;
  }

  for (const item of items) {
    const row = document.createElement("div");
    row.className = "list-item";
    row.innerHTML = `
      <img loading="lazy" src="${thumbnailUrl(item.id)}" alt="thumb" />
      <div>
        <h3>${escapeHtml(item.name)}</h3>
        <p>${escapeHtml(formatDate(item.captured_at))}</p>
      </div>
    `;
    row.addEventListener("click", () => openPreview(item));
    el.clusterList.appendChild(row);
  }
}

function renderMapMarkers() {
  if (!state.map) return;
  state.markersLayer.clearLayers();

  const clusters = buildClusters(state.filtered);

  for (const cluster of clusters) {
    const cover = cluster.items.find(item => item.type === "image") || cluster.items[0];
    const countText = cluster.items.length > 1 ? `<span class="count">${cluster.items.length}</span>` : "";
    const iconHtml = `
      <div class="thumb-pin">
        <img loading="lazy" src="${thumbnailUrl(cover.id)}" alt="thumb" />
        ${countText}
      </div>
    `;

    const icon = L.divIcon({
      className: "thumb-pin-wrapper",
      html: iconHtml,
      iconSize: [84, 94],
      iconAnchor: [42, 90],
    });

    const marker = L.marker([cluster.lat, cluster.lon], { icon }).addTo(state.markersLayer);
    marker.on("click", () => {
      renderClusterList(cluster.items);
      if (cluster.items.length === 1) {
        openPreview(cluster.items[0]);
      } else {
        const bounds = L.latLngBounds(cluster.items.map(x => [x.lat, x.lon]));
        state.map.fitBounds(bounds.pad(0.6), { maxZoom: Math.max(state.map.getZoom() + 1, 6) });
      }
    });
  }
}

function renderUnlocated() {
  el.unlocatedList.innerHTML = "";
  const keyword = el.keywordInput.value.trim().toLowerCase();
  const type = el.typeSelect.value;

  const rows = state.allUnlocated.filter(item => {
    if (keyword && !item.name.toLowerCase().includes(keyword)) return false;
    if (type !== "all" && item.type !== type) return false;
    return true;
  });

  if (!rows.length) {
    el.unlocatedList.innerHTML = '<div class="summary">无未定位文件</div>';
    return;
  }

  for (const item of rows.slice(0, 600)) {
    const row = document.createElement("div");
    row.className = "list-item";
    row.innerHTML = `
      <img loading="lazy" src="${thumbnailUrl(item.id)}" alt="thumb" />
      <div>
        <h3>${escapeHtml(item.name)}</h3>
        <p>${escapeHtml(formatDate(item.captured_at))}</p>
      </div>
    `;
    row.addEventListener("click", () => openPreview(item));
    el.unlocatedList.appendChild(row);
  }
}

function renderCacheList() {
  el.cacheList.innerHTML = "";
  if (!state.cacheEntries.length) {
    el.cacheList.innerHTML = '<div class="summary">暂无目录扫描缓存</div>';
    syncSelectionState();
    return;
  }

  for (const entry of state.cacheEntries) {
    const row = document.createElement("div");
    row.className = "list-item cache-item";
    const checked = state.selectedCacheIds.has(entry.scan_id) ? "checked" : "";
    row.innerHTML = `
      <div class="cache-header">
        <input class="cache-check" type="checkbox" data-action="select" data-scan-id="${escapeHtml(entry.scan_id)}" ${checked} />
        <div class="cache-head">${escapeHtml(entry.root_path || "")}</div>
      </div>
      <div class="cache-meta">${escapeHtml(formatDate(entry.updated_at))} · 总计 ${entry.total || 0} · 已定位 ${entry.located || 0}</div>
      <div class="cache-buttons">
        <button type="button" data-action="load" data-scan-id="${escapeHtml(entry.scan_id)}">加载</button>
        <button type="button" data-action="delete" data-scan-id="${escapeHtml(entry.scan_id)}">删除</button>
      </div>
    `;
    row.querySelector('[data-action="select"]').addEventListener("change", event => {
      const enabled = Boolean(event.target.checked);
      if (enabled) {
        state.selectedCacheIds.add(entry.scan_id);
      } else {
        state.selectedCacheIds.delete(entry.scan_id);
      }
      syncSelectionState();
    });
    row.querySelector('[data-action="load"]').addEventListener("click", async () => {
      await loadCachedScan(entry.scan_id);
    });
    row.querySelector('[data-action="delete"]').addEventListener("click", async () => {
      await deleteCachedScan(entry.scan_id);
    });
    el.cacheList.appendChild(row);
  }
  syncSelectionState();
}

function updateCacheSummary(stats) {
  if (!stats) {
    el.cacheSummary.textContent = "缓存：目录 0，元数据 0，缩略图 0，预览 0";
    return;
  }
  el.cacheSummary.textContent =
    `缓存：目录 ${stats.scan_entries || 0}，元数据 ${stats.meta_entries || 0}，` +
    `缩略图 ${stats.thumb_files || 0}，预览 ${stats.preview_files || 0}`;
}

async function refreshCacheList() {
  try {
    const resp = await fetch("/api/cache/scans");
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.error || "读取缓存列表失败");
    }
    state.cacheEntries = data.entries || [];
    const validIds = new Set(state.cacheEntries.map(entry => entry.scan_id));
    state.selectedCacheIds = new Set(
      Array.from(state.selectedCacheIds).filter(scanId => validIds.has(scanId))
    );
    state.loadedCacheIds = new Set(
      Array.from(state.loadedCacheIds).filter(scanId => validIds.has(scanId))
    );
    updateCacheSummary(data.stats || null);
    renderCacheList();
  } catch (err) {
    console.error(err);
    el.cacheSummary.textContent = "缓存信息读取失败";
    state.cacheEntries = [];
    state.selectedCacheIds = new Set();
    renderCacheList();
  }
}

async function loadCachedScan(scanId) {
  await loadCachedScans([scanId], false);
}

async function loadCachedScans(scanIds, isBatch) {
  el.scanStatus.textContent = isBatch ? "正在批量加载缓存结果..." : "正在加载缓存结果...";
  try {
    const resp = await fetch("/api/cache/load", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        scanIds.length === 1 ? { scan_id: scanIds[0] } : { scan_ids: scanIds }
      ),
    });
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.error || "加载缓存失败");
    }
    applyScanPayload(data);
    const loadedIds = Array.isArray(data.loaded_scan_ids) && data.loaded_scan_ids.length
      ? data.loaded_scan_ids
      : scanIds;
    state.loadedCacheIds = new Set(loadedIds);
    const loadedCount = loadedIds.length;
    const missingCount = Array.isArray(data.missing_scan_ids) ? data.missing_scan_ids.length : 0;
    el.scanStatus.textContent = `已加载缓存目录：${loadedCount}${missingCount ? `（缺失 ${missingCount}）` : ""}`;
  } catch (err) {
    console.error(err);
    el.scanStatus.textContent = "加载缓存失败";
    alert(err.message || "加载缓存失败");
  }
}

function syncSelectionState() {
  const total = state.cacheEntries.length;
  const selected = state.selectedCacheIds.size;
  const allSelected = total > 0 && selected === total;
  el.toggleSelectCacheBtn.textContent = allSelected ? "取消全选" : "全选";
  el.loadSelectedCacheBtn.disabled = selected === 0;
}

function toggleSelectAllCaches() {
  const total = state.cacheEntries.length;
  if (!total) return;
  const allSelected = state.selectedCacheIds.size === total;
  if (allSelected) {
    state.selectedCacheIds = new Set();
  } else {
    state.selectedCacheIds = new Set(state.cacheEntries.map(entry => entry.scan_id));
  }
  renderCacheList();
}

async function loadSelectedCaches() {
  const scanIds = Array.from(state.selectedCacheIds);
  if (!scanIds.length) {
    alert("请先勾选至少一个缓存目录");
    return;
  }
  await loadCachedScans(scanIds, true);
}

async function deleteCachedScan(scanId) {
  if (!confirm("确认删除该目录扫描缓存？")) return;
  try {
    const resp = await fetch("/api/cache/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scan_id: scanId }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.error || "删除缓存失败");
    }
    updateCacheSummary(data.stats || null);
    if (state.loadedCacheIds.has(scanId)) {
      state.loadedCacheIds.delete(scanId);
      const remaining = Array.from(state.loadedCacheIds);
      if (remaining.length) {
        await loadCachedScans(remaining, true);
      } else {
        state.allLocated = [];
        state.allUnlocated = [];
        state.filtered = [];
        renderMapMarkers();
        renderClusterList([]);
        renderUnlocated();
        updateSummary();
      }
    }
    await refreshCacheList();
  } catch (err) {
    console.error(err);
    alert(err.message || "删除缓存失败");
  }
}

async function clearAllCaches() {
  if (!confirm("确认清空全部缓存？这会删除目录扫描记录、元数据缓存和缩略图缓存。")) return;
  try {
    const resp = await fetch("/api/cache/clear", { method: "POST" });
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.error || "清空缓存失败");
    }
    state.allLocated = [];
    state.allUnlocated = [];
    state.filtered = [];
    state.selectedCacheIds = new Set();
    state.loadedCacheIds = new Set();
    renderMapMarkers();
    renderClusterList([]);
    renderUnlocated();
    updateSummary();
    state.cacheEntries = [];
    renderCacheList();
    syncSelectionState();
    updateCacheSummary(data.stats || null);
    el.scanStatus.textContent = "缓存已清空";
  } catch (err) {
    console.error(err);
    alert(err.message || "清空缓存失败");
  }
}

function openPreview(item) {
  el.previewMedia.innerHTML = "";
  if (item.type === "video") {
    const video = document.createElement("video");
    video.controls = true;
    video.autoplay = true;
    video.src = `/api/file?id=${encodeURIComponent(item.id)}`;
    video.poster = thumbnailUrl(item.id);
    video.addEventListener("error", () => {
      const fallback = document.createElement("img");
      fallback.src = thumbnailUrl(item.id);
      fallback.alt = "video preview unavailable";
      el.previewMedia.innerHTML = "";
      el.previewMedia.appendChild(fallback);
      const msg = document.createElement("div");
      msg.className = "summary";
      msg.textContent = "当前浏览器无法播放该视频编码，已显示缩略图。";
      el.previewMedia.appendChild(msg);
    });
    el.previewMedia.appendChild(video);
  } else {
    const img = document.createElement("img");
    img.src = previewUrl(item.id);
    img.alt = "preview";
    img.addEventListener("error", () => {
      img.src = thumbnailUrl(item.id);
    });
    el.previewMedia.appendChild(img);
  }

  el.previewMeta.innerHTML = `
    <div><strong>文件：</strong>${escapeHtml(item.path)}</div>
    <div><strong>类型：</strong>${escapeHtml(item.type)}</div>
    <div><strong>拍摄时间：</strong>${escapeHtml(formatDate(item.captured_at))}</div>
    <div><strong>坐标：</strong>${item.lat != null && item.lon != null ? `${item.lat.toFixed(6)}, ${item.lon.toFixed(6)}` : "未定位"}</div>
  `;

  el.previewModal.classList.remove("hidden");
}

function closePreview() {
  el.previewModal.classList.add("hidden");
  el.previewMedia.innerHTML = "";
}

function compactPath(path) {
  if (!path) return "";
  if (path.length <= 48) return path;
  return `${path.slice(0, 20)}...${path.slice(-24)}`;
}

async function pickDirectory() {
  el.pickDirBtn.disabled = true;
  el.scanStatus.textContent = "正在打开目录选择器...";
  try {
    const resp = await fetch("/api/pick-directory", {
      method: "POST",
    });
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.error || "无法打开目录选择器");
    }
    if (!data.path) {
      el.scanStatus.textContent = "未选择目录";
      return;
    }
    el.pathInput.value = data.path;
    el.scanStatus.textContent = `已选择目录：${compactPath(data.path)}`;
  } catch (err) {
    console.error(err);
    el.scanStatus.textContent = "目录选择失败";
    alert(err.message || "目录选择失败");
  } finally {
    el.pickDirBtn.disabled = false;
  }
}

async function fetchStatus(jobId) {
  const resp = await fetch(`/api/scan/status?job_id=${encodeURIComponent(jobId)}`);
  if (!resp.ok) {
    throw new Error("读取任务状态失败");
  }
  return resp.json();
}

async function fetchResult(jobId) {
  const resp = await fetch(`/api/scan/result?job_id=${encodeURIComponent(jobId)}`);
  if (!resp.ok) {
    throw new Error("读取扫描结果失败");
  }
  return resp.json();
}

async function pollScan(jobId) {
  while (true) {
    const payload = await fetchStatus(jobId);
    const status = payload.status;
    const total = Math.max(status.total || 0, 1);
    const progress = Math.min(100, Math.round((status.processed / total) * 100));

    el.scanStatus.textContent = `${status.status} ${status.processed}/${status.total}`;
    el.progressBarFill.style.width = `${progress}%`;

    if (status.status === "failed") {
      throw new Error(status.error || "扫描失败");
    }
    if (status.status === "completed") {
      return;
    }

    await new Promise(resolve => setTimeout(resolve, 900));
  }
}

async function startScan() {
  const path = el.pathInput.value.trim();
  if (!path) {
    alert("请输入目录路径");
    return;
  }

  el.scanBtn.disabled = true;
  el.scanStatus.textContent = "正在提交任务...";
  el.progressBarFill.style.width = "0%";

  try {
    const resp = await fetch("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });

    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.error || "无法启动扫描");
    }

    state.activeJobId = data.job_id;
    await pollScan(data.job_id);
    await refreshCacheList();

    if (data.scan_id) {
      const merged = Array.from(new Set([...state.loadedCacheIds, data.scan_id]));
      await loadCachedScans(merged, true);
    } else {
      const result = await fetchResult(data.job_id);
      applyScanPayload(result);
    }

    el.scanStatus.textContent = `完成：已定位 ${state.allLocated.length}`;
  } catch (err) {
    console.error(err);
    el.scanStatus.textContent = "扫描失败";
    alert(err.message || "扫描失败");
  } finally {
    el.scanBtn.disabled = false;
  }
}

function bindEvents() {
  el.scanBtn.addEventListener("click", startScan);
  el.pickDirBtn.addEventListener("click", pickDirectory);
  el.refreshCacheBtn.addEventListener("click", refreshCacheList);
  el.loadSelectedCacheBtn.addEventListener("click", loadSelectedCaches);
  el.toggleSelectCacheBtn.addEventListener("click", toggleSelectAllCaches);
  el.clearCacheBtn.addEventListener("click", clearAllCaches);
  el.keywordInput.addEventListener("input", applyFilters);
  el.typeSelect.addEventListener("change", applyFilters);
  el.fromDate.addEventListener("change", applyFilters);
  el.toDate.addEventListener("change", applyFilters);
  el.closeModal.addEventListener("click", closePreview);
  el.previewModal.querySelector(".modal-backdrop").addEventListener("click", closePreview);
}

function init() {
  initMap();
  bindEvents();
  renderClusterList([]);
  renderUnlocated();
  renderCacheList();
  syncSelectionState();
  refreshCacheList();
}

init();
