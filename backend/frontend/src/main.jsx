import React, { useEffect, useMemo, useRef, useState } from "react";
import ReactDOM from "react-dom/client";
import "./style.css";

const PLATFORMS = [
  { id: "ALL", title: "Все" },
  { id: "WB", title: "WB" },
  { id: "OZON", title: "Ozon" },
  { id: "YM", title: "ЯМ" },
];

const NAV = [
  { id: "tower", title: "Control Tower", group: "Главное" },
  { id: "communications", title: "Коммуникации", group: "Работа" },
  { id: "catalog", title: "Каталог товаров", group: "Работа" },
  { id: "quality", title: "Quality Hub", group: "Работа" },
  { id: "operations", title: "Операции", group: "Работа" },
  { id: "fbo", title: "FBO Center", group: "Работа" },
  { id: "analytics", title: "Аналитика", group: "Управление" },
  { id: "settings", title: "Настройки", group: "Управление" },
  { id: "security", title: "Пользователи и роли", group: "Управление" },
];

const ANSWER_STATES = [
  ["all", "Все"],
  ["unanswered", "Требуют ответа"],
  ["drafts", "Черновики готовы"],
  ["answered", "С ответом"],
  ["no_text", "Оценки без комментария"],
  ["risk", "Высокий риск"],
];

const MODULES = [
  { title: "Communications", desc: "Отзывы, вопросы, AI-ответы, автопубликация" },
  { title: "Product Catalog", desc: "Товары, рейтинги, ссылки на WB/Ozon/ЯМ" },
  { title: "Quality Hub", desc: "Жалобы, аномалии, AI-рекомендации" },
  { title: "Marketplace Operations", desc: "Возвраты, акты, недостачи, излишки, обезличка" },
  { title: "FBO Control", desc: "Slot Hunter, календарь, уведомления, история" },
  { title: "Security", desc: "Роли, права, аудит действий" },
];

const ACCESS_MODULES = [
  ["tower", "Control Tower"],
  ["communications", "Коммуникации"],
  ["catalog", "Каталог товаров"],
  ["quality", "Quality Hub"],
  ["operations", "Operations Hub"],
  ["fbo", "FBO Center"],
  ["analytics", "Аналитика"],
  ["settings", "Настройки"],
  ["security", "Пользователи и роли"],
];

const DEFAULT_ROLE_RIGHTS = {
  "Оператор": { communications: { view: true, edit: true, publish: false }, catalog: { view: true }, quality: { view: true } },
  "Старший оператор": { communications: { view: true, edit: true, publish: true }, catalog: { view: true }, quality: { view: true }, analytics: { view: true } },
  "Руководитель": { tower: { view: true }, communications: { view: true }, catalog: { view: true }, quality: { view: true, edit: true }, operations: { view: true, edit: true }, fbo: { view: true, edit: true }, analytics: { view: true } },
  "Администратор": Object.fromEntries(ACCESS_MODULES.map(([id]) => [id, { view: true, edit: true, publish: true, admin: true }]))
};

function openRecoveryTopic(topic) {
  window.open(`/recovery-v5/topics/${encodeURIComponent(topic)}/items?platform=${platform || "ALL"}&limit=20000`, "_blank");
}
async function api(path, options = {}) {
  const { timeoutMs = 30000, ...fetchOptions } = options || {};
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" }, signal: controller.signal, ...fetchOptions });
    const raw = await res.text();
    let data = null;
    try { data = raw ? JSON.parse(raw) : null; } catch { data = raw; }
    if (!res.ok) throw new Error(typeof data === "object" ? (data.detail || data.error || JSON.stringify(data)) : data);
    return data;
  } finally {
    clearTimeout(timer);
  }
}

function asList(data) {
  if (Array.isArray(data)) return data;
  return data?.items || data?.data || data?.reviews || data?.questions || [];
}

function dt(value) { return value ? String(value).replace("T", " ").slice(0, 19) : "—"; }

function countsPayloadScore(payload) {
  const c = payload?.counts || {};
  return Number(c.reviews_total || 0)
    + Number(c.questions_total || 0)
    + Number(c.communications_total || 0)
    + Number(c.products_total || 0)
    + Number(c.quality_attention || 0);
}

function preserveCountsPayload(prev, next) {
  if (!next) return prev || next;
  const prevScore = countsPayloadScore(prev);
  const nextScore = countsPayloadScore(next);

  // Never allow a temporary empty/fallback API response to overwrite known non-zero counters.
  if (prev?.counts && prevScore > 0 && (!next.counts || nextScore === 0)) {
    return {
      ...next,
      counts: prev.counts,
      stale_counts: true,
      counts_guard: "kept_previous_non_zero_counts",
    };
  }

  return next;
}

function day(value) { return value ? String(value).slice(0, 10) : "Без даты"; }
function month(value) { return value ? String(value).slice(0, 7) : "Без месяца"; }


function week(value) {
  if (!value) return "Без недели";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "Без недели";
  const jan1 = new Date(d.getFullYear(), 0, 1);
  const days = Math.floor((d - jan1) / 86400000);
  const w = Math.ceil((days + jan1.getDay() + 1) / 7);
  return `${d.getFullYear()}-W${String(w).padStart(2, "0")}`;
}

function minutesBetween(start, end) {
  if (!start || !end) return null;
  const a = new Date(start);
  const b = new Date(end);
  if (Number.isNaN(a.getTime()) || Number.isNaN(b.getTime())) return null;
  const minutes = Math.round((b - a) / 60000);
  return minutes >= 0 ? minutes : null;
}

function fmtMinutes(value) {
  if (!Number.isFinite(Number(value))) return "—";
  const m = Math.round(Number(value));
  if (m < 60) return `${m} мин`;
  const h = Math.floor(m / 60);
  const rest = m % 60;
  return rest ? `${h} ч ${rest} мин` : `${h} ч`;
}
function bool(value) { return value ? "да" : "нет"; }
function num(value) { return Number(value || 0).toLocaleString("ru-RU"); }
function avg(nums) { const v = nums.filter(x => Number.isFinite(Number(x))); return v.length ? (v.reduce((a,b)=>a+Number(b),0)/v.length).toFixed(2) : "—"; }
function pretty(obj) { return typeof obj === "string" ? obj : JSON.stringify(obj, null, 2); }
function normPlatform(value) { return String(value || "").trim().toUpperCase(); }

function rowMatchesPlatform(row, selectedPlatform) {
  const target = normPlatform(selectedPlatform);
  if (!target || target === "ALL") return true;
  const direct = normPlatform(row?.platform);
  const platforms = Array.isArray(row?.platforms) ? row.platforms.map(normPlatform) : [];
  return direct === target || platforms.includes(target);
}

function rowEffectivePlatform(row, selectedPlatform) {
  const target = normPlatform(selectedPlatform);
  if (target && target !== "ALL") return target;
  if (row?.platform) return normPlatform(row.platform);
  if (Array.isArray(row?.platforms) && row.platforms.length) return normPlatform(row.platforms[0]);
  return target || "ALL";
}

function productUrl(item) {
  if (item?.product_url) return item.product_url;
  if (!item?.sku) return null;
  if (normPlatform(item.platform) === "WB") return `https://www.wildberries.ru/catalog/${item.sku}/detail.aspx`;
  if (normPlatform(item.platform) === "OZON") return `https://www.ozon.ru/search/?text=${item.sku}`;
  return null;
}

function groupCount(items, fn) {
  const map = new Map();
  items.forEach((item) => {
    const key = fn(item) || "Без значения";
    map.set(key, (map.get(key) || 0) + 1);
  });
  return Array.from(map.entries()).sort((a, b) => b[1] - a[1]);
}

function hasText(item) {
  return Boolean(String(item?.text || item?.pros || item?.cons || "").trim());
}

function isNoTextRating(item) {
  return normPlatform(item?.platform) === "OZON" && item?.kind === "review" && !hasText(item);
}

function canRespond(item) {
  return !isNoTextRating(item);
}

function needsResponse(item) {
  return canRespond(item) && (item?.operational_status === "needs_response" || item?.has_answer === false);
}

function loadSavedRights() {
  try { return JSON.parse(localStorage.getItem("karatov_role_rights") || "null") || DEFAULT_ROLE_RIGHTS; }
  catch { return DEFAULT_ROLE_RIGHTS; }
}

function Badge({ children, type = "" }) { return <span className={`badge ${type}`}>{children}</span>; }
function PlatformBadge({ value }) { return <span className={`platformBadge ${value || ""}`}>{value || "—"}</span>; }
function Card({ title, value, hint, onClick, type = "" }) { return <button className={`metricCard ${type}`} onClick={onClick}><b>{value}</b><span>{title}</span>{hint && <small>{hint}</small>}</button>; }
function Section({ title, subtitle, actions, children }) { return <><div className="top"><div><h2>{title}</h2><p>{subtitle}</p></div><div className="actions">{actions}</div></div>{children}</>; }
function Empty({ children = "Данных нет" }) { return <div className="empty">{children}</div>; }

function App() {
  const [page, setPage] = useState("tower");
  const [platform, setPlatform] = useState("ALL");
  const platformRef = useRef("ALL");
  useEffect(() => {
    platformRef.current = normPlatform(platform || "ALL");
  }, [platform]);
  const [state, setState] = useState("unanswered");
  const [kind, setKind] = useState("reviews");
  const [search, setSearch] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [lastRefresh, setLastRefresh] = useState(null);

  const [reviews, setReviews] = useState([]);
  const [questions, setQuestions] = useState([]);
  const [products, setProducts] = useState([]);
  const [productCard, setProductCard] = useState(null);
  const [selected, setSelected] = useState(null);
  const [draft, setDraft] = useState("");

  const [diagnostics, setDiagnostics] = useState(null);
  const [syncHistory, setSyncHistory] = useState(null);
  const [publishHistory, setPublishHistory] = useState(null);
  const [rules, setRules] = useState({});
  const [booking, setBooking] = useState(null);
  const [operations, setOperations] = useState([]);
  const [operationsSummary, setOperationsSummary] = useState(null); const [customerOpsTab, setCustomerOpsTab] = useState("operations"); const [customerOpsSummary, setCustomerOpsSummary] = useState(null); const [chats, setChats] = useState([]); const [returnsList, setReturnsList] = useState([]); const [selectedChat, setSelectedChat] = useState(null); const [chatMessages, setChatMessages] = useState([]); const [chatDraft, setChatDraft] = useState(""); const [chatSla, setChatSla] = useState(null);
  const [operationType, setOperationType] = useState("all"); const operationTypeRef = useRef("all"); useEffect(() => { operationTypeRef.current = operationType || "all"; }, [operationType]);
  const [roleRights, setRoleRights] = useState(loadSavedRights);
  const refreshRequestSeq = useRef(0);
  const productsRequestSeq = useRef(0);

  const rawItems = useMemo(() => [...reviews.map(x => ({ ...x, kind: "review" })), ...questions.map(x => ({ ...x, kind: "question" }))], [reviews, questions]);
  const platformItems = useMemo(() => platform === "ALL" ? rawItems : rawItems.filter(x => normPlatform(x.platform) === platform), [rawItems, platform]);
  const visibleItems = useMemo(() => platformItems.filter(matchesFilters), [platformItems, state, search]);
  const visibleReviews = visibleItems.filter(x => x.kind === "review");
  const visibleQuestions = visibleItems.filter(x => x.kind === "question");
  const activeList = kind === "reviews" ? visibleReviews : visibleQuestions;

  const localMetrics = useMemo(() => buildMetrics(platformItems), [platformItems]);
  const metrics = useMemo(() => {
    const c = (normPlatform(diagnostics?.platform || platform) === normPlatform(platform) ? diagnostics?.counts : null);
    if (c) {
      const reviewsTotal = Number(c.reviews_total || 0);
      const questionsTotal = Number(c.questions_total || 0);
      const reviewsUnanswered = Number(c.reviews_unanswered || 0);
      const questionsUnanswered = Number(c.questions_unanswered || 0);
      const highRisk = Number(c.high_risk || 0);
      const ready = Number(c.ready_to_publish || 0);

      return {
        ...localMetrics,
        total: reviewsTotal + questionsTotal,
        reviews: reviewsTotal,
        questions: questionsTotal,
        needs: reviewsUnanswered + questionsUnanswered,
        drafts: ready,
        ready,
        risks: highRisk,
        highRisk,
      };
    }
    return localMetrics;
  }, [localMetrics, diagnostics, platform]);

  const allMetrics = useMemo(() => {
    const base = buildMetrics(rawItems);
    const c = (normPlatform(diagnostics?.platform || platform) === normPlatform(platform) ? diagnostics?.counts : null);
    if (c) {
      const reviewsTotal = Number(c.reviews_total || 0);
      const questionsTotal = Number(c.questions_total || 0);
      return {
        ...base,
        total: reviewsTotal + questionsTotal,
        reviews: reviewsTotal,
        questions: questionsTotal,
        needs: Number(c.reviews_unanswered || 0) + Number(c.questions_unanswered || 0),
        drafts: Number(c.ready_to_publish || 0),
        ready: Number(c.ready_to_publish || 0),
        risks: Number(c.high_risk || 0),
        highRisk: Number(c.high_risk || 0),
      };
    }
    return base;
  }, [rawItems, diagnostics]);
  const insights = useMemo(() => buildInsights(platformItems, products), [platformItems, products]);

  useEffect(() => {
    let alive = true;

    async function fastDiagnostics() {
      try {
        const data = await api(`/system/dashboard?platform=${encodeURIComponent(platformRef.current || "ALL")}`, { timeoutMs: 8000 }).catch(() => ({ ok: false, platform: platformRef.current || "ALL", counts: null, source: "frontend_dashboard_timeout" }));
        if (alive && data) setDiagnostics(data);
      } catch (e) {
        console.warn("fast diagnostics failed", e);
      }
    }

    fastDiagnostics();
    refreshAll(false);

    const diagnosticsTimer = setInterval(fastDiagnostics, 15000);
    const fullTimer = setInterval(() => refreshAll(false), 120000);

    return () => {
      alive = false;
      clearInterval(diagnosticsTimer);
      clearInterval(fullTimer);
    };
  }, []);

  useEffect(() => {
    setProductCard(null);
    setSelected(null);
    setDraft("");
    loadProducts(false, platform);
    refreshAll(false, platform, operationType);
  }, [platform, operationType]);

  useEffect(() => {
    if (!selected) return;
    const platformMismatch = platform !== "ALL" && normPlatform(selected.platform) !== platform;
    const kindMismatch = (kind === "reviews" && selected.kind !== "review") || (kind === "questions" && selected.kind !== "question");
    const hiddenByFilters = !matchesFilters(selected);
    if (platformMismatch || kindMismatch || hiddenByFilters) {
      setSelected(null);
      setDraft("");
    }
  }, [platform, state, search, kind, reviews, questions]);

  function matchesFilters(item) {
    if (state === "unanswered" && !needsResponse(item)) return false;
    if (state === "drafts" && !(canRespond(item) && (item.final_answer || item.draft_answer || item.status === "ready_to_review" || item.status === "ready_to_publish"))) return false;
    if (state === "answered" && !(item.has_answer || item.response_origin || String(item.status || "").includes("published"))) return false;
    if (state === "no_text" && !isNoTextRating(item)) return false;
    if (state === "risk" && item.ai_risk_level !== "high") return false;
    const q = search.trim().toLowerCase();
    if (q) {
      const hay = [item.sku, item.product_name, item.text, item.pros, item.cons, item.ai_category, item.external_id].join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  }

  function buildMetrics(items) {
    const noTextRatings = items.filter(isNoTextRating);
    const needs = items.filter(needsResponse);
    const drafts = items.filter(x => canRespond(x) && (x.final_answer || x.draft_answer || x.status === "ready_to_review" || x.status === "ready_to_publish"));
    const risks = items.filter(x => x.ai_risk_level === "high");
    const reviewsOnly = items.filter(x => x.kind === "review" || x.rating !== undefined);
    return {
      total: items.length,
      reviews: reviewsOnly.length,
      questions: items.length - reviewsOnly.length,
      needs: needs.length,
      drafts: drafts.length,
      noTextRatings: noTextRatings.length,
      risks: risks.length,
      avgRating: avg(reviewsOnly.map(x => x.rating)),
      wb: rawItems.filter(x => normPlatform(x.platform) === "WB").length,
      ozon: rawItems.filter(x => normPlatform(x.platform) === "OZON").length,
      ym: rawItems.filter(x => normPlatform(x.platform) === "YM").length,
    };
  }

  function buildResponseAnalytics(items) {
    const eligible = items.filter(x => canRespond(x));
    const answered = eligible.filter(x => x.has_answer || x.response_origin || String(x.status || "").includes("published") || x.final_answer);
    const reviewAnswered = answered.filter(x => x.kind === "review");
    const questionAnswered = answered.filter(x => x.kind === "question");

    function stats(rows, thresholdMinutes) {
      const durations = rows.map(x => minutesBetween(x.created_at_marketplace || x.created_at, x.answered_at)).filter(x => x !== null);
      const inSla = durations.filter(x => x <= thresholdMinutes).length;
      const outSla = durations.filter(x => x > thresholdMinutes).length;
      const avgMin = durations.length ? durations.reduce((a,b)=>a+b,0) / durations.length : null;
      const p90 = durations.length ? durations.slice().sort((a,b)=>a-b)[Math.min(durations.length - 1, Math.floor(durations.length * 0.9))] : null;
      return { total: rows.length, measured: durations.length, inSla, outSla, avgMin, p90 };
    }

    const reviewSla = stats(reviewAnswered, 60);
    const questionSla = stats(questionAnswered, 15);
    return {
      answeredTotal: answered.length,
      pending: eligible.filter(needsResponse).length,
      noText: items.filter(isNoTextRating).length,
      reviewSla,
      questionSla,
      reviewSlaPct: reviewSla.measured ? Math.round(reviewSla.inSla / reviewSla.measured * 100) : 0,
      questionSlaPct: questionSla.measured ? Math.round(questionSla.inSla / questionSla.measured * 100) : 0,
    };
  }

  function buildInsights(items, productRows) {
    const categories = groupCount(items, x => x.ai_category).slice(0, 5);
    const riskyProducts = productRows.filter(x => Number(x.high_risk || 0) > 0 || Number(x.negative || 0) > 0).slice(0, 5);
    const highRiskCount = items.filter(x => x.ai_risk_level === "high").length;
    const mainCat = categories[0]?.[0] || "нет выраженной темы";
    const summary = `По выбранному контуру собрано ${items.length} коммуникаций. Требуют ответа: ${items.filter(needsResponse).length}. Основная тема: ${mainCat}. Высоких рисков: ${highRiskCount}.`;
    const recs = [];
    if (highRiskCount) recs.push("Разобрать высокорисковые отзывы и передать повторяющиеся темы в качество.");
    if (riskyProducts.length) recs.push(`Проверить товары: ${riskyProducts.map(x => x.sku || x.key).filter(Boolean).slice(0,3).join(", ")}.`);
    if (!recs.length) recs.push("Критичных отклонений нет, продолжать мониторинг динамики и SLA.");
    return { categories, riskyProducts, summary, recs };
  }

  async function refreshAll(show = true, platformOverride = platform, operationTypeOverride = operationType) {
    const requestId = ++refreshRequestSeq.current;
    const requestedPlatform = normPlatform(platformOverride || platform);
    const requestedOperationType = operationTypeOverride || operationType;
    if (show) { setLoading(true); setMessage("Обновляю данные из базы…"); }
    try {
      const [r, q, d, s, p, rulesData, b, opsData, opsSummaryData] = await Promise.allSettled([
        api(`/reviews?platform=${requestedPlatform}&limit=2000000`).catch(() => null),
        api(`/questions?platform=${requestedPlatform}&limit=2000000`).catch(() => null),
        api(`/system/dashboard?platform=${encodeURIComponent(requestedPlatform)}`, { timeoutMs: 8000 }).catch(() => ({ ok: false, platform: requestedPlatform, counts: null, source: "frontend_dashboard_timeout" })),
        api("/ops/sync-history", { timeoutMs: 4000 }).catch(() => null),
        api("/ops/publish-history", { timeoutMs: 4000 }).catch(() => null),
        api("/settings/automation-rules", { timeoutMs: 4000 }).catch(() => ({})),
        api("/wb-booking/status", { timeoutMs: 4000 }).catch(() => null),
        api(`/operations?platform=${requestedPlatform}&operation_type=${requestedOperationType}&limit=10000`, { timeoutMs: 7000 }).catch(() => null),
        api(`/operations/summary?platform=${requestedPlatform}`, { timeoutMs: 7000 }).catch(() => null),
      ]);
      if (requestId !== refreshRequestSeq.current || requestedPlatform !== (platformRef.current || "ALL")) return;
      if (r.status === "fulfilled" && r.value) setReviews(asList(r.value));
      if (q.status === "fulfilled" && q.value) setQuestions(asList(q.value));
      if (d.status === "fulfilled" && d.value) setDiagnostics(prev => (typeof preserveCountsPayload === "function" ? preserveCountsPayload(prev, d.value) : d.value));
      if (s.status === "fulfilled") setSyncHistory(s.value);
      if (p.status === "fulfilled") setPublishHistory(p.value);
      if (rulesData.status === "fulfilled") setRules(rulesData.value || {});
      if (b.status === "fulfilled") setBooking(b.value);
      if (opsData.status === "fulfilled" && opsData.value) setOperations(prev => { const items = opsData.value?.items || []; return (items.length || requestedPlatform !== "ALL") ? items : prev; });
      if (opsSummaryData.status === "fulfilled" && opsSummaryData.value) setOperationsSummary(prev => { const total = Number(opsSummaryData.value?.total || opsSummaryData.value?.total_operations || 0); return (total || requestedPlatform !== "ALL") ? opsSummaryData.value : prev; });
      setLastRefresh(new Date().toISOString());
      if (show) setMessage("Данные обновлены");
    } catch (e) {
      setMessage(`Ошибка обновления: ${e.message}`);
    } finally {
      if (show) setLoading(false);
    }
  }

  async function loadProducts(show = true, platformOverride = platform) {
    const requestId = ++productsRequestSeq.current;
    const requestedPlatform = normPlatform(platformOverride || platform);
    if (show) setMessage("Обновляю каталог товаров…");
    try {
      const meta = await api(`/ops/product-summary?platform=${requestedPlatform}&limit=1`, { timeoutMs: 10000 });
      const productTotal = Number(meta?.total || 0);
      const data = productTotal > 1
        ? await api(`/ops/product-summary?platform=${requestedPlatform}&limit=${encodeURIComponent(productTotal)}`, { timeoutMs: 20000 })
        : meta;
      if (requestId !== productsRequestSeq.current) return;
      const rows = (data.items || []).filter(row => rowMatchesPlatform(row, requestedPlatform));
      setProducts(rows);
      if (show) setMessage("Каталог обновлен");
    } catch (e) {
      if (requestId === productsRequestSeq.current && show) setMessage(`Ошибка товаров: ${e.message}`);
    }
  }

  async function openProduct(sku, rowPlatform = null) {
    if (!sku) return;
    setPage("quality");
    setMessage("Открываю карточку товара…");
    try {
      const effectivePlatform = normPlatform(rowPlatform || platform);
      const data = await api(`/ops/product/${encodeURIComponent(sku)}?platform=${effectivePlatform}`);
      if (platform !== "ALL" && effectivePlatform !== normPlatform(platform)) return;
      setProductCard(data);
      setMessage("Карточка товара открыта");
    } catch (e) {
      setMessage(`Ошибка карточки: ${e.message}`);
    }
  }

  function openProductCommunications(sku, targetKind = "reviews", rowPlatform = null) {
    if (!sku) return;
    const effectivePlatform = normPlatform(rowPlatform || platform);
    if (effectivePlatform && effectivePlatform !== "ALL") setPlatform(effectivePlatform);
    setPage("communications");
    setKind(targetKind === "questions" ? "questions" : "reviews");
    setState("all");
    setSearch(String(sku));
    setSelected(null);
    setDraft("");
    setMessage(targetKind === "questions" ? `Показаны вопросы по товару ${sku}` : `Показаны отзывы по товару ${sku}`);
  }

  function chooseItem(item) {
    setSelected(item);
    setDraft(item.final_answer || item.draft_answer || "");
  }

  async function generateSelected() {
    if (!selected?.id) return;
    setLoading(true); setMessage("Генерирую ответ 10/10…");
    try {
      const base = selected.kind === "question" ? "/questions" : "/reviews";
      const fresh = await api(`${base}/${selected.id}/generate`, { method: "POST" });
      chooseItem({ ...fresh, kind: selected.kind });
      await refreshAll(false);
      setMessage("Ответ сгенерирован");
    } catch (e) { setMessage(`Ошибка генерации: ${e.message}`); }
    finally { setLoading(false); }
  }

  async function saveSelected() {
    if (!selected?.id) return;
    setLoading(true); setMessage("Сохраняю ответ…");
    try {
      const base = selected.kind === "question" ? "/questions" : "/reviews";
      const fresh = await api(`${base}/${selected.id}/answer`, { method: "PATCH", body: JSON.stringify({ final_answer: draft }) });
      chooseItem({ ...fresh, kind: selected.kind });
      await refreshAll(false);
      setMessage("Ответ сохранен");
    } catch (e) { setMessage(`Ошибка сохранения: ${e.message}`); }
    finally { setLoading(false); }
  }

  async function publishSelected() {
    if (!selected?.id) return;
    setLoading(true); setMessage("Публикую / dry-run…");
    try {
      const base = selected.kind === "question" ? "/questions" : "/reviews";
      const res = await api(`${base}/${selected.id}/publish`, { method: "POST" });
      await refreshAll(false);
      setMessage(`Результат: ${pretty(res)}`);
    } catch (e) { setMessage(`Ошибка публикации: ${e.message}`); }
    finally { setLoading(false); }
  }

  function setRule(key, value) { setRules(prev => ({ ...prev, [key]: value })); }
  function setMatrix(mp, key, value) { setRules(prev => ({ ...prev, autopublish_matrix: { ...(prev.autopublish_matrix || {}), [mp]: { ...(prev.autopublish_matrix?.[mp] || {}), [key]: value } } })); }

  async function saveRules() {
    setLoading(true); setMessage("Сохраняю настройки…");
    try {
      const payload = { ...rules }; delete payload.updated_at;
      const data = await api("/settings/automation-rules", { method: "PUT", body: JSON.stringify(payload) });
      setRules(data || payload);
      setMessage("Настройки сохранены");
    } catch (e) { setMessage(`Ошибка настроек: ${e.message}`); }
    finally { setLoading(false); }
  }

  async function startBackgroundSync(kindName, label, mode = "full") {
  setLoading(true);
  setMessage(`Запускаю в фоне: ${label}`);
  try {
    const res = await api(`/sync-control/start?kind=${kindName}&platform=${platform}&mode=${mode}`, { method: "POST", timeoutMs: 15000 });
    if (res?.already_running) {
      setMessage(`${label}: уже выполняется в фоне. Данные не обнуляю, обновлю через несколько секунд.`);
    } else if (res?.started) {
      setMessage(`${label}: запущено в фоне. Можно не ждать — интерфейс не будет очищаться.`);
    } else {
      setMessage(`${label}: ${res?.error || "не запущено"}`);
    }
    setTimeout(() => { refreshAll(false); loadCustomerOps(false); }, 7000);
    setTimeout(() => { refreshAll(false); loadCustomerOps(false); }, 22000);
  } catch (e) {
    setMessage(`${label}: ошибка запуска фоновой задачи: ${e.message}. Последние данные сохранены на экране.`);
  } finally {
    setLoading(false);
  }
} async function run(path, label) {
  if (String(path || "").startsWith("/operations/sync")) {
    return startBackgroundSync("operations", label || "Синхронизация Operations Hub", "full");
  }
  setLoading(true);
  setMessage(`Запускаю: ${label}`);
  try {
    const res = await api(path, { method: "POST", timeoutMs: 30000 });
    await refreshAll(false);
    setMessage(`${label}: ${res?.message || "готово"}`);
  } catch (e) {
    setMessage(`Ошибка: ${e.message}. Последние данные сохранены на экране.`);
  } finally {
    setLoading(false);
  }
}

  
async function loadCustomerOps(show = true) {
  const requestedPlatform = normPlatform(platformRef.current || platform || "ALL");
  if (show) setMessage("Обновляю Customer Ops…");
  try {
    const [summaryRes, chatsRes, returnsRes, slaRes] = await Promise.allSettled([
      api(`/customer-ops/summary?platform=${requestedPlatform}`, { timeoutMs: 10000 }).catch(() => null),
      api(`/customer-ops/chats?platform=${requestedPlatform}&limit=10000`, { timeoutMs: 10000 }).catch(() => null),
      api(`/customer-ops/returns?platform=${requestedPlatform}&limit=10000`, { timeoutMs: 10000 }).catch(() => null),
      api(`/reports/chat-sla?platform=${requestedPlatform}&days=30`, { timeoutMs: 10000 }).catch(() => null),
    ]);
    if (summaryRes.status === "fulfilled" && summaryRes.value) setCustomerOpsSummary(prev => summaryRes.value || prev);
    if (chatsRes.status === "fulfilled" && chatsRes.value) setChats((chatsRes.value.items || []).filter(x => requestedPlatform === "ALL" || normPlatform(x.platform) === requestedPlatform));
    if (returnsRes.status === "fulfilled" && returnsRes.value) setReturnsList((returnsRes.value.items || []).filter(x => requestedPlatform === "ALL" || normPlatform(x.platform) === requestedPlatform));
    if (slaRes.status === "fulfilled" && slaRes.value) setChatSla(slaRes.value);
    if (show) setMessage("Customer Ops обновлен");
  } catch (e) { if (show) setMessage(`Ошибка Customer Ops: ${e.message}`); }
}
async function runCustomerOpsSync(mode = "full") {
  return startBackgroundSync("customer_ops", "Синхронизация Customer Ops", mode);
}
async function openChat(chat) {
  setSelectedChat(chat); setChatDraft("");
  try { const data = await api(`/customer-ops/chats/${chat.id}/messages?limit=1000000`, { timeoutMs: 60000 }); setSelectedChat(data.chat || chat); setChatMessages(data.items || []); }
  catch (e) { setMessage(`Ошибка истории чата: ${e.message}`); }
}
async function patchChat(id, payload) {
  try { const updated = await api(`/customer-ops/chats/${id}`, { method: "PATCH", body: JSON.stringify(payload) }); setChats(prev => prev.map(x => x.id === id ? { ...x, ...updated } : x)); if (selectedChat?.id === id) setSelectedChat(prev => ({ ...prev, ...updated })); }
  catch (e) { setMessage(`Ошибка статуса чата: ${e.message}`); }
}
async function sendChatReply() {
  if (!selectedChat?.id || !chatDraft.trim()) return;
  setLoading(true);
  try { const res = await api(`/customer-ops/chats/${selectedChat.id}/reply`, { method: "POST", body: JSON.stringify({ message: chatDraft }), timeoutMs: 20000 }); await openChat(selectedChat); await loadCustomerOps(false); setChatDraft(""); setMessage(res?.status === "sent" ? "Ответ отправлен в маркетплейс" : (res?.message || "Ответ сохранен")); }
  catch (e) { setMessage(`Ошибка отправки чата: ${e.message}`); } finally { setLoading(false); }
}
async function patchReturn(id, payload) {
  try { const updated = await api(`/customer-ops/returns/${id}`, { method: "PATCH", body: JSON.stringify(payload) }); setReturnsList(prev => prev.map(x => x.id === id ? { ...x, ...updated } : x)); }
  catch (e) { setMessage(`Ошибка статуса возврата: ${e.message}`); }
}
 async function saveBookingConfig(next) {
    setBooking(next);
    try {
      const data = await api("/wb-booking/config", { method: "POST", body: JSON.stringify(next) });
      setBooking(data);
      setMessage("Настройки Slot Hunter сохранены");
    } catch (e) { setMessage(`Ошибка Slot Hunter: ${e.message}`); }
  }

  function renderPlatformSwitch() {
    return <div className="marketSwitch">{PLATFORMS.map(p => <button key={p.id} className={platform === p.id ? "active" : ""} onClick={() => { setPlatform(p.id); setSelected(null); setDraft(""); }}>{p.title}</button>)}</div>;
  }

  function renderSidebar() {
    const groups = [...new Set(NAV.map(x => x.group))];
    return <aside>
      <h1>KARATOV<br/>CX Hub</h1>
      {renderPlatformSwitch()}
      {groups.map(group => <div key={group} className="navGroup"><div className="navGroupTitle">{group}</div>{NAV.filter(x => x.group === group).map(item => <button key={item.id} className={page === item.id ? "active" : ""} onClick={() => setPage(item.id)}>{item.title}{navCount(item.id)}</button>)}</div>)}
      <div className="syncMini">Обновлено: {dt(lastRefresh)}<br/>Режим публикации: {diagnostics?.publishing?.mode || "—"} · Аудит: /sync-audit/marketplace?platform={platform}<br/>{loading ? "Выполняется…" : "Готово"}</div>
    </aside>;
  }

  function navCount(id) {
    if (id === "communications") return <span className="navCount">{metrics.needs}</span>;
    if (id === "catalog") return <span className="navCount">{products.length}</span>;
    if (id === "quality") return <span className="navCount">{products.filter(x => x.high_risk || x.negative).length}</span>;
    if (id === "fbo" && booking?.enabled) return <span className="navCount greenDot">●</span>;
    return null;
  }

  function renderTower() {
    return <Section title="Control Tower" subtitle="Что требует внимания прямо сейчас" actions={<button className="primary" onClick={() => refreshAll(true)}>Обновить</button>}>
      <div className="cards wideCards">
        <Card title="Отзывы" value={num(metrics.reviews)} hint={platform === "ALL" ? "все площадки" : platform} onClick={() => { setPage("communications"); setKind("reviews"); }} />
        <Card title="Вопросы" value={num(metrics.questions)} hint="очередь операторов" onClick={() => { setPage("communications"); setKind("questions"); }} />
        <Card title="Требуют ответа" value={num(metrics.needs)} type={metrics.needs ? "warn" : ""} onClick={() => { setPage("communications"); setState("unanswered"); }} />
        <Card title="Черновики AI" value={num(metrics.drafts)} onClick={() => { setPage("communications"); setState("drafts"); }} />
        <Card title="Оценки без текста" value={num(metrics.noTextRatings)} hint="Ozon: без SLA и AI" onClick={() => { setPage("communications"); setKind("reviews"); setState("no_text"); }} />
        <Card title="Высокий риск" value={num(metrics.risks)} type={metrics.risks ? "danger" : ""} onClick={() => { setPage("quality"); }} />
        <Card title="Средний рейтинг" value={metrics.avgRating} />
      </div>
      <div className="layoutTwo">
        <div className="panel">
          <h3>AI Summary</h3>
          <p className="bigText">{insights.summary}</p>
          <h4>Рекомендации</h4>
          {insights.recs.map((x, i) => <div className="recommendation" key={i}>☝️ {x}</div>)}
        </div>
        <div className="panel">
          <h3>Операционная лента</h3>
          {buildEventFeed().slice(0, 10).map((e, i) => <div className="eventRow" key={i}><b>{e.title}</b><span>{e.text}</span><em>{dt(e.at)}</em></div>)}
        </div>
      </div>
      <div className="panel attentionPanel">
        <h3>Требует внимания</h3>
        {buildAttentionItems().map((x, i) => <div className={`attentionItem ${x.type || ""}`} key={i} onClick={x.onClick}>
          <b>{x.title}</b><span>{x.text}</span>
        </div>)}
      </div>
    </Section>;
  }

  function buildEventFeed() {
  const events = [];
  const sortedItems = platformItems
    .slice()
    .sort((a, b) => new Date((b.created_at_marketplace || b.created_at || b.updated_at) || 0) - new Date((a.created_at_marketplace || a.created_at || a.updated_at) || 0));
  sortedItems.slice(0, 12).forEach(x => events.push({
    at: x.created_at_marketplace || x.created_at || x.updated_at,
    title: x.kind === "review" ? "Новый отзыв" : "Новый вопрос",
    text: `${x.platform} · ${x.sku || x.product_name || "товар"}`
  }));
  (booking?.events || booking?.history || []).slice(0, 5).forEach(x => events.push({
    at: x.at,
    title: "Slot Hunter",
    text: x.message || x.event || x.kind || "событие"
  }));
  return events.sort((a, b) => new Date(b.at || 0) - new Date(a.at || 0));
}

  function buildAttentionItems() {
    const items = [];
    if (metrics.needs) items.push({ type: "danger", title: `${metrics.needs} коммуникаций требуют ответа`, text: "Открыть очередь операторов", onClick: () => { setPage("communications"); setState("unanswered"); } });
    if (metrics.risks) items.push({ type: "danger", title: `${metrics.risks} high-risk отзывов`, text: "Нужна ручная проверка и передача в Quality Hub", onClick: () => { setPage("quality"); } });
    if (metrics.noTextRatings) items.push({ type: "info", title: `${metrics.noTextRatings} Ozon оценок без текста`, text: "Не требуют ответа, не участвуют в SLA и не тратят AI", onClick: () => { setPage("communications"); setKind("reviews"); setState("no_text"); } });
    const risky = products.filter(x => x.high_risk || x.negative).length;
    if (risky) items.push({ type: "warn", title: `${risky} товаров требуют внимания`, text: "Проверить жалобы, рейтинг и AI-рекомендации", onClick: () => { setPage("quality"); } });
    if (!items.length) items.push({ type: "ok", title: "Критических событий нет", text: "Продолжаем мониторинг SLA, рейтинга, отзывов и слотов" });
    return items;
  }

  function renderCommunications() {
    return <Section title="Communications Center" subtitle="Отзывы, вопросы, AI-ответы, автопубликация и история" actions={<><button onClick={() => run("/autopublish", "Автопубликация")}>Автопубликация</button><button className="primary" onClick={() => refreshAll(true)}>Обновить</button></>}>
      <div className="tabs"><button className={kind === "reviews" ? "active" : ""} onClick={() => setKind("reviews")}>Отзывы · {visibleReviews.length}</button><button className={kind === "questions" ? "active" : ""} onClick={() => setKind("questions")}>Вопросы · {visibleQuestions.length}</button><button onClick={() => setKind("history")} className={kind === "history" ? "active" : ""}>История публикаций</button></div>
      {kind === "history" ? renderPublishHistory() : <><Filters/><div className="workspace"><div className="list">{activeList.length ? activeList.map(item => renderCommRow(item)) : <Empty>Нет записей по выбранным фильтрам</Empty>}</div>{renderDetail()}</div></>}
    </Section>;
  }

  function Filters() { return <div className="sectionFilters"><div><label>Состояние</label><select value={state} onChange={e => setState(e.target.value)}>{ANSWER_STATES.map(([v,t]) => <option value={v} key={v}>{t}</option>)}</select></div><div className="grow"><label>Поиск</label><input value={search} onChange={e => setSearch(e.target.value)} placeholder="SKU, товар, текст, категория" /></div></div>; }

  function renderCommRow(item) {
    return <div className={`row ${selected?.id === item.id && selected?.kind === item.kind ? "selected" : ""}`} key={`${item.kind}-${item.id}`} onClick={() => chooseItem(item)}>
      <div className="rowhead"><b>{item.product_name || item.sku || `Запись ${item.id}`}</b><PlatformBadge value={item.platform}/></div>
      <div className="dateMeta">{item.rating && <span>⭐ {item.rating}</span>}<span>{dt(item.created_at_marketplace)}</span><span>{item.source_status}</span></div>
      <div className="text">{isNoTextRating(item) ? "Оценка без комментария. Ответ на Ozon невозможен." : (item.text || item.pros || item.cons || "Без текста")}</div>
      <div className="tags"><Badge>{isNoTextRating(item) ? "без комментария" : (item.status || "new")}</Badge>{item.ai_category && <Badge type="yellow">{item.ai_category}</Badge>}{item.ai_risk_level && <Badge type={item.ai_risk_level === "high" ? "red" : ""}>{item.ai_risk_level}</Badge>}{isNoTextRating(item) && <Badge type="yellow">не требует ответа</Badge>}{(item.final_answer || item.draft_answer) && !isNoTextRating(item) && <Badge type="green">ответ готов</Badge>}</div>
    </div>;
  }

  function renderDetail() {
    if (!selected) return <div className="detail"><Empty>Выбери запись слева</Empty></div>;
    const url = productUrl(selected);
    return <div className="detail"><div className="detailhead"><div><h3>{selected.product_name || selected.sku || "Карточка"}</h3><p className="meta">{selected.kind === "question" ? "Вопрос" : "Отзыв"} · {selected.platform} · {selected.source_status}</p></div><div className="actions">{selected.sku && <button onClick={() => openProduct(selected.sku, selected.platform)}>Карточка товара</button>}{url && <a className="buttonLike" href={url} target="_blank" rel="noreferrer">Открыть на площадке</a>}</div></div>
      {isNoTextRating(selected) && <div className="noticeBox">Ozon не позволяет отвечать на оценки без текста. Эта запись видна для аналитики рейтинга, но не участвует в SLA, AI-генерации и автопубликации.</div>}
      <div className="clientText">{isNoTextRating(selected) ? "Оценка без комментария" : (selected.text || selected.pros || selected.cons || "Нет текста")}</div>
      <div className="twoCols"><div className="exampleBox"><b>AI-категория</b><p>{selected.ai_category || "—"}</p></div><div className="exampleBox"><b>Причина / quality gate</b><p>{selected.ai_reason || selected.publish_blocked_reason || "—"}</p></div></div>
      <label>Финальный ответ</label><textarea value={draft} onChange={e => setDraft(e.target.value)} placeholder="Сгенерируй или введи ответ" />
      <div className="actions"><button className="primary" disabled={isNoTextRating(selected)} onClick={generateSelected}>Сгенерировать 10/10</button><button disabled={isNoTextRating(selected)} onClick={saveSelected}>Сохранить</button><button disabled={isNoTextRating(selected)} onClick={publishSelected}>Опубликовать</button><button onClick={() => navigator.clipboard.writeText(draft || "")}>Скопировать</button></div>
    </div>;
  }

  function renderPublishHistory() {
    const items = publishHistory?.items || [];
    return <div className="panel"><h3>История публикаций и готовых ответов</h3><table><thead><tr><th>Дата</th><th>Площадка</th><th>Товар</th><th>Статус</th><th>Ответ</th></tr></thead><tbody>{items.slice(0,120).map((x,i)=><tr key={i}><td>{dt(x.updated_at || x.created_at)}</td><td>{x.platform}</td><td>{x.sku || x.product_name}</td><td>{x.status || "—"}</td><td>{x.final_answer || "—"}</td></tr>)}</tbody></table></div>;
  }

  function renderCatalog() {
    return <Section title="Product Catalog Hub" subtitle="Единый каталог товаров WB/Ozon/ЯМ: рейтинг, отзывы, вопросы и ссылки на карточки" actions={<button className="primary" onClick={() => loadProducts(true, platform)}>Обновить каталог</button>}>
      <div className="cards wideCards"><Card title="Товаров" value={num(products.length)} /><Card title="С рейтингом" value={num(products.filter(x => x.avg_rating || x.latest_rating).length)} /><Card title="С отзывами" value={num(products.filter(x => x.reviews).length)} /><Card title="С вопросами" value={num(products.filter(x => x.questions).length)} /><Card title="High risk" value={num(products.filter(x => x.high_risk).length)} type="danger"/><Card title="С негативом" value={num(products.filter(x => x.negative).length)} type="warn"/></div>
      <div className="panel"><table><thead><tr><th>SKU</th><th>Название</th><th>Площадки</th><th>Рейтинг</th><th>Отзывы</th><th>Вопросы</th><th>Риск</th><th>Ссылка</th></tr></thead><tbody>{products.map(row => <tr key={row.key}><td><button className="linkBtn" onClick={() => openProduct(row.sku || row.key, rowEffectivePlatform(row, platform))}>{row.sku || row.key}</button></td><td>{row.product_name || "—"}</td><td>{(row.platforms || []).join(", ")}</td><td>{row.avg_rating || row.latest_rating || "—"}</td><td>{Number(row.reviews || row.feedbacks_count || 0) ? <button className="linkBtn" onClick={() => openProductCommunications(row.sku || row.key, "reviews", rowEffectivePlatform(row, platform))}>{row.reviews || row.feedbacks_count || 0}</button> : 0}</td><td>{Number(row.questions || 0) ? <button className="linkBtn" onClick={() => openProductCommunications(row.sku || row.key, "questions", rowEffectivePlatform(row, platform))}>{row.questions || 0}</button> : 0}</td><td>{row.high_risk ? <Badge type="red">high</Badge> : row.negative ? <Badge type="yellow">attention</Badge> : <Badge type="green">ok</Badge>}</td><td>{row.product_url && <a href={row.product_url} target="_blank" rel="noreferrer">Открыть</a>}</td></tr>)}</tbody></table></div>
    </Section>;
  }

  function renderQuality() {
    return <Section title="Quality Hub" subtitle="Товары, жалобы, аномалии, AI Summary и рекомендации" actions={<button className="primary" onClick={() => loadProducts(true, platform)}>Обновить товары</button>}>
      <div className="cards wideCards"><Card title="Товаров" value={num(products.length)} /><Card title="High risk" value={num(products.filter(x => x.high_risk).length)} type="danger"/><Card title="С негативом" value={num(products.filter(x => x.negative).length)} type="warn"/><Card title="Основная тема" value={insights.categories[0]?.[0] || "—"}/></div>
      <div className="layoutTwo"><div className="panel"><h3>AI Summary по товарам</h3><p className="bigText">{insights.summary}</p>{insights.riskyProducts.map(x => <div className="recommendation clickable" key={x.key} onClick={() => openProduct(x.sku || x.key, rowEffectivePlatform(x, platform))}><b>{x.sku || x.key}</b><span>{x.recommendation}</span></div>)}</div><div className="panel"><h3>Тематики</h3>{insights.categories.length ? insights.categories.map(([k,v]) => <div className="metricRow" key={k}><span>{k}</span><b>{v}</b></div>) : <Empty/>}</div></div>
      <div className="layoutTwo"><div className="panel"><h3>Список товаров</h3><table><thead><tr><th>Товар</th><th>Площадки</th><th>Отзывы</th><th>Вопросы</th><th>Риск</th><th></th></tr></thead><tbody>{products.map(row => <tr key={row.key}><td><button className="linkBtn" onClick={() => openProduct(row.sku || row.key, rowEffectivePlatform(row, platform))}>{row.sku || row.key}</button><br/><small>{row.product_name}</small></td><td>{(row.platforms || []).join(", ")}</td><td>{Number(row.reviews || 0) ? <button className="linkBtn" onClick={() => openProductCommunications(row.sku || row.key, "reviews", rowEffectivePlatform(row, platform))}>{row.reviews}</button> : 0}</td><td>{Number(row.questions || 0) ? <button className="linkBtn" onClick={() => openProductCommunications(row.sku || row.key, "questions", rowEffectivePlatform(row, platform))}>{row.questions}</button> : 0}</td><td>{row.high_risk ? <Badge type="red">high</Badge> : row.negative ? <Badge type="yellow">attention</Badge> : <Badge type="green">ok</Badge>}</td><td>{row.product_url && <a href={row.product_url} target="_blank" rel="noreferrer">Открыть</a>}</td></tr>)}</tbody></table></div>{renderProductCard()}</div>
    </Section>;
  }

  function renderProductCard() {
    if (!productCard) return <div className="panel"><h3>Карточка товара</h3><Empty>Выбери SKU в списке или из AI Summary</Empty></div>;
    const s = productCard.summary || {};
    const cats = Object.entries(s.categories || {});
    return <div className="panel"><div className="detailhead"><div><h3>{productCard.sku}</h3><p className="meta">{productCard.product_name || "Товар"}</p></div>{productCard.product_url && <a className="buttonLike" href={productCard.product_url} target="_blank" rel="noreferrer">Открыть на маркетплейсе</a>}</div>
      <div className="cards mini"><Card title="Отзывы" value={s.reviews_total || 0} onClick={() => openProductCommunications(productCard.sku, "reviews", productCard.platform || platform)}/><Card title="Вопросы" value={s.questions_total || 0} onClick={() => openProductCommunications(productCard.sku, "questions", productCard.platform || platform)}/><Card title="High risk" value={s.high_risk || 0} type={s.high_risk ? "danger" : ""}/></div>
      <h4>Тематики</h4>{cats.length ? cats.map(([k,v]) => <div className="metricRow" key={k}><span>{k}</span><b>{v}</b></div>) : <Empty/>}
      <h4>Последние отзывы</h4>{(productCard.reviews || []).slice(0,5).map(x => <div className="miniItem" key={`r-${x.id}`}><b>{x.rating ? `⭐ ${x.rating}` : "Отзыв"}</b><span>{x.text || "Без текста"}</span></div>)}
      <h4>Последние вопросы</h4>{(productCard.questions || []).slice(0,5).map(x => <div className="miniItem" key={`q-${x.id}`}><b>Вопрос</b><span>{x.text || "Без текста"}</span></div>)}
    </div>;
  }

  function renderOperations() {
  const labels = {
    return_request: "Заявки на возврат",
    return_received: "Полученные возвраты",
    return: "Возвраты",
    act: "Акты",
    shortage: "Недостачи",
    surplus: "Излишки",
    anonymization: "Обезличка",
    anonymized_item: "Обезличка",
    discrepancy: "Расхождения",
    finance_discrepancy: "Финансовые расхождения",
    acceptance_issue: "Приемка",
    shipment_issue: "Отгрузка",
    supply_issue: "Поставка",
    chat_escalation: "Эскалации из чатов",
  };
  const byType = operationsSummary?.by_type || {};
  const byStatus = operationsSummary?.by_status || {};
  const sla = chatSla || customerOpsSummary?.chat_sla || operationsSummary?.chat_sla || {};
  const rawErrors = customerOpsSummary?.raw_errors || [];
  return <Section title="Customer Ops + Operations Hub" subtitle="Чаты покупателей, заявки на возврат, акты, недостачи, излишки, обезличка и рабочие статусы операторов" actions={<><button onClick={() => runCustomerOpsSync("full")}>Синхронизировать Customer Ops</button><button onClick={() => run(`/operations/sync?platform=${platform}`, "Синхронизация Operations Hub")}>Синхронизировать операции</button><button className="primary" onClick={() => { refreshAll(true); loadCustomerOps(true); }}>Обновить</button></>}>
    <div className="cards wideCards">
      <Card title="Операций" value={num(operationsSummary?.total || operationsSummary?.total_operations || operations.length)} />
      <Card title="Активные возвраты" value={num(customerOpsSummary?.returns_active || returnsList.filter(x => !["closed","resolved"].includes(x.internal_status)).length)} type={(customerOpsSummary?.returns_active || 0) ? "warn" : ""} onClick={() => setCustomerOpsTab("returns")} />
      <Card title="Заявок на возврат" value={num(customerOpsSummary?.returns_total || returnsList.length)} onClick={() => setCustomerOpsTab("returns")} />
      <Card title="Чаты без ответа" value={num(customerOpsSummary?.chats_unanswered || chats.filter(x => x.needs_response).length)} type={(customerOpsSummary?.chats_unanswered || 0) ? "danger" : ""} onClick={() => setCustomerOpsTab("chats")} />
      <Card title="Средний ответ в чатах" value={fmtMinutes(sla.avg_first_response_minutes)} hint="SLA ≤ 10 мин" />
      <Card title="Просрочка чатов" value={num(sla.overdue_chats_count || 0)} type={sla.overdue_chats_count ? "danger" : ""} />
    </div>

    <div className="tabs opsTabs">
      <button className={customerOpsTab === "chats" ? "active" : ""} onClick={() => { setCustomerOpsTab("chats"); loadCustomerOps(false); }}>Чаты ({chats.filter(x => platform === "ALL" || normPlatform(x.platform) === platform).length})</button>
      <button className={customerOpsTab === "returns" ? "active" : ""} onClick={() => { setCustomerOpsTab("returns"); loadCustomerOps(false); }}>Заявки на возврат ({returnsList.filter(x => platform === "ALL" || normPlatform(x.platform) === platform).length})</button>
      <button className={customerOpsTab === "operations" ? "active" : ""} onClick={() => setCustomerOpsTab("operations")}>Операции ({operations.length})</button>
      <button className={customerOpsTab === "diagnostics" ? "active" : ""} onClick={() => setCustomerOpsTab("diagnostics")}>Диагностика API</button>
    </div>

    {customerOpsTab === "chats" && <div className="layoutTwo"><div className="panel"><h3>Чаты покупателей</h3><p className="meta">История должна включать ответы из ЛК продавца после синхронизации Customer Ops.</p>{chats.length ? chats.map(chat => <div className="miniItem clickable" key={chat.id} onClick={() => openChat(chat)}><b>{chat.platform} · {chat.product_name || chat.sku || chat.external_chat_id}</b><span>{chat.needs_response ? "требует ответа" : (chat.response_sla_status || "—")} · последнее: {dt(chat.last_message_at || chat.updated_at)}</span><small>{chat.internal_status || "new"} · {chat.assigned_to || "без ответственного"}</small></div>) : <Empty>Чаты пока не загружены. Нажми «Синхронизировать Customer Ops». Если после успешного Action тут пусто, открой «Диагностика API»: там будет ошибка прав/endpoint, без демо-строк.</Empty>}</div><div className="panel stickyPanel"><h3>Карточка чата</h3>{selectedChat ? <><div className="detailhead"><div><b>{selectedChat.platform} · {selectedChat.external_chat_id}</b><p className="meta">{selectedChat.product_name || selectedChat.sku || "Без товара"} · SLA: {selectedChat.response_sla_status || "—"} · {fmtMinutes(selectedChat.response_minutes)}</p></div><select value={selectedChat.internal_status || "new"} onChange={e => patchChat(selectedChat.id, { internal_status: e.target.value })}><option value="new">Новый</option><option value="in_progress">В работе</option><option value="waiting_customer">Ждем клиента</option><option value="waiting_marketplace">Ждем МП</option><option value="waiting_warehouse">Ждем склад</option><option value="closed">Закрыто</option></select></div><div className="chatBox">{chatMessages.length ? chatMessages.map(m => <div className={`chatMsg ${m.direction === "seller" ? "seller" : "customer"}`} key={m.id}><b>{m.direction === "seller" ? "Продавец/оператор" : "Клиент"}</b><p>{m.text || "[текст не распознан — обнови Customer Ops после v2.1]"}</p><em>{dt(m.sent_at || m.created_at)}</em></div>) : <Empty>История сообщений пока не загружена по этому чату</Empty>}</div><label>Ответ</label><textarea value={chatDraft} onChange={e => setChatDraft(e.target.value)} placeholder="Введите ответ клиенту" /><div className="actions"><button className="primary" onClick={sendChatReply}>Отправить / сохранить</button><button onClick={() => navigator.clipboard.writeText(chatDraft || "")}>Скопировать</button></div><label>Комментарий оператора</label><textarea value={selectedChat.operator_comment || ""} onChange={e => setSelectedChat(prev => ({ ...prev, operator_comment: e.target.value }))} onBlur={e => patchChat(selectedChat.id, { operator_comment: e.target.value })}/></> : <Empty>Выбери чат слева</Empty>}</div></div>}

    {customerOpsTab === "returns" && <div className="panel"><h3>Заявки на возврат</h3>{returnsList.length ? <table><thead><tr><th>Дата</th><th>Площадка</th><th>Возврат</th><th>Заказ/отправление</th><th>SKU</th><th>Причина</th><th>Статус МП</th><th>Наш статус</th><th>Комментарий</th></tr></thead><tbody>{returnsList.map(r => <tr key={r.id}><td>{dt(r.created_at_marketplace || r.created_at)}</td><td><PlatformBadge value={r.platform}/></td><td>{r.external_return_id}</td><td>{r.posting_number || r.order_id || "—"}</td><td>{r.sku || "—"}</td><td>{r.reason || "—"}</td><td>{r.marketplace_status || "—"}</td><td><select value={r.internal_status || "new"} onChange={e => patchReturn(r.id, { internal_status: e.target.value })}><option value="new">Новая</option><option value="in_progress">В работе</option><option value="waiting_marketplace">Ждем МП</option><option value="waiting_customer">Ждем клиента</option><option value="waiting_warehouse">Ждем склад</option><option value="resolved">Решено</option><option value="closed">Закрыто</option></select></td><td><input value={r.operator_comment || ""} onChange={e => setReturnsList(prev => prev.map(x => x.id === r.id ? { ...x, operator_comment: e.target.value } : x))} onBlur={e => patchReturn(r.id, { operator_comment: e.target.value })}/></td></tr>)}</tbody></table> : <Empty>Заявок на возврат пока нет. Нажми «Синхронизировать Customer Ops». Если API недоступен токену, причина появится во вкладке «Диагностика API».</Empty>}</div>}

    {customerOpsTab === "operations" && <div className="panel"><h3>Реестр операций</h3><div className="sectionFilters"><div><label>Тип операции</label><select value={operationType} onChange={e => setOperationType(e.target.value)}><option value="all">Все</option>{Object.entries(labels).map(([k,v]) => <option key={k} value={k}>{v}</option>)}</select></div><div className="grow"><label>Статусы</label><div className="tags"><Badge>Синхр.: {byStatus.synced || 0}</Badge><Badge>Новые: {byStatus.new || 0}</Badge><Badge type="yellow">В работе: {byStatus.in_progress || 0}</Badge><Badge type="green">Закрыто: {byStatus.closed || 0}</Badge></div></div></div>{operationsSummary?.api_status?.message && <div className="message soft">{operationsSummary.api_status.message}</div>}{operations.length ? <table><thead><tr><th>Дата</th><th>Площадка</th><th>Тип</th><th>Документ</th><th>SKU</th><th>Кол-во</th><th>Сумма</th><th>Статус МП</th><th>Наш статус</th><th>Ответственный</th></tr></thead><tbody>{operations.map(x => <tr key={x.id}><td>{dt(x.occurred_at || x.created_at)}</td><td><PlatformBadge value={x.platform}/></td><td>{labels[x.operation_type] || x.operation_type}</td><td>{x.document_number || x.external_id}</td><td>{x.sku || "—"}</td><td>{x.quantity ?? "—"}</td><td>{x.amount || "—"}</td><td>{x.marketplace_status || "—"}</td><td><select value={x.cx_workflow_status || x.status || "new_to_review"} onChange={async e => { await api(`/operations/${x.id}`, { method: "PATCH", body: JSON.stringify({ cx_workflow_status: e.target.value }) }); await refreshAll(false); }}><option value="new_to_review">Новая</option><option value="in_progress">В работе</option><option value="waiting_marketplace">Ждем МП</option><option value="waiting_customer">Ждем клиента</option><option value="waiting_warehouse">Ждем склад</option><option value="resolved">Решено</option><option value="closed">Закрыто</option></select></td><td>{x.responsible || "—"}</td></tr>)}</tbody></table> : <Empty>Данных по операциям пока нет. Запусти синхронизацию операций или проверь диагностику API.</Empty>}</div>}

    {customerOpsTab === "diagnostics" && <div className="panel"><h3>Диагностика API</h3><p className="meta">Если Action зеленый, но данных нет, причина должна быть здесь: нет прав токена, endpoint недоступен, маркетплейс вернул 403/404/429 или пустой ответ.</p>{rawErrors.length ? <table><thead><tr><th>Дата</th><th>Площадка</th><th>Блок</th><th>Ошибка</th></tr></thead><tbody>{rawErrors.map((e,i) => <tr key={i}><td>{dt(e.created_at)}</td><td>{e.platform}</td><td>{e.block}</td><td>{String(e.error || "—").slice(0, 260)}</td></tr>)}</tbody></table> : <Empty>Ошибок API в последних raw events нет. Если и данных нет, запусти Customer Ops Sync и обнови страницу.</Empty>}</div>}
  </Section>;
}  function renderFbo() {
    const b = booking || {};
    const update = (key, value) => setBooking(prev => ({ ...(prev || {}), [key]: value }));
    return <Section title="FBO Control Center" subtitle="Slot Hunter PRO: расписание, коэффициенты, уведомления и история" actions={<><button onClick={() => run("/wb-booking/check", "Проверка Slot Hunter")}>Проверить сейчас</button><button onClick={() => run("/wb-booking/notify-test", "Тест Telegram")}>Тест Telegram</button><button className="primary" onClick={() => saveBookingConfig(b)}>Сохранить</button></>}>
      <div className="layoutTwo"><div className="panel"><h3>Расписание поиска слотов</h3><label>Склады</label><input value={(b.warehouses || []).join(", ")} onChange={e => update("warehouses", e.target.value.split(",").map(x=>x.trim()).filter(Boolean))}/><label>Тип поставки</label><input value={b.supply_type || "Суперсейф"} onChange={e => update("supply_type", e.target.value)}/><label>Максимальный коэффициент</label><input inputMode="numeric" value={b.coefficient_limit ?? ""} onChange={e => update("coefficient_limit", e.target.value === "" ? "" : Number(e.target.value))}/><label>Стартовая дата</label><input type="date" value={(b.start_date || "").slice(0,10)} onChange={e => update("start_date", e.target.value)}/><label>Каждые N рабочих дней</label><input inputMode="numeric" value={b.every_n_workdays ?? b.interval_workdays ?? ""} onChange={e => update("every_n_workdays", e.target.value === "" ? "" : Number(e.target.value))}/><label>Горизонт поиска, дней</label><input inputMode="numeric" value={b.horizon_days ?? ""} onChange={e => update("horizon_days", e.target.value === "" ? "" : Number(e.target.value))}/></div>
      <div className="panel"><h3>Режим и уведомления</h3><label>Режим</label><select value={b.mode || "auto_book"} onChange={e => { const nextMode = e.target.value; update("mode", nextMode); if ((b.work_time_mode || "auto") === "auto") update("work_time_mode", "auto"); }}><option value="monitor_only">Только мониторинг</option><option value="notify_only">Найти и уведомить</option><option value="auto_book">Автобронь + уведомление</option></select><label>Время работы</label><select value={b.work_time_mode || "auto"} onChange={e => update("work_time_mode", e.target.value)}><option value="auto">Авто: автобронь 24/7, остальные режимы 09:00–21:00</option><option value="business_hours">Только рабочее окно</option><option value="24_7">Круглосуточно</option></select><div className="twoCols"><div><label>С</label><input type="time" value={b.work_time_from || "09:00"} onChange={e => update("work_time_from", e.target.value)}/></div><div><label>До</label><input type="time" value={b.work_time_to || "21:00"} onChange={e => update("work_time_to", e.target.value)}/></div></div><p className="meta">Сейчас: {b.runtime_label || (b.work_time_mode === "24_7" ? "круглосуточно" : `${b.work_time_from || "09:00"}–${b.work_time_to || "21:00"}`)}. Для полной автоброни рекомендуется 24/7, чтобы не пропускать ночные окна.</p><label className="check"><input type="checkbox" checked={!!b.telegram_enabled} onChange={e => update("telegram_enabled", e.target.checked)}/> Telegram-уведомления</label><p className="meta">Chat ID не вводим вручную: используем подключенную группу/бота из окружения или ранее сохраненной настройки.</p><label className="check"><input type="checkbox" checked={!!b.email_enabled} onChange={e => update("email_enabled", e.target.checked)}/> Email-уведомления</label><label>Email получатели</label><textarea value={(b.email_recipients || []).join("\n")} onChange={e => update("email_recipients", e.target.value.split("\n").map(x=>x.trim()).filter(Boolean))}/><button onClick={() => run(b.enabled ? "/wb-booking/stop" : "/wb-booking/start", b.enabled ? "Остановить Slot Hunter" : "Включить Slot Hunter")}>{b.enabled ? "Остановить" : "Включить"}</button></div></div>
      <div className="layoutTwo"><div className="panel"><h3>Плановые даты</h3><div className="dateGrid">{(b.planned_dates || b.target_dates || []).slice(0,30).map(x => <span key={x}>{x}</span>)}</div></div><div className="panel"><h3>История Slot Hunter</h3>{(b.events || b.history || []).slice(0,20).map((e,i)=><div className="eventRow" key={i}><b>{e.kind || e.event}</b><span>{e.message || e.status || "событие"}</span><em>{dt(e.at)}</em></div>)}</div></div>
    </Section>;
  }

  function renderAnalytics() {
    const response = buildResponseAnalytics(platformItems);
    const byDay = groupCount(platformItems, x => day(x.created_at_marketplace)).slice(0, 14);
    const byWeek = groupCount(platformItems, x => week(x.created_at_marketplace)).slice(0, 12);
    const byMonth = groupCount(platformItems, x => month(x.created_at_marketplace)).slice(0, 12);
    const reviewByDay = groupCount(platformItems.filter(x => x.kind === "review"), x => day(x.created_at_marketplace)).slice(0, 14);
    const questionByDay = groupCount(platformItems.filter(x => x.kind === "question"), x => day(x.created_at_marketplace)).slice(0, 14);
    return <Section title="Аналитика" subtitle="Executive Dashboard, SLA скорости ответа, динамика отзывов/вопросов и AI Insights" actions={<button className="primary" onClick={() => refreshAll(true)}>Обновить</button>}>
      <div className="cards wideCards"><Card title="Чаты: средний ответ" value={fmtMinutes(chatSla?.avg_first_response_minutes)} hint="SLA ≤ 10 мин"/><Card title="Чаты без ответа" value={num(chatSla?.unanswered_chats_count || 0)} type={chatSla?.unanswered_chats_count ? "danger" : ""}/><Card title="Просрочка чатов" value={num(chatSla?.overdue_chats_count || 0)} type={chatSla?.overdue_chats_count ? "danger" : ""}/><Card title="Все коммуникации" value={num(metrics.total)}/><Card title="Требуют ответа" value={num(metrics.needs)} type={metrics.needs ? "warn" : ""}/><Card title="Оценки без текста" value={num(metrics.noTextRatings)} /><Card title="Риски" value={num(metrics.risks)} type={metrics.risks ? "danger" : ""}/><Card title="WB" value={num(allMetrics.wb)}/><Card title="Ozon" value={num(allMetrics.ozon)}/></div>
      <div className="panel"><h3>SLA скорости ответа</h3><div className="cards wideCards"><Card title="Отзывы ≤ 1 часа" value={num(response.reviewSla.inSla)} hint={`${response.reviewSlaPct}% из измеренных`} type={response.reviewSla.outSla ? "warn" : ""}/><Card title="Отзывы > 1 часа" value={num(response.reviewSla.outSla)} type={response.reviewSla.outSla ? "danger" : ""}/><Card title="Вопросы ≤ 15 минут" value={num(response.questionSla.inSla)} hint={`${response.questionSlaPct}% из измеренных`} type={response.questionSla.outSla ? "warn" : ""}/><Card title="Вопросы > 15 минут" value={num(response.questionSla.outSla)} type={response.questionSla.outSla ? "danger" : ""}/><Card title="Среднее время ответа" value={fmtMinutes(response.reviewSla.avgMin)} hint="по отзывам"/><Card title="P90 ответа" value={fmtMinutes(response.reviewSla.p90)} hint="по отзывам"/></div><p className="meta">Ozon-оценки без комментария исключены из SLA, AI и автопубликации. SLA считается только по answered_at из ЛК маркетплейса или публикации CX Hub. Записи без даты ответа не искажают среднее и P90.</p></div>
      <div className="layoutTwo"><div className="panel"><h3>Динамика день к дню</h3><table><tbody>{byDay.map(([k,v]) => <tr key={k}><td>{k}</td><td>{v}</td></tr>)}</tbody></table></div><div className="panel"><h3>Динамика неделя к неделе</h3><table><tbody>{byWeek.map(([k,v]) => <tr key={k}><td>{k}</td><td>{v}</td></tr>)}</tbody></table></div></div>
      <div className="layoutTwo"><div className="panel"><h3>Отзывы по дням</h3><table><tbody>{reviewByDay.map(([k,v]) => <tr key={k}><td>{k}</td><td>{v}</td></tr>)}</tbody></table></div><div className="panel"><h3>Вопросы по дням</h3><table><tbody>{questionByDay.map(([k,v]) => <tr key={k}><td>{k}</td><td>{v}</td></tr>)}</tbody></table></div></div>
      <div className="layoutTwo"><div className="panel"><h3>Динамика месяц к месяцу</h3><table><tbody>{byMonth.map(([k,v]) => <tr key={k}><td>{k}</td><td>{v}</td></tr>)}</tbody></table></div><div className="panel"><h3>Статус очереди</h3><div className="metricRow"><span>Готовые черновики</span><b>{num(metrics.drafts)}</b></div><div className="metricRow"><span>Требуют ответа</span><b>{num(response.pending)}</b></div><div className="metricRow"><span>Отвеченные / с ответом</span><b>{num(response.answeredTotal)}</b></div><div className="metricRow"><span>Без комментария</span><b>{num(response.noText)}</b></div></div></div>
      <div className="panel"><h3>История синхронизаций</h3>{renderSyncHistory()}</div>
    </Section>;
  }

  function renderSyncHistory() {
    const wb = syncHistory?.wb || {};
    const blocks = wb.blocks_state || {};
    const rows = Object.entries(blocks).map(([name, row]) => ({ name, ...row }));
    return rows.length ? <table><thead><tr><th>Блок</th><th>Статус</th><th>Получено</th><th>Последний успех</th><th>Следующая попытка</th><th>Ошибка</th></tr></thead><tbody>{rows.map(r => <tr key={r.name}><td>{r.name}</td><td>{r.status}</td><td>{r.last_received || 0}</td><td>{dt(r.last_success_at)}</td><td>{dt(r.next_retry_at)}</td><td>{r.last_error ? String(r.last_error).slice(0,120) : "—"}</td></tr>)}</tbody></table> : <Empty>Истории пока нет</Empty>;
  }

  function renderSettings() {
    return <Section title="Настройки" subtitle="Маркетплейсы, AI, автопубликация, интеграции и уведомления" actions={<button className="primary" onClick={saveRules}>Сохранить</button>}>
      <div className="settingsPanel"><div className="panel"><h3>Маркетплейсы</h3><div className="metricRow"><span>WB API</span><b>{bool(diagnostics?.keys?.wb_api_key || diagnostics?.keys?.wb_api_token)}</b></div><div className="metricRow"><span>Ozon Client ID</span><b>{bool(diagnostics?.keys?.ozon_client_id)}</b></div><div className="metricRow"><span>Ozon API Key</span><b>{bool(diagnostics?.keys?.ozon_api_key)}</b></div><div className="metricRow"><span>Публикация</span><b>{diagnostics?.publishing?.mode || "—"}</b></div></div>
      <div className="panel"><h3>AI и fallback</h3><label className="check"><input type="checkbox" checked={!!rules.ai_generation_enabled} onChange={e => setRule("ai_generation_enabled", e.target.checked)}/> Генерация AI</label><label className="check"><input type="checkbox" checked={!!rules.ai_fallback_to_local_templates} onChange={e => setRule("ai_fallback_to_local_templates", e.target.checked)}/> Fallback на шаблоны</label><label>Quality gate</label><input value={rules.quality_gate_min_score || 10} onChange={e => setRule("quality_gate_min_score", Number(e.target.value) || 10)}/></div>
      <div className="panel"><h3>Автопубликация</h3>{["WB","OZON","YM"].map(mp => <div key={mp} className="matrixCard"><b>{mp}</b><label className="check"><input type="checkbox" checked={!!rules.autopublish_matrix?.[mp]?.reviews} onChange={e => setMatrix(mp,"reviews",e.target.checked)}/> Отзывы</label><label className="check"><input type="checkbox" checked={!!rules.autopublish_matrix?.[mp]?.questions} onChange={e => setMatrix(mp,"questions",e.target.checked)}/> Вопросы</label></div>)}</div>
      <div className="panel wide"><h3>Промт KARATOV</h3><textarea className="templateText" value={rules.custom_system_prompt || ""} onChange={e => setRule("custom_system_prompt", e.target.value)}/></div><div className="panel wide"><h3>Локальные шаблоны</h3><textarea className="templateText" value={rules.local_templates_text || ""} onChange={e => setRule("local_templates_text", e.target.value)}/></div></div>
    </Section>;
  }

  function renderSecurity() {
    const roles = Object.keys(roleRights);
    const rights = [
      ["view", "Просмотр"],
      ["edit", "Редактирование"],
      ["publish", "Публикация"],
      ["admin", "Администрирование"],
    ];

    function toggleRight(role, moduleId, right) {
      const next = { ...roleRights, [role]: { ...(roleRights[role] || {}) } };
      next[role][moduleId] = { ...(next[role][moduleId] || {}) };
      next[role][moduleId][right] = !next[role][moduleId][right];
      setRoleRights(next);
      localStorage.setItem("karatov_role_rights", JSON.stringify(next));
      setMessage("Матрица прав сохранена локально. Backend-аудит и авторизация подключаются следующим пакетом.");
    }

    return <Section title="Пользователи и роли" subtitle="Матрица доступа по модулям: просмотр, редактирование, публикация, администрирование" actions={<button onClick={() => { setRoleRights(DEFAULT_ROLE_RIGHTS); localStorage.setItem("karatov_role_rights", JSON.stringify(DEFAULT_ROLE_RIGHTS)); }}>Сбросить к стандарту</button>}>
      <div className="panel"><h3>Роли</h3><p className="bigText">Администратор или руководитель сможет управлять доступом к модулям. Сейчас матрица работает на уровне интерфейса и готова к подключению backend-авторизации.</p></div>
      <div className="panel wide"><table><thead><tr><th>Роль</th><th>Модуль</th>{rights.map(([id, title]) => <th key={id}>{title}</th>)}</tr></thead><tbody>{roles.flatMap(role => ACCESS_MODULES.map(([moduleId, moduleTitle], i) => <tr key={`${role}-${moduleId}`}><td>{i === 0 ? <b>{role}</b> : ""}</td><td>{moduleTitle}</td>{rights.map(([rightId]) => <td key={rightId}><input type="checkbox" checked={!!roleRights?.[role]?.[moduleId]?.[rightId]} onChange={() => toggleRight(role, moduleId, rightId)} /></td>)}</tr>))}</tbody></table></div>
    </Section>;
  }

  function content() {
    if (page === "tower") return renderTower();
    if (page === "communications") return renderCommunications();
    if (page === "catalog") return renderCatalog();
    if (page === "quality") return renderQuality();
    if (page === "operations") return renderOperations();
    if (page === "fbo") return renderFbo();
    if (page === "analytics") return renderAnalytics();
    if (page === "settings") return renderSettings();
    if (page === "security") return renderSecurity();
    return renderTower();
  }

  return <div className="app">{renderSidebar()}<main>{message && <div className="message">{message}</div>}{content()}</main></div>;
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
