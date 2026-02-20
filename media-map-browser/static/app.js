const state = {
  map: null,
  baseLayers: {},
  mapLayerControl: null,
  markersLayer: null,
  regionLayer: null,
  regionHighlightLayer: null,
  regionMode: false,
  regionScope: "global",
  worldBoundaries: null,
  chinaProvinceBoundaries: null,
  chinaPrefectureBoundaries: null,
  worldBoundaryFeatures: [],
  chinaProvinceBoundaryFeatures: [],
  chinaPrefectureBoundaryFeatures: [],
  worldAliasIndex: new Map(),
  chinaProvinceAliasIndex: new Map(),
  chinaPrefectureAliasIndex: new Map(),
  chinaPrefectureProvinceLabelFeatureIds: new Set(),
  chinaPrefectureProvinceLabelAnchors: new Map(),
  chinaPrefectureFeatureProvinceCodes: new Map(),
  activePrefectureProvinceCodes: new Set(),
  regionMatchFilterActive: false,
  activeRegionFeatureIds: new Set(),
  allLocated: [],
  allUnlocated: [],
  activeJobId: null,
  cacheEntries: [],
  selectedCacheIds: new Set(),
  loadedCacheIds: new Set(),
  activeSheet: "media",
};

const el = {
  pathInput: document.getElementById("pathInput"),
  pickDirBtn: document.getElementById("pickDirBtn"),
  scanBtn: document.getElementById("scanBtn"),
  scanStatus: document.getElementById("scanStatus"),
  mediaSheet: document.getElementById("mediaSheet"),
  regionSheet: document.getElementById("regionSheet"),
  mediaSheetTab: document.getElementById("mediaSheetTab"),
  regionSheetTab: document.getElementById("regionSheetTab"),
  globalScopeTab: document.getElementById("globalScopeTab"),
  chinaProvinceScopeTab: document.getElementById("chinaProvinceScopeTab"),
  chinaPrefectureScopeTab: document.getElementById("chinaPrefectureScopeTab"),
  summaryText: document.getElementById("summaryText"),
  progressBarFill: document.querySelector("#progressBar span"),
  placeFileInput: document.getElementById("placeFileInput"),
  placeTextInput: document.getElementById("placeTextInput"),
  highlightPlacesBtn: document.getElementById("highlightPlacesBtn"),
  clearHighlightsBtn: document.getElementById("clearHighlightsBtn"),
  exportRegionSvgBtn: document.getElementById("exportRegionSvgBtn"),
  exportRegionPngBtn: document.getElementById("exportRegionPngBtn"),
  placeResultSummary: document.getElementById("placeResultSummary"),
  chinaScopeHint: document.getElementById("chinaScopeHint"),
  placeUnmatchedList: document.getElementById("placeUnmatchedList"),
  refreshCacheBtn: document.getElementById("refreshCacheBtn"),
  loadSelectedCacheBtn: document.getElementById("loadSelectedCacheBtn"),
  toggleSelectCacheBtn: document.getElementById("toggleSelectCacheBtn"),
  clearCacheBtn: document.getElementById("clearCacheBtn"),
  cacheSummary: document.getElementById("cacheSummary"),
  cacheList: document.getElementById("cacheList"),
  clusterList: document.getElementById("clusterList"),
  unlocatedList: document.getElementById("unlocatedList"),
  mapZoomInput: document.getElementById("mapZoomInput"),
  applyMapZoomBtn: document.getElementById("applyMapZoomBtn"),
  previewModal: document.getElementById("previewModal"),
  closeModal: document.getElementById("closeModal"),
  previewMedia: document.getElementById("previewMedia"),
  previewMeta: document.getElementById("previewMeta"),
};

function initMap() {
  state.map = L.map("map", {
    worldCopyJump: true,
    zoomControl: true,
    zoomSnap: 0.1,
    zoomDelta: 0.5,
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

  state.baseLayers = {
    标准底图: street,
    地形底图: topo,
    简洁底图: carto,
  };
  street.addTo(state.map);
  state.mapLayerControl = L.control.layers(state.baseLayers).addTo(state.map);

  state.markersLayer = L.layerGroup().addTo(state.map);
  state.regionLayer = L.geoJSON(null, {
    style: feature => getFeatureRegionStyle(feature, state.regionScope),
    onEachFeature: (feature, layer) => {
      const label = getFeatureMapLabel(feature, state.regionScope);
      if (label) {
        const offset = getLabelOffsetPx(feature, state.regionScope, 12);
        const tooltip = L.tooltip({
          direction: "center",
          permanent: true,
          interactive: false,
          className: "region-map-label",
          offset: L.point(offset.x, offset.y),
          opacity: 1,
        });
        const anchor = getFeatureLabelAnchorForScope(feature, state.regionScope);
        tooltip.setContent(String(label));
        layer.bindTooltip(tooltip);
        if (anchor) {
          const anchorLatLng = L.latLng(anchor[1], anchor[0]);
          const pinTooltipAnchor = () => {
            const bound = layer.getTooltip();
            if (bound) {
              bound.setLatLng(anchorLatLng);
            }
          };
          layer.on("add", pinTooltipAnchor);
          layer.on("tooltipopen", pinTooltipAnchor);
          pinTooltipAnchor();
        }
      }
    },
  });
  state.regionHighlightLayer = L.geoJSON(null, {
    interactive: false,
    style: feature => getFeatureHighlightStyle(feature, state.regionScope),
    onEachFeature: (feature, layer) => {
      layer.on("add", () => {
        applyHatchPatternToLayerPath(layer);
      });
    },
  });

  state.map.on("zoomend", () => {
    syncZoomInputFromMap();
    if (!state.regionMode) {
      renderMapMarkers();
      return;
    }
    if (state.regionHighlightLayer) {
      state.regionHighlightLayer.eachLayer(layer => {
        applyHatchPatternToLayerPath(layer);
      });
    }
  });
  syncZoomInputFromMap();
}

function getMapZoomBounds() {
  const map = state.map;
  if (!map) return { min: 1, max: 19 };
  const minRaw = map.getMinZoom();
  const maxRaw = map.getMaxZoom();
  const min = Number.isFinite(minRaw) ? Math.max(0, Math.floor(minRaw)) : 1;
  const max = Number.isFinite(maxRaw) && maxRaw < 60 ? Math.ceil(maxRaw) : 19;
  return { min, max: Math.max(min, max) };
}

function syncZoomInputFromMap() {
  if (!state.map || !el.mapZoomInput) return;
  const zoom = state.map.getZoom();
  const { min, max } = getMapZoomBounds();
  el.mapZoomInput.min = String(min);
  el.mapZoomInput.max = String(max);
  el.mapZoomInput.step = "0.1";
  el.mapZoomInput.value = String(Number(zoom.toFixed(1)));
}

function applyMapZoomFromInput() {
  if (!state.map || !el.mapZoomInput) return;
  const raw = Number.parseFloat(el.mapZoomInput.value);
  if (!Number.isFinite(raw)) {
    syncZoomInputFromMap();
    return;
  }
  const { min, max } = getMapZoomBounds();
  const clamped = Math.max(min, Math.min(max, raw));
  const target = Math.round(clamped * 10) / 10;
  state.map.setZoom(target);
  el.mapZoomInput.value = String(Number(target.toFixed(1)));
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

const PLACE_ALIAS_OVERRIDES = {
  北京: ["北京", "北京市", "beijing"],
  成都市: ["成都", "成都市", "四川", "四川省", "chengdu", "sichuan"],
  成都: ["成都", "成都市", "四川", "四川省", "chengdu", "sichuan"],
  俄罗斯: ["russia", "russianfederation", "russiafederation", "俄罗斯"],
  中国: ["china", "people'srepublicofchina", "中华人民共和国", "中国"],
  美国: ["usa", "unitedstates", "unitedstatesofamerica", "美国"],
  英国: ["unitedkingdom", "greatbritain", "england", "英国"],
  日本: ["japan", "日本"],
  韩国: ["southkorea", "korea", "韩国"],
  马来西亚: ["malaysia", "马来西亚"],
  尼日利亚: ["nigeria", "尼日利亚"],
  肯尼亚: ["kenya", "肯尼亚"],
  卡塔尔: ["qatar", "卡塔尔"],
  阿坝州: ["阿坝州", "阿坝藏族羌族自治州", "阿坝"],
  甘孜州: ["甘孜州", "甘孜藏族自治州", "甘孜"],
  博尔塔拉: ["博尔塔拉", "博尔塔拉蒙古自治州", "博州", "博乐"],
  巴音郭楞: ["巴音郭楞", "巴音郭楞州", "巴音郭楞蒙古自治州", "巴州", "库尔勒"],
  伊犁: ["伊犁", "伊犁州", "伊犁哈萨克自治州", "伊犁哈萨克"],
  海北州: ["海北州", "海北", "海北藏族自治州"],
  海西州: ["海西州", "海西", "海西蒙古族藏族自治州"],
  恩施: ["恩施", "恩施州", "恩施土家族苗族自治州"],
  大理: ["大理", "大理州", "大理白族自治州"],
  红河: ["红河", "红河州", "红河哈尼族彝族自治州"],
  香港: ["香港", "香港特别行政区", "hongkong", "hongkongsar", "hk", "hksar"],
  澳门: ["澳门", "澳门特别行政区", "macao", "macau", "macaosar", "mo", "msar"],
  德国: ["germany", "deutschland", "德国"],
  法国: ["france", "法国"],
  西班牙: ["spain", "españa", "西班牙"],
  意大利: ["italy", "italia", "意大利"],
  澳大利亚: ["australia", "澳大利亚"],
  加拿大: ["canada", "加拿大"],
  巴西: ["brazil", "brasil", "巴西"],
  印度: ["india", "印度"],
  南非: ["southafrica", "南非"],
  墨西哥: ["mexico", "méxico", "墨西哥"],
  新加坡: ["singapore", "新加坡"],
};

const CHINA_PROVINCE_ALIAS_OVERRIDES = {
  "110000": ["北京", "北京市"],
  "120000": ["天津", "天津市"],
  "130000": ["河北", "河北省"],
  "140000": ["山西", "山西省"],
  "150000": ["内蒙古", "内蒙古自治区"],
  "210000": ["辽宁", "辽宁省"],
  "220000": ["吉林", "吉林省"],
  "230000": ["黑龙江", "黑龙江省"],
  "310000": ["上海", "上海市"],
  "320000": ["江苏", "江苏省"],
  "330000": ["浙江", "浙江省"],
  "340000": ["安徽", "安徽省"],
  "350000": ["福建", "福建省"],
  "360000": ["江西", "江西省"],
  "370000": ["山东", "山东省"],
  "410000": ["河南", "河南省"],
  "420000": ["湖北", "湖北省"],
  "430000": ["湖南", "湖南省"],
  "440000": ["广东", "广东省"],
  "450000": ["广西", "广西壮族自治区"],
  "460000": ["海南", "海南省"],
  "500000": ["重庆", "重庆市"],
  "510000": ["四川", "四川省"],
  "520000": ["贵州", "贵州省"],
  "530000": ["云南", "云南省"],
  "540000": ["西藏", "西藏自治区"],
  "610000": ["陕西", "陕西省"],
  "620000": ["甘肃", "甘肃省"],
  "630000": ["青海", "青海省"],
  "640000": ["宁夏", "宁夏回族自治区"],
  "650000": ["新疆", "新疆维吾尔自治区"],
  "710000": ["台湾", "台湾省"],
  "810000": ["香港", "香港特别行政区"],
  "820000": ["澳门", "澳门特别行政区"],
};

const CHINA_PROVINCE_LABEL_ANCHOR_OVERRIDES = {
  // Shared province label anchors for both "中国.省" and "中国.地级市".
  "150000": [111.0, 44.8], // 内蒙古
  "620000": [103.1, 37.5], // 甘肃
};

const CHINA_PROVINCE_LABEL_OFFSET_FACTORS = {
  // Beijing-Tianjin-Hebei
  "110000": { x: -1.25, y: -0.8 },
  "120000": { x: 1.2, y: -0.75 },
  "130000": { x: 0.0, y: 0.3 },
  // Guangxi-Guangdong-HK-Macau
  "450000": { x: -0.5, y: 0.35 },
  "440000": { x: -0.2, y: -0.08 },
  "810000": { x: 1.45, y: -0.85 },
  "820000": { x: 1.55, y: 1.0 },
};

const CHINA_PROVINCE_ORDERED_CODES = Object.keys(CHINA_PROVINCE_ALIAS_OVERRIDES).sort();
const CHINA_PROVINCE_CODE_INDEX = new Map(
  CHINA_PROVINCE_ORDERED_CODES.map((code, index) => [code, index])
);

const CHINA_AUTONOMOUS_ETHNIC_KEYWORDS = [
  "土家族苗族",
  "蒙古族藏族",
  "哈尼族彝族",
  "傣族景颇族",
  "柯尔克孜",
  "哈萨克",
  "蒙古族",
  "朝鲜族",
  "藏族",
  "白族",
  "彝族",
  "苗族",
  "回族",
  "壮族",
  "傣族",
  "黎族",
  "羌族",
  "土家族",
  "哈尼族",
  "满族",
  "侗族",
  "布依族",
];

function normalizePlaceName(value) {
  return String(value || "")
    .toLowerCase()
    .trim()
    .replace(/[\s\-_.;,:'"`~!?@#$%^&*()+={}\[\]<>\\/|，。；：、（）【】《》]/g, "");
}

function parsePlaceNames(rawText) {
  if (!rawText) return [];
  const tokens = rawText
    .split(/[\n\r,，;；、]+/g)
    .map(x => x.trim())
    .filter(Boolean);
  const unique = [];
  const seen = new Set();
  for (const token of tokens) {
    const key = normalizePlaceName(token);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    unique.push(token);
  }
  return unique;
}

function expandPlaceTokens(rawName) {
  const normalized = normalizePlaceName(rawName);
  const expanded = new Set([normalized]);
  const override = PLACE_ALIAS_OVERRIDES[rawName] || PLACE_ALIAS_OVERRIDES[normalized];
  if (Array.isArray(override)) {
    for (const candidate of override) {
      const c = normalizePlaceName(candidate);
      if (c) expanded.add(c);
    }
  }
  return Array.from(expanded);
}

function addAlias(aliasMap, alias, featureId) {
  const key = normalizePlaceName(alias);
  if (!key) return;
  const variants = new Set([key]);
  const suffixes = [
    "特别行政区",
    "维吾尔自治区",
    "回族自治区",
    "壮族自治区",
    "自治区",
    "省",
    "市",
    "地区",
    "盟",
    "州",
  ];
  for (const suffix of suffixes) {
    const normSuffix = normalizePlaceName(suffix);
    if (normSuffix && key.endsWith(normSuffix) && key.length > normSuffix.length) {
      variants.add(key.slice(0, key.length - normSuffix.length));
    }
  }
  const autonomous = normalizePlaceName("自治州");
  if (key.endsWith(autonomous) && key.length > autonomous.length + 1) {
    const base = key.slice(0, key.length - autonomous.length);
    let bestIndex = Number.POSITIVE_INFINITY;
    for (const token of CHINA_AUTONOMOUS_ETHNIC_KEYWORDS) {
      const t = normalizePlaceName(token);
      if (!t) continue;
      const idx = base.indexOf(t);
      if (idx >= 2 && idx < bestIndex) {
        bestIndex = idx;
      }
    }
    if (Number.isFinite(bestIndex)) {
      const short = base.slice(0, bestIndex);
      if (short) {
        variants.add(short);
      }
    }
  }
  if (key.endsWith("federation") && key.length > "federation".length + 3) {
    variants.add(key.replace(/federation$/, ""));
  }
  if (key.endsWith("province") && key.length > "province".length + 3) {
    variants.add(key.replace(/province$/, ""));
  }
  if (key.endsWith("city") && key.length > "city".length + 2) {
    variants.add(key.replace(/city$/, ""));
  }
  for (const variant of variants) {
    if (!variant) continue;
    if (!aliasMap.has(variant)) {
      aliasMap.set(variant, new Set());
    }
    aliasMap.get(variant).add(featureId);
  }
}

function featureDisplayName(feature) {
  const props = feature?.properties || {};
  return (
    props.name ||
    props.NAME ||
    props.ADMIN ||
    props.NAME_ZH ||
    props.fullname ||
    props._display_name ||
    ""
  );
}

function countryAliasesFromIso2(iso2) {
  const code = String(iso2 || "").trim().toUpperCase();
  if (!/^[A-Z]{2}$/.test(code)) return [];
  if (typeof Intl === "undefined" || typeof Intl.DisplayNames !== "function") {
    return [code];
  }

  const out = new Set([code, code.toLowerCase()]);
  const locales = ["zh-CN", "zh-Hans", "zh", "en"];
  for (const locale of locales) {
    try {
      const formatter = new Intl.DisplayNames([locale], { type: "region" });
      const text = formatter.of(code);
      if (text && text !== code) {
        out.add(String(text));
      }
    } catch (_) {
      // Ignore locale-specific failures and continue.
    }
  }
  return Array.from(out);
}

function featureAliasCandidates(feature) {
  const props = feature?.properties || {};
  const keys = [
    "name",
    "NAME",
    "NAME_LONG",
    "ADMIN",
    "FORMAL_EN",
    "NAME_ZH",
    "fullname",
    "NAME_CHINESE",
    "iso_a2",
    "ISO_A2",
    "iso_a3",
    "ISO_A3",
    "adcode",
  ];
  const candidates = [];
  for (const key of keys) {
    const value = props[key];
    if (typeof value === "string" || typeof value === "number") {
      candidates.push(String(value));
    }
  }

  const iso2 = props.iso_a2 || props.ISO_A2 || props.iso2 || props.ISO2 || "";
  const iso2Aliases = countryAliasesFromIso2(iso2);
  for (const alias of iso2Aliases) {
    candidates.push(alias);
  }

  const source = String(props._source || "");
  const adcodeRaw = props.adcode;
  const adcode = String(adcodeRaw == null ? "" : adcodeRaw).trim().padStart(6, "0");
  if (source.startsWith("china_") && CHINA_PROVINCE_ALIAS_OVERRIDES[adcode]) {
    for (const alias of CHINA_PROVINCE_ALIAS_OVERRIDES[adcode]) {
      candidates.push(alias);
    }
  }
  return candidates;
}

async function fetchBoundaryGeoJson(path) {
  const resp = await fetch(path);
  const text = await resp.text();
  if (!resp.ok) {
    throw new Error(text || `请求失败: ${path}`);
  }
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new Error(`边界数据解析失败: ${path}`);
  }
}

function collectBoundaryFeatures(rawFeatures, sourceKey) {
  const merged = [];
  let sequence = 0;
  const rows = Array.isArray(rawFeatures) ? rawFeatures : [];
  for (const feature of rows) {
    if (!feature || feature.type !== "Feature" || !feature.geometry) continue;
    const cloned = {
      type: "Feature",
      geometry: feature.geometry,
      properties: { ...(feature.properties || {}) },
    };
    const displayName = featureDisplayName(cloned);
    const featureId = `${sourceKey}:${sequence}`;
    sequence += 1;
    cloned.properties._feature_id = featureId;
    cloned.properties._source = sourceKey;
    cloned.properties._display_name = displayName || featureId;
    merged.push(cloned);
  }
  return merged;
}

function rebuildBoundaryIndex(features) {
  const aliasMap = new Map();
  for (const feature of features) {
    const featureId = feature?.properties?._feature_id;
    if (!featureId) continue;
    const aliases = featureAliasCandidates(feature);
    for (const alias of aliases) {
      addAlias(aliasMap, alias, featureId);
    }
    addAlias(aliasMap, feature?.properties?._display_name || "", featureId);
  }
  return aliasMap;
}

function regionScopeLabel(scope) {
  if (scope === "china_province") return "中国.省";
  if (scope === "china_prefecture") return "中国.地级市";
  return "全球国家";
}

function regionScopeFileLabel(scope) {
  if (scope === "china_province") return "china-provinces";
  if (scope === "china_prefecture") return "china-prefecture";
  return "global-countries";
}

function isSelectiveRegionScope(scope) {
  return scope === "global" || scope === "china_province";
}

function getProvinceCodeFromAdcode(adcode) {
  if (!adcode || adcode.length !== 6) return "";
  return `${adcode.slice(0, 2)}0000`;
}

function getProvinceChineseNameByCode(adcode) {
  const aliases = CHINA_PROVINCE_ALIAS_OVERRIDES[adcode];
  if (Array.isArray(aliases) && aliases.length) {
    return aliases[0];
  }
  return "";
}

function buildPrefectureProvinceLabelFeatureIds(features) {
  const byProvince = new Map();
  for (const feature of features) {
    const featureId = feature?.properties?._feature_id || "";
    if (!featureId) continue;
    const adcode = getFeatureAdcode(feature);
    const provinceCode = getProvinceCodeFromAdcode(adcode);
    if (!provinceCode) continue;
    const { area } = featurePrimaryRing(feature);
    const score = Number.isFinite(area) ? area : 0;
    const prev = byProvince.get(provinceCode);
    if (!prev || score > prev.score) {
      byProvince.set(provinceCode, { featureId, score });
    }
  }
  return new Set(Array.from(byProvince.values()).map(item => item.featureId));
}

function buildPrefectureFeatureProvinceCodeMap(features) {
  const out = new Map();
  for (const feature of features) {
    const featureId = feature?.properties?._feature_id || "";
    if (!featureId) continue;
    const adcode = getFeatureAdcode(feature);
    const provinceCode = getProvinceCodeFromAdcode(adcode) || adcode;
    if (!provinceCode) continue;
    out.set(featureId, provinceCode);
  }
  return out;
}

function buildProvinceCenterAnchorsFromProvinceFeatures(features) {
  const anchors = new Map();
  for (const feature of features) {
    const adcode = getFeatureAdcode(feature);
    const provinceCode = getProvinceCodeFromAdcode(adcode) || adcode;
    if (!provinceCode) continue;
    const anchor = getProvinceLabelAnchorFromFeature(feature);
    if (!anchor) continue;
    anchors.set(provinceCode, anchor);
  }
  return anchors;
}

function createSyntheticPrefectureFromProvinceFeature(feature, seq) {
  const props = feature?.properties || {};
  const adcode = getFeatureAdcode(feature);
  if (!adcode || !feature?.geometry) return null;
  return {
    type: "Feature",
    geometry: feature.geometry,
    properties: {
      ...props,
      _source: "china_prefecture_synthetic",
      _display_name: props.name || props.fullname || adcode,
      _synthetic_prefecture: true,
      _synthetic_seq: seq,
      level: "city",
      adcode,
    },
  };
}

function getRenderableBoundaryFeatures() {
  return getCurrentBoundaryFeatures();
}

function refreshRegionLayerData() {
  refreshPrefectureProvinceMatchCache();
  state.regionLayer.clearLayers();
  state.regionLayer.addData(getRenderableBoundaryFeatures());
  applyRegionStyles();
  refreshRegionHighlightLayer();
}

function refreshPrefectureProvinceMatchCache() {
  if (state.regionScope !== "china_prefecture") {
    state.activePrefectureProvinceCodes = new Set();
    return;
  }
  const codes = new Set();
  for (const featureId of state.activeRegionFeatureIds) {
    const provinceCode = state.chinaPrefectureFeatureProvinceCodes.get(featureId);
    if (provinceCode) {
      codes.add(provinceCode);
    }
  }
  state.activePrefectureProvinceCodes = codes;
}

function getCurrentBoundaryFeatures() {
  if (state.regionScope === "china_province") {
    return state.chinaProvinceBoundaryFeatures;
  }
  if (state.regionScope === "china_prefecture") {
    return state.chinaPrefectureBoundaryFeatures;
  }
  return state.worldBoundaryFeatures;
}

function getCurrentBoundaryAliasIndex() {
  if (state.regionScope === "china_province") {
    return state.chinaProvinceAliasIndex;
  }
  if (state.regionScope === "china_prefecture") {
    return state.chinaPrefectureAliasIndex;
  }
  return state.worldAliasIndex;
}

async function ensureBoundaryDataLoaded(scope) {
  if (scope === "global") {
    if (!state.worldBoundaries) {
      state.worldBoundaries = await fetchBoundaryGeoJson("/api/boundaries/world");
    }
    if (!state.worldBoundaryFeatures.length) {
      const features = collectBoundaryFeatures(state.worldBoundaries?.features, "world");
      state.worldBoundaryFeatures = features;
      state.worldAliasIndex = rebuildBoundaryIndex(features);
    }
    return;
  }

  if (scope === "china_province") {
    if (!state.chinaProvinceBoundaries) {
      state.chinaProvinceBoundaries = await fetchBoundaryGeoJson("/api/boundaries/china-provinces");
    }
    if (!state.chinaProvinceBoundaryFeatures.length) {
      const features = collectBoundaryFeatures(state.chinaProvinceBoundaries?.features, "china_province");
      state.chinaProvinceBoundaryFeatures = features;
      state.chinaProvinceAliasIndex = rebuildBoundaryIndex(features);
    }
    return;
  }

  if (scope === "china_prefecture") {
    if (!state.chinaPrefectureBoundaries) {
      state.chinaPrefectureBoundaries = await fetchBoundaryGeoJson("/api/boundaries/china-prefecture-cities");
    }
    await ensureBoundaryDataLoaded("china_province");
    if (!state.chinaPrefectureBoundaryFeatures.length) {
      const features = collectBoundaryFeatures(state.chinaPrefectureBoundaries?.features, "china_prefecture");
      const coveredProvinceCodes = new Set(
        features
          .map(getFeatureAdcode)
          .map(code => getProvinceCodeFromAdcode(code) || code)
          .filter(Boolean)
      );
      const missingSpecial = ["710000", "810000", "820000"].filter(
        code => !coveredProvinceCodes.has(code)
      );
      if (missingSpecial.length) {
        let seq = 0;
        for (const code of missingSpecial) {
          const provinceFeature = state.chinaProvinceBoundaryFeatures.find(
            feature => getFeatureAdcode(feature) === code
          );
          if (!provinceFeature) continue;
          seq += 1;
          const synthetic = createSyntheticPrefectureFromProvinceFeature(provinceFeature, seq);
          if (synthetic) {
            synthetic.properties._feature_id = `china_prefecture:synthetic-${code}`;
            features.push(synthetic);
          }
        }
      }
      state.chinaPrefectureBoundaryFeatures = features;
      state.chinaPrefectureAliasIndex = rebuildBoundaryIndex(features);
      state.chinaPrefectureProvinceLabelFeatureIds = buildPrefectureProvinceLabelFeatureIds(features);
      state.chinaPrefectureFeatureProvinceCodes = buildPrefectureFeatureProvinceCodeMap(features);
    }
    state.chinaPrefectureProvinceLabelAnchors =
      buildProvinceCenterAnchorsFromProvinceFeatures(state.chinaProvinceBoundaryFeatures);
    return;
  }

  throw new Error(`未知区域范围: ${scope}`);
}

function applyRegionStyles() {
  state.regionLayer.setStyle(feature => {
    return getFeatureRegionStyle(feature, state.regionScope);
  });
}

function renderUnmatchedPlaces(unmatched) {
  el.placeUnmatchedList.innerHTML = "";
  if (!unmatched.length) {
    el.placeUnmatchedList.innerHTML = '<div class="summary">全部地名已匹配到区域</div>';
    return;
  }
  for (const name of unmatched) {
    const row = document.createElement("div");
    row.className = "summary";
    row.textContent = `未匹配：${name}`;
    el.placeUnmatchedList.appendChild(row);
  }
}

function fitMapToHighlightedRegions() {
  const layers = [];
  state.regionLayer.eachLayer(layer => {
    const featureId = layer?.feature?.properties?._feature_id;
    if (featureId && state.activeRegionFeatureIds.has(featureId)) {
      layers.push(layer);
    }
  });
  if (!layers.length) {
    if (state.regionScope === "china_province" || state.regionScope === "china_prefecture") {
      state.map.setView([35, 104], 4);
    } else {
      state.map.setView([20, 0], 2);
    }
    return;
  }
  const bounds = L.featureGroup(layers).getBounds();
  if (bounds.isValid()) {
    state.map.fitBounds(bounds.pad(0.15), { maxZoom: 6 });
  }
}

function renderSheetTabs() {
  const mediaActive = state.activeSheet === "media";
  el.mediaSheet.classList.toggle("active", mediaActive);
  el.regionSheet.classList.toggle("active", !mediaActive);
  el.mediaSheetTab.classList.toggle("active", mediaActive);
  el.regionSheetTab.classList.toggle("active", !mediaActive);
}

function renderRegionScopeTabs() {
  const globalActive = state.regionScope === "global";
  const provinceActive = state.regionScope === "china_province";
  const prefectureActive = state.regionScope === "china_prefecture";
  el.globalScopeTab.classList.toggle("active", globalActive);
  el.chinaProvinceScopeTab.classList.toggle("active", provinceActive);
  el.chinaPrefectureScopeTab.classList.toggle("active", prefectureActive);
  if (globalActive) {
    el.chinaScopeHint.style.display = "none";
    return;
  }
  el.chinaScopeHint.style.display = "block";
  el.chinaScopeHint.textContent = prefectureActive
    ? "中国.地级市模式包含台湾、香港、澳门。"
    : "中国.省模式按省级行政区边界着色。";
}

async function loadActiveRegionScopeIntoLayer(resetHighlights = false) {
  await ensureBoundaryDataLoaded(state.regionScope);
  if (resetHighlights) {
    state.activeRegionFeatureIds = new Set();
    state.regionMatchFilterActive = false;
  }
  refreshRegionLayerData();
  if (resetHighlights || state.activeRegionFeatureIds.size === 0) {
    renderUnmatchedPlaces([]);
    el.placeResultSummary.textContent = `当前为${regionScopeLabel(state.regionScope)}边界模式`;
  }
}

async function switchSheet(target) {
  if (target === "region") {
    if (state.regionMode) {
      state.activeSheet = "region";
      renderSheetTabs();
      return;
    }
    const ok = await enterRegionMode();
    if (!ok) {
      state.activeSheet = "media";
      renderSheetTabs();
      return;
    }
    state.activeSheet = "region";
    renderSheetTabs();
    return;
  }

  if (state.regionMode) {
    exitRegionMode();
  }
  state.activeSheet = "media";
  renderSheetTabs();
}

async function switchRegionScope(scope) {
  if (!["global", "china_province", "china_prefecture"].includes(scope)) return;
  if (state.regionScope === scope) return;
  state.regionScope = scope;
  renderRegionScopeTabs();
  if (!state.regionMode) {
    return;
  }
  try {
    await loadActiveRegionScopeIntoLayer(true);
    fitMapToHighlightedRegions();
  } catch (error) {
    alert(error.message || "切换区域范围失败");
  }
}

async function enterRegionMode() {
  try {
    await loadActiveRegionScopeIntoLayer(false);
  } catch (error) {
    alert(error.message || "加载区域边界失败");
    return false;
  }

  state.regionMode = true;
  state.markersLayer.clearLayers();
  Object.values(state.baseLayers).forEach(layer => {
    if (state.map.hasLayer(layer)) {
      state.map.removeLayer(layer);
    }
  });
  if (state.mapLayerControl) {
    state.map.removeControl(state.mapLayerControl);
  }
  if (!state.map.hasLayer(state.regionLayer)) {
    state.regionLayer.addTo(state.map);
  }
  if (state.regionHighlightLayer && !state.map.hasLayer(state.regionHighlightLayer)) {
    state.regionHighlightLayer.addTo(state.map);
  }
  fitMapToHighlightedRegions();
  el.scanStatus.textContent = "区域模式已启用";
  return true;
}

function exitRegionMode() {
  state.regionMode = false;
  if (state.regionHighlightLayer && state.map.hasLayer(state.regionHighlightLayer)) {
    state.map.removeLayer(state.regionHighlightLayer);
  }
  if (state.map.hasLayer(state.regionLayer)) {
    state.map.removeLayer(state.regionLayer);
  }
  if (state.mapLayerControl) {
    state.mapLayerControl.addTo(state.map);
  }
  const defaultLayer = state.baseLayers["标准底图"];
  if (defaultLayer && !state.map.hasLayer(defaultLayer)) {
    defaultLayer.addTo(state.map);
  }
  renderMapMarkers();
  el.scanStatus.textContent = "已返回媒体模式";
}

async function applyPlaceHighlight() {
  const names = parsePlaceNames(el.placeTextInput.value);
  if (!names.length) {
    alert("请先输入或上传地名列表");
    return;
  }

  const ok = await enterRegionMode();
  if (!ok) return;
  state.activeSheet = "region";
  renderSheetTabs();
  renderRegionScopeTabs();
  const matched = new Set();
  const unmatched = [];
  const aliasIndex = getCurrentBoundaryAliasIndex();

  for (const rawName of names) {
    const tokens = expandPlaceTokens(rawName);
    let found = false;
    for (const token of tokens) {
      const ids = aliasIndex.get(token);
      if (!ids || !ids.size) continue;
      for (const id of ids) {
        matched.add(id);
      }
      found = true;
    }
    if (!found) {
      unmatched.push(rawName);
    }
  }

  state.activeRegionFeatureIds = matched;
  state.regionMatchFilterActive = true;
  refreshRegionLayerData();
  fitMapToHighlightedRegions();
  renderUnmatchedPlaces(unmatched);
  const scopeText = regionScopeLabel(state.regionScope);
  el.placeResultSummary.textContent =
    `范围 ${scopeText}：输入 ${names.length} 个地名，匹配 ${names.length - unmatched.length}，未匹配 ${unmatched.length}`;
}

function clearPlaceHighlight() {
  state.activeRegionFeatureIds = new Set();
  state.regionMatchFilterActive = false;
  refreshRegionLayerData();
  renderUnmatchedPlaces([]);
  el.placeResultSummary.textContent = "已清除地名着色";
}

function getGeometryPolygons(geometry) {
  if (!geometry || !Array.isArray(geometry.coordinates)) return [];
  if (geometry.type === "Polygon") return [geometry.coordinates];
  if (geometry.type === "MultiPolygon") return geometry.coordinates;
  return [];
}

function forEachGeometryPoint(geometry, callback) {
  const polygons = getGeometryPolygons(geometry);
  for (const polygon of polygons) {
    if (!Array.isArray(polygon)) continue;
    for (const ring of polygon) {
      if (!Array.isArray(ring)) continue;
      for (const point of ring) {
        if (!Array.isArray(point) || point.length < 2) continue;
        const lon = Number(point[0]);
        const lat = Number(point[1]);
        if (!Number.isFinite(lon) || !Number.isFinite(lat)) continue;
        callback(lon, lat);
      }
    }
  }
}

function computeFeatureGeoBounds(features) {
  let minLon = Infinity;
  let maxLon = -Infinity;
  let minLat = Infinity;
  let maxLat = -Infinity;
  for (const feature of features) {
    forEachGeometryPoint(feature?.geometry, (lon, lat) => {
      if (lon < minLon) minLon = lon;
      if (lon > maxLon) maxLon = lon;
      if (lat < minLat) minLat = lat;
      if (lat > maxLat) maxLat = lat;
    });
  }
  if (!Number.isFinite(minLon) || !Number.isFinite(maxLon) || !Number.isFinite(minLat) || !Number.isFinite(maxLat)) {
    return null;
  }
  return { minLon, maxLon, minLat, maxLat };
}

function getExportGeoBounds(scope, features) {
  if (scope === "global") {
    return { minLon: -180, maxLon: 180, minLat: -60, maxLat: 85 };
  }
  if (scope === "china_province") {
    return { minLon: 73, maxLon: 136, minLat: 17, maxLat: 54 };
  }
  const computed = computeFeatureGeoBounds(features);
  if (!computed) {
    return { minLon: 73, maxLon: 136, minLat: 17, maxLat: 54 };
  }
  const lonPad = (computed.maxLon - computed.minLon) * 0.06 || 1;
  const latPad = (computed.maxLat - computed.minLat) * 0.08 || 1;
  return {
    minLon: computed.minLon - lonPad,
    maxLon: computed.maxLon + lonPad,
    minLat: computed.minLat - latPad,
    maxLat: computed.maxLat + latPad,
  };
}

function computeExportCanvasSize(bounds) {
  const xSpan = Math.max(
    Math.abs(mercatorXFromLon(bounds.maxLon) - mercatorXFromLon(bounds.minLon)),
    1e-9
  );
  const ySpan = Math.max(
    Math.abs(mercatorYFromLat(bounds.maxLat) - mercatorYFromLat(bounds.minLat)),
    1e-9
  );
  const aspect = xSpan / ySpan;
  const longSide = 4096;
  const shortSideTarget = 1800;
  if (aspect >= 1) {
    let width = longSide;
    let height = Math.max(1, Math.round(width / aspect));
    if (height < shortSideTarget) {
      const scale = shortSideTarget / height;
      width = Math.round(width * scale);
      height = Math.round(height * scale);
    }
    return { width, height };
  }
  let height = longSide;
  let width = Math.max(1, Math.round(height * aspect));
  if (width < shortSideTarget) {
    const scale = shortSideTarget / width;
    width = Math.round(width * scale);
    height = Math.round(height * scale);
  }
  return { width, height };
}

function clampMercatorLat(lat) {
  const v = Number(lat);
  if (!Number.isFinite(v)) return 0;
  const max = 85.05112878;
  if (v > max) return max;
  if (v < -max) return -max;
  return v;
}

function mercatorXFromLon(lon) {
  return (Number(lon) * Math.PI) / 180;
}

function mercatorYFromLat(lat) {
  const c = clampMercatorLat(lat);
  const rad = (c * Math.PI) / 180;
  return Math.log(Math.tan(Math.PI / 4 + rad / 2));
}

function projectLonLatToCanvas(lon, lat, bounds, width, height, padding) {
  const xMin = mercatorXFromLon(bounds.minLon);
  const xMax = mercatorXFromLon(bounds.maxLon);
  const xVal = mercatorXFromLon(lon);
  const xRatio = (xVal - xMin) / Math.max(xMax - xMin, 1e-9);

  const yTop = mercatorYFromLat(bounds.maxLat);
  const yBottom = mercatorYFromLat(bounds.minLat);
  const yVal = mercatorYFromLat(lat);
  const yRatio = (yTop - yVal) / Math.max(yTop - yBottom, 1e-9);
  return {
    x: padding + xRatio * (width - padding * 2),
    y: padding + yRatio * (height - padding * 2),
  };
}

function ringSignedArea(ring) {
  if (!Array.isArray(ring) || ring.length < 3) return 0;
  let sum = 0;
  for (let i = 0; i < ring.length; i += 1) {
    const cur = ring[i];
    const next = ring[(i + 1) % ring.length];
    const x0 = Number(cur?.[0]);
    const y0 = Number(cur?.[1]);
    const x1 = Number(next?.[0]);
    const y1 = Number(next?.[1]);
    if (!Number.isFinite(x0) || !Number.isFinite(y0) || !Number.isFinite(x1) || !Number.isFinite(y1)) {
      continue;
    }
    sum += x0 * y1 - x1 * y0;
  }
  return sum / 2;
}

function ringBoundsCenter(ring) {
  let minLon = Infinity;
  let maxLon = -Infinity;
  let minLat = Infinity;
  let maxLat = -Infinity;
  for (const point of ring || []) {
    const lon = Number(point?.[0]);
    const lat = Number(point?.[1]);
    if (!Number.isFinite(lon) || !Number.isFinite(lat)) continue;
    if (lon < minLon) minLon = lon;
    if (lon > maxLon) maxLon = lon;
    if (lat < minLat) minLat = lat;
    if (lat > maxLat) maxLat = lat;
  }
  if (!Number.isFinite(minLon) || !Number.isFinite(maxLon) || !Number.isFinite(minLat) || !Number.isFinite(maxLat)) {
    return null;
  }
  return [(minLon + maxLon) / 2, (minLat + maxLat) / 2];
}

function ringCentroid(ring) {
  const area = ringSignedArea(ring);
  if (Math.abs(area) < 1e-9) {
    return ringBoundsCenter(ring);
  }
  let cx = 0;
  let cy = 0;
  for (let i = 0; i < ring.length; i += 1) {
    const cur = ring[i];
    const next = ring[(i + 1) % ring.length];
    const x0 = Number(cur?.[0]);
    const y0 = Number(cur?.[1]);
    const x1 = Number(next?.[0]);
    const y1 = Number(next?.[1]);
    if (!Number.isFinite(x0) || !Number.isFinite(y0) || !Number.isFinite(x1) || !Number.isFinite(y1)) {
      continue;
    }
    const cross = x0 * y1 - x1 * y0;
    cx += (x0 + x1) * cross;
    cy += (y0 + y1) * cross;
  }
  const k = 1 / (6 * area);
  return [cx * k, cy * k];
}

function featurePrimaryRing(feature) {
  const polygons = getGeometryPolygons(feature?.geometry);
  let bestRing = null;
  let bestArea = -1;
  for (const polygon of polygons) {
    if (!Array.isArray(polygon) || !Array.isArray(polygon[0])) continue;
    const outer = polygon[0];
    const area = Math.abs(ringSignedArea(outer));
    if (area > bestArea) {
      bestArea = area;
      bestRing = outer;
    }
  }
  return { ring: bestRing, area: bestArea };
}

function isPointInRing(lon, lat, ring) {
  if (!Array.isArray(ring) || ring.length < 3) return false;
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
    const xi = Number(ring[i]?.[0]);
    const yi = Number(ring[i]?.[1]);
    const xj = Number(ring[j]?.[0]);
    const yj = Number(ring[j]?.[1]);
    if (!Number.isFinite(xi) || !Number.isFinite(yi) || !Number.isFinite(xj) || !Number.isFinite(yj)) {
      continue;
    }
    const dy = yj - yi;
    const safeDy = Math.abs(dy) < 1e-12 ? (dy >= 0 ? 1e-12 : -1e-12) : dy;
    const intersect =
      yi > lat !== yj > lat &&
      lon < ((xj - xi) * (lat - yi)) / safeDy + xi;
    if (intersect) {
      inside = !inside;
    }
  }
  return inside;
}

function isPointInPolygon(lon, lat, polygon) {
  if (!Array.isArray(polygon) || !Array.isArray(polygon[0])) return false;
  if (!isPointInRing(lon, lat, polygon[0])) return false;
  for (let i = 1; i < polygon.length; i += 1) {
    if (isPointInRing(lon, lat, polygon[i])) {
      return false;
    }
  }
  return true;
}

function isPointInGeometry(lon, lat, geometry) {
  const polygons = getGeometryPolygons(geometry);
  for (const polygon of polygons) {
    if (isPointInPolygon(lon, lat, polygon)) {
      return true;
    }
  }
  return false;
}

function getFeatureLabelLonLat(feature) {
  const { ring } = featurePrimaryRing(feature);
  if (!ring || !ring.length) return null;

  const centroid = ringCentroid(ring);
  if (centroid && isPointInGeometry(centroid[0], centroid[1], feature?.geometry)) {
    return centroid;
  }

  const center = ringBoundsCenter(ring);
  if (center && isPointInGeometry(center[0], center[1], feature?.geometry)) {
    return center;
  }

  for (const point of ring) {
    const lon = Number(point?.[0]);
    const lat = Number(point?.[1]);
    if (Number.isFinite(lon) && Number.isFinite(lat)) {
      return [lon, lat];
    }
  }
  return null;
}

function getFeatureBoundsCenterLonLat(feature) {
  const polygons = getGeometryPolygons(feature?.geometry);
  let minLon = Number.POSITIVE_INFINITY;
  let minLat = Number.POSITIVE_INFINITY;
  let maxLon = Number.NEGATIVE_INFINITY;
  let maxLat = Number.NEGATIVE_INFINITY;
  for (const polygon of polygons) {
    for (const ring of polygon) {
      for (const point of ring) {
        const lon = Number(point?.[0]);
        const lat = Number(point?.[1]);
        if (!Number.isFinite(lon) || !Number.isFinite(lat)) continue;
        minLon = Math.min(minLon, lon);
        minLat = Math.min(minLat, lat);
        maxLon = Math.max(maxLon, lon);
        maxLat = Math.max(maxLat, lat);
      }
    }
  }
  if (!Number.isFinite(minLon) || !Number.isFinite(minLat) || !Number.isFinite(maxLon) || !Number.isFinite(maxLat)) {
    return null;
  }
  const center = [(minLon + maxLon) / 2, (minLat + maxLat) / 2];
  if (isPointInGeometry(center[0], center[1], feature?.geometry)) {
    return center;
  }
  return null;
}

function pointToSegmentDistanceSq(px, py, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  if (dx === 0 && dy === 0) {
    const sx = px - ax;
    const sy = py - ay;
    return sx * sx + sy * sy;
  }
  const t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy);
  const clamped = Math.max(0, Math.min(1, t));
  const cx = ax + clamped * dx;
  const cy = ay + clamped * dy;
  const sx = px - cx;
  const sy = py - cy;
  return sx * sx + sy * sy;
}

function pointToRingSignedDistance(lon, lat, ring) {
  let inside = false;
  let minDistSq = Number.POSITIVE_INFINITY;
  const n = ring.length;
  if (n < 2) return Number.NEGATIVE_INFINITY;
  let j = n - 1;
  for (let i = 0; i < n; i += 1) {
    const a = ring[i];
    const b = ring[j];
    const ax = Number(a?.[0]);
    const ay = Number(a?.[1]);
    const bx = Number(b?.[0]);
    const by = Number(b?.[1]);
    if (!Number.isFinite(ax) || !Number.isFinite(ay) || !Number.isFinite(bx) || !Number.isFinite(by)) {
      j = i;
      continue;
    }
    const intersect =
      ay > lat !== by > lat &&
      lon < ((bx - ax) * (lat - ay)) / ((by - ay) || Number.EPSILON) + ax;
    if (intersect) inside = !inside;
    minDistSq = Math.min(minDistSq, pointToSegmentDistanceSq(lon, lat, ax, ay, bx, by));
    j = i;
  }
  if (!Number.isFinite(minDistSq)) return Number.NEGATIVE_INFINITY;
  const dist = Math.sqrt(minDistSq);
  return inside ? dist : -dist;
}

function polylabelForRing(ring, precision = 0.05) {
  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  for (const p of ring) {
    const x = Number(p?.[0]);
    const y = Number(p?.[1]);
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    maxX = Math.max(maxX, x);
    maxY = Math.max(maxY, y);
  }
  if (!Number.isFinite(minX) || !Number.isFinite(minY) || !Number.isFinite(maxX) || !Number.isFinite(maxY)) {
    return null;
  }
  const width = maxX - minX;
  const height = maxY - minY;
  const cellSize = Math.min(width, height);
  if (cellSize <= 0) {
    return [minX, minY];
  }
  const h = cellSize / 2;
  const queue = [];
  for (let x = minX; x < maxX; x += cellSize) {
    for (let y = minY; y < maxY; y += cellSize) {
      const cx = x + h;
      const cy = y + h;
      const d = pointToRingSignedDistance(cx, cy, ring);
      queue.push({ x: cx, y: cy, h, d, max: d + h * Math.SQRT2 });
    }
  }
  const centroid = ringCentroid(ring);
  let best = null;
  if (centroid && Number.isFinite(centroid[0]) && Number.isFinite(centroid[1])) {
    const d = pointToRingSignedDistance(centroid[0], centroid[1], ring);
    best = { x: centroid[0], y: centroid[1], h: 0, d, max: d };
  }
  const boxCenter = {
    x: (minX + maxX) / 2,
    y: (minY + maxY) / 2,
    h: 0,
    d: pointToRingSignedDistance((minX + maxX) / 2, (minY + maxY) / 2, ring),
    max: 0,
  };
  boxCenter.max = boxCenter.d;
  if (!best || boxCenter.d > best.d) {
    best = boxCenter;
  }

  const eps = Math.max(precision, cellSize / 120);
  while (queue.length) {
    queue.sort((a, b) => b.max - a.max);
    const cell = queue.shift();
    if (!cell) break;
    if (cell.d > best.d) {
      best = cell;
    }
    if (cell.max - best.d <= eps) {
      continue;
    }
    const nh = cell.h / 2;
    if (nh <= 0) continue;
    const candidates = [
      { x: cell.x - nh, y: cell.y - nh, h: nh },
      { x: cell.x + nh, y: cell.y - nh, h: nh },
      { x: cell.x - nh, y: cell.y + nh, h: nh },
      { x: cell.x + nh, y: cell.y + nh, h: nh },
    ];
    for (const c of candidates) {
      const d = pointToRingSignedDistance(c.x, c.y, ring);
      queue.push({ ...c, d, max: d + c.h * Math.SQRT2 });
    }
  }
  if (!best || !Number.isFinite(best.x) || !Number.isFinite(best.y)) {
    return null;
  }
  return [best.x, best.y];
}

function getFeatureVisualCenterLonLat(feature) {
  const { ring } = featurePrimaryRing(feature);
  if (ring && ring.length >= 3) {
    const anchor = polylabelForRing(ring, 0.03);
    if (anchor && isPointInGeometry(anchor[0], anchor[1], feature?.geometry)) {
      return anchor;
    }
  }
  return getFeatureBoundsCenterLonLat(feature) || getFeatureLabelLonLat(feature);
}

function getProvinceLabelAnchorFromFeature(feature) {
  const adcode = getFeatureAdcode(feature);
  const provinceCode = getProvinceCodeFromAdcode(adcode) || adcode;
  const override = CHINA_PROVINCE_LABEL_ANCHOR_OVERRIDES[provinceCode];
  if (Array.isArray(override) && override.length >= 2) {
    const lon = Number(override[0]);
    const lat = Number(override[1]);
    if (Number.isFinite(lon) && Number.isFinite(lat) && isPointInGeometry(lon, lat, feature?.geometry)) {
      return [lon, lat];
    }
  }
  return getFeatureVisualCenterLonLat(feature);
}

function getFeatureLabelAnchorForScope(feature, scope) {
  if (scope === "china_province") {
    return getProvinceLabelAnchorFromFeature(feature);
  }
  if (scope === "china_prefecture") {
    const adcode = getFeatureAdcode(feature);
    const provinceCode = getProvinceCodeFromAdcode(adcode) || adcode;
    const provinceAnchor = state.chinaPrefectureProvinceLabelAnchors.get(provinceCode);
    if (provinceAnchor) {
      return provinceAnchor;
    }
    return getProvinceLabelAnchorFromFeature(feature);
  }
  return getFeatureLabelLonLat(feature);
}

function getGlobalChineseName(feature) {
  const props = feature?.properties || {};
  const iso2 = props.iso_a2 || props.ISO_A2 || props.iso2 || props.ISO2 || "";
  const aliases = countryAliasesFromIso2(iso2);
  const zh = aliases.find(text => /[\u4e00-\u9fff]/.test(String(text)));
  if (zh) return String(zh);

  const display = featureDisplayName(feature);
  const displayNorm = normalizePlaceName(display);
  for (const [cn, aliasList] of Object.entries(PLACE_ALIAS_OVERRIDES)) {
    const hasMatch = aliasList.some(alias => normalizePlaceName(alias) === displayNorm);
    if (hasMatch) return cn;
  }
  return display;
}

function getFeatureChineseLabel(feature, scope) {
  if (scope === "global") {
    return getGlobalChineseName(feature);
  }
  const props = feature?.properties || {};
  const adcode = String(props.adcode == null ? "" : props.adcode).trim().padStart(6, "0");
  if (scope === "china_province" && CHINA_PROVINCE_ALIAS_OVERRIDES[adcode]?.length) {
    return CHINA_PROVINCE_ALIAS_OVERRIDES[adcode][0];
  }
  return "";
}

function getFeatureAdcode(feature) {
  const raw = feature?.properties?.adcode;
  return String(raw == null ? "" : raw).trim().padStart(6, "0");
}

function getLabelOffsetPx(feature, scope, base = 18) {
  const code = getFeatureAdcode(feature);
  if (scope === "china_prefecture" || scope === "china_province") {
    const provinceCode = getProvinceCodeFromAdcode(code) || code;
    const factor = CHINA_PROVINCE_LABEL_OFFSET_FACTORS[provinceCode];
    if (factor) {
      return {
        x: Math.round(base * factor.x),
        y: Math.round(base * factor.y),
      };
    }
    return { x: 0, y: 0 };
  }
  return { x: 0, y: 0 };
}

function getProvincePaletteStyle(provinceCode) {
  const code = String(provinceCode || "").trim().padStart(6, "0");
  const knownIndex = CHINA_PROVINCE_CODE_INDEX.get(code);
  const prefix = Number.parseInt(code.slice(0, 2), 10);
  const seed = Number.isFinite(knownIndex) ? knownIndex : Number.isFinite(prefix) ? prefix : 0;
  const hue = (seed * 137.508 + 23) % 360;
  return {
    fill: `hsl(${hue.toFixed(1)}, 54%, 89%)`,
    stroke: `hsl(${hue.toFixed(1)}, 36%, 63%)`,
  };
}

function getFeatureRegionStyle(feature, scope) {
  const featureId = feature?.properties?._feature_id || "";
  const highlighted = state.activeRegionFeatureIds.has(featureId);

  if (scope === "china_prefecture") {
    const adcode = getFeatureAdcode(feature);
    const provinceCode = getProvinceCodeFromAdcode(adcode) || adcode;
    const paletteStyle = getProvincePaletteStyle(provinceCode);
    if (state.regionMatchFilterActive) {
      const provinceMatched = state.activePrefectureProvinceCodes.has(provinceCode);
      if (!highlighted) {
        return {
          color: "transparent",
          weight: 0,
          fillColor: provinceMatched ? paletteStyle.fill : "#ffffff",
          fillOpacity: provinceMatched ? 0.86 : 0.96,
          opacity: 0,
        };
      }
      return {
        color: paletteStyle.stroke,
        weight: 0.95,
        fillColor: paletteStyle.fill,
        fillOpacity: 0.9,
        opacity: 0.95,
      };
    }
    return {
      color: paletteStyle.stroke,
      weight: 0.7,
      fillColor: paletteStyle.fill,
      fillOpacity: 0.82,
      opacity: 0.9,
    };
  }

  if (scope === "china_province") {
    const adcode = getFeatureAdcode(feature);
    const provinceCode = getProvinceCodeFromAdcode(adcode) || adcode;
    const paletteStyle = getProvincePaletteStyle(provinceCode);
    if (state.regionMatchFilterActive) {
      if (!highlighted) {
        return {
          color: "transparent",
          weight: 0,
          fillColor: "#ffffff",
          fillOpacity: 0.96,
          opacity: 0,
        };
      }
      return {
        color: paletteStyle.stroke,
        weight: 0.95,
        fillColor: paletteStyle.fill,
        fillOpacity: 0.9,
        opacity: 0.95,
      };
    }
    return {
      color: paletteStyle.stroke,
      weight: 0.7,
      fillColor: paletteStyle.fill,
      fillOpacity: 0.82,
      opacity: 0.9,
    };
  }

  return {
    color: highlighted ? "#2f6f5a" : "#a7adb3",
    weight: highlighted ? 1.6 : 0.7,
    fillColor: highlighted ? "#63b59a" : "#ffffff",
    fillOpacity: highlighted ? 0.78 : 0.92,
    opacity: highlighted ? 1 : 0.8,
  };
}

function getFeatureHighlightStyle(feature, scope) {
  if (scope === "china_prefecture") {
    return {
      color: "#7d1f35",
      weight: 2.2,
      fillColor: "#ffffff",
      fillOpacity: 0.01,
      opacity: 1,
    };
  }
  return {
    color: "#7d1f35",
    weight: 2.1,
    fillColor: "#f7d6df",
    fillOpacity: 0.2,
    opacity: 1,
  };
}

function ensureMapHatchPattern() {
  const svg = state.map?.getPanes?.()?.overlayPane?.querySelector?.("svg");
  if (!svg) return "";
  const ns = "http://www.w3.org/2000/svg";
  let defs = svg.querySelector("defs");
  if (!defs) {
    defs = document.createElementNS(ns, "defs");
    svg.insertBefore(defs, svg.firstChild);
  }
  const patternId = "region-match-hatch";
  let pattern = defs.querySelector(`#${patternId}`);
  if (!pattern) {
    pattern = document.createElementNS(ns, "pattern");
    pattern.setAttribute("id", patternId);
    pattern.setAttribute("patternUnits", "userSpaceOnUse");
    pattern.setAttribute("width", "10");
    pattern.setAttribute("height", "10");
    pattern.setAttribute("patternTransform", "rotate(45)");
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", "M 0 0 L 0 10 M 5 0 L 5 10 M 10 0 L 10 10");
    path.setAttribute("stroke", "#7d1f35");
    path.setAttribute("stroke-width", "2");
    path.setAttribute("stroke-opacity", "0.6");
    pattern.appendChild(path);
    defs.appendChild(pattern);
  }
  return patternId;
}

function applyHatchPatternToLayerPath(layer) {
  const path = layer?._path;
  if (!path) return;
  const patternId = ensureMapHatchPattern();
  if (!patternId) return;
  path.setAttribute("fill", `url(#${patternId})`);
  path.setAttribute("fill-opacity", "1");
}

function refreshRegionHighlightLayer() {
  if (!state.regionHighlightLayer) return;
  state.regionHighlightLayer.clearLayers();
  if (!state.activeRegionFeatureIds.size) return;
  const highlights = getCurrentBoundaryFeatures().filter(feature =>
    state.activeRegionFeatureIds.has(feature?.properties?._feature_id || "")
  );
  if (!highlights.length) return;
  state.regionHighlightLayer.addData(highlights);
}

function getFeatureMapLabel(feature, scope) {
  if (scope === "china_prefecture") {
    const featureId = feature?.properties?._feature_id || "";
    if (!state.chinaPrefectureProvinceLabelFeatureIds.has(featureId)) {
      return "";
    }
    const adcode = getFeatureAdcode(feature);
    const provinceCode = getProvinceCodeFromAdcode(adcode);
    return (
      getProvinceChineseNameByCode(provinceCode) ||
      getProvinceChineseNameByCode(adcode) ||
      ""
    );
  }

  if (!isSelectiveRegionScope(scope)) {
    return "";
  }
  const featureId = feature?.properties?._feature_id || "";
  const shouldFilterByMatched = scope === "global";
  if (shouldFilterByMatched && state.activeRegionFeatureIds.size > 0 && !state.activeRegionFeatureIds.has(featureId)) {
    return "";
  }
  const { area } = featurePrimaryRing(feature);
  if (scope === "global" && area > 0 && area < 0.5) {
    return "";
  }
  return getFeatureChineseLabel(feature, scope).trim();
}

function escapeXml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function svgNum(value) {
  return Number(value).toFixed(2);
}

function buildSvgPathData(geometry, bounds, width, height, padding) {
  const polygons = getGeometryPolygons(geometry);
  const parts = [];
  for (const polygon of polygons) {
    if (!Array.isArray(polygon)) continue;
    for (const ring of polygon) {
      if (!Array.isArray(ring) || !ring.length) continue;
      const points = [];
      for (const point of ring) {
        if (!Array.isArray(point) || point.length < 2) continue;
        const lon = Number(point[0]);
        const lat = Number(point[1]);
        if (!Number.isFinite(lon) || !Number.isFinite(lat)) continue;
        const p = projectLonLatToCanvas(lon, lat, bounds, width, height, padding);
        points.push(p);
      }
      if (points.length < 3) continue;
      parts.push(`M ${svgNum(points[0].x)} ${svgNum(points[0].y)}`);
      for (let i = 1; i < points.length; i += 1) {
        parts.push(`L ${svgNum(points[i].x)} ${svgNum(points[i].y)}`);
      }
      parts.push("Z");
    }
  }
  return parts.join(" ");
}

async function buildRegionExportSvgPayload() {
  const ok = state.regionMode ? true : await enterRegionMode();
  if (!ok) return null;
  const features = getRenderableBoundaryFeatures();
  if (!Array.isArray(features) || !features.length) {
    alert("当前没有可导出的区域边界");
    return null;
  }

  const bounds = getExportGeoBounds(state.regionScope, features);
  const size = computeExportCanvasSize(bounds);
  const width = size.width;
  const height = size.height;
  const padding = Math.round(Math.min(width, height) * 0.04);
  const svgParts = [];
  svgParts.push(`<?xml version="1.0" encoding="UTF-8"?>`);
  svgParts.push(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">`
  );
  svgParts.push(
    `<defs><pattern id="match-hatch" patternUnits="userSpaceOnUse" width="10" height="10" patternTransform="rotate(45)"><path d="M 0 0 L 0 10 M 5 0 L 5 10 M 10 0 L 10 10" stroke="#7d1f35" stroke-width="2" stroke-opacity="0.6"/></pattern></defs>`
  );
  svgParts.push(`<rect x="0" y="0" width="${width}" height="${height}" fill="#ffffff"/>`);

  for (const feature of features) {
    const style = getFeatureRegionStyle(feature, state.regionScope);
    const path = buildSvgPathData(feature?.geometry, bounds, size.width, size.height, padding);
    if (!path) continue;
    svgParts.push(
      `<path d="${path}" fill="${style.fillColor}" stroke="${style.color}" stroke-width="${style.weight}" fill-opacity="${style.fillOpacity}" stroke-opacity="${style.opacity}" fill-rule="evenodd"/>`
    );
  }

  if (state.activeRegionFeatureIds.size) {
    for (const feature of features) {
      const featureId = feature?.properties?._feature_id || "";
      if (!state.activeRegionFeatureIds.has(featureId)) continue;
      const path = buildSvgPathData(feature?.geometry, bounds, size.width, size.height, padding);
      if (!path) continue;
      const hs = getFeatureHighlightStyle(feature, state.regionScope);
      svgParts.push(
        `<path d="${path}" fill="url(#match-hatch)" stroke="${hs.color}" stroke-width="${hs.weight}" fill-rule="evenodd" fill-opacity="1" stroke-opacity="${hs.opacity}"/>`
      );
    }
  }

  // Keep province contour lines visible in exports for both China scopes.
  if (state.regionScope === "china_province" || state.regionScope === "china_prefecture") {
    const provinceFeatures = Array.isArray(state.chinaProvinceBoundaryFeatures)
      ? state.chinaProvinceBoundaryFeatures
      : [];
    const provinceOutlineWidth = state.regionScope === "china_prefecture" ? 1.35 : 1.6;
    for (const provinceFeature of provinceFeatures) {
      const path = buildSvgPathData(provinceFeature?.geometry, bounds, size.width, size.height, padding);
      if (!path) continue;
      svgParts.push(
        `<path d="${path}" fill="none" stroke="#8d836f" stroke-width="${provinceOutlineWidth}" stroke-opacity="0.95" vector-effect="non-scaling-stroke" fill-rule="evenodd"/>`
      );
    }
  }

  if (isSelectiveRegionScope(state.regionScope) || state.regionScope === "china_prefecture") {
    const baseFont =
      state.regionScope === "global"
        ? Math.round(Math.min(width, height) * 0.018)
        : Math.round(Math.min(width, height) * 0.022);
    const fontSize = Math.max(14, baseFont);

    for (const feature of features) {
      const { ring, area } = featurePrimaryRing(feature);
      if (!ring || area <= 0) continue;
      if (state.regionScope === "global" && area < 0.55) continue;

      const anchor = getFeatureLabelAnchorForScope(feature, state.regionScope);
      if (!anchor) continue;
      const [lon, lat] = anchor;
      const p = projectLonLatToCanvas(lon, lat, bounds, size.width, size.height, padding);
      const label = getFeatureMapLabel(feature, state.regionScope).trim();
      if (!label) continue;
      const labelOffset = getLabelOffsetPx(feature, state.regionScope, fontSize);
      const lx = p.x + labelOffset.x;
      const ly = p.y + labelOffset.y;
      if (labelOffset.x !== 0 || labelOffset.y !== 0) {
        svgParts.push(
          `<line x1="${svgNum(p.x)}" y1="${svgNum(p.y)}" x2="${svgNum(lx)}" y2="${svgNum(ly)}" stroke="#406a5d" stroke-width="${Math.max(1.2, fontSize * 0.08)}" opacity="0.85"/>`
        );
      }
      svgParts.push(
        `<text x="${svgNum(lx)}" y="${svgNum(ly)}" text-anchor="middle" dominant-baseline="middle" font-size="${fontSize}" font-weight="600" font-family="PingFang SC, Microsoft YaHei, Noto Sans SC, sans-serif" fill="#183b31" stroke="rgba(255,255,255,0.9)" stroke-width="${Math.max(2, Math.round(fontSize * 0.28))}" paint-order="stroke fill">${escapeXml(label)}</text>`
      );
    }
  }
  svgParts.push(`</svg>`);

  const scopeText = regionScopeFileLabel(state.regionScope);
  const ts = new Date().toISOString().replace(/[:.]/g, "-");
  const filenameBase = `region-map-${scopeText}-${ts}`;
  return {
    svgText: svgParts.join("\n"),
    width,
    height,
    filenameBase,
  };
}

function triggerBlobDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.download = filename;
  link.href = url;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function exportRegionAsSvg() {
  const payload = await buildRegionExportSvgPayload();
  if (!payload) return;
  const filename = `${payload.filenameBase}.svg`;
  const blob = new Blob([payload.svgText], { type: "image/svg+xml;charset=utf-8" });
  triggerBlobDownload(blob, filename);
  el.scanStatus.textContent = `已导出 SVG：${filename}`;
}

async function exportRegionAsPng() {
  const payload = await buildRegionExportSvgPayload();
  if (!payload) return;

  const svgBlob = new Blob([payload.svgText], { type: "image/svg+xml;charset=utf-8" });
  const svgUrl = URL.createObjectURL(svgBlob);
  try {
    const image = await new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error("SVG 图像加载失败"));
      img.src = svgUrl;
    });

    const canvas = document.createElement("canvas");
    canvas.width = payload.width;
    canvas.height = payload.height;
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      throw new Error("当前环境不支持 PNG 导出");
    }
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(image, 0, 0, canvas.width, canvas.height);

    const pngBlob = await new Promise((resolve, reject) => {
      canvas.toBlob(blob => {
        if (blob) {
          resolve(blob);
          return;
        }
        reject(new Error("PNG 编码失败"));
      }, "image/png");
    });
    const filename = `${payload.filenameBase}.png`;
    triggerBlobDownload(pngBlob, filename);
    el.scanStatus.textContent = `已导出 PNG：${filename}`;
  } catch (error) {
    console.error(error);
    alert(error.message || "导出 PNG 失败");
  } finally {
    URL.revokeObjectURL(svgUrl);
  }
}

function appendPlacesToInput(text) {
  const incoming = parsePlaceNames(text);
  if (!incoming.length) return;
  const existing = parsePlaceNames(el.placeTextInput.value);
  const merged = [...existing];
  const seen = new Set(existing.map(normalizePlaceName));
  for (const name of incoming) {
    const key = normalizePlaceName(name);
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push(name);
  }
  el.placeTextInput.value = merged.join("\n");
}

async function handlePlaceFileUpload(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  try {
    const content = await file.text();
    appendPlacesToInput(content);
    el.scanStatus.textContent = `已读取地名文件：${file.name}`;
  } catch (error) {
    alert("读取地名文件失败");
  } finally {
    event.target.value = "";
  }
}

function updateSummary() {
  const located = state.allLocated.length;
  const unlocated = state.allUnlocated.length;
  const total = located + unlocated;
  el.summaryText.textContent = `总计 ${total}，已定位 ${located}，未定位 ${unlocated}`;
}

function applyScanPayload(payload) {
  state.allLocated = payload.items || [];
  state.allUnlocated = payload.unlocated || [];
  updateSummary();
  renderMapMarkers();
  renderUnlocated();

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
  if (state.regionMode) return;

  const clusters = buildClusters(state.allLocated);

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
  const rows = state.allUnlocated;

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
    el.cacheSummary.textContent = "缓存：目录 0，元数据 0，缩略图 0，预览 0，边界 0";
    return;
  }
  el.cacheSummary.textContent =
    `缓存：目录 ${stats.scan_entries || 0}，元数据 ${stats.meta_entries || 0}，` +
    `缩略图 ${stats.thumb_files || 0}，预览 ${stats.preview_files || 0}，边界 ${stats.boundary_files || 0}`;
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
  if (!confirm("确认清空全部缓存？这会删除目录扫描记录、元数据、缩略图、预览和边界缓存。")) return;
  try {
    const resp = await fetch("/api/cache/clear", { method: "POST" });
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.error || "清空缓存失败");
    }
    state.allLocated = [];
    state.allUnlocated = [];
    state.selectedCacheIds = new Set();
    state.loadedCacheIds = new Set();
    state.worldBoundaries = null;
    state.chinaProvinceBoundaries = null;
    state.chinaPrefectureBoundaries = null;
    state.worldBoundaryFeatures = [];
    state.chinaProvinceBoundaryFeatures = [];
    state.chinaPrefectureBoundaryFeatures = [];
    state.worldAliasIndex = new Map();
    state.chinaProvinceAliasIndex = new Map();
    state.chinaPrefectureAliasIndex = new Map();
    state.chinaPrefectureProvinceLabelFeatureIds = new Set();
    state.chinaPrefectureProvinceLabelAnchors = new Map();
    state.chinaPrefectureFeatureProvinceCodes = new Map();
    state.activePrefectureProvinceCodes = new Set();
    state.regionScope = "global";
    state.activeRegionFeatureIds = new Set();
    state.regionMatchFilterActive = false;
    state.regionHighlightLayer.clearLayers();
    state.regionLayer.clearLayers();
    renderMapMarkers();
    renderClusterList([]);
    renderUnlocated();
    updateSummary();
    state.cacheEntries = [];
    renderCacheList();
    syncSelectionState();
    renderRegionScopeTabs();
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
  if (el.applyMapZoomBtn) {
    el.applyMapZoomBtn.addEventListener("click", applyMapZoomFromInput);
  }
  if (el.mapZoomInput) {
    el.mapZoomInput.addEventListener("keydown", event => {
      if (event.key === "Enter") {
        event.preventDefault();
        applyMapZoomFromInput();
      }
    });
    el.mapZoomInput.addEventListener("blur", applyMapZoomFromInput);
  }
  el.scanBtn.addEventListener("click", startScan);
  el.pickDirBtn.addEventListener("click", pickDirectory);
  el.mediaSheetTab.addEventListener("click", () => {
    switchSheet("media");
  });
  el.regionSheetTab.addEventListener("click", () => {
    switchSheet("region");
  });
  el.globalScopeTab.addEventListener("click", () => {
    switchRegionScope("global");
  });
  el.chinaProvinceScopeTab.addEventListener("click", () => {
    switchRegionScope("china_province");
  });
  el.chinaPrefectureScopeTab.addEventListener("click", () => {
    switchRegionScope("china_prefecture");
  });
  el.highlightPlacesBtn.addEventListener("click", applyPlaceHighlight);
  el.clearHighlightsBtn.addEventListener("click", clearPlaceHighlight);
  el.exportRegionSvgBtn.addEventListener("click", exportRegionAsSvg);
  if (el.exportRegionPngBtn) {
    el.exportRegionPngBtn.addEventListener("click", exportRegionAsPng);
  }
  el.placeFileInput.addEventListener("change", handlePlaceFileUpload);
  el.refreshCacheBtn.addEventListener("click", refreshCacheList);
  el.loadSelectedCacheBtn.addEventListener("click", loadSelectedCaches);
  el.toggleSelectCacheBtn.addEventListener("click", toggleSelectAllCaches);
  el.clearCacheBtn.addEventListener("click", clearAllCaches);
  el.closeModal.addEventListener("click", closePreview);
  el.previewModal.querySelector(".modal-backdrop").addEventListener("click", closePreview);
}

function init() {
  initMap();
  bindEvents();
  renderSheetTabs();
  renderRegionScopeTabs();
  renderClusterList([]);
  renderUnlocated();
  renderUnmatchedPlaces([]);
  renderCacheList();
  syncSelectionState();
  refreshCacheList();
}

init();
