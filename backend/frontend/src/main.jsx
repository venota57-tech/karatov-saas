import React, { useEffect, useMemo, useState } from "react";
import ReactDOM from "react-dom/client";
import "./style.css";

const NAV = [
  ["dashboard", "Дашборд"],
  ["reviews", "Отзывы"],
  ["questions", "Вопросы"],
  ["reports", "Отчеты"],
  ["summary", "Саммари CX"],
  ["settings", "AI / шаблоны"],
  ["autopublish", "Автопубликация"],
  ["sync", "Синхронизация"],
  ["booking", "WB FBO слоты"],
  ["system", "Диагностика"],
];

const PLATFORMS = ["WB", "OZON", "YM"];
const ANSWER_STATES = [
  ["all", "Все"],
  ["unanswered", "Без ответа"],
  ["answered", "С ответом"],
  ["manual", "Черновики/ручная"],
  ["auto_published", "Опубликовано"],
  ["stale", "Устаревшие"],
];

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
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

function Badge({ children, type = "" }) { return <span className={`badge ${type}`}>{children}</span>; }
function PlatformBadge({ p }) { return <span className={`platformBadge ${p || ""}`}>{p || "—"}</span>; }

function App() {
  const [section, setSection] = useState("dashboard");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [platform, setPlatform] = useState("WB");
  const [answerState, setAnswerState] = useState("all");
  const [product, setProduct] = useState("");
  const [category, setCategory] = useState("");
  const [risk, setRisk] = useState("");
  const [reviews, setReviews] = useState([]);
  const [questions, setQuestions] = useState([]);
  const [selected, setSelected] = useState(null);
  const [draft, setDraft] = useState("");
  const [rules, setRules] = useState({});
  const [diagnostics, setDiagnostics] = useState(null);
  const [syncStatus, setSyncStatus] = useState(null);
  const [ozonStatus, setOzonStatus] = useState(null);
  const [report, setReport] = useState("");
  const [summary, setSummary] = useState("");
  const [booking, setBooking] = useState(null);

  const allItems = useMemo(() => [...reviews, ...questions], [reviews, questions]);
  const metrics = useMemo(() => ({
    reviews: reviews.length,
    questions: questions.length,
    noAnswer: allItems.filter(x => x.operational_status === "needs_response" || (!x.has_answer && !x.final_answer)).length,
    ready: allItems.filter(x => x.status === "ready_to_publish").length,
    risk: allItems.filter(x => x.ai_risk_level === "high").length,
  }), [allItems]);

  useEffect(() => { bootstrap(); }, []);

  async function bootstrap() {
    await Promise.allSettled([loadDiagnostics(false), loadRules(false), loadSyncStatus(false), loadBooking(false)]);
  }

  function setOk(text) { setMessage(text); }
  function setErr(prefix, e) { setMessage(`${prefix}: ${e.message}`); }

  function queryParams(extra = {}) {
    const p = new URLSearchParams();
    if (platform !== "ALL") p.set("platform", platform);
    if (answerState) p.set("answer_state", answerState);
    if (product.trim()) p.set("product", product.trim());
    if (category.trim()) p.set("category", category.trim());
    if (risk.trim()) p.set("risk", risk.trim());
    p.set("limit", "500");
    for (const [k, v] of Object.entries(extra)) if (v !== undefined && v !== null && v !== "") p.set(k, v);
    return `?${p.toString()}`;
  }

  async function loadReviews(show = true) {
    setLoading(true); if (show) setOk("Загружаю отзывы...");
    try {
      const data = await api(`/reviews${queryParams()}`);
      const list = asList(data);
      setReviews(list);
      if (!selected && list[0]) { setSelected({ type: "review", item: list[0] }); setDraft(list[0].final_answer || list[0].draft_answer || ""); }
      if (show) setOk(`Отзывы загружены: ${list.length}`);
    } catch (e) { setErr("Ошибка загрузки отзывов", e); }
    finally { setLoading(false); }
  }

  async function loadQuestions(show = true) {
    setLoading(true); if (show) setOk("Загружаю вопросы...");
    try {
      const data = await api(`/questions${queryParams()}`);
      const list = asList(data);
      setQuestions(list);
      if (!selected && list[0]) { setSelected({ type: "question", item: list[0] }); setDraft(list[0].final_answer || list[0].draft_answer || ""); }
      if (show) setOk(`Вопросы загружены: ${list.length}`);
    } catch (e) { setErr("Ошибка загрузки вопросов", e); }
    finally { setLoading(false); }
  }

  async function loadCurrent() { return section === "questions" ? loadQuestions() : loadReviews(); }

  async function generateSelected() {
    if (!selected?.item?.id) return;
    setLoading(true); setOk("Генерирую ответ через AI / локальные шаблоны с quality gate 10/10...");
    try {
      const base = selected.type === "question" ? "/questions" : "/reviews";
      const fresh = await api(`${base}/${selected.item.id}/generate`, { method: "POST" });
      setSelected({ ...selected, item: fresh });
      setDraft(fresh.final_answer || fresh.draft_answer || "");
      setOk(fresh.final_answer ? "Ответ прошел quality gate и готов к проверке" : "Ответ не прошел quality gate 10/10 — нужна ручная правка");
      await loadCurrent();
    } catch (e) { setErr("Ошибка генерации", e); }
    finally { setLoading(false); }
  }

  async function saveAnswer() {
    if (!selected?.item?.id) return;
    setLoading(true); setOk("Сохраняю ответ...");
    try {
      const base = selected.type === "question" ? "/questions" : "/reviews";
      const fresh = await api(`${base}/${selected.item.id}/answer`, { method: "PATCH", body: JSON.stringify({ final_answer: draft }) });
      setSelected({ ...selected, item: fresh });
      setOk("Ответ сохранен как готовый к публикации / ручной проверке");
      await loadCurrent();
    } catch (e) { setErr("Ошибка сохранения", e); }
    finally { setLoading(false); }
  }

  async function publishSelected() {
    if (!selected?.item?.id) return;
    setLoading(true); setOk("Публикую / dry-run в зависимости от ENABLE_MARKETPLACE_PUBLISHING...");
    try {
      const base = selected.type === "question" ? "/questions" : "/reviews";
      const result = await api(`${base}/${selected.item.id}/publish`, { method: "POST" });
      setOk(`Результат публикации: ${pretty(result)}`);
      await loadCurrent();
    } catch (e) { setErr("Ошибка публикации", e); }
    finally { setLoading(false); }
  }

  async function loadRules(show = true) {
    if (show) setOk("Загружаю AI-правила и шаблоны...");
    try {
      const data = await api("/settings/automation-rules");
      setRules(data || {});
      if (show) setOk("Правила загружены");
    } catch (e) { setErr("Ошибка загрузки правил", e); }
  }

  async function saveRules() {
    setLoading(true); setOk("Сохраняю AI-правила...");
    try {
      const payload = { ...rules };
      delete payload.updated_at;
      const data = await api("/settings/automation-rules", { method: "PUT", body: JSON.stringify(payload) });
      setRules(data || payload);
      await api("/autopublish-settings/api", { method: "POST", body: JSON.stringify(payload) }).catch(() => null);
      setOk("AI-правила, шаблоны и автопубликация сохранены");
    } catch (e) { setErr("Ошибка сохранения правил", e); }
    finally { setLoading(false); }
  }

  async function runAutopublish() {
    setLoading(true); setOk("Запускаю один проход автопубликации...");
    try { setOk(`Автопубликация: ${pretty(await api("/autopublish", { method: "POST" }))}`); }
    catch (e) { setErr("Ошибка автопубликации", e); }
    finally { setLoading(false); }
  }

  async function loadDiagnostics(show = true) {
    if (show) setOk("Загружаю диагностику...");
    try {
      const data = await api("/system/diagnostics").catch(() => api("/system/status"));
      setDiagnostics(data);
      if (data?.rules) setRules(data.rules);
      if (show) setOk("Диагностика обновлена");
    } catch (e) { setErr("Ошибка диагностики", e); }
  }

  async function loadSyncStatus(show = true) {
    try {
      const wb = await api("/sync/status").catch(() => null);
      const oz = await api("/ozon-sync/status").catch(() => null);
      setSyncStatus(wb); setOzonStatus(oz);
      if (show) setOk("Статусы синхронизации обновлены");
    } catch (e) { if (show) setErr("Ошибка статуса синхронизации", e); }
  }

  async function runSync(path, label) {
    setLoading(true); setOk(`Запускаю ${label}...`);
    try {
      const data = await api(path, { method: "POST" });
      setOk(`${label}: ${pretty(data)}`);
      await Promise.allSettled([loadReviews(false), loadQuestions(false), loadSyncStatus(false), loadDiagnostics(false)]);
    } catch (e) { setErr(`Ошибка ${label}`, e); }
    finally { setLoading(false); }
  }

  async function loadReport(path = "/reports/daily") {
    setLoading(true); setOk("Формирую отчет...");
    try { setReport(pretty(await api(path))); setOk("Отчет сформирован"); }
    catch (e) { setErr("Ошибка отчета", e); }
    finally { setLoading(false); }
  }

  async function loadSummary() {
    setLoading(true); setOk("Формирую CX-саммари...");
    try {
      const paths = ["/summary", "/summary/cx", "/reports/summary"];
      let data = null, err = null;
      for (const p of paths) { try { data = await api(p); break; } catch(e) { err = e; } }
      if (!data && err) throw err;
      setSummary(pretty(data)); setOk("Саммари сформировано");
    } catch (e) { setErr("Ошибка саммари", e); }
    finally { setLoading(false); }
  }

  async function loadBooking(show = true) {
    try { const data = await api("/wb-booking/status"); setBooking(data); if (show) setOk("Статус WB FBO слотов обновлен"); }
    catch (e) { if (show) setErr("Ошибка WB FBO модуля", e); }
  }

  async function toggleBooking(enabled) {
    setLoading(true);
    try {
      const data = await api(enabled ? "/wb-booking/start" : "/wb-booking/stop", { method: "POST", body: JSON.stringify(booking || {}) });
      setBooking(data); setOk(enabled ? "Мониторинг WB FBO слотов включен" : "Мониторинг WB FBO слотов остановлен");
    } catch (e) { setErr("Ошибка WB FBO", e); }
    finally { setLoading(false); }
  }

  function updateRule(key, value) { setRules(prev => ({ ...prev, [key]: value })); }
  function updateMatrix(p, kind, value) {
    setRules(prev => ({ ...prev, autopublish_matrix: { ...(prev.autopublish_matrix || {}), [p]: { ...(prev.autopublish_matrix?.[p] || {}), [kind]: value } } }));
  }

  function top(title, subtitle, actions = null) {
    return <div className="top"><div><h2>{title}</h2><p>{subtitle}</p></div><div className="actions">{actions}</div></div>;
  }

  function renderDashboard() {
    return <>
      {top("Операционный кабинет KARATOV CX Hub", "Отзывы, вопросы, AI-ответы, отчеты, настройки, автосинхронизация и автопубликация")}
      <div className="cards">
        <button onClick={() => setSection("reviews")}><b>{metrics.reviews}</b><span>Отзывы</span></button>
        <button onClick={() => setSection("questions")}><b>{metrics.questions}</b><span>Вопросы</span></button>
        <button onClick={() => setSection("autopublish")}><b>{metrics.ready}</b><span>Готово к публикации</span></button>
        <button onClick={() => setSection("reports")}><b>{metrics.risk}</b><span>Риски</span></button>
      </div>
      <div className="settingsPanel">
        <div className="settingCard"><h3>API-ключи</h3>
          {diagnostics?.keys ? Object.entries(diagnostics.keys).map(([k,v]) => <div className="metricRow" key={k}><span>{k}</span><b>{boolText(v)}</b></div>) : <p>Нажми «Обновить диагностику»</p>}
          <button onClick={() => loadDiagnostics(true)}>Обновить диагностику</button>
        </div>
        <div className="settingCard"><h3>Быстрые действия</h3><div className="blockButtons">
          <button onClick={() => runSync("/sync/wb", "WB sync next block")}>WB: безопасная синхронизация</button>
          <button onClick={() => runSync("/ozon-sync/all", "Ozon sync all")}>Ozon: синхронизация</button>
          <button onClick={() => { loadReviews(); loadQuestions(); }}>Обновить списки</button>
          <button onClick={runAutopublish}>Запустить автопубликацию</button>
        </div></div>
        <div className="settingCard"><h3>Режим публикации</h3>
          <div className="metricRow"><span>Marketplace publishing</span><b>{diagnostics?.publishing?.mode || "—"}</b></div>
          <div className="metricRow"><span>OpenAI model</span><b>{diagnostics?.openai?.model || "—"}</b></div>
          <div className="metricRow"><span>AI fallback</span><b>{boolText(rules.ai_fallback_to_local_templates)}</b></div>
        </div>
      </div>
    </>;
  }

  function filters() { return <div className="sectionFilters">
    <div className="productFilterBox inline"><label>Площадка</label><select value={platform} onChange={e => setPlatform(e.target.value)}><option value="ALL">Все</option>{PLATFORMS.map(p => <option key={p}>{p}</option>)}</select></div>
    <div className="productFilterBox inline"><label>Состояние</label><select value={answerState} onChange={e => setAnswerState(e.target.value)}>{ANSWER_STATES.map(([v,t]) => <option key={v} value={v}>{t}</option>)}</select></div>
    <div className="productFilterBox inline"><label>Товар / SKU</label><input value={product} onChange={e => setProduct(e.target.value)} placeholder="артикул, название, id" /></div>
    <button onClick={loadCurrent}>Применить</button>
  </div>; }

  function listPane(type) {
    const items = type === "question" ? questions : reviews;
    return <div className="list">
      {items.length === 0 ? <div className="empty">Данных нет. Запусти синхронизацию или поменяй фильтры.</div> : items.map((item, i) => <div key={`${type}-${item.id || i}`} className={`row ${selected?.type === type && selected?.item?.id === item.id ? "selected" : ""}`} onClick={() => { setSelected({ type, item }); setDraft(item.final_answer || item.draft_answer || ""); }}>
        <div className="rowhead"><b>{item.product_name || item.sku || `Запись ${item.id || i + 1}`}</b><PlatformBadge p={item.platform} /></div>
        <div className="dateMeta">{item.rating && <span>⭐ <b>{item.rating}</b></span>}<span>{dt(item.created_at_marketplace)}</span><span>{item.source_status || "—"}</span></div>
        <div className="text">{item.text || "Без текста"}</div>
        <div className="tags"><Badge>{item.status || "new"}</Badge>{item.ai_category && <Badge type="yellow">{item.ai_category}</Badge>}{item.ai_risk_level && <Badge type={item.ai_risk_level === "high" ? "red" : ""}>{item.ai_risk_level}</Badge>}{item.ai_can_autopublish && <Badge type="green">AI can publish</Badge>}</div>
      </div>)}
    </div>;
  }

  function detailPane() {
    const item = selected?.item;
    if (!item) return <div className="detail"><div className="empty">Выбери запись слева</div></div>;
    return <div className="detail">
      <div className="detailhead"><div><h3>{item.product_name || item.sku || "Карточка"}</h3><p className="meta">{selected.type === "question" ? "Вопрос" : "Отзыв"} · {item.platform} · {item.source_status}</p></div>{item.product_url && <a className="buttonLike" href={item.product_url} target="_blank" rel="noreferrer">Открыть товар</a>}</div>
      <div className="clientText">{item.text || "Нет текста"}</div>
      <div className="twoCols"><div className="exampleBox"><b>AI-категория</b><p>{item.ai_category || "—"}</p></div><div className="exampleBox"><b>Quality / причина</b><p>{item.ai_reason || item.publish_blocked_reason || "—"}</p></div></div>
      <label>Финальный ответ</label><textarea value={draft} onChange={e => setDraft(e.target.value)} placeholder="Сгенерируй или введи ответ" />
      <div className="actions"><button className="primary" onClick={generateSelected}>Сгенерировать 10/10</button><button onClick={saveAnswer}>Сохранить</button><button onClick={publishSelected}>Опубликовать / dry-run</button><button onClick={() => navigator.clipboard.writeText(draft || "")}>Скопировать</button></div>
    </div>;
  }

  function renderWork(type) {
    return <>{top(type === "question" ? "Вопросы покупателей" : "Отзывы покупателей", "Реальные данные из WB/Ozon после синхронизации, генерация AI и публикация", <button className="primary" onClick={type === "question" ? loadQuestions : loadReviews}>{loading ? "Загрузка..." : "Обновить"}</button>)}{filters()}<div className="workspace">{listPane(type)}{detailPane()}</div></>;
  }

  function renderSettings() {
    return <>{top("AI / шаблоны / quality gate", "Промты, локальные шаблоны, fallback, подписи, запреты и правила качества", <><button onClick={() => loadRules(true)}>Загрузить</button><button className="primary" onClick={saveRules}>Сохранить</button></>)}
      <div className="settingsPanel">
        <div className="settingCard"><h3>AI</h3>
          <label className="check"><input type="checkbox" checked={!!rules.ai_generation_enabled} onChange={e => updateRule("ai_generation_enabled", e.target.checked)} /> Генерация AI включена</label>
          <label className="check"><input type="checkbox" checked={!!rules.ai_fallback_to_local_templates} onChange={e => updateRule("ai_fallback_to_local_templates", e.target.checked)} /> Fallback на локальные шаблоны</label>
          <label className="check"><input type="checkbox" checked={!!rules.auto_generate_on_sync} onChange={e => updateRule("auto_generate_on_sync", e.target.checked)} /> Автогенерация при синхронизации</label>
        </div>
        <div className="settingCard"><h3>Quality gate</h3>
          <div className="metricRow"><span>Публикация только если</span><b>10/10</b></div>
          <label>Макс. длина ответа</label><input type="number" value={rules.max_auto_answer_chars || 900} onChange={e => updateRule("max_auto_answer_chars", Number(e.target.value))} />
          <label>Категории ручной проверки</label><textarea value={(rules.require_review_categories || []).join("\n")} onChange={e => updateRule("require_review_categories", e.target.value.split("\n").map(x=>x.trim()).filter(Boolean))} />
        </div>
        <div className="settingCard"><h3>Подписи</h3><textarea value={(rules.signatures || []).join("\n")} onChange={e => updateRule("signatures", e.target.value.split("\n").map(x=>x.trim()).filter(Boolean))} /></div>
        <div className="settingCard wide"><h3>Системный промт KARATOV</h3><textarea className="templateText" value={rules.custom_system_prompt || ""} onChange={e => updateRule("custom_system_prompt", e.target.value)} /></div>
        <div className="settingCard wide"><h3>Промт для отзывов</h3><textarea className="largeText" value={rules.review_prompt_template || ""} onChange={e => updateRule("review_prompt_template", e.target.value)} /></div>
        <div className="settingCard wide"><h3>Промт для вопросов</h3><textarea className="largeText" value={rules.question_prompt_template || ""} onChange={e => updateRule("question_prompt_template", e.target.value)} /></div>
        <div className="settingCard wide"><h3>Правила использования шаблонов</h3><textarea className="templateText" value={rules.template_rules_text || ""} onChange={e => updateRule("template_rules_text", e.target.value)} /></div>
        <div className="settingCard wide"><h3>Локальные шаблоны fallback</h3><textarea className="templateText" value={rules.local_templates_text || ""} onChange={e => updateRule("local_templates_text", e.target.value)} /></div>
        <div className="settingCard wide"><h3>Запрещенные фразы</h3><textarea value={(rules.forbidden_phrases || []).join("\n")} onChange={e => updateRule("forbidden_phrases", e.target.value.split("\n").map(x=>x.trim()).filter(Boolean))} /></div>
      </div>
    </>;
  }

  function renderAutopublish() {
    return <>{top("Автопубликация", "Матрица площадок, лимиты, dry-run/real publish и ручные ограничения", <><button onClick={() => loadRules(true)}>Загрузить</button><button className="primary" onClick={saveRules}>Сохранить</button><button onClick={runAutopublish}>Запустить сейчас</button></>)}
      <div className="settingsPanel">
        <div className="settingCard"><h3>Главный переключатель</h3><label className="check"><input type="checkbox" checked={!!rules.real_autopublish_enabled} onChange={e => updateRule("real_autopublish_enabled", e.target.checked)} /> Автопубликация разрешена в правилах</label><p className="meta">Реальная отправка дополнительно требует ENABLE_MARKETPLACE_PUBLISHING=true на Render.</p></div>
        <div className="settingCard"><h3>Лимиты</h3><label>Макс. за запуск</label><input type="number" value={rules.autopublish_max_per_run || 10} onChange={e => updateRule("autopublish_max_per_run", Number(e.target.value))} /><label>Пауза между публикациями, сек</label><input type="number" value={rules.autopublish_pause_between_items_seconds || 8} onChange={e => updateRule("autopublish_pause_between_items_seconds", Number(e.target.value))} /></div>
        <div className="settingCard"><h3>Минимальная оценка</h3><input type="number" min="1" max="5" value={rules.positive_review_min_rating || 5} onChange={e => updateRule("positive_review_min_rating", Number(e.target.value))} /></div>
      </div>
      <div className="matrixGrid">{PLATFORMS.map(p => <div className="matrixCard" key={p}><b>{p}</b><label className="check"><input type="checkbox" checked={!!rules.autopublish_matrix?.[p]?.reviews} onChange={e => updateMatrix(p, "reviews", e.target.checked)} />Отзывы</label><label className="check"><input type="checkbox" checked={!!rules.autopublish_matrix?.[p]?.questions} onChange={e => updateMatrix(p, "questions", e.target.checked)} />Вопросы</label></div>)}</div>
    </>;
  }

  function renderSync() {
    return <>{top("Синхронизация", "WB/Ozon обновляются автоматически; ручные кнопки запускают безопасные блоки", <button onClick={() => loadSyncStatus(true)}>Обновить статус</button>)}
      <div className="settingsPanel">
        <div className="settingCard"><h3>WB</h3><div className="blockButtons"><button onClick={() => runSync("/sync/wb", "WB next block")}>Следующий безопасный блок</button><button onClick={() => runSync("/sync/wb/operational/next", "WB operational")}>Очередь без ответа</button><button onClick={() => runSync("/sync/wb/backfill/next", "WB archive backfill")}>Дозагрузка архива</button></div></div>
        <div className="settingCard"><h3>Ozon</h3><div className="blockButtons"><button onClick={() => runSync("/ozon-sync/all", "Ozon all")}>Все блоки</button><button onClick={() => runSync("/ozon-sync/next", "Ozon next")}>Следующий блок</button></div></div>
        <div className="settingCard"><h3>Статус автообновления</h3><pre className="reportText smallPre">{pretty({ wb: syncStatus, ozon: ozonStatus })}</pre></div>
      </div>
      <div className="settingCard wide"><h3>WB blocks/cooldown</h3><pre className="reportText">{pretty(syncStatus || "Нет статуса")}</pre></div>
    </>;
  }

  function renderReports() {
    return <>{top("Отчеты", "Динамика, SLA, категории, товары, CSV-выгрузки", <><button onClick={() => loadReport("/reports/daily")}>Daily</button><button onClick={() => loadReport("/reports/products")}>Products</button><button onClick={() => loadReport("/reports/categories")}>Categories</button></>)}<div className="settingCard wide"><pre className="reportText">{report || "Выбери отчет сверху"}</pre></div></>;
  }

  function renderSummary() { return <>{top("Саммари CX", "Сводка по отзывам, вопросам, рискам и тематикам", <button onClick={loadSummary}>Сформировать</button>)}<div className="settingCard wide"><pre className="reportText">{summary || "Нажми «Сформировать»"}</pre></div></>; }

  function renderBooking() { return <>{top("WB FBO слоты", "Модуль управления автобронированием поставок: Коледино/Электросталь, Суперсейф, коэффициент 20", <><button onClick={() => loadBooking(true)}>Обновить</button><button className="primary" onClick={() => toggleBooking(true)}>Включить</button><button onClick={() => toggleBooking(false)}>Остановить</button></>)}<div className="settingsPanel"><div className="settingCard"><h3>Параметры</h3><div className="metricRow"><span>Склады</span><b>{(booking?.warehouses || []).join(", ")}</b></div><div className="metricRow"><span>Тип</span><b>{booking?.supply_type || "Суперсейф"}</b></div><div className="metricRow"><span>Коэффициент</span><b>{booking?.coefficient_limit || 20}x</b></div></div><div className="settingCard wide"><h3>Статус</h3><pre className="reportText">{pretty(booking || "Нет статуса")}</pre></div></div></>; }

  function renderSystem() { return <>{top("Диагностика", "Ключи не раскрываются, показывается только наличие и статусы модулей", <button onClick={() => loadDiagnostics(true)}>Обновить</button>)}<div className="settingCard wide"><pre className="reportText">{pretty(diagnostics || "Нет диагностики")}</pre></div></>; }

  function content() {
    if (section === "dashboard") return renderDashboard();
    if (section === "reviews") return renderWork("review");
    if (section === "questions") return renderWork("question");
    if (section === "settings") return renderSettings();
    if (section === "autopublish") return renderAutopublish();
    if (section === "sync") return renderSync();
    if (section === "reports") return renderReports();
    if (section === "summary") return renderSummary();
    if (section === "booking") return renderBooking();
    if (section === "system") return renderSystem();
    return renderDashboard();
  }

  return <div className="app"><aside><h1>KARATOV<br/>CX Hub</h1><div className={`currentPlatform ${platform}`}>{platform}</div>{NAV.map(([id, title]) => <button key={id} className={section === id ? "active" : ""} onClick={() => setSection(id)}>{title}{id === "reviews" && <span className="navCount">{metrics.reviews}</span>}{id === "questions" && <span className="navCount">{metrics.questions}</span>}</button>)}<div className="syncMini">Backend: {diagnostics?.status || "ok"}<br/>{loading ? "Выполняется..." : "Готово"}</div><div className="hint">Боевой интерфейс: реальные роуты, AI-правила, шаблоны, автосинк, отчеты, dry-run/real publish.</div></aside><main>{message && <div className="message">{message}</div>}{content()}</main></div>;
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
