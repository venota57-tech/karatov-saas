import React, { useEffect, useMemo, useState } from "react";
import ReactDOM from "react-dom/client";
import "./style.css";

const NAV = [
  ["dashboard", "Дашборд"],
  ["reviews", "Отзывы"],
  ["questions", "Вопросы"],
  ["summary", "AI Summary"],
  ["products", "Товары"],
  ["anomalies", "Аномалии"],
  ["reports", "Отчеты"],
  ["autopublish", "Автопубликация"],
  ["sync", "Синхронизация"],
  ["booking", "WB FBO слоты"],
  ["settings", "AI / шаблоны"],
  ["system", "Диагностика"],
];

const PLATFORMS = ["ALL", "WB", "OZON", "YM"];
const STATES = [["all", "Все"], ["unanswered", "Без ответа"], ["drafts", "Черновики"], ["answered", "С ответом"]];

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) throw new Error(typeof data === "object" ? (data.detail || data.error || JSON.stringify(data)) : data);
  return data;
}
function asList(d) { return Array.isArray(d) ? d : (d?.items || d?.data || d?.reviews || d?.questions || []); }
function pretty(x) { return typeof x === "string" ? x : JSON.stringify(x, null, 2); }
function dt(v) { return v ? String(v).replace("T", " ").slice(0, 19) : "—"; }
function bool(v) { return v ? "да" : "нет"; }
function Badge({children, type=""}) { return <span className={`badge ${type}`}>{children}</span>; }
function PlatformBadge({p}) { return <span className={`platformBadge ${p || ""}`}>{p || "—"}</span>; }
function productUrl(item) {
  if (item?.product_url) return item.product_url;
  if (!item?.sku) return null;
  if (item.platform === "WB") return `https://www.wildberries.ru/catalog/${item.sku}/detail.aspx`;
  if (item.platform === "OZON") return `https://www.ozon.ru/search/?text=${item.sku}`;
  return null;
}
function dayKey(v) { return v ? String(v).slice(0, 10) : "Без даты"; }
function monthKey(v) { return v ? String(v).slice(0, 7) : "Без месяца"; }
function group(items, fn) { const m={}; items.forEach(x=>{const k=fn(x); m[k]=(m[k]||0)+1}); return Object.entries(m).sort(([a],[b])=>String(b).localeCompare(String(a))).slice(0,20); }

function App() {
  const [section, setSection] = useState("dashboard");
  const [platform, setPlatform] = useState("ALL");
  const [state, setState] = useState("unanswered");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [reviews, setReviews] = useState([]);
  const [questions, setQuestions] = useState([]);
  const [overview, setOverview] = useState(null);
  const [products, setProducts] = useState([]);
  const [productCard, setProductCard] = useState(null);
  const [diagnostics, setDiagnostics] = useState(null);
  const [syncHistory, setSyncHistory] = useState(null);
  const [publishHistory, setPublishHistory] = useState(null);
  const [rules, setRules] = useState({});
  const [booking, setBooking] = useState(null);
  const [lastRefresh, setLastRefresh] = useState(null);
  const [selected, setSelected] = useState(null);
  const [draft, setDraft] = useState("");

  const filteredReviews = useMemo(() => filterItems(reviews), [reviews, platform, state]);
  const filteredQuestions = useMemo(() => filterItems(questions), [questions, platform, state]);
  const all = useMemo(() => [...reviews, ...questions], [reviews, questions]);
  const counts = useMemo(() => ({
    reviews: reviews.length,
    questions: questions.length,
    needs: all.filter(x => x.operational_status === "needs_response" || x.has_answer === false).length,
    drafts: all.filter(x => x.final_answer || x.draft_answer).length,
    risks: all.filter(x => x.ai_risk_level === "high").length,
    wb: all.filter(x => x.platform === "WB").length,
    ozon: all.filter(x => x.platform === "OZON").length,
  }), [all]);

  useEffect(() => {
    refreshAll(false);
    const timer = setInterval(() => refreshAll(false), 30000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => { loadProducts(false); }, [platform]);

  function filterItems(list) {
    return list.filter(x => {
      if (platform !== "ALL" && x.platform !== platform) return false;
      if (state === "unanswered" && !(x.operational_status === "needs_response" || x.has_answer === false)) return false;
      if (state === "drafts" && !(x.final_answer || x.draft_answer || x.status === "ready_to_review" || x.status === "ready_to_publish")) return false;
      if (state === "answered" && !(x.has_answer || x.response_origin || String(x.status||"").includes("published"))) return false;
      return true;
    });
  }

  async function refreshAll(show=true) {
    if (show) { setLoading(true); setMessage("Обновляю данные…"); }
    try {
      const [r,q,o,d,s,p,rulesData,b] = await Promise.allSettled([
        api("/reviews?limit=2000"), api("/questions?limit=2000"), api("/ops/overview"),
        api("/system/diagnostics").catch(()=>api("/system/status")), api("/ops/sync-history"), api("/ops/publish-history"),
        api("/settings/automation-rules"), api("/wb-booking/status")
      ]);
      if (r.status === "fulfilled") setReviews(asList(r.value));
      if (q.status === "fulfilled") setQuestions(asList(q.value));
      if (o.status === "fulfilled") setOverview(o.value);
      if (d.status === "fulfilled") setDiagnostics(d.value);
      if (s.status === "fulfilled") setSyncHistory(s.value);
      if (p.status === "fulfilled") setPublishHistory(p.value);
      if (rulesData.status === "fulfilled") setRules(rulesData.value || {});
      if (b.status === "fulfilled") setBooking(b.value);
      setLastRefresh(new Date().toISOString());
      if (show) setMessage("Данные обновлены");
    } catch (e) { setMessage(`Ошибка обновления: ${e.message}`); }
    finally { if (show) setLoading(false); }
  }

  async function loadProducts(show=true) {
    try { const data = await api(`/ops/product-summary?platform=${platform}&limit=200`); setProducts(data.items || []); }
    catch(e) { if(show) setMessage(`Ошибка товаров: ${e.message}`); }
  }

  async function openProduct(sku) {
    setSection("products");
    setMessage("Открываю карточку товара…");
    try { setProductCard(await api(`/ops/product/${sku}?platform=${platform}`)); setMessage("Карточка товара открыта"); }
    catch(e) { setMessage(`Ошибка карточки: ${e.message}`); }
  }

  async function generateSelected() {
    if (!selected?.id) return;
    setLoading(true); setMessage("Генерирую ответ 10/10…");
    try {
      const base = selected.kind === "question" ? "/questions" : "/reviews";
      const fresh = await api(`${base}/${selected.id}/generate`, {method:"POST"});
      setSelected({...fresh, kind:selected.kind}); setDraft(fresh.final_answer || fresh.draft_answer || "");
      await refreshAll(false); setMessage("Ответ сгенерирован");
    } catch(e) { setMessage(`Ошибка генерации: ${e.message}`); }
    finally { setLoading(false); }
  }

  async function saveSelected() {
    if (!selected?.id) return;
    setLoading(true); setMessage("Сохраняю ответ…");
    try {
      const base = selected.kind === "question" ? "/questions" : "/reviews";
      const fresh = await api(`${base}/${selected.id}/answer`, {method:"PATCH", body:JSON.stringify({final_answer:draft})});
      setSelected({...fresh, kind:selected.kind}); await refreshAll(false); setMessage("Ответ сохранен");
    } catch(e) { setMessage(`Ошибка сохранения: ${e.message}`); }
    finally { setLoading(false); }
  }

  async function publishSelected() {
    if (!selected?.id) return;
    setLoading(true); setMessage("Публикую / dry-run…");
    try {
      const base = selected.kind === "question" ? "/questions" : "/reviews";
      const res = await api(`${base}/${selected.id}/publish`, {method:"POST"});
      await refreshAll(false); setMessage(`Результат: ${pretty(res)}`);
    } catch(e) { setMessage(`Ошибка публикации: ${e.message}`); }
    finally { setLoading(false); }
  }

  async function saveRules() {
    setLoading(true); setMessage("Сохраняю правила…");
    try { const payload={...rules}; delete payload.updated_at; setRules(await api("/settings/automation-rules", {method:"PUT", body:JSON.stringify(payload)})); setMessage("Правила сохранены"); }
    catch(e){ setMessage(`Ошибка правил: ${e.message}`); }
    finally{ setLoading(false); }
  }

  function setRule(k,v){ setRules(prev=>({...prev,[k]:v})); }
  function setMatrix(p,k,v){ setRules(prev=>({...prev, autopublish_matrix:{...(prev.autopublish_matrix||{}), [p]:{...(prev.autopublish_matrix?.[p]||{}), [k]:v}}})); }

  async function run(path,label){ setLoading(true); setMessage(`Запускаю ${label}…`); try{ const res=await api(path,{method:"POST"}); await refreshAll(false); setMessage(`${label}: ${pretty(res)}`);} catch(e){setMessage(`Ошибка ${label}: ${e.message}`)} finally{setLoading(false)} }

  function top(t,sub,actions=null){return <div className="top"><div><h2>{t}</h2><p>{sub}</p></div><div className="actions">{actions}</div></div>}
  function filters(){return <div className="sectionFilters"><div className="productFilterBox inline"><label>Состояние</label><select value={state} onChange={e=>setState(e.target.value)}>{STATES.map(([v,t])=><option key={v} value={v}>{t}</option>)}</select></div><button onClick={()=>refreshAll(true)}>Обновить сейчас</button></div>}
  function platformSwitch(){return <div className="marketSwitch">{PLATFORMS.map(p=><button key={p} className={platform===p?"active":""} onClick={()=>setPlatform(p)}>{p==="ALL"?"Все":p}</button>)}</div>}

  function renderDashboard(){return <>{top("KARATOV CX Hub", "Рабочий кабинет сервиса: данные обновляются каждые 30 секунд, API WB защищен cooldown и раздельными очередями.", <button className="primary" onClick={()=>refreshAll(true)}>Обновить</button>)}<div className="cards"><button><b>{counts.reviews}</b><span>Отзывы</span></button><button><b>{counts.questions}</b><span>Вопросы</span></button><button><b>{counts.needs}</b><span>Требуют ответа</span></button><button><b>{counts.drafts}</b><span>Черновики</span></button><button><b>{counts.risks}</b><span>Риски</span></button><button><b>{counts.wb}</b><span>WB</span></button><button><b>{counts.ozon}</b><span>Ozon</span></button></div><div className="settingsPanel"><div className="settingCard"><h3>Статус API</h3>{diagnostics?.keys && Object.entries(diagnostics.keys).map(([k,v])=><div className="metricRow" key={k}><span>{k}</span><b>{bool(v)}</b></div>)}<div className="metricRow"><span>Публикация</span><b>{diagnostics?.publishing?.mode || "—"}</b></div></div><div className="settingCard"><h3>Автосинхронизация</h3><div className="metricRow"><span>WB running</span><b>{bool(syncHistory?.wb?.running)}</b></div><div className="metricRow"><span>WB mode</span><b>{syncHistory?.wb?.sync_mode || "—"}</b></div><div className="metricRow"><span>Последнее обновление UI</span><b>{dt(lastRefresh)}</b></div></div></div></>}

  function list(type){ const items=type==="question"?filteredQuestions:filteredReviews; return <div className="list">{items.length===0?<div className="empty">Нет данных по фильтрам</div>:items.map((x,i)=><div className={`row ${selected?.id===x.id&&selected?.kind===type?"selected":""}`} key={`${type}-${x.id||i}`} onClick={()=>{setSelected({...x,kind:type}); setDraft(x.final_answer||x.draft_answer||"")}}><div className="rowhead"><b className="skuLink" onClick={(e)=>{e.stopPropagation(); if(x.sku) openProduct(x.sku)}}>{x.product_name||x.sku||`Запись ${x.id}`}</b><PlatformBadge p={x.platform}/></div><div className="dateMeta">{x.rating&&<span>⭐ {x.rating}</span>}<span>{dt(x.created_at_marketplace)}</span><span>{x.source_status}</span></div><div className="text">{x.text||x.pros||x.cons||"Без текста"}</div><div className="tags"><Badge>{x.status||"new"}</Badge>{x.ai_category&&<Badge type="yellow">{x.ai_category}</Badge>}{x.ai_risk_level&&<Badge type={x.ai_risk_level==="high"?"red":""}>{x.ai_risk_level}</Badge>}{(x.final_answer||x.draft_answer)&&<Badge type="green">ответ готов</Badge>}</div></div>)}</div>}
  function detail(){ if(!selected) return <div className="detail"><div className="empty">Выбери запись слева</div></div>; const url=productUrl(selected); return <div className="detail"><div className="detailhead"><div><h3>{selected.product_name||selected.sku}</h3><p className="meta">{selected.kind==="question"?"Вопрос":"Отзыв"} · {selected.platform} · {selected.source_status}</p></div><div className="actions">{selected.sku&&<button onClick={()=>openProduct(selected.sku)}>Карточка товара</button>}{url&&<a className="buttonLike" href={url} target="_blank" rel="noreferrer">Открыть на МП</a>}</div></div><div className="clientText">{selected.text||selected.pros||selected.cons||"Нет текста"}</div><div className="twoCols"><div className="exampleBox"><b>AI категория</b><p>{selected.ai_category||"—"}</p></div><div className="exampleBox"><b>Quality / причина</b><p>{selected.ai_reason||selected.publish_blocked_reason||"—"}</p></div></div><label>Финальный ответ</label><textarea value={draft} onChange={e=>setDraft(e.target.value)} placeholder="Ответ"/><div className="actions"><button className="primary" onClick={generateSelected}>Сгенерировать</button><button onClick={saveSelected}>Сохранить</button><button onClick={publishSelected}>Опубликовать</button><button onClick={()=>navigator.clipboard.writeText(draft||"")}>Скопировать</button></div></div>}
  function renderWork(type){return <>{top(type==="question"?"Вопросы":"Отзывы", "Данные обновляются сами; кнопка нужна только для проверки.", <button onClick={()=>refreshAll(true)}>Обновить</button>)}{filters()}<div className="workspace">{list(type)}{detail()}</div></>}

  function renderSummary(){ const cats=overview?.counts?.by_category||{}; return <>{top("AI Summary", "Потоварная выжимка, темы, риски и рекомендации", <button onClick={()=>{loadProducts(true); refreshAll(true)}}>Обновить</button>)}<div className="cards"><button><b>{counts.reviews}</b><span>Отзывы</span></button><button><b>{counts.questions}</b><span>Вопросы</span></button><button><b>{counts.needs}</b><span>Без ответа</span></button><button><b>{counts.risks}</b><span>High risk</span></button></div><div className="settingsPanel"><div className="settingCard wide"><h3>AI-выжимка</h3><p>Сейчас в базе {counts.reviews} отзывов и {counts.questions} вопросов. Главные темы: {Object.entries(cats).slice(0,5).map(([k,v])=>`${k} (${v})`).join(", ") || "нет данных"}. Товары с высоким риском вынесены в блок «Аномалии» и требуют ручной проверки ответов.</p></div>{products.slice(0,8).map(p=><div className="settingCard" key={p.key}><h3>{p.sku||p.product_name}</h3><p>{p.ai_summary}</p><p><b>Рекомендация:</b> {p.recommendation}</p><div className="actions"><button onClick={()=>openProduct(p.sku)}>Открыть карточку</button>{p.product_url&&<a className="buttonLike" href={p.product_url} target="_blank" rel="noreferrer">Маркетплейс</a>}</div></div>)}</div></>}
  function renderProducts(){ return <>{top("Товары", "Карточки товаров с отзывами, вопросами, темами и ссылкой на маркетплейс", <button onClick={()=>loadProducts(true)}>Обновить товары</button>)}<div className="settingsPanel">{products.map(p=><div className="settingCard" key={p.key}><h3>{p.sku||p.product_name}</h3><div className="metricRow"><span>Отзывы</span><b>{p.reviews}</b></div><div className="metricRow"><span>Вопросы</span><b>{p.questions}</b></div><div className="metricRow"><span>Рейтинг</span><b>{p.avg_rating||"—"}</b></div><div className="metricRow"><span>Риск</span><b>{p.high_risk}</b></div><p>{p.ai_summary}</p><div className="actions"><button onClick={()=>openProduct(p.sku)}>Открыть</button>{p.product_url&&<a className="buttonLike" href={p.product_url} target="_blank" rel="noreferrer">На МП</a>}</div></div>)}</div>{productCard&&<div className="settingCard wide"><h3>Карточка {productCard.sku}</h3><p>{productCard.product_name}</p>{productCard.product_url&&<a href={productCard.product_url} target="_blank" rel="noreferrer">Открыть на маркетплейсе</a>}<h4>Темы</h4><pre className="reportText smallPre">{pretty(productCard.summary)}</pre><h4>Последние отзывы</h4><pre className="reportText smallPre">{pretty((productCard.reviews||[]).slice(0,10))}</pre></div>}</>}
  function renderAnomalies(){ const riskProducts=products.filter(p=>p.high_risk||p.negative>0).slice(0,30); return <>{top("Аномалии", "Товары с высокими рисками, негативом и повторяющимися темами", <button onClick={()=>loadProducts(true)}>Обновить</button>)}<div className="settingsPanel">{riskProducts.length===0?<div className="empty">Критичных аномалий не найдено</div>:riskProducts.map(p=><div className="settingCard" key={p.key}><h3>🚨 {p.sku||p.product_name}</h3><p>{p.ai_summary}</p><p><b>{p.recommendation}</b></p><button onClick={()=>openProduct(p.sku)}>Разобрать товар</button></div>)}</div></>}
  function renderReports(){ const rd=group(reviews,x=>dayKey(x.created_at_marketplace)); const rm=group(reviews,x=>monthKey(x.created_at_marketplace)); const qd=group(questions,x=>dayKey(x.created_at_marketplace)); function tbl(title,rows){return <div className="settingCard"><h3>{title}</h3><table><tbody>{rows.map(([k,v])=><tr key={k}><td>{k}</td><td><b>{v}</b></td></tr>)}</tbody></table></div>} return <>{top("Отчеты", "Динамика день/месяц, объемы и SLA-контроль", <button onClick={()=>refreshAll(true)}>Обновить</button>)}<div className="cards"><button><b>{counts.reviews}</b><span>Отзывы total</span></button><button><b>{counts.questions}</b><span>Вопросы total</span></button><button><b>{counts.needs}</b><span>Без ответа</span></button><button><b>{counts.drafts}</b><span>Черновики</span></button></div><div className="settingsPanel">{tbl("Отзывы день к дню", rd)}{tbl("Отзывы месяц к месяцу", rm)}{tbl("Вопросы день к дню", qd)}</div></>}
  function renderSync(){ const wb=syncHistory?.wb||{}; const oz=syncHistory?.ozon||{}; const rows=[]; Object.entries(wb.blocks_state||{}).forEach(([k,v])=>rows.push({platform:"WB",block:k,...v})); Object.entries(oz.blocks||{}).forEach(([k,v])=>rows.push({platform:"OZON",block:k,...v})); return <>{top("Синхронизация", "Без технического JSON: статус блоков, cooldown и история последних запусков", <button onClick={()=>refreshAll(true)}>Обновить статус</button>)}<div className="cards"><button><b>{bool(wb.auto_sync_enabled)}</b><span>WB auto</span></button><button><b>{wb.sync_mode||"—"}</b><span>WB mode</span></button><button><b>{bool(wb.running)}</b><span>WB running</span></button><button><b>{dt(wb.last_success_at)}</b><span>WB success</span></button></div><div className="settingCard wide"><h3>История блоков</h3><table><thead><tr><th>Площадка</th><th>Блок</th><th>Статус</th><th>Получено</th><th>Создано/обновлено</th><th>Следующая попытка</th></tr></thead><tbody>{rows.map((r,i)=><tr key={i}><td>{r.platform}</td><td>{r.block}</td><td>{r.status}</td><td>{r.last_received||r.last_result?.received||0}</td><td>{(r.last_result?.imported_reviews||r.last_result?.created||0)}/{(r.last_result?.updated_reviews||r.last_result?.updated||0)}</td><td>{dt(r.next_retry_at)}</td></tr>)}</tbody></table></div><div className="settingCard wide"><h3>Очередь публикаций</h3><pre className="reportText smallPre">{pretty(publishHistory?.items?.slice(0,20)||[])}</pre></div></>}
  function renderAutopublish(){ return <>{top("Автопубликация", "Раздельная очередь публикации. Не смешивается с синхронизацией, чтобы не ловить 429.", <button className="primary" onClick={saveRules}>Сохранить</button>)}<div className="settingsPanel"><div className="settingCard"><h3>Главное</h3><label className="check"><input type="checkbox" checked={!!rules.real_autopublish_enabled} onChange={e=>setRule("real_autopublish_enabled",e.target.checked)}/> Разрешить автопубликацию в правилах</label><label className="check"><input type="checkbox" checked={!!rules.ai_generation_enabled} onChange={e=>setRule("ai_generation_enabled",e.target.checked)}/> Генерация AI</label><label className="check"><input type="checkbox" checked={!!rules.ai_fallback_to_local_templates} onChange={e=>setRule("ai_fallback_to_local_templates",e.target.checked)}/> Fallback на шаблоны</label><div className="metricRow"><span>Render publish mode</span><b>{diagnostics?.publishing?.mode||"—"}</b></div></div><div className="settingCard"><h3>Оценки и лимиты</h3><label>Минимальная оценка для автоответа</label><input type="number" min="1" max="5" value={rules.positive_review_min_rating||5} onChange={e=>setRule("positive_review_min_rating",Number(e.target.value))}/><label>Макс. за запуск</label><input type="number" value={rules.autopublish_max_per_run||10} onChange={e=>setRule("autopublish_max_per_run",Number(e.target.value))}/><label>Пауза между публикациями, сек</label><input type="number" value={rules.autopublish_pause_between_items_seconds||30} onChange={e=>setRule("autopublish_pause_between_items_seconds",Number(e.target.value))}/></div></div><div className="matrixGrid">{["WB","OZON","YM"].map(p=><div className="matrixCard" key={p}><b>{p}</b><label className="check"><input type="checkbox" checked={!!rules.autopublish_matrix?.[p]?.reviews} onChange={e=>setMatrix(p,"reviews",e.target.checked)}/>Отзывы</label><label className="check"><input type="checkbox" checked={!!rules.autopublish_matrix?.[p]?.questions} onChange={e=>setMatrix(p,"questions",e.target.checked)}/>Вопросы</label></div>)}</div></>}
  function renderBooking(){ const planned=booking?.planned_dates||[]; async function saveBooking(enabled){ const cfg={...(booking||{}), enabled}; const res=await api(enabled?"/wb-booking/start":"/wb-booking/config",{method:"POST",body:JSON.stringify(cfg)}); setBooking(res); } return <>{top("WB FBO Slot Hunter", "API-first: расписание поставок, мониторинг окон, Telegram/email уведомления и журнал событий", <><button onClick={()=>run("/wb-booking/check","Slot check")}>Проверить</button><button className="primary" onClick={()=>saveBooking(true)}>Сохранить/включить</button><button onClick={()=>run("/wb-booking/stop","Slot stop")}>Стоп</button></>)}<div className="settingsPanel"><div className="settingCard"><h3>Расписание</h3><label>Стартовая дата</label><input type="date" value={booking?.start_date||""} onChange={e=>setBooking({...booking,start_date:e.target.value})}/><label>Каждые N рабочих дней</label><input type="number" value={booking?.every_n_workdays||3} onChange={e=>setBooking({...booking,every_n_workdays:Number(e.target.value)})}/><label>Горизонт, дней</label><input type="number" value={booking?.horizon_days||30} onChange={e=>setBooking({...booking,horizon_days:Number(e.target.value)})}/><label>Макс. коэффициент</label><input type="number" value={booking?.coefficient_limit||20} onChange={e=>setBooking({...booking,coefficient_limit:Number(e.target.value)})}/></div><div className="settingCard"><h3>Уведомления</h3><label className="check"><input type="checkbox" checked={!!booking?.telegram_enabled} onChange={e=>setBooking({...booking,telegram_enabled:e.target.checked})}/>Telegram</label><textarea value={(booking?.telegram_chat_ids||[]).join("\n")} onChange={e=>setBooking({...booking,telegram_chat_ids:e.target.value.split("\n").filter(Boolean)})} placeholder="chat_id по одному в строке"/><label className="check"><input type="checkbox" checked={!!booking?.email_enabled} onChange={e=>setBooking({...booking,email_enabled:e.target.checked})}/>Email</label><textarea value={(booking?.email_recipients||[]).join("\n")} onChange={e=>setBooking({...booking,email_recipients:e.target.value.split("\n").filter(Boolean)})} placeholder="email по одному в строке"/></div><div className="settingCard"><h3>Ближайшие даты</h3>{planned.slice(0,12).map(d=><div className="metricRow" key={d}><span>{d}</span><b>план</b></div>)}</div></div><div className="settingCard wide"><h3>История Slot Hunter</h3><table><tbody>{(booking?.events||[]).slice(0,30).map((e,i)=><tr key={i}><td>{dt(e.at)}</td><td>{e.kind}</td><td>{e.message}</td></tr>)}</tbody></table></div></>}
  function renderSettings(){ return <>{top("AI / шаблоны", "Промты, шаблоны, подписи и fallback", <button className="primary" onClick={saveRules}>Сохранить</button>)}<div className="settingsPanel"><div className="settingCard wide"><h3>Системный промт</h3><textarea className="templateText" value={rules.custom_system_prompt||""} onChange={e=>setRule("custom_system_prompt",e.target.value)}/></div><div className="settingCard wide"><h3>Промт отзывов</h3><textarea className="largeText" value={rules.review_prompt_template||""} onChange={e=>setRule("review_prompt_template",e.target.value)}/></div><div className="settingCard wide"><h3>Промт вопросов</h3><textarea className="largeText" value={rules.question_prompt_template||""} onChange={e=>setRule("question_prompt_template",e.target.value)}/></div><div className="settingCard wide"><h3>Локальные шаблоны</h3><textarea className="templateText" value={rules.local_templates_text||""} onChange={e=>setRule("local_templates_text",e.target.value)}/></div></div></>}
  function renderSystem(){return <>{top("Диагностика", "Только техническим пользователям", <button onClick={()=>refreshAll(true)}>Обновить</button>)}<div className="settingCard wide"><pre className="reportText">{pretty(diagnostics||{})}</pre></div></>}

  function content(){ if(section==="dashboard")return renderDashboard(); if(section==="reviews")return renderWork("review"); if(section==="questions")return renderWork("question"); if(section==="summary")return renderSummary(); if(section==="products")return renderProducts(); if(section==="anomalies")return renderAnomalies(); if(section==="reports")return renderReports(); if(section==="sync")return renderSync(); if(section==="autopublish")return renderAutopublish(); if(section==="booking")return renderBooking(); if(section==="settings")return renderSettings(); if(section==="system")return renderSystem(); return renderDashboard(); }

  return <div className="app"><aside><h1>KARATOV<br/>CX Hub</h1>{platformSwitch()}<div className={`currentPlatform ${platform}`}>{platform==="ALL"?"Все площадки":platform}</div>{NAV.map(([id,title])=><button key={id} className={section===id?"active":""} onClick={()=>setSection(id)}>{title}{id==="reviews"&&<span className="navCount">{counts.reviews}</span>}{id==="questions"&&<span className="navCount">{counts.questions}</span>}</button>)}<div className="syncMini">UI: {dt(lastRefresh)}<br/>{loading?"Выполняется…":"Готово"}</div><div className="hint">WB API не дергается фронтом. UI читает базу, sync/publish идут отдельными очередями.</div></aside><main>{message&&<div className="message">{message}</div>}{content()}</main></div>;
}

ReactDOM.createRoot(document.getElementById("root")).render(<App/>);
