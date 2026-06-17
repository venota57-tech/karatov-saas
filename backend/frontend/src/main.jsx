import React, { useEffect, useMemo, useState } from "react";
import ReactDOM from "react-dom/client";
import "./style.css";

const NAV = [
  ["dashboard", "Дашборд"],
  ["reviews", "Отзывы"],
  ["questions", "Вопросы"],
  ["reports", "Отчеты"],
  ["summary", "Саммари CX"],
  ["anomalies", "Аномалии"],
  ["product", "Карточка товара"],
  ["settings", "AI / шаблоны"],
  ["autopublish", "Автопубликация"],
  ["sync", "Синхронизация"],
  ["booking", "WB FBO слоты"],
  ["system", "Диагностика"],
];

const PLATFORMS = ["ALL", "WB", "OZON", "YM"];
const ANSWER_STATES = [["all", "Все"], ["unanswered", "Без ответа"], ["answered", "С ответом"], ["drafts", "Черновики"], ["published", "Опубликовано"], ["stale", "Устаревшие"]];

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
function boolText(v) { return v ? "да" : "нет"; }
function dt(v) { return v ? String(v).replace("T", " ").slice(0, 19) : "—"; }
function dayKey(v) { return v ? String(v).slice(0, 10) : "Без даты"; }
function monthKey(v) { return v ? String(v).slice(0, 7) : "Без месяца"; }
function weekKey(v) { const d = new Date(v); if (!v || Number.isNaN(d.getTime())) return "Без недели"; const y = new Date(d.getFullYear(), 0, 1); const w = Math.ceil((((d - y) / 86400000) + y.getDay() + 1) / 7); return `${d.getFullYear()}-W${String(w).padStart(2, "0")}`; }
function Badge({ children, type = "" }) { return <span className={`badge ${type}`}>{children}</span>; }
function PlatformBadge({ p }) { return <span className={`platformBadge ${p || ""}`}>{p || "—"}</span>; }
function avg(arr) { const xs = arr.filter(x => typeof x === "number"); return xs.length ? xs.reduce((a,b)=>a+b,0)/xs.length : null; }
function groupCount(items, getKey) { const m = {}; items.forEach(x => { const k = getKey(x); m[k] = (m[k] || 0) + 1; }); return Object.entries(m).sort(([a],[b]) => String(b).localeCompare(String(a))).slice(0, 30); }
function topCategory(items) { const m = {}; items.forEach(x => { if (x.ai_category) m[x.ai_category] = (m[x.ai_category] || 0) + 1; }); return Object.entries(m).sort((a,b)=>b[1]-a[1])[0]?.[0] || "—"; }
function mins(start, end) { const a = new Date(start).getTime(); const b = new Date(end).getTime(); if (!start || !end || Number.isNaN(a) || Number.isNaN(b)) return null; return Math.max(0, Math.round((b-a)/60000)); }

function App() {
  const [section, setSection] = useState("dashboard");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
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
  const [summary, setSummary] = useState("");
  const [booking, setBooking] = useState(null);
  const [bookingConfig, setBookingConfig] = useState({});
  const [lastRefreshAt, setLastRefreshAt] = useState(null);

  function setOk(t) { setMessage(t); }
  function setErr(p, e) { setMessage(`${p}: ${e.message}`); }

  function byPlatform(item) { return platform === "ALL" || String(item.platform || "").toUpperCase() === platform; }
  function matches(item) {
    if (!byPlatform(item)) return false;
    if (answerState === "unanswered" && !(item.operational_status === "needs_response" || item.has_answer === false)) return false;
    if (answerState === "answered" && !(item.has_answer === true || item.response_origin === "seller_cabinet")) return false;
    if (answerState === "drafts" && !(item.final_answer || item.draft_answer || item.status === "ready_to_review" || item.status === "ready_to_publish")) return false;
    if (answerState === "published" && !(String(item.status || "").includes("published") || item.response_origin === "auto_app")) return false;
    if (answerState === "stale" && !String(item.operational_status || "").includes("stale")) return false;
    const q = product.trim().toLowerCase();
    if (q && ![item.sku, item.product_name, item.external_id, item.text, item.pros, item.cons].join(" ").toLowerCase().includes(q)) return false;
    if (category.trim() && String(item.ai_category || "") !== category.trim()) return false;
    if (risk.trim() && String(item.ai_risk_level || "") !== risk.trim()) return false;
    return true;
  }

  const platformItems = useMemo(() => [...rawReviews, ...rawQuestions].filter(byPlatform), [rawReviews, rawQuestions, platform]);
  const reviews = useMemo(() => rawReviews.filter(matches), [rawReviews, platform, answerState, product, category, risk]);
  const questions = useMemo(() => rawQuestions.filter(matches), [rawQuestions, platform, answerState, product, category, risk]);
  const allItems = useMemo(() => [...rawReviews, ...rawQuestions], [rawReviews, rawQuestions]);

  const productSummary = useMemo(() => {
    const map = {};
    platformItems.forEach(x => {
      const key = x.sku || x.product_name || x.external_id || "unknown";
      map[key] ||= { sku: x.sku || "—", product_name: x.product_name || x.sku || "—", items: [] };
      map[key].items.push(x);
    });
    return Object.values(map).map(g => {
      const ratings = g.items.map(x => x.rating).filter(Boolean);
      const riskCount = g.items.filter(x => x.ai_risk_level === "high").length;
      const negative = g.items.filter(x => x.ai_sentiment === "negative" || x.rating <= 3 || x.ai_risk_level === "high").length;
      const drafts = g.items.filter(x => x.final_answer || x.draft_answer).length;
      const needs = g.items.filter(x => x.operational_status === "needs_response" || x.has_answer === false).length;
      return { ...g, count: g.items.length, avgRating: avg(ratings), riskCount, negative, drafts, needs, topCategory: topCategory(g.items), lastDate: g.items.map(x => x.created_at_marketplace).filter(Boolean).sort().at(-1) };
    }).sort((a,b) => (b.riskCount - a.riskCount) || (b.negative - a.negative) || (b.needs - a.needs) || (b.count - a.count)).slice(0, 50);
  }, [platformItems]);

  const anomalies = useMemo(() => productSummary.filter(p => p.riskCount > 0 || p.negative >= 2 || p.needs >= 5).slice(0, 30), [productSummary]);

  const metrics = useMemo(() => {
    const items = platformItems;
    return {
      reviews: rawReviews.filter(byPlatform).length,
      questions: rawQuestions.filter(byPlatform).length,
      noAnswer: items.filter(x => x.operational_status === "needs_response" || x.has_answer === false).length,
      ready: items.filter(x => x.final_answer || x.draft_answer || x.status === "ready_to_publish" || x.status === "ready_to_review").length,
      risk: items.filter(x => x.ai_risk_level === "high").length,
      wb: allItems.filter(x => x.platform === "WB").length,
      ozon: allItems.filter(x => x.platform === "OZON").length,
      products: productSummary.length,
    };
  }, [platformItems, rawReviews, rawQuestions, allItems, productSummary]);

  useEffect(() => { bootstrap(); const t = setInterval(() => refreshAll(false), 60000); return () => clearInterval(t); }, []);
  useEffect(() => { if (section === "reviews" && reviews[0] && (!selected || selected.type !== "review")) pick("review", reviews[0]); if (section === "questions" && questions[0] && (!selected || selected.type !== "question")) pick("question", questions[0]); }, [section, reviews, questions]);
  useEffect(() => { refreshAll(false); }, [platform]);

  async function bootstrap() { await Promise.allSettled([refreshAll(false), loadDiagnostics(false), loadRules(false), loadSyncStatus(false), loadBooking(false)]); }
  async function refreshAll(show = true) { if (show) { setLoading(true); setOk("Обновляю данные..."); } try { const [r,q] = await Promise.all([api("/reviews?limit=500"), api("/questions?limit=500")]); setRawReviews(asList(r)); setRawQuestions(asList(q)); setLastRefreshAt(new Date().toISOString()); if (show) setOk("Данные обновлены"); } catch(e) { setErr("Ошибка обновления данных", e); } finally { if (show) setLoading(false); } }
  async function loadDiagnostics(show=true) { try { const d = await api("/system/diagnostics").catch(()=>api("/system/status")); setDiagnostics(d); if (d?.rules) setRules(d.rules); if (show) setOk("Диагностика обновлена"); } catch(e) { if(show) setErr("Ошибка диагностики", e); } }
  async function loadSyncStatus(show=true) { try { const wb = await api("/sync/status").catch(()=>null); const oz = await api("/sync/ozon/status").catch(()=>null); setSyncStatus(wb); setOzonStatus(oz); if (show) setOk("Статус синхронизации обновлен"); } catch(e) { if(show) setErr("Ошибка синхронизации", e); } }
  async function loadRules(show=true) { try { const d = await api("/settings/automation-rules"); setRules(d || {}); if (show) setOk("Правила загружены"); } catch(e) { if(show) setErr("Ошибка правил", e); } }
  async function saveRules() { setLoading(true); try { const payload = {...rules}; delete payload.updated_at; const d = await api("/settings/automation-rules", {method:"PUT", body:JSON.stringify(payload)}); setRules(d || payload); await api("/autopublish-settings/api", {method:"POST", body:JSON.stringify(payload)}).catch(()=>null); setOk("Правила сохранены"); } catch(e){ setErr("Ошибка сохранения", e); } finally{ setLoading(false); } }
  async function runAutopublish() { setLoading(true); try { setOk(`Автопубликация: ${pretty(await api("/autopublish", {method:"POST"}))}`); await refreshAll(false); } catch(e){ setErr("Ошибка автопубликации", e); } finally{ setLoading(false); } }
  async function runSync(path,label) { setLoading(true); setOk(`Запускаю ${label}...`); try { const d = await api(path,{method:"POST"}); setOk(`${label}: ${pretty(d)}`); await Promise.allSettled([refreshAll(false),loadSyncStatus(false),loadDiagnostics(false)]); } catch(e){ setErr(`Ошибка ${label}`, e); } finally{ setLoading(false); } }
  async function loadSummary() { setLoading(true); try { const d = await api("/summary").catch(()=>api("/reports/summary")); setSummary(pretty(d)); setOk("Саммари сформировано"); } catch(e){ setErr("Ошибка саммари", e); } finally{ setLoading(false); } }
  async function loadBooking(show=true) { try { const d = await api("/wb-booking/status"); setBooking(d); setBookingConfig(d?.config || {}); if(show) setOk("Slot Hunter обновлен"); } catch(e){ if(show) setErr("Ошибка WB FBO", e); } }
  async function saveBookingConfig(extra={}) { setLoading(true); try { const payload = {...bookingConfig, ...extra}; const d = await api("/wb-booking/config", {method:"POST", body:JSON.stringify(payload)}); setBookingConfig(d.config || payload); await loadBooking(false); setOk("Настройки Slot Hunter сохранены"); } catch(e){ setErr("Ошибка настроек Slot Hunter", e); } finally{ setLoading(false); } }
  async function checkBooking() { setLoading(true); try { setOk(`Проверка слотов: ${pretty(await api("/wb-booking/check", {method:"POST"}))}`); await loadBooking(false); } catch(e){ setErr("Ошибка проверки слотов", e); } finally{ setLoading(false); } }
  async function toggleBooking(enabled) { await saveBookingConfig({enabled}); await api(enabled ? "/wb-booking/start" : "/wb-booking/stop", {method:"POST", body:JSON.stringify({...bookingConfig, enabled})}).catch(()=>null); await loadBooking(false); }
  function updateRule(k,v){ setRules(prev => ({...prev, [k]:v})); }
  function updateMatrix(p,k,v){ setRules(prev => ({...prev, autopublish_matrix:{...(prev.autopublish_matrix||{}), [p]:{...(prev.autopublish_matrix?.[p]||{}), [k]:v}}})); }
  function pick(type,item){ setSelected({type,item}); setDraft(item.final_answer || item.draft_answer || ""); }
  function openProduct(p){ setSelectedProduct(p); setProduct(p.sku === "—" ? p.product_name : p.sku); setSection("product"); }
  async function generateSelected(){ if(!selected?.item?.id) return; setLoading(true); try{ const base = selected.type === "question" ? "/questions" : "/reviews"; const fresh = await api(`${base}/${selected.item.id}/generate`, {method:"POST"}); pick(selected.type, fresh); await refreshAll(false); setOk("Ответ сгенерирован"); }catch(e){ setErr("Ошибка генерации", e); }finally{ setLoading(false); } }
  async function saveAnswer(){ if(!selected?.item?.id) return; setLoading(true); try{ const base = selected.type === "question" ? "/questions" : "/reviews"; const fresh = await api(`${base}/${selected.item.id}/answer`, {method:"PATCH", body:JSON.stringify({final_answer:draft})}); pick(selected.type, fresh); await refreshAll(false); setOk("Ответ сохранен"); }catch(e){ setErr("Ошибка сохранения", e); }finally{ setLoading(false); } }
  async function publishSelected(){ if(!selected?.item?.id) return; setLoading(true); try{ const base = selected.type === "question" ? "/questions" : "/reviews"; setOk(`Публикация: ${pretty(await api(`${base}/${selected.item.id}/publish`, {method:"POST"}))}`); await refreshAll(false); }catch(e){ setErr("Ошибка публикации", e); }finally{ setLoading(false); } }

  function top(title, subtitle, actions=null){ return <div className="top"><div><h2>{title}</h2><p>{subtitle}</p></div><div className="actions">{actions}</div></div>; }
  function platformSwitch(){ return <div className="marketSwitcher">{PLATFORMS.map(p => <button key={p} className={platform===p?"active":""} onClick={()=>setPlatform(p)}>{p==="ALL"?"Все":p}<span>{p==="ALL"?allItems.length:allItems.filter(x=>x.platform===p).length}</span></button>)}</div>; }
  function filters(){ return <div className="sectionFilters"><div className="productFilterBox inline"><label>Состояние</label><select value={answerState} onChange={e=>setAnswerState(e.target.value)}>{ANSWER_STATES.map(([v,t])=><option key={v} value={v}>{t}</option>)}</select></div><div className="productFilterBox inline"><label>Товар / SKU</label><input value={product} onChange={e=>setProduct(e.target.value)} placeholder="артикул, название, id" /></div><button onClick={()=>refreshAll(true)}>Обновить сейчас</button></div>; }
  function cards(){ return <div className="cards"><button onClick={()=>setSection("reviews")}><b>{metrics.reviews}</b><span>Отзывы</span></button><button onClick={()=>setSection("questions")}><b>{metrics.questions}</b><span>Вопросы</span></button><button onClick={()=>setSection("reviews")}><b>{metrics.noAnswer}</b><span>Требуют ответа</span></button><button onClick={()=>setSection("summary")}><b>{metrics.products}</b><span>Товары в CX</span></button><button onClick={()=>setSection("autopublish")}><b>{metrics.ready}</b><span>Черновики</span></button><button onClick={()=>setSection("anomalies")}><b>{metrics.risk}</b><span>High risk</span></button></div>; }

  function renderDashboard(){ return <>{top("Операционный кабинет KARATOV CX Hub", "Общий обзор и разрез по выбранному маркетплейсу", <button className="primary" onClick={()=>refreshAll(true)}>Обновить</button>)}{cards()}<div className="settingsPanel"><div className="settingCard"><h3>Статус данных</h3><div className="metricRow"><span>Площадка</span><b>{platform==="ALL"?"Все":platform}</b></div><div className="metricRow"><span>UI refresh</span><b>{dt(lastRefreshAt)}</b></div><div className="metricRow"><span>Отзывы</span><b>{metrics.reviews}</b></div><div className="metricRow"><span>Вопросы</span><b>{metrics.questions}</b></div></div><div className="settingCard"><h3>Синхронизация</h3><div className="metricRow"><span>WB auto</span><b>{boolText(syncStatus?.auto_sync_enabled)}</b></div><div className="metricRow"><span>WB running</span><b>{boolText(syncStatus?.running)}</b></div><div className="metricRow"><span>WB mode</span><b>{syncStatus?.sync_mode || "—"}</b></div><button onClick={()=>loadSyncStatus(true)}>Обновить статус</button></div><div className="settingCard"><h3>API</h3>{diagnostics?.keys ? Object.entries(diagnostics.keys).map(([k,v])=><div className="metricRow" key={k}><span>{k}</span><b>{boolText(v)}</b></div>) : <p>Нет диагностики</p>}</div></div>{renderProductSummary(false)}</>; }

  function renderProductSummary(full=true){ return <div className="settingCard wide"><h3>{full ? "Саммари по товарам" : "Топ товаров по CX-сигналам"}</h3><table><thead><tr><th>SKU / товар</th><th>Всего</th><th>Нужно ответить</th><th>Черновики</th><th>Рейтинг</th><th>Тема</th><th>Риск</th><th>Дата</th></tr></thead><tbody>{productSummary.slice(0, full?50:10).map(p => <tr key={`${p.sku}-${p.product_name}`} className="clickableRow" onClick={()=>openProduct(p)}><td><b>{p.sku}</b><br/><span>{p.product_name}</span></td><td>{p.count}</td><td>{p.needs}</td><td>{p.drafts}</td><td>{p.avgRating ? p.avgRating.toFixed(2) : "—"}</td><td>{p.topCategory}</td><td>{p.riskCount ? <Badge type="red">{p.riskCount}</Badge> : <Badge>0</Badge>}</td><td>{dt(p.lastDate)}</td></tr>)}</tbody></table></div>; }

  function listPane(type){ const items = type === "question" ? questions : reviews; return <div className="list">{items.length===0 ? <div className="empty">Данных по выбранным фильтрам нет.</div> : items.map((item,i)=><div key={`${type}-${item.id||i}`} className={`row ${selected?.type===type && selected?.item?.id===item.id ? "selected" : ""}`} onClick={()=>pick(type,item)}><div className="rowhead"><b>{item.product_name || item.sku || `Запись ${item.id || i+1}`}</b><PlatformBadge p={item.platform}/></div><div className="dateMeta">{item.rating && <span>⭐ <b>{item.rating}</b></span>}<span>{dt(item.created_at_marketplace)}</span><span>{item.source_status || "—"}</span></div><div className="text">{item.text || item.pros || item.cons || "Без текста"}</div><div className="tags"><Badge>{item.status || "new"}</Badge>{item.ai_category && <Badge type="yellow">{item.ai_category}</Badge>}{item.ai_risk_level && <Badge type={item.ai_risk_level==="high"?"red":""}>{item.ai_risk_level}</Badge>}{item.final_answer && <Badge type="green">черновик</Badge>}</div></div>)}</div>; }
  function detailPane(){ const item = selected?.item; if(!item) return <div className="detail"><div className="empty">Выбери запись слева</div></div>; return <div className="detail"><div className="detailhead"><div><h3>{item.product_name || item.sku || "Карточка"}</h3><p className="meta">{selected.type === "question" ? "Вопрос" : "Отзыв"} · {item.platform} · {item.source_status}</p></div>{item.product_url && <a className="buttonLike" href={item.product_url} target="_blank" rel="noreferrer">Открыть товар</a>}</div><div className="clientText">{item.text || item.pros || item.cons || "Нет текста"}</div><div className="twoCols"><div className="exampleBox"><b>AI-категория</b><p>{item.ai_category || "—"}</p></div><div className="exampleBox"><b>Quality / причина</b><p>{item.ai_reason || item.publish_blocked_reason || "—"}</p></div></div><label>Финальный ответ</label><textarea value={draft} onChange={e=>setDraft(e.target.value)} placeholder="Сгенерируй или введи ответ"/><div className="actions"><button className="primary" onClick={generateSelected}>Сгенерировать 10/10</button><button onClick={saveAnswer}>Сохранить</button><button onClick={publishSelected}>Опубликовать</button><button onClick={()=>navigator.clipboard.writeText(draft||"")}>Скопировать</button></div></div>; }
  function renderWork(type){ return <>{top(type==="question"?"Вопросы покупателей":"Отзывы покупателей", "Автообновление и общий фильтр маркетплейса", <button className="primary" onClick={()=>refreshAll(true)}>Обновить</button>)}{filters()}<div className="workspace">{listPane(type)}{detailPane()}</div></>; }

  function renderReports(){ const answeredR = rawReviews.filter(byPlatform).filter(x=>x.has_answer || x.response_origin || String(x.status||"").includes("published")); const answeredQ = rawQuestions.filter(byPlatform).filter(x=>x.has_answer || x.response_origin || String(x.status||"").includes("published")); const ru60 = answeredR.filter(x=>{const m=mins(x.created_at_marketplace,x.updated_at); return m!==null && m<=60}).length; const ro60 = answeredR.filter(x=>{const m=mins(x.created_at_marketplace,x.updated_at); return m!==null && m>60}).length; const qu15 = answeredQ.filter(x=>{const m=mins(x.created_at_marketplace,x.updated_at); return m!==null && m<=15}).length; const qo15 = answeredQ.filter(x=>{const m=mins(x.created_at_marketplace,x.updated_at); return m!==null && m>15}).length; const table=(title, rows)=><div className="settingCard"><h3>{title}</h3><table><tbody>{rows.map(([k,v])=><tr key={k}><td>{k}</td><td><b>{v}</b></td></tr>)}</tbody></table></div>; return <>{top("Отчеты", "Total, SLA и динамика по выбранному маркетплейсу", <button onClick={()=>refreshAll(true)}>Обновить</button>)}<div className="cards"><button><b>{metrics.reviews}</b><span>Отзывы total</span></button><button><b>{metrics.questions}</b><span>Вопросы total</span></button><button><b>{ru60}</b><span>Отзывы ≤ 1 часа</span></button><button><b>{ro60}</b><span>Отзывы &gt; 1 часа</span></button><button><b>{qu15}</b><span>Вопросы ≤ 15 мин</span></button><button><b>{qo15}</b><span>Вопросы &gt; 15 мин</span></button></div><div className="settingsPanel">{table("Отзывы день к дню", groupCount(rawReviews.filter(byPlatform), x=>dayKey(x.created_at_marketplace)))}{table("Отзывы неделя к неделе", groupCount(rawReviews.filter(byPlatform), x=>weekKey(x.created_at_marketplace)))}{table("Отзывы месяц к месяцу", groupCount(rawReviews.filter(byPlatform), x=>monthKey(x.created_at_marketplace)))}{table("Вопросы день к дню", groupCount(rawQuestions.filter(byPlatform), x=>dayKey(x.created_at_marketplace)))}{table("Вопросы неделя к неделе", groupCount(rawQuestions.filter(byPlatform), x=>weekKey(x.created_at_marketplace)))}{table("Вопросы месяц к месяцу", groupCount(rawQuestions.filter(byPlatform), x=>monthKey(x.created_at_marketplace)))}</div><div className="settingCard wide"><h3>SLA уточнение</h3><p>Для точного SLA нужен backend-field answered_at/published_at. Сейчас расчет идет по created_at_marketplace → updated_at.</p></div></>; }
  function renderSummary(){ return <>{top("Саммари CX", "По умолчанию — товары, темы, риски и рекомендуемые действия", <button onClick={loadSummary}>AI-саммари JSON</button>)}{cards()}<div className="settingsPanel"><div className="settingCard wide"><h3>Executive summary</h3><p>По выбранному срезу: {metrics.reviews} отзывов, {metrics.questions} вопросов, {metrics.noAnswer} требуют ответа, {metrics.risk} high-risk сигналов. Главная тема: <b>{topCategory(platformItems)}</b>.</p></div>{renderProductSummary(true)}<div className="settingCard wide"><h3>AI-саммари backend</h3><pre className="reportText">{summary || "Нажми, если нужен технический backend summary"}</pre></div></div></>; }
  function renderAnomalies(){ return <>{top("Аномалии", "Товары с ростом риска, негатива или очереди без ответа", <button onClick={()=>refreshAll(true)}>Обновить</button>)}<div className="settingCard wide"><table><thead><tr><th>Товар</th><th>Сигнал</th><th>Что делать</th></tr></thead><tbody>{anomalies.map(p=><tr key={p.sku+p.product_name} className="clickableRow" onClick={()=>openProduct(p)}><td><b>{p.sku}</b><br/><span>{p.product_name}</span></td><td>{p.riskCount ? `High-risk: ${p.riskCount}` : p.negative ? `Негатив/низкая оценка: ${p.negative}` : `Очередь без ответа: ${p.needs}`}</td><td>Проверить карточку, отзывы, конструкцию/описание и подготовить ответ.</td></tr>)}</tbody></table></div></>; }
  function renderProduct(){ const p = selectedProduct || productSummary[0]; if(!p) return <>{top("Карточка товара", "Выбери SKU в саммари или аномалиях")}<div className="empty">Нет выбранного товара</div></>; const items = platformItems.filter(x => (x.sku || x.product_name) === p.sku || x.sku === p.sku || x.product_name === p.product_name); return <>{top("Карточка товара", `${p.sku} · ${p.product_name}`, <button onClick={()=>setSection("reviews")}>К отзывам</button>)}<div className="cards"><button><b>{items.length}</b><span>Сигналы</span></button><button><b>{p.needs}</b><span>Нужно ответить</span></button><button><b>{p.avgRating ? p.avgRating.toFixed(2) : "—"}</b><span>Рейтинг</span></button><button><b>{p.riskCount}</b><span>High risk</span></button></div><div className="workspace"><div className="list">{items.map((x,i)=><div className="row" key={x.id||i} onClick={()=>pick(x.rating?"review":"question",x)}><div className="rowhead"><b>{x.ai_category || "Без категории"}</b><PlatformBadge p={x.platform}/></div><div className="text">{x.text || x.pros || x.cons || "Без текста"}</div></div>)}</div>{detailPane()}</div></>; }

  function renderSettings(){ return <>{top("AI / шаблоны / quality gate", "Промты, шаблоны и правила", <><button onClick={()=>loadRules(true)}>Загрузить</button><button className="primary" onClick={saveRules}>Сохранить</button></>)}<div className="settingsPanel"><div className="settingCard"><h3>AI</h3><label className="check"><input type="checkbox" checked={!!rules.ai_generation_enabled} onChange={e=>updateRule("ai_generation_enabled", e.target.checked)}/> Генерация AI</label><label className="check"><input type="checkbox" checked={!!rules.ai_fallback_to_local_templates} onChange={e=>updateRule("ai_fallback_to_local_templates", e.target.checked)}/> Fallback шаблоны</label><label className="check"><input type="checkbox" checked={!!rules.auto_generate_on_sync} onChange={e=>updateRule("auto_generate_on_sync", e.target.checked)}/> Автогенерация при синке</label></div><div className="settingCard"><h3>Quality gate</h3><div className="metricRow"><span>Публикация только если</span><b>10/10</b></div><label>Макс. длина</label><input type="number" value={rules.max_auto_answer_chars||900} onChange={e=>updateRule("max_auto_answer_chars", Number(e.target.value))}/></div><div className="settingCard wide"><h3>Системный промт</h3><textarea className="templateText" value={rules.custom_system_prompt||""} onChange={e=>updateRule("custom_system_prompt", e.target.value)}/></div><div className="settingCard wide"><h3>Промт отзывов</h3><textarea className="largeText" value={rules.review_prompt_template||""} onChange={e=>updateRule("review_prompt_template", e.target.value)}/></div><div className="settingCard wide"><h3>Промт вопросов</h3><textarea className="largeText" value={rules.question_prompt_template||""} onChange={e=>updateRule("question_prompt_template", e.target.value)}/></div><div className="settingCard wide"><h3>Шаблоны fallback</h3><textarea className="templateText" value={rules.local_templates_text||""} onChange={e=>updateRule("local_templates_text", e.target.value)}/></div></div></>; }
  function renderAutopublish(){ return <>{top("Автопубликация", "Матрица и лимиты", <><button onClick={()=>loadRules(true)}>Загрузить</button><button className="primary" onClick={saveRules}>Сохранить</button><button onClick={runAutopublish}>Запустить</button></>)}<div className="settingsPanel"><div className="settingCard"><h3>Главный переключатель</h3><label className="check"><input type="checkbox" checked={!!rules.real_autopublish_enabled} onChange={e=>updateRule("real_autopublish_enabled", e.target.checked)}/> Автопубликация разрешена</label><p className="meta">Нужен также ENABLE_MARKETPLACE_PUBLISHING=true.</p></div><div className="settingCard"><h3>Лимиты</h3><input type="number" value={rules.autopublish_max_per_run||10} onChange={e=>updateRule("autopublish_max_per_run", Number(e.target.value))}/></div></div><div className="matrixGrid">{["WB","OZON","YM"].map(p=><div className="matrixCard" key={p}><b>{p}</b><label className="check"><input type="checkbox" checked={!!rules.autopublish_matrix?.[p]?.reviews} onChange={e=>updateMatrix(p,"reviews",e.target.checked)}/>Отзывы</label><label className="check"><input type="checkbox" checked={!!rules.autopublish_matrix?.[p]?.questions} onChange={e=>updateMatrix(p,"questions",e.target.checked)}/>Вопросы</label></div>)}</div></>; }
  function renderSync(){ return <>{top("Синхронизация", "Автоматическая загрузка данных и аварийные ручные действия", <button onClick={()=>loadSyncStatus(true)}>Обновить</button>)}<div className="settingsPanel"><div className="settingCard"><h3>WB</h3><div className="blockButtons"><button onClick={()=>runSync("/sync/wb","WB next")}>Следующий блок</button><button onClick={()=>runSync("/sync/wb/operational/next","WB без ответа")}>Без ответа</button><button onClick={()=>runSync("/sync/wb/backfill/next","WB архив")}>Архив</button></div></div><div className="settingCard"><h3>Ozon</h3><div className="blockButtons"><button onClick={()=>runSync("/sync/ozon","Ozon all")}>Все блоки</button><button onClick={()=>runSync("/sync/ozon/block/reviews_unanswered","Ozon отзывы")}>Отзывы без ответа</button><button onClick={()=>runSync("/sync/ozon/block/questions_unanswered","Ozon вопросы")}>Вопросы без ответа</button></div></div><div className="settingCard wide"><pre className="reportText">{pretty({wb:syncStatus, ozon:ozonStatus})}</pre></div></div></>; }
  function renderBooking(){ const cfg = bookingConfig || {}; const setCfg=(k,v)=>setBookingConfig(prev=>({...prev,[k]:v})); return <>{top("WB FBO Slot Hunter", "Поиск слотов поставки: Коледино/Электросталь, Суперсейф, лимит коэффициента", <><button onClick={()=>loadBooking(true)}>Обновить</button><button className="primary" onClick={checkBooking}>Проверить сейчас</button></>)}<div className="settingsPanel"><div className="settingCard"><h3>Режим</h3><label className="check"><input type="checkbox" checked={!!cfg.enabled} onChange={e=>toggleBooking(e.target.checked)}/> Мониторинг включен</label><label>Действие</label><select value={cfg.mode||"monitor_only"} onChange={e=>setCfg("mode",e.target.value)}><option value="monitor_only">Только уведомить</option><option value="reserve_draft">Резерв черновика</option><option value="auto_book">Автобронирование</option></select><p className="meta">До подтверждения WB endpoint бронирования безопасный режим — monitor only.</p></div><div className="settingCard"><h3>Правила слота</h3><label>Склады</label><textarea value={(cfg.warehouses||[]).join("\n")} onChange={e=>setCfg("warehouses", e.target.value.split("\n").map(x=>x.trim()).filter(Boolean))}/><label>Коэффициент ≤</label><input type="number" value={cfg.coefficient_limit||20} onChange={e=>setCfg("coefficient_limit", Number(e.target.value))}/><label>Интервал проверки, сек</label><input type="number" value={cfg.check_interval_seconds||300} onChange={e=>setCfg("check_interval_seconds", Number(e.target.value))}/><button className="primary" onClick={()=>saveBookingConfig()}>Сохранить настройки</button></div><div className="settingCard"><h3>Статус</h3><div className="metricRow"><span>Включен</span><b>{boolText(booking?.enabled)}</b></div><div className="metricRow"><span>Running</span><b>{boolText(booking?.running)}</b></div><div className="metricRow"><span>Ошибка</span><b>{booking?.last_error || "—"}</b></div></div><div className="settingCard wide"><h3>Найденные слоты</h3><table><tbody>{(booking?.last_matched_slots||[]).map((s,i)=><tr key={i}><td>{s.warehouse}</td><td>{s.date}</td><td>{s.coefficient}</td><td>{s.score}</td></tr>)}</tbody></table></div><div className="settingCard wide"><h3>Журнал действий</h3><table><tbody>{(booking?.events||[]).map((e,i)=><tr key={i}><td>{dt(e.created_at)}</td><td>{e.event_type}</td><td>{e.status}</td><td>{e.message}</td></tr>)}</tbody></table></div></div></>; }
  function renderSystem(){ return <>{top("Диагностика", "Технический статус", <button onClick={()=>loadDiagnostics(true)}>Обновить</button>)}<div className="settingCard wide"><pre className="reportText">{pretty(diagnostics || "Нет диагностики")}</pre></div></>; }

  function content(){ if(section==="dashboard") return renderDashboard(); if(section==="reviews") return renderWork("review"); if(section==="questions") return renderWork("question"); if(section==="reports") return renderReports(); if(section==="summary") return renderSummary(); if(section==="anomalies") return renderAnomalies(); if(section==="product") return renderProduct(); if(section==="settings") return renderSettings(); if(section==="autopublish") return renderAutopublish(); if(section==="sync") return renderSync(); if(section==="booking") return renderBooking(); if(section==="system") return renderSystem(); return renderDashboard(); }

  return <div className="app"><aside><h1>KARATOV<br/>CX Hub</h1>{platformSwitch()}<div className={`currentPlatform ${platform}`}>{platform==="ALL"?"Все площадки":platform}</div>{NAV.map(([id,title])=><button key={id} className={section===id?"active":""} onClick={()=>setSection(id)}>{title}{id==="reviews"&&<span className="navCount">{metrics.reviews}</span>}{id==="questions"&&<span className="navCount">{metrics.questions}</span>}{id==="anomalies"&&<span className="navCount">{anomalies.length}</span>}</button>)}<div className="syncMini">Backend: {diagnostics?.status || "ok"}<br/>UI refresh: {dt(lastRefreshAt)}<br/>{loading ? "Выполняется..." : "Готово"}</div><div className="hint">Переключатель площадки влияет на все разделы.</div></aside><main>{message && <div className="message">{message}</div>}{content()}</main></div>;
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
