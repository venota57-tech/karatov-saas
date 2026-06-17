import React, { useEffect, useMemo, useState } from "react";
import ReactDOM from "react-dom/client";
import "./style.css";

const NAV = [
  ["dashboard", "Дашборд"],
  ["reviews", "Отзывы"],
  ["questions", "Вопросы"],
  ["summary", "Саммари"],
  ["products", "Товары"],
  ["anomalies", "Аномалии"],
  ["reports", "Отчеты"],
  ["autopublish", "Автопубликация"],
  ["sync", "Синхронизация"],
  ["booking", "WB FBO Slot Hunter"],
  ["settings", "AI / шаблоны"],
  ["system", "Диагностика"],
];

const PLATFORMS = ["ALL", "WB", "OZON", "YM"];
const ANSWER_STATES = [
  ["all", "Все"],
  ["unanswered", "Без ответа"],
  ["answered", "С ответом"],
  ["drafts", "Черновики"],
  ["published", "Опубликовано"],
  ["risk", "Риск"],
];

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) throw new Error(typeof data === "object" ? (data.detail || data.error || JSON.stringify(data)) : data);
  return data;
}

function asList(data) {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.items)) return data.items;
  if (Array.isArray(data?.data)) return data.data;
  if (Array.isArray(data?.reviews)) return data.reviews;
  if (Array.isArray(data?.questions)) return data.questions;
  return [];
}

function pretty(x) { return typeof x === "string" ? x : JSON.stringify(x, null, 2); }
function dt(v) { return v ? String(v).replace("T", " ").slice(0, 19) : "—"; }
function boolText(v) { return v ? "да" : "нет"; }
function num(v) { return Number(v || 0).toLocaleString("ru-RU"); }
function dateKey(v) { return v ? String(v).slice(0, 10) : "Без даты"; }
function monthKey(v) { return v ? String(v).slice(0, 7) : "Без месяца"; }
function weekKey(v) {
  if (!v) return "Без недели";
  const d = new Date(v); if (Number.isNaN(d.getTime())) return "Без недели";
  const oneJan = new Date(d.getFullYear(), 0, 1);
  const week = Math.ceil((((d - oneJan) / 86400000) + oneJan.getDay() + 1) / 7);
  return `${d.getFullYear()}-W${String(week).padStart(2, "0")}`;
}

function Badge({ children, type = "" }) { return <span className={`badge ${type}`}>{children}</span>; }
function PlatformBadge({ p }) { return <span className={`platformBadge ${p || ""}`}>{p || "—"}</span>; }

function buildProductUrl(platform, sku, itemUrl) {
  if (itemUrl) return itemUrl;
  const p = String(platform || "").toUpperCase();
  if (p === "WB" && sku) return `https://www.wildberries.ru/catalog/${sku}/detail.aspx`;
  if (p === "OZON" && sku) return `https://www.ozon.ru/search/?text=${encodeURIComponent(sku)}`;
  return "";
}

function groupBy(items, keyFn) {
  const map = new Map();
  items.forEach((x) => {
    const key = keyFn(x) || "—";
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(x);
  });
  return map;
}

function groupCount(items, keyFn, limit = 20) {
  return Array.from(groupBy(items, keyFn).entries())
    .map(([k, arr]) => [k, arr.length])
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit);
}

function avgRating(items) {
  const ratings = items.map(x => Number(x.rating)).filter(Boolean);
  if (!ratings.length) return "—";
  return (ratings.reduce((a, b) => a + b, 0) / ratings.length).toFixed(2);
}

function slaMinutes(start, end) {
  if (!start || !end) return null;
  const a = new Date(start).getTime(); const b = new Date(end).getTime();
  if (Number.isNaN(a) || Number.isNaN(b)) return null;
  return Math.max(0, Math.round((b - a) / 60000));
}

function makeProductSummary(items) {
  const map = groupBy(items, x => `${x.platform || "—"}::${x.sku || x.external_id || "—"}`);
  return Array.from(map.entries()).map(([key, rows]) => {
    const [platform, sku] = key.split("::");
    const risks = rows.filter(x => x.ai_risk_level === "high" || x.ai_risk_level === "medium").length;
    const negatives = rows.filter(x => String(x.ai_sentiment || "").includes("negative") || Number(x.rating || 5) <= 3).length;
    const categories = groupCount(rows, x => x.ai_category, 5);
    const highRisk = rows.some(x => x.ai_risk_level === "high");
    const latest = rows.slice().sort((a, b) => String(b.created_at_marketplace || "").localeCompare(String(a.created_at_marketplace || "")))[0] || {};
    return {
      platform,
      sku,
      product_name: latest.product_name || sku,
      total: rows.length,
      unanswered: rows.filter(x => x.operational_status === "needs_response" || x.has_answer === false).length,
      avg_rating: avgRating(rows),
      negatives,
      risks,
      highRisk,
      categories,
      url: buildProductUrl(platform, sku, latest.product_url),
      items: rows,
    };
  }).sort((a, b) => (b.risks * 10 + b.negatives * 3 + b.total) - (a.risks * 10 + a.negatives * 3 + a.total));
}

function buildAiSummary(productRows, visibleItems, platform) {
  const topProblems = groupCount(visibleItems, x => x.ai_category, 7).filter(([k]) => k && k !== "—");
  const highRisk = visibleItems.filter(x => x.ai_risk_level === "high");
  const needs = visibleItems.filter(x => x.operational_status === "needs_response" || x.has_answer === false);
  const topProducts = productRows.slice(0, 5);
  const scope = platform === "ALL" ? "по всем площадкам" : `по ${platform}`;
  return {
    text: `Сейчас ${scope} в базе ${num(visibleItems.length)} записей, из них ${num(needs.length)} требуют ответа. Основные темы: ${topProblems.slice(0, 4).map(([k, v]) => `${k} (${v})`).join(", ") || "данных пока недостаточно"}. Высокий риск найден в ${num(highRisk.length)} записях.`,
    recommendations: [
      highRisk.length ? `В первую очередь разобрать ${num(highRisk.length)} high-risk отзывов: они могут влиять на рейтинг и требуют ручной проверки перед публикацией.` : "High-risk отзывов в текущем фильтре нет.",
      topProducts[0] ? `Проверить товар ${topProducts[0].sku}: больше всего сигналов по отзывам/вопросам в текущем срезе.` : "После накопления архива появятся потоварные рекомендации.",
      topProblems[0] ? `Передать категорию «${topProblems[0][0]}» ответственным: это самая частая тема текущего периода.` : "Категории пока не накоплены.",
      "Для точного SLA публикации нужно хранить отдельное поле answered_at/published_at после отправки ответа в WB/Ozon.",
    ],
  };
}

function App() {
  const [section, setSection] = useState("dashboard");
  const [platform, setPlatform] = useState("ALL");
  const [answerState, setAnswerState] = useState("unanswered");
  const [product, setProduct] = useState("");
  const [category, setCategory] = useState("");
  const [risk, setRisk] = useState("");
  const [rawReviews, setRawReviews] = useState([]);
  const [rawQuestions, setRawQuestions] = useState([]);
  const [selected, setSelected] = useState(null);
  const [selectedProduct, setSelectedProduct] = useState(null);
  const [draft, setDraft] = useState("");
  const [rules, setRules] = useState({});
  const [diagnostics, setDiagnostics] = useState(null);
  const [syncStatus, setSyncStatus] = useState(null);
  const [ozonStatus, setOzonStatus] = useState(null);
  const [booking, setBooking] = useState(null);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [lastRefreshAt, setLastRefreshAt] = useState(null);

  function matchesPlatform(item) {
    return platform === "ALL" || String(item.platform || "").toUpperCase() === platform;
  }

  function matchesFilters(item) {
    if (!matchesPlatform(item)) return false;
    if (answerState === "unanswered" && !(item.operational_status === "needs_response" || item.has_answer === false)) return false;
    if (answerState === "answered" && !(item.has_answer === true || item.response_origin === "seller_cabinet")) return false;
    if (answerState === "drafts" && !(item.final_answer || item.draft_answer || item.status === "ready_to_review" || item.status === "ready_to_publish")) return false;
    if (answerState === "published" && !(String(item.status || "").includes("published") || item.response_origin === "auto_app")) return false;
    if (answerState === "risk" && !(item.ai_risk_level === "high" || item.ai_risk_level === "medium")) return false;
    const q = product.trim().toLowerCase();
    if (q) {
      const hay = [item.sku, item.product_name, item.external_id, item.text, item.pros, item.cons].join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    if (category.trim() && String(item.ai_category || "") !== category.trim()) return false;
    if (risk.trim() && String(item.ai_risk_level || "") !== risk.trim()) return false;
    return true;
  }

  const rawAll = useMemo(() => [...rawReviews, ...rawQuestions], [rawReviews, rawQuestions]);
  const scopedAll = useMemo(() => rawAll.filter(matchesPlatform), [rawAll, platform]);
  const reviews = useMemo(() => rawReviews.filter(matchesFilters), [rawReviews, platform, answerState, product, category, risk]);
  const questions = useMemo(() => rawQuestions.filter(matchesFilters), [rawQuestions, platform, answerState, product, category, risk]);
  const visibleAll = useMemo(() => [...reviews, ...questions], [reviews, questions]);
  const productRows = useMemo(() => makeProductSummary(scopedAll), [scopedAll]);
  const aiSummary = useMemo(() => buildAiSummary(productRows, scopedAll, platform), [productRows, scopedAll, platform]);

  const metrics = useMemo(() => {
    const needs = scopedAll.filter(x => x.operational_status === "needs_response" || x.has_answer === false);
    return {
      reviews: rawReviews.filter(matchesPlatform).length,
      questions: rawQuestions.filter(matchesPlatform).length,
      total: scopedAll.length,
      needs: needs.length,
      drafts: scopedAll.filter(x => x.final_answer || x.draft_answer).length,
      risk: scopedAll.filter(x => x.ai_risk_level === "high").length,
      avg: avgRating(scopedAll),
      wb: rawAll.filter(x => x.platform === "WB").length,
      ozon: rawAll.filter(x => x.platform === "OZON").length,
    };
  }, [rawAll, rawReviews, rawQuestions, scopedAll, platform]);

  useEffect(() => {
    bootstrap();
    const timer = setInterval(() => refreshAll(false), 60000);
    return () => clearInterval(timer);
  }, []);

  async function bootstrap() {
    await Promise.allSettled([refreshAll(false), loadDiagnostics(false), loadRules(false), loadSyncStatus(false), loadBooking(false)]);
  }

  async function refreshAll(show = true) {
    if (show) { setLoading(true); setMessage("Обновляю данные..."); }
    try {
      const [r, q] = await Promise.all([api("/reviews?limit=500"), api("/questions?limit=500")]);
      setRawReviews(asList(r)); setRawQuestions(asList(q)); setLastRefreshAt(new Date().toISOString());
      if (show) setMessage("Данные обновлены");
    } catch(e) { setMessage(`Ошибка обновления данных: ${e.message}`); }
    finally { if (show) setLoading(false); }
  }

  async function loadDiagnostics(show = true) {
    try { const d = await api("/system/diagnostics").catch(() => api("/system/status")); setDiagnostics(d); if (d?.rules) setRules(d.rules); if (show) setMessage("Диагностика обновлена"); }
    catch(e) { if (show) setMessage(`Ошибка диагностики: ${e.message}`); }
  }
  async function loadRules(show = true) {
    try { const d = await api("/settings/automation-rules"); setRules(d || {}); if (show) setMessage("Правила загружены"); }
    catch(e) { if (show) setMessage(`Ошибка правил: ${e.message}`); }
  }
  async function saveRules() {
    setLoading(true);
    try { const payload = {...rules}; delete payload.updated_at; const d = await api("/settings/automation-rules", {method:"PUT", body:JSON.stringify(payload)}); setRules(d || payload); setMessage("Правила сохранены"); }
    catch(e) { setMessage(`Ошибка сохранения правил: ${e.message}`); }
    finally { setLoading(false); }
  }
  async function loadSyncStatus(show = true) {
    try { const wb = await api("/sync/status").catch(() => null); const oz = await api("/sync/ozon/status").catch(() => null); setSyncStatus(wb); setOzonStatus(oz); if (show) setMessage("Статусы обновлены"); }
    catch(e) { if (show) setMessage(`Ошибка статуса: ${e.message}`); }
  }
  async function runSync(path, label) {
    setLoading(true); setMessage(`Запускаю ${label}...`);
    try { await api(path, {method:"POST"}); await Promise.allSettled([refreshAll(false), loadSyncStatus(false), loadDiagnostics(false)]); setMessage(`${label}: запущено/выполнено`); }
    catch(e) { setMessage(`Ошибка ${label}: ${e.message}`); }
    finally { setLoading(false); }
  }
  async function loadBooking(show = true) {
    try { const d = await api("/wb-booking/status"); setBooking(d); if (show) setMessage("Slot Hunter обновлен"); }
    catch(e) { if (show) setMessage(`Ошибка Slot Hunter: ${e.message}`); }
  }
  async function saveBooking() {
    setLoading(true);
    try { const d = await api("/wb-booking/config", {method:"POST", body:JSON.stringify(booking || {})}); setBooking(d); setMessage("Расписание Slot Hunter сохранено"); }
    catch(e) { setMessage(`Ошибка сохранения Slot Hunter: ${e.message}`); }
    finally { setLoading(false); }
  }
  async function checkBooking() {
    setLoading(true);
    try { const d = await api("/wb-booking/check", {method:"POST"}); setBooking(d); setMessage("Проверка Slot Hunter выполнена"); }
    catch(e) { setMessage(`Ошибка проверки Slot Hunter: ${e.message}`); }
    finally { setLoading(false); }
  }
  async function toggleBooking(enabled) {
    setLoading(true);
    try { const d = await api(enabled ? "/wb-booking/start" : "/wb-booking/stop", {method:"POST", body:JSON.stringify(booking || {})}); setBooking(d); setMessage(enabled ? "Slot Hunter включен" : "Slot Hunter остановлен"); }
    catch(e) { setMessage(`Ошибка Slot Hunter: ${e.message}`); }
    finally { setLoading(false); }
  }
  async function generateSelected() {
    if (!selected?.item?.id) return;
    setLoading(true);
    try { const base = selected.type === "question" ? "/questions" : "/reviews"; const fresh = await api(`${base}/${selected.item.id}/generate`, {method:"POST"}); setSelected({...selected, item:fresh}); setDraft(fresh.final_answer || fresh.draft_answer || ""); await refreshAll(false); setMessage("Ответ сгенерирован"); }
    catch(e) { setMessage(`Ошибка генерации: ${e.message}`); }
    finally { setLoading(false); }
  }
  async function saveAnswer() {
    if (!selected?.item?.id) return;
    setLoading(true);
    try { const base = selected.type === "question" ? "/questions" : "/reviews"; const fresh = await api(`${base}/${selected.item.id}/answer`, {method:"PATCH", body:JSON.stringify({final_answer:draft})}); setSelected({...selected, item:fresh}); await refreshAll(false); setMessage("Ответ сохранен"); }
    catch(e) { setMessage(`Ошибка сохранения: ${e.message}`); }
    finally { setLoading(false); }
  }
  async function publishSelected() {
    if (!selected?.item?.id) return;
    setLoading(true);
    try { const base = selected.type === "question" ? "/questions" : "/reviews"; const res = await api(`${base}/${selected.item.id}/publish`, {method:"POST"}); await refreshAll(false); setMessage(`Публикация: ${pretty(res)}`); }
    catch(e) { setMessage(`Ошибка публикации: ${e.message}`); }
    finally { setLoading(false); }
  }
  async function runAutopublish() {
    setLoading(true);
    try { const res = await api("/autopublish", {method:"POST"}); await refreshAll(false); setMessage(`Автопубликация: ${pretty(res)}`); }
    catch(e) { setMessage(`Ошибка автопубликации: ${e.message}`); }
    finally { setLoading(false); }
  }

  function updateRule(key, value) { setRules(prev => ({...prev, [key]: value})); }
  function updateMatrix(p, kind, value) { setRules(prev => ({...prev, autopublish_matrix:{...(prev.autopublish_matrix || {}), [p]:{...(prev.autopublish_matrix?.[p] || {}), [kind]: value}}})); }
  function updateRating(r, value) { setRules(prev => ({...prev, autopublish_rating_matrix:{...(prev.autopublish_rating_matrix || {}), [r]: value}})); }
  function updateBooking(key, value) { setBooking(prev => ({...(prev || {}), [key]: value})); }
  function toggleWarehouse(name) { const arr = booking?.warehouses || []; updateBooking("warehouses", arr.includes(name) ? arr.filter(x => x !== name) : [...arr, name]); }

  function top(title, subtitle, actions = null) { return <div className="top"><div><h2>{title}</h2><p>{subtitle}</p></div><div className="actions">{actions}</div></div>; }
  function marketplaceSwitcher() { return <div className="marketSwitch">{PLATFORMS.map(p => <button key={p} className={platform === p ? "active" : ""} onClick={() => setPlatform(p)}>{p === "ALL" ? "Все" : p}<span>{p === "ALL" ? rawAll.length : rawAll.filter(x => x.platform === p).length}</span></button>)}</div>; }
  function filters() { return <div className="sectionFilters"><div className="productFilterBox inline"><label>Состояние</label><select value={answerState} onChange={e => setAnswerState(e.target.value)}>{ANSWER_STATES.map(([v,t]) => <option key={v} value={v}>{t}</option>)}</select></div><div className="productFilterBox inline"><label>Товар / SKU</label><input value={product} onChange={e => setProduct(e.target.value)} placeholder="артикул, название, id" /></div><button onClick={() => refreshAll(true)}>Обновить сейчас</button></div>; }

  function card(title, value, sub, onClick) { return <button onClick={onClick}><b>{value}</b><span>{title}</span>{sub && <small>{sub}</small>}</button>; }
  function renderDashboard() { return <>{top("Операционный кабинет KARATOV CX Hub", "Общее состояние сервиса, маркетплейсов, отзывов, вопросов и рисков", <button className="primary" onClick={() => refreshAll(true)}>Обновить</button>)}<div className="cards">{card("Всего записей", num(metrics.total), platform)}{card("Отзывы", num(metrics.reviews), null, () => setSection("reviews"))}{card("Вопросы", num(metrics.questions), null, () => setSection("questions"))}{card("Требуют ответа", num(metrics.needs), null, () => setSection("reviews"))}{card("Черновики", num(metrics.drafts), null, () => setSection("autopublish"))}{card("High risk", num(metrics.risk), null, () => setSection("anomalies"))}{card("Средний рейтинг", metrics.avg)}{card("WB", num(metrics.wb), null, () => {setPlatform("WB"); setSection("reviews");})}{card("Ozon", num(metrics.ozon), null, () => {setPlatform("OZON"); setSection("reviews");})}</div><div className="settingsPanel"><div className="settingCard"><h3>AI summary</h3><p>{aiSummary.text}</p>{aiSummary.recommendations.slice(0,3).map((r,i)=><div className="exampleBox" key={i}>{r}</div>)}</div><div className="settingCard"><h3>Синхронизация</h3><div className="metricRow"><span>WB auto</span><b>{boolText(syncStatus?.auto_sync_enabled)}</b></div><div className="metricRow"><span>WB running</span><b>{boolText(syncStatus?.running)}</b></div><div className="metricRow"><span>Последнее UI обновление</span><b>{dt(lastRefreshAt)}</b></div><button onClick={() => setSection("sync")}>Открыть</button></div><div className="settingCard"><h3>Slot Hunter</h3><div className="metricRow"><span>Статус</span><b>{booking?.enabled ? "включен" : "выключен"}</b></div><div className="metricRow"><span>Режим</span><b>{booking?.mode || "—"}</b></div><button onClick={() => setSection("booking")}>Настроить</button></div></div></>; }

  function renderList(type) { const items = type === "question" ? questions : reviews; return <div className="list">{items.length === 0 ? <div className="empty">Данных по фильтру нет</div> : items.map((item,i) => <div key={`${type}-${item.id || i}`} className={`row ${selected?.type === type && selected?.item?.id === item.id ? "selected" : ""}`} onClick={() => {setSelected({type, item}); setDraft(item.final_answer || item.draft_answer || "");}}><div className="rowhead"><b>{item.product_name || item.sku || `Запись ${item.id}`}</b><PlatformBadge p={item.platform} /></div><div className="dateMeta">{item.rating && <span>⭐ <b>{item.rating}</b></span>}<span>{dt(item.created_at_marketplace)}</span><span>{item.source_status || "—"}</span></div><div className="text">{item.text || item.pros || item.cons || "Без текста"}</div><div className="tags"><Badge>{item.status || "new"}</Badge>{item.ai_category && <Badge type="yellow">{item.ai_category}</Badge>}{item.ai_risk_level && <Badge type={item.ai_risk_level === "high" ? "red" : ""}>{item.ai_risk_level}</Badge>}{item.final_answer && <Badge type="green">черновик готов</Badge>}</div></div>)}</div>; }
  function renderDetail() { const item = selected?.item; if (!item) return <div className="detail"><div className="empty">Выбери запись слева</div></div>; const url = buildProductUrl(item.platform, item.sku, item.product_url); return <div className="detail"><div className="detailhead"><div><h3>{item.product_name || item.sku || "Карточка"}</h3><p className="meta">{selected.type === "question" ? "Вопрос" : "Отзыв"} · {item.platform} · {item.source_status}</p></div>{url && <a className="buttonLike" href={url} target="_blank" rel="noreferrer">Открыть товар</a>}</div><div className="clientText">{item.text || item.pros || item.cons || "Нет текста"}</div><div className="twoCols"><div className="exampleBox"><b>AI-категория</b><p>{item.ai_category || "—"}</p></div><div className="exampleBox"><b>Quality / причина</b><p>{item.ai_reason || item.publish_blocked_reason || "—"}</p></div></div><label>Финальный ответ</label><textarea value={draft} onChange={e => setDraft(e.target.value)} placeholder="Сгенерируй или введи ответ" /><div className="actions"><button className="primary" onClick={generateSelected}>Сгенерировать 10/10</button><button onClick={saveAnswer}>Сохранить</button><button onClick={publishSelected}>Опубликовать</button><button onClick={() => navigator.clipboard.writeText(draft || "")}>Скопировать</button></div></div>; }
  function renderWork(type) { return <>{top(type === "question" ? "Вопросы покупателей" : "Отзывы покупателей", "Реальные данные из маркетплейсов, AI-черновики и публикация", <button className="primary" onClick={() => refreshAll(true)}>Обновить</button>)}{filters()}<div className="workspace">{renderList(type)}{renderDetail()}</div></>; }

  function renderSummary() { return <>{top("Саммари CX", "Потоварная AI-выжимка, темы, риски и рекомендации", <button onClick={() => refreshAll(true)}>Обновить</button>)}<div className="settingsPanel"><div className="settingCard wide"><h3>AI Summary</h3><p>{aiSummary.text}</p><h4>Рекомендации</h4>{aiSummary.recommendations.map((r,i)=><div className="exampleBox" key={i}>{r}</div>)}</div><div className="settingCard"><h3>Топ тем</h3><table><tbody>{groupCount(scopedAll, x=>x.ai_category,10).map(([k,v])=><tr key={k}><td>{k}</td><td>{v}</td></tr>)}</tbody></table></div></div><div className="settingCard wide"><h3>Саммари по товарам</h3><table><thead><tr><th>Товар</th><th>Площадка</th><th>Всего</th><th>Рейтинг</th><th>Риски</th><th>Темы</th><th></th></tr></thead><tbody>{productRows.slice(0,50).map(p=><tr key={`${p.platform}-${p.sku}`}><td><button className="linkBtn" onClick={()=>{setSelectedProduct(p); setSection("products");}}>{p.sku}</button><br/><small>{p.product_name}</small></td><td>{p.platform}</td><td>{p.total}</td><td>{p.avg_rating}</td><td>{p.risks}</td><td>{p.categories.map(([k,v])=>`${k}: ${v}`).join(", ")}</td><td>{p.url && <a href={p.url} target="_blank" rel="noreferrer">Открыть</a>}</td></tr>)}</tbody></table></div></>; }
  function renderProducts() { const rows = productRows; const p = selectedProduct || rows[0]; return <>{top("Карточки товаров", "Отзывы, вопросы, категории, риски и ссылки на маркетплейсы", <button onClick={() => refreshAll(true)}>Обновить</button>)}<div className="workspace"><div className="list">{rows.slice(0,100).map(row=><div key={`${row.platform}-${row.sku}`} className={`row ${p?.sku===row.sku && p?.platform===row.platform ? "selected" : ""}`} onClick={()=>setSelectedProduct(row)}><div className="rowhead"><b>{row.sku}</b><PlatformBadge p={row.platform}/></div><div className="dateMeta"><span>Всего: {row.total}</span><span>Рейтинг: {row.avg_rating}</span><span>Риск: {row.risks}</span></div><div className="tags">{row.categories.map(([k,v])=><Badge key={k} type="yellow">{k}: {v}</Badge>)}</div></div>)}</div><div className="detail">{p ? <><div className="detailhead"><div><h3>{p.product_name || p.sku}</h3><p className="meta">{p.platform} · SKU {p.sku}</p></div>{p.url && <a className="buttonLike" href={p.url} target="_blank" rel="noreferrer">Открыть карточку</a>}</div><div className="cards"><button><b>{p.total}</b><span>Записей</span></button><button><b>{p.unanswered}</b><span>Без ответа</span></button><button><b>{p.avg_rating}</b><span>Рейтинг</span></button><button><b>{p.risks}</b><span>Риски</span></button></div><h3>Рекомендация</h3><div className="exampleBox">{p.risks ? `Проверить товар ${p.sku}: есть рискованные отзывы и темы ${p.categories.map(([k])=>k).join(", ")}.` : `Критичных рисков по ${p.sku} в текущем срезе нет.`}</div><h3>Отзывы/вопросы</h3>{p.items.slice(0,20).map((x,i)=><div className="exampleBox" key={i}><b>{x.rating ? `⭐ ${x.rating}` : "Вопрос"} · {dt(x.created_at_marketplace)}</b><p>{x.text || x.pros || x.cons || "Без текста"}</p></div>)}</> : <div className="empty">Нет товара</div>}</div></div></>; }
  function renderAnomalies() { const rows = productRows.filter(p => p.highRisk || p.risks > 0 || p.negatives > 0).slice(0,50); return <>{top("Аномалии", "Товары с high-risk отзывами, негативом и концентрацией проблем", <button onClick={() => refreshAll(true)}>Обновить</button>)}<div className="settingsPanel">{rows.length ? rows.map(p=><div className="settingCard" key={`${p.platform}-${p.sku}`}><h3>{p.sku} <PlatformBadge p={p.platform}/></h3><div className="metricRow"><span>Риски</span><b>{p.risks}</b></div><div className="metricRow"><span>Негатив</span><b>{p.negatives}</b></div><div className="metricRow"><span>Рейтинг</span><b>{p.avg_rating}</b></div><p>{p.categories.map(([k,v])=>`${k}: ${v}`).join(", ")}</p>{p.url && <a href={p.url} target="_blank" rel="noreferrer">Открыть товар</a>}</div>) : <div className="settingCard wide">Аномалий в текущем срезе нет.</div>}</div></>; }
  function renderReports() { const revDay = groupCount(rawReviews.filter(matchesPlatform), x=>dateKey(x.created_at_marketplace), 30); const qDay = groupCount(rawQuestions.filter(matchesPlatform), x=>dateKey(x.created_at_marketplace), 30); const revMonth = groupCount(rawReviews.filter(matchesPlatform), x=>monthKey(x.created_at_marketplace), 12); const answeredR = rawReviews.filter(x=>matchesPlatform(x) && (x.has_answer || String(x.status||"").includes("published"))); const r60 = answeredR.filter(x => { const m = slaMinutes(x.created_at_marketplace, x.updated_at); return m !== null && m <= 60; }).length; const rOver = answeredR.filter(x => { const m = slaMinutes(x.created_at_marketplace, x.updated_at); return m !== null && m > 60; }).length; function table(title, rows){return <div className="settingCard"><h3>{title}</h3><table><tbody>{rows.map(([k,v])=><tr key={k}><td>{k}</td><td>{v}</td></tr>)}</tbody></table></div>} return <>{top("Отчеты", "Метрики total, SLA и динамика по периодам", <button onClick={()=>refreshAll(true)}>Обновить</button>)}<div className="cards">{card("Отзывы total", num(metrics.reviews))}{card("Вопросы total", num(metrics.questions))}{card("Отзывы ≤ 1 часа", num(r60))}{card("Отзывы > 1 часа", num(rOver))}{card("High risk", num(metrics.risk))}</div><div className="settingsPanel">{table("Отзывы день к дню", revDay)}{table("Вопросы день к дню", qDay)}{table("Отзывы месяц к месяцу", revMonth)}<div className="settingCard"><h3>SLA примечание</h3><p>Сейчас SLA считается по created_at_marketplace → updated_at. Для точности нужно сохранять answered_at/published_at после реальной публикации.</p></div></div></>; }
  function renderSync() { const blocks = syncStatus?.blocks_state || {}; return <>{top("Синхронизация", "Понятный статус загрузки данных без технического JSON", <button onClick={()=>loadSyncStatus(true)}>Обновить статус</button>)}<div className="cards">{card("WB auto", boolText(syncStatus?.auto_sync_enabled))}{card("WB running", boolText(syncStatus?.running))}{card("Режим", syncStatus?.sync_mode || "—")}{card("Ozon", ozonStatus?.enabled ? "вкл" : "—")}</div><div className="settingsPanel"><div className="settingCard"><h3>WB блоки</h3><table><thead><tr><th>Блок</th><th>Статус</th><th>Получено</th><th>Ошибка</th></tr></thead><tbody>{Object.entries(blocks).map(([k,v])=><tr key={k}><td>{k}</td><td>{v.status}</td><td>{v.last_received || 0}</td><td>{v.last_error ? String(v.last_error).slice(0,80) : "—"}</td></tr>)}</tbody></table></div><div className="settingCard"><h3>Ручная проверка</h3><div className="blockButtons"><button onClick={()=>runSync("/sync/wb", "WB next")}>WB безопасный блок</button><button onClick={()=>runSync("/sync/wb/backfill/next", "WB archive")}>WB архив</button><button onClick={()=>runSync("/sync/ozon", "Ozon all")}>Ozon все</button></div></div><div className="settingCard"><h3>История</h3>{(syncStatus?.sweep_results || []).slice(0,10).map((x,i)=><div className="exampleBox" key={i}>{x.block || x.platform}: {x.status || (x.failed ? "ошибка" : "ок")} {x.error ? String(x.error).slice(0,100) : ""}</div>)}</div></div></>; }
  function renderBooking() { const b = booking || {}; return <>{top("WB FBO Slot Hunter", "API-first поиск и бронирование слотов по заданному расписанию", <><button onClick={saveBooking}>Сохранить</button><button className="primary" onClick={checkBooking}>Проверить сейчас</button><button onClick={()=>toggleBooking(true)}>Включить</button><button onClick={()=>toggleBooking(false)}>Остановить</button></>)}<div className="settingsPanel"><div className="settingCard"><h3>Надежный режим</h3><p>Основной вариант — через WB API. Сайт не входит в ЛК WB как человек и не кликает календарь браузером. Browser automation лучше держать только как fallback.</p><div className="metricRow"><span>API mode</span><b>{b.api_mode || "api_first"}</b></div><div className="metricRow"><span>Статус</span><b>{b.enabled ? "включен" : "выключен"}</b></div></div><div className="settingCard"><h3>Расписание</h3><label>Стартовая дата</label><input type="date" value={b.start_date || ""} onChange={e=>updateBooking("start_date", e.target.value)} /><label>Каждые N рабочих дней</label><input type="number" value={b.interval_workdays || 3} onChange={e=>updateBooking("interval_workdays", Number(e.target.value))} /><label>Горизонт, дней</label><input type="number" value={b.horizon_days || 30} onChange={e=>updateBooking("horizon_days", Number(e.target.value))} /></div><div className="settingCard"><h3>Условия</h3><label className="check"><input type="checkbox" checked={(b.warehouses||[]).includes("Коледино")} onChange={()=>toggleWarehouse("Коледино")} />Коледино</label><label className="check"><input type="checkbox" checked={(b.warehouses||[]).includes("Электросталь")} onChange={()=>toggleWarehouse("Электросталь")} />Электросталь</label><label>Тип поставки</label><input value={b.supply_type || "Суперсейф"} onChange={e=>updateBooking("supply_type", e.target.value)} /><label>Коэффициент не выше</label><input type="number" value={b.coefficient_limit || 20} onChange={e=>updateBooking("coefficient_limit", Number(e.target.value))} /></div><div className="settingCard"><h3>Режим</h3><select value={b.mode || "monitor_only"} onChange={e=>updateBooking("mode", e.target.value)}><option value="monitor_only">Только мониторинг</option><option value="notify">Уведомить</option><option value="reserve_draft">Создать/подготовить черновик</option><option value="auto_book">Автобронь</option></select><label>Мониторинг с</label><input value={b.work_time_from || "09:00"} onChange={e=>updateBooking("work_time_from", e.target.value)} /><label>до</label><input value={b.work_time_to || "21:00"} onChange={e=>updateBooking("work_time_to", e.target.value)} /></div><div className="settingCard wide"><h3>Целевые даты</h3><div className="tags">{(b.target_dates || []).slice(0,40).map(d=><Badge key={d}>{d}</Badge>)}</div></div><div className="settingCard wide"><h3>История проверок / бронирований</h3>{(b.history || []).slice(0,30).map((h,i)=><div className="exampleBox" key={i}><b>{dt(h.at)} · {h.event}</b><p>{h.message || h.status || pretty(h).slice(0,180)}</p></div>)}</div></div></>; }
  function renderAutopublish() { const matrix = rules.autopublish_matrix || {}; const ratingMatrix = rules.autopublish_rating_matrix || {}; return <>{top("Автопубликация", "Полные правила: площадки, оценки, AI/шаблоны, fallback, подписи и лимиты", <><button onClick={()=>loadRules(true)}>Загрузить</button><button className="primary" onClick={saveRules}>Сохранить</button><button onClick={runAutopublish}>Запустить сейчас</button></>)}<div className="settingsPanel"><div className="settingCard"><h3>Главный режим</h3><label className="check"><input type="checkbox" checked={!!rules.real_autopublish_enabled} onChange={e=>updateRule("real_autopublish_enabled", e.target.checked)} />Разрешить автопубликацию правилами</label><label>Режим ответа</label><select value={rules.answer_generation_mode || "hybrid"} onChange={e=>updateRule("answer_generation_mode", e.target.value)}><option value="ai_only">Только AI</option><option value="templates_only">Только шаблоны</option><option value="hybrid">AI + шаблоны</option></select><label>Fallback при недоступности OpenAI</label><select value={rules.openai_fallback_action || "templates"} onChange={e=>updateRule("openai_fallback_action", e.target.value)}><option value="templates">Использовать шаблоны</option><option value="operator">Оператору</option><option value="skip">Не публиковать</option></select></div><div className="settingCard"><h3>Quality Gate</h3><label>Минимальная оценка качества</label><input type="number" min="1" max="10" value={rules.quality_gate_min_score || 10} onChange={e=>updateRule("quality_gate_min_score", Number(e.target.value))} /><label>Минимальная оценка отзыва для автопубликации</label><input type="number" min="1" max="5" value={rules.positive_review_min_rating || 5} onChange={e=>updateRule("positive_review_min_rating", Number(e.target.value))} /><label>Макс. ответов за запуск</label><input type="number" value={rules.autopublish_max_per_run || 10} onChange={e=>updateRule("autopublish_max_per_run", Number(e.target.value))} /></div><div className="settingCard"><h3>По оценкам</h3>{[5,4,3,2,1].map(r=><label className="check" key={r}><input type="checkbox" checked={!!ratingMatrix[r]} onChange={e=>updateRating(r, e.target.checked)} />{r}★</label>)}</div><div className="settingCard"><h3>Подписи</h3><textarea value={(rules.signatures || ["С уважением, команда KARATOV"]).join("\n")} onChange={e=>updateRule("signatures", e.target.value.split("\n").map(x=>x.trim()).filter(Boolean))} /></div></div><div className="matrixGrid">{["WB","OZON","YM"].map(p=><div className="matrixCard" key={p}><b>{p}</b><label className="check"><input type="checkbox" checked={!!matrix?.[p]?.reviews} onChange={e=>updateMatrix(p,"reviews",e.target.checked)} />Отзывы</label><label className="check"><input type="checkbox" checked={!!matrix?.[p]?.questions} onChange={e=>updateMatrix(p,"questions",e.target.checked)} />Вопросы</label></div>)}</div></>; }
  function renderSettings(){return <>{top("AI / шаблоны", "Промты, шаблоны, правила, запрещенные фразы", <><button onClick={()=>loadRules(true)}>Загрузить</button><button className="primary" onClick={saveRules}>Сохранить</button></>)}<div className="settingsPanel"><div className="settingCard"><h3>AI</h3><label className="check"><input type="checkbox" checked={!!rules.ai_generation_enabled} onChange={e=>updateRule("ai_generation_enabled",e.target.checked)} />AI включен</label><label className="check"><input type="checkbox" checked={!!rules.ai_fallback_to_local_templates} onChange={e=>updateRule("ai_fallback_to_local_templates",e.target.checked)} />Fallback на шаблоны</label><label className="check"><input type="checkbox" checked={!!rules.auto_generate_on_sync} onChange={e=>updateRule("auto_generate_on_sync",e.target.checked)} />Автогенерация при синхронизации</label></div><div className="settingCard wide"><h3>Системный промт</h3><textarea className="templateText" value={rules.custom_system_prompt || ""} onChange={e=>updateRule("custom_system_prompt",e.target.value)} /></div><div className="settingCard wide"><h3>Промт отзывов</h3><textarea className="largeText" value={rules.review_prompt_template || ""} onChange={e=>updateRule("review_prompt_template",e.target.value)} /></div><div className="settingCard wide"><h3>Промт вопросов</h3><textarea className="largeText" value={rules.question_prompt_template || ""} onChange={e=>updateRule("question_prompt_template",e.target.value)} /></div><div className="settingCard wide"><h3>Локальные шаблоны</h3><textarea className="templateText" value={rules.local_templates_text || ""} onChange={e=>updateRule("local_templates_text",e.target.value)} /></div></div></>}
  function renderSystem(){return <>{top("Диагностика", "Служебная диагностика", <button onClick={()=>loadDiagnostics(true)}>Обновить</button>)}<div className="settingCard wide"><pre className="reportText">{pretty(diagnostics || "Нет диагностики")}</pre></div></>}
  function content(){ if(section==="dashboard")return renderDashboard(); if(section==="reviews")return renderWork("review"); if(section==="questions")return renderWork("question"); if(section==="summary")return renderSummary(); if(section==="products")return renderProducts(); if(section==="anomalies")return renderAnomalies(); if(section==="reports")return renderReports(); if(section==="sync")return renderSync(); if(section==="booking")return renderBooking(); if(section==="autopublish")return renderAutopublish(); if(section==="settings")return renderSettings(); if(section==="system")return renderSystem(); return renderDashboard(); }

  return <div className="app"><aside><h1>KARATOV<br/>CX Hub</h1>{marketplaceSwitcher()}{NAV.map(([id,title])=><button key={id} className={section===id?"active":""} onClick={()=>setSection(id)}>{title}{id==="reviews" && <span className="navCount">{metrics.reviews}</span>}{id==="questions" && <span className="navCount">{metrics.questions}</span>}</button>)}<div className="syncMini">Площадка: {platform === "ALL" ? "Все" : platform}<br/>UI: {dt(lastRefreshAt)}<br/>{loading ? "Выполняется..." : "Готово"}</div><div className="hint">API-first. Автообновление без ручной перезагрузки.</div></aside><main>{message && <div className="message">{message}</div>}{content()}</main></div>;
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
