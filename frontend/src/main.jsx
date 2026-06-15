import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { RefreshCw, MessageSquare, HelpCircle, Send, Sparkles, AlertTriangle, Settings, CheckCircle2, CircleDashed, BarChart3, TrendingUp, CalendarDays, Warehouse, BellRing, Save, PlayCircle } from 'lucide-react';
import './style.css';

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000';

async function api(path, options={}) {
  const res = await fetch(`${API}${path}`, { headers: {'Content-Type': 'application/json'}, ...options });
  if (!res.ok) { const txt = await res.text(); throw new Error(txt || `HTTP ${res.status}`); }
  return res.json();
}

function Badge({children, tone='neutral'}) { return <span className={`badge ${tone}`}>{children}</span> }

function formatDate(value) {
  if (!value) return '—';
  if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const [y,m,d] = value.split('-');
    return `${d}.${m}.${y}`;
  }
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString('ru-RU');
}

const responseOriginLabels = {
  auto_app: 'авто из приложения',
  manual_app: 'вручную из приложения',
  seller_cabinet: 'из ЛК продавца',
};
function ResponseOriginBadge({origin}) {
  if (!origin) return null;
  const label = responseOriginLabels[origin] || origin;
  const tone = origin === 'auto_app' ? 'green' : origin === 'manual_app' ? 'yellow' : 'neutral';
  return <Badge tone={tone}>{label}</Badge>;
}
function SectionProductFilter({section, productFilter, setProductFilter, productOptions, listFilters, setListFilters}) {
  const showOriginFilter = ['reviews-answered','questions-answered'].includes(section.key);
  return <div className="sectionFilters">
    <div className="productFilterBox inline">
      <label>Фильтр по товару</label>
      <input list="product-options" value={productFilter} onChange={e=>setProductFilter(e.target.value)} placeholder="SKU или название" />
      <datalist id="product-options">{productOptions.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}</datalist>
      {productFilter && <button onClick={()=>setProductFilter('')}>Сбросить товар</button>}
    </div>
    {showOriginFilter && <div className="productFilterBox inline">
      <label>Источник ответа</label>
      <select value={listFilters.response_origin || ''} onChange={e=>setListFilters(prev => ({...prev, response_origin: e.target.value}))}>
        <option value="">Все источники</option>
        <option value="auto_app">Автоматически из приложения</option>
        <option value="manual_app">Вручную из приложения</option>
        <option value="seller_cabinet">Загружен из ЛК продавца</option>
      </select>
    </div>}
  </div>
}

function ItemDates({item}) {
  const incoming = item?.created_at_marketplace || item?.created_at;
  const answered = item?.has_answer ? (item?.updated_at || item?.created_at_marketplace) : null;
  return <div className="dateMeta">
    <span>Поступил: <b>{formatDate(incoming)}</b></span>
    <span>Ответ: <b>{answered ? formatDate(answered) : '—'}</b></span>
  </div>;
}

function ProductLink({sku, productName, url, platform}) {
  const label = productName || sku || 'Товар';
  let finalUrl = url;
  let suffix = '';
  const pf = (platform || '').toUpperCase();
  if (!finalUrl && pf === 'WB' && sku && String(sku).match(/^\d+$/)) {
    finalUrl = `https://www.wildberries.ru/catalog/${sku}/detail.aspx`;
  }
  if (!finalUrl && (sku || productName)) {
    if (pf === 'OZON') {
      finalUrl = `https://www.ozon.ru/search/?text=${encodeURIComponent(sku || productName)}`;
      suffix = ' · поиск Ozon';
    } else {
      finalUrl = `https://www.wildberries.ru/catalog/0/search.aspx?search=${encodeURIComponent(sku || productName)}`;
      suffix = ' · поиск WB';
    }
  }
  if (!finalUrl) return <span>{label} <em className="meta">нет данных для ссылки</em></span>;
  return <a className="productLink" href={finalUrl} target="_blank" rel="noreferrer" onClick={e=>e.stopPropagation()}>{label}{suffix}</a>;
}

const sections = [
  {key:'reviews-unanswered', label:'Отзывы без ответа', type:'reviews', answerState:'unanswered', icon:MessageSquare},
  {key:'reviews-answered', label:'Отзывы с ответом/архив', type:'reviews', answerState:'answered', icon:CheckCircle2},
  {key:'manual-review', label:'Требуют ручного подтверждения', type:'reviews', answerState:'manual', icon:AlertTriangle},
  {key:'auto-published', label:'Отвечены автоматически/опубликованы', type:'reviews', answerState:'auto_published', icon:CheckCircle2},
  {key:'questions-unanswered', label:'Вопросы без ответа', type:'questions', answerState:'unanswered', icon:HelpCircle},
  {key:'questions-answered', label:'Вопросы с ответом', type:'questions', answerState:'answered', icon:CircleDashed},
  {key:'cx-summary', label:'Саммари CX', type:'cx', answerState:'all', icon:BarChart3},
  {key:'products', label:'Товары и рейтинги', type:'products', answerState:'all', icon:TrendingUp},
  {key:'anomalies', label:'Аномалии', type:'anomalies', answerState:'all', icon:AlertTriangle},
  {key:'reports', label:'Отчеты и выгрузки', type:'reports', answerState:'all', icon:BarChart3},
  {key:'fbo-booking', label:'Автобронирование FBO', type:'fboBooking', answerState:'all', icon:CalendarDays},
  {key:'sync-diagnostics', label:'Диагностика синхронизации', type:'sync', answerState:'all', icon:RefreshCw},
  {key:'settings', label:'Правила ИИ', type:'settings', answerState:'all', icon:Settings},
];

function App() {
  const savedSectionKey = localStorage.getItem('karatov_active_section');
  const [section, setSection] = useState(sections.find(s => s.key === savedSectionKey) || sections[0]);
  const [platformFilter, setPlatformFilter] = useState('all');
  const [productFilter, setProductFilter] = useState('');
  const [listFilters, setListFilters] = useState({});
  const [ratingDynamicFilter, setRatingDynamicFilter] = useState('all');
  const [reviews, setReviews] = useState([]);
  const [questions, setQuestions] = useState([]);
  const [summary, setSummary] = useState(null);
  const [selected, setSelected] = useState(null);
  const [message, setMessage] = useState('');
  const [syncStatus, setSyncStatus] = useState(null);
  const [ozonStatus, setOzonStatus] = useState(null);
  const [rules, setRules] = useState(null);
  const [rulesDirty, setRulesDirty] = useState(false);
  const rulesDirtyRef = useRef(false);
  const [openaiStatus, setOpenaiStatus] = useState(null);
  const [products, setProducts] = useState([]);
  const [anomalies, setAnomalies] = useState([]);
  const [reports, setReports] = useState({daily: [], pivot: {products_dynamic: [], categories_dynamic: []}, text: ''});
  const [fboBooking, setFboBooking] = useState(null);
  const [bulkSelected, setBulkSelected] = useState({reviews: new Set(), questions: new Set()});

  const params = new URLSearchParams();
  if (platformFilter !== 'all') params.set('platform', platformFilter);
  if (productFilter) params.set('product', productFilter);
  const baseParam = params.toString() ? `?${params.toString()}` : '';
  const extraListParams = () => {
    const p = new URLSearchParams(params);
    Object.entries(listFilters || {}).forEach(([k,v]) => { if (v) p.set(k, v); });
    return p.toString() ? `&${p.toString()}` : '';
  };
  const platformPrefix = platformFilter === 'WB' ? 'WB · ' : platformFilter === 'OZON' ? 'Ozon · ' : platformFilter === 'YM' ? 'Яндекс · ' : '';
  const platformTitle = platformFilter === 'all' ? 'Все площадки' : platformFilter === 'WB' ? 'Wildberries' : platformFilter === 'OZON' ? 'Ozon' : 'Яндекс Маркет';

  async function load(active = section) {
    if (active.type === 'fboBooking') {
      const data = await api('/fbo-booking/state');
      setFboBooking(data);
      return;
    }

    if (active.type === 'reports') {
      const [dailyData, pivotData, textData, summaryData] = await Promise.all([
        api('/reports/daily'),
        api('/reports/pivot'),
        api('/reports/text'),
        api(`/summary${baseParam}`),
      ]);
      setReports({daily: dailyData, pivot: pivotData, text: textData.report});
      setSummary(summaryData);
      return;
    }

    if (active.type === 'settings') {
      const [rulesData, summaryData, statusData, ozonStatusData, openaiStatusData] = await Promise.all([
        api('/settings/automation-rules'),
        api(`/summary${baseParam}`),
        api('/sync/status'),
        api('/sync/ozon/status').catch(() => null),
        api('/settings/openai-status').catch(() => null),
      ]);
      // Не затираем несохраненный промпт/настройки при автообновлении страницы.
      // Раньше settings-панель каждые 30 секунд перечитывала backend и могла сбрасывать
      // текст, который пользователь только что печатает в textarea.
      if (!rulesDirtyRef.current) {
        setRules(rulesData);
      }
      setSummary(summaryData);
      setSyncStatus(statusData);
      setOzonStatus(ozonStatusData);
      setOpenaiStatus(openaiStatusData);
      return;
    }

    if (['cx','products','anomalies','sync'].includes(active.type)) {
      const [summaryData, productsData, anomaliesData, statusData, ozonStatusData] = await Promise.all([
        api(`/summary${baseParam}`),
        api(`/analytics/products${baseParam}`),
        api(`/analytics/anomalies${baseParam}`),
        api('/sync/status'),
        api('/sync/ozon/status').catch(() => null),
      ]);
      setSummary(summaryData);
      setProducts(productsData);
      setAnomalies(anomaliesData);
      setSyncStatus(statusData);
      setOzonStatus(ozonStatusData);
      return;
    }

    const [reviewData, questionData, summaryData, statusData, ozonStatusData] = await Promise.all([
      api(`/reviews?answer_state=${active.type === 'reviews' ? active.answerState : 'all'}&limit=200${extraListParams()}`),
      api(`/questions?answer_state=${active.type === 'questions' ? active.answerState : 'all'}&limit=200${extraListParams()}`),
      api(`/summary${baseParam}`),
      api('/sync/status'),
      api('/sync/ozon/status').catch(() => null),
    ]);
    setReviews(reviewData);
    setQuestions(questionData);
    setSummary(summaryData);
    setSyncStatus(statusData);
    setOzonStatus(ozonStatusData);
  }

  useEffect(() => { load().catch(e => setMessage(e.message)); }, [platformFilter, productFilter, listFilters]);
  useEffect(() => {
    const timer = setInterval(() => load().catch(() => {}), 30000);
    return () => clearInterval(timer);
  }, [section]);

  async function selectSection(next) {
    localStorage.setItem('karatov_active_section', next.key);
    setSection(next);
    setSelected(null);
    setBulkSelected({reviews: new Set(), questions: new Set()});
    setListFilters({});
    await load(next).catch(e => setMessage(e.message));
  }

  async function syncWB() {
    setMessage('Синхронизирую следующий безопасный блок WB...');
    try {
      const r = await api('/sync/wb/next', {method:'POST'});
      setMessage(`${r.message || 'Блок завершен'}: ${r.block || ''}; новые отзывы ${r.imported_reviews || 0}, обновлены ${r.updated_reviews || 0}; новые вопросы ${r.imported_questions || 0}, обновлены ${r.updated_questions || 0}`);
      await load();
    }
    catch(e){ setMessage(e.message); }
  }

  async function syncBlock(block) {
    setMessage(`Синхронизирую блок ${block}...`);
    try {
      const r = await api(`/sync/wb/block/${block}`, {method:'POST'});
      setMessage(`${r.message || 'Блок завершен'}: ${r.block}; смотри результат в диагностике`);
      await load();
    }
    catch(e){ setMessage(e.message); }
  }

  async function syncOzonAll() {
    setMessage('Синхронизирую Ozon: отзывы и вопросы...');
    try {
      const r = await api('/sync/ozon', {method:'POST'});
      setMessage('Ozon синхронизирован. Подробности смотри в диагностике Ozon.');
      await load();
    } catch(e) { setMessage(e.message); }
  }

  async function syncOzonBlock(block) {
    setMessage(`Синхронизирую Ozon блок ${block}...`);
    try {
      const r = await api(`/sync/ozon/block/${block}`, {method:'POST'});
      setMessage(r.message || `Ozon блок ${block} завершен`);
      await load();
    } catch(e) { setMessage(e.message); }
  }


  const items = section.type === 'reviews' ? reviews : section.type === 'questions' ? questions : [];
  const title = section.type === 'settings' ? 'Настройки ИИ, шаблонов и публикации' : section.type === 'fboBooking' ? 'Автобронирование FBO — Wildberries' : (platformPrefix && ['reviews','questions'].includes(section.type) ? platformPrefix + section.label : section.label);

  async function generate(item) {
    const endpoint = section.type === 'reviews' ? `/reviews/${item.id}/generate` : `/questions/${item.id}/generate`;
    setMessage('Генерирую новый вариант ответа...');
    try {
      const updated = await api(endpoint, {method:'POST'});
      setSelected(updated);
      await load();
      setMessage((updated.final_answer || updated.draft_answer) ? 'Сгенерирован новый вариант. Черновик прошел проверку 10/10 и готов к ручной проверке.' : 'Ответ не выдан: quality gate не пропустил текст ниже 10/10. Смотри причину в карточке.');
    }
    catch(e){ setMessage(`Ошибка генерации: ${e.message}`); }
  }

  async function saveAnswer() {
    const endpoint = section.type === 'reviews' ? `/reviews/${selected.id}/answer` : `/questions/${selected.id}/answer`;
    const updated = await api(endpoint, {method:'PATCH', body: JSON.stringify({final_answer: selected.final_answer || ''})});
    setSelected(updated); await load(); setMessage('Ответ сохранен');
  }

  function toggleBulkItem(item) {
    const key = section.type;
    if (!['reviews','questions'].includes(key)) return;
    setBulkSelected(prev => {
      const nextSet = new Set(prev[key] || []);
      if (nextSet.has(item.id)) nextSet.delete(item.id); else nextSet.add(item.id);
      return {...prev, [key]: nextSet};
    });
  }

  function selectAllReadyItems() {
    const key = section.type;
    if (!['reviews','questions'].includes(key)) return;
    const ready = items.filter(x => x.operational_status === 'needs_response' && (x.final_answer || x.draft_answer)).map(x => x.id);
    setBulkSelected(prev => ({...prev, [key]: new Set(ready)}));
  }

  async function publishSelected() {
    const key = section.type;
    if (!['reviews','questions'].includes(key)) return;
    const ids = Array.from(bulkSelected[key] || []);
    if (!ids.length) { setMessage('Выбери хотя бы одну карточку для публикации.'); return; }
    const endpoint = key === 'reviews' ? '/reviews/bulk-publish' : '/questions/bulk-publish';
    setMessage(`Публикую выбранные: ${ids.length} шт...`);
    try {
      const r = await api(endpoint, {method:'POST', body: JSON.stringify({ids})});
      await load();
      setSelected(null);
      setBulkSelected(prev => ({...prev, [key]: new Set()}));
      setMessage(`Массовая публикация завершена: опубликовано ${r.published || 0}, ошибок ${r.failed || 0}.`);
    } catch(e) {
      setMessage(`Ошибка массовой публикации: ${e.message}`);
    }
  }

  async function publish() {
    if (!selected) return;
    const endpoint = section.type === 'reviews' ? `/reviews/${selected.id}/publish` : `/questions/${selected.id}/publish`;
    setMessage('Отправляю ответ в backend...');
    try {
      const r = await api(endpoint, {method:'POST'});
      await load();
      setSelected(null);
      setMessage(r.message || `Публикация выполнена: ${r.status || 'ok'}`);
    } catch(e) {
      setMessage(`Ошибка публикации: ${e.message}`);
    }
  }

  async function editPublishedReviewAnswer() {
    if (!selected || section.type !== 'reviews') return;
    setMessage('Отправляю редактирование ответа в WB...');
    try {
      await saveAnswer();
      const r = await api(`/reviews/${selected.id}/edit-published-answer`, {method:'POST'});
      await load();
      setSelected(null);
      setMessage(r.message || `Редактирование выполнено: ${r.status || 'ok'}`);
    } catch(e) {
      setMessage(`Ошибка редактирования ответа: ${e.message}`);
    }
  }

  async function saveRules(nextRules) {
    const updated = await api('/settings/automation-rules', {method:'PUT', body: JSON.stringify(nextRules)});
    setRules(updated);
    rulesDirtyRef.current = false;
    setRulesDirty(false);
    try { localStorage.removeItem('karatov_rules_draft'); } catch (_) {}
    setMessage('Правила сохранены');
  }


  async function drillTo(nextKey, filters = {}) {
    const next = sections.find(s => s.key === nextKey) || section;
    localStorage.setItem('karatov_active_section', next.key);
    setSection(next);
    setSelected(null);
    setBulkSelected({reviews: new Set(), questions: new Set()});
    setListFilters(filters);
  }

  const productOptions = useMemo(() => {
    const seen = new Map();
    (products || []).forEach(p => {
      const key = p.sku || p.product_name || p.product_key;
      if (key && !seen.has(key)) seen.set(key, {value: key, label: `${p.sku || '—'} · ${p.product_name || p.product_key || ''}`});
    });
    return Array.from(seen.values()).slice(0, 300);
  }, [products]);

  const navCounts = useMemo(() => ({
    reviewsUnanswered: summary?.unanswered_reviews ?? 0,
    reviewsAnswered: summary?.answered_reviews ?? Math.max(0, (summary?.total_reviews ?? 0) - (summary?.unanswered_reviews ?? 0)),
    questionsUnanswered: summary?.unanswered_questions ?? 0,
    questionsAnswered: summary?.answered_questions ?? Math.max(0, (summary?.total_questions ?? 0) - (summary?.unanswered_questions ?? 0)),
    reviewsStale: summary?.stale_unanswered_reviews ?? 0,
    questionsStale: summary?.stale_unanswered_questions ?? 0,
  }), [summary]);

  return <div className="app">
    <aside>
      <h1>KARATOV<br/>CX Hub</h1><div className={`currentPlatform ${platformFilter}`}>{platformTitle}</div>
      <div className="platformSwitch">
        <button className={platformFilter==='all'?'active':''} onClick={()=>setPlatformFilter('all')}>Все</button>
        <button className={platformFilter==='WB'?'active':''} onClick={()=>setPlatformFilter('WB')}>WB</button>
        <button className={platformFilter==='OZON'?'active':''} onClick={()=>setPlatformFilter('OZON')}>Ozon</button>
        <button className={platformFilter==='YM'?'active':''} onClick={()=>setPlatformFilter('YM')}>Яндекс</button>
      </div>
      {sections.map(s => {
        const Icon = s.icon;
        const count = s.key === 'reviews-unanswered' ? navCounts.reviewsUnanswered : s.key === 'reviews-answered' ? navCounts.reviewsAnswered : s.key === 'questions-unanswered' ? navCounts.questionsUnanswered : s.key === 'questions-answered' ? navCounts.questionsAnswered : s.key === 'reviews-stale' ? navCounts.reviewsStale : s.key === 'questions-stale' ? navCounts.questionsStale : null;
        return <button key={s.key} className={section.key===s.key?'active':''} onClick={()=>selectSection(s)}><Icon size={18}/> {platformPrefix && ['reviews','questions'].includes(s.type) ? platformPrefix + s.label : s.label}{count !== null && <span className="navCount">{count}</span>}</button>
      })}
      <button onClick={syncWB}><RefreshCw size={18}/> Синхронизировать следующий блок WB</button>
      <div className="blockButtons">
        <button onClick={()=>syncBlock('feedbacks_unanswered')}>Отзывы без ответа</button>
        <button onClick={()=>syncBlock('questions_unanswered')}>Вопросы без ответа</button>
        <button onClick={()=>syncBlock('feedbacks_answered')}>Отзывы с ответом</button>
        <button onClick={()=>syncBlock('questions_answered')}>Вопросы с ответом</button>
        <button onClick={()=>syncBlock('feedbacks_archive')}>Архив отзывов</button>
      </div>
      <div className="blockButtons ozonButtons">
        <b>Ozon</b>
        <button onClick={syncOzonAll}>Ozon: синхр. все</button>
        <button onClick={()=>syncOzonBlock('reviews_unanswered')}>Ozon отзывы без ответа</button>
        <button onClick={()=>syncOzonBlock('questions_unanswered')}>Ozon вопросы без ответа</button>
        <button onClick={()=>syncOzonBlock('reviews_answered')}>Ozon отзывы с ответом</button>
        <button onClick={()=>syncOzonBlock('questions_answered')}>Ozon вопросы с ответом</button>
      </div>
      <div className="blockButtons ymButtons">
        <b>Яндекс Маркет</b>
        <button disabled title="API Яндекс Маркета пока не подключен в backend">Яндекс отзывы без ответа</button>
        <button disabled title="API Яндекс Маркета пока не подключен в backend">Яндекс вопросы без ответа</button>
        <button disabled title="API Яндекс Маркета пока не подключен в backend">Яндекс отзывы с ответом</button>
        <button disabled title="API Яндекс Маркета пока не подключен в backend">Яндекс вопросы с ответом</button>
      </div>
      <div className="hint">MVP v4.0: фильтры внутри разделов, источник ответа в архиве, кликабельный саммари, рекомендации и динамика рейтинга.</div>
      {syncStatus && <div className="syncMini">Автосинк: {syncStatus.auto_sync_enabled ? 'вкл' : 'выкл'}<br/>Режим: {syncStatus.sync_mode}<br/>Последний успех: {syncStatus.last_success_at ? new Date(syncStatus.last_success_at).toLocaleString() : '—'}</div>}
    </aside>
    <main>
      <section className="top">
        <div><h2>{title}</h2><p>{section.type === 'settings' ? 'Обучай ИИ ответам, редактируй локальные шаблоны и управляй автопубликацией.' : section.type === 'fboBooking' ? 'Календарь целевых дат, правила поиска окон, уведомления и подготовка к автозаписи.' : 'Данные обновляются фоном по расписанию и кнопкой ручной синхронизации.'}</p></div>
        {summary && section.type !== 'fboBooking' && <div className="cards">
          <button onClick={()=>selectSection(sections[0])}><b>{summary.unanswered_reviews}</b><span>отзывов без ответа</span></button>
          <button onClick={()=>selectSection(sections[1])}><b>{summary.answered_reviews ?? navCounts.reviewsAnswered}</b><span>отзывов с ответом</span></button>
          <button onClick={()=>selectSection(sections.find(x=>x.key==='questions-unanswered'))}><b>{summary.unanswered_questions}</b><span>вопросов без ответа</span></button>
          <button onClick={()=>selectSection(sections.find(x=>x.key==='questions-answered'))}><b>{summary.answered_questions ?? navCounts.questionsAnswered}</b><span>вопросов с ответом</span></button>
        </div>}
      </section>
      {!['settings','fboBooking'].includes(section.type) && <SectionProductFilter section={section} productFilter={productFilter} setProductFilter={setProductFilter} productOptions={productOptions} listFilters={listFilters} setListFilters={setListFilters} />}
      {(productFilter || Object.keys(listFilters).length > 0) && <div className="message">Активные фильтры: {productFilter && `товар: ${productFilter}`} {Object.entries(listFilters).map(([k,v]) => v ? `${k}: ${v}` : '').filter(Boolean).join(' · ')} <button onClick={()=>{setProductFilter(''); setListFilters({});}}>Сбросить фильтры</button></div>}
      {message && <div className="message">{message}</div>}

      {section.type === 'settings' ? <RulesPanel rules={rules} setRules={setRules} saveRules={saveRules} openaiStatus={openaiStatus} rulesDirty={rulesDirty} markRulesDirty={() => { rulesDirtyRef.current = true; setRulesDirty(true); }} /> :
       section.type === 'cx' ? <CxSummary summary={summary} onDrill={drillTo} setProductFilter={setProductFilter} /> :
       section.type === 'products' ? <ProductsPanel products={products} dynamicFilter={ratingDynamicFilter} setDynamicFilter={setRatingDynamicFilter} setProductFilter={setProductFilter} /> :
       section.type === 'anomalies' ? <AnomaliesPanel anomalies={anomalies} /> :
       section.type === 'reports' ? <ReportsPanel reports={reports} /> :
       section.type === 'fboBooking' ? <FboBookingPanel state={fboBooking} setState={setFboBooking} setMessage={setMessage} /> :
       section.type === 'sync' ? <SyncDiagnostics status={syncStatus} ozonStatus={ozonStatus} platformFilter={platformFilter} /> :
       <section className="workspace">
        <div className="list">
          {['reviews','questions'].includes(section.type) && <div className="bulkBar">
            <button onClick={selectAllReadyItems}>Выбрать готовые</button>
            <button className="primary" disabled={(bulkSelected[section.type]?.size || 0) === 0} onClick={publishSelected}><Send size={16}/> Опубликовать выбранные ({bulkSelected[section.type]?.size || 0})</button>
            <button disabled={(bulkSelected[section.type]?.size || 0) === 0} onClick={()=>setBulkSelected(prev => ({...prev, [section.type]: new Set()}))}>Снять выбор</button>
          </div>}
          {items.length === 0 && <div className="empty">В этом разделе пока нет данных по выбранной площадке. Проверь диагностику синхронизации именно этой площадки.</div>}
          {items.map(item => <div key={item.id} onClick={()=>setSelected(item)} className={`row ${selected?.id===item.id?'selected':''}`}>
            <div className="rowhead"><label className="bulkCheck" onClick={e=>e.stopPropagation()}><input type="checkbox" checked={bulkSelected[section.type]?.has(item.id) || false} onChange={()=>toggleBulkItem(item)} /></label><b className={`platformBadge ${item.platform}`}>{item.platform}</b><Badge tone={item.operational_status==='needs_response'?'yellow':item.has_answer?'green':'neutral'}>{item.source_status || item.status}</Badge><ResponseOriginBadge origin={item.response_origin} /></div>
            <div className="meta">SKU: {item.sku || '—'} {item.product_url && <a href={item.product_url} target="_blank" rel="noreferrer" onClick={e=>e.stopPropagation()}> · открыть товар</a>} {item.rating ? ` · ${item.rating}★` : ''}</div>
            <ItemDates item={item} />
            <div className="text">{item.text || 'Без текста'}</div>
            <div className="tags">{item.ai_category && <Badge>{item.ai_category}</Badge>} {item.ai_sentiment && <Badge>{item.ai_sentiment}</Badge>} {(item.ai_tags || []).map(t => <Badge key={t}>{t}</Badge>)} {item.ai_risk_level==='high' && <Badge tone="red">риск</Badge>} {item.ai_can_autopublish && <Badge tone="green">допущено quality gate</Badge>}</div>
          </div>)}
        </div>
        <div className="detail">
          {!selected ? <div className="empty">Выбери отзыв или вопрос слева.</div> : <>
            <div className="detailhead"><h3><ProductLink sku={selected.sku} productName={selected.product_name || 'Товар без названия'} url={selected.product_url} platform={selected.platform} /></h3><Badge>{selected.platform}</Badge></div>
            <p className="meta">ID: {selected.external_id} · SKU: {selected.sku || '—'} {selected.rating ? ` · ${selected.rating}★` : ''} · {selected.has_answer ? 'уже есть ответ на площадке' : 'без ответа'} · источник: {selected.source_status || '—'} · ответ: {responseOriginLabels[selected.response_origin] || selected.response_origin || '—'}</p>
            <ItemDates item={selected} />
            <div className="clientText">{selected.text || 'Без текста'}</div>
            {selected.publish_blocked_reason && <div className="risk"><AlertTriangle size={16}/> {selected.publish_blocked_reason}</div>}
            {selected.status === 'answer_rejected_quality_gate' && <div className="risk"><AlertTriangle size={16}/> Ответ не показан, потому что не прошел внутреннюю проверку качества 10/10.</div>}
            {selected.ai_reason && <div className="risk"><AlertTriangle size={16}/> {selected.ai_reason}</div>}
            {selected.ai_tags?.length > 0 && <div className="tags">{selected.ai_tags.map(t => <Badge key={t}>{t}</Badge>)}</div>}
            <button className="primary" onClick={()=>generate(selected)}><Sparkles size={16}/> Сгенерировать новый вариант ответа</button>
            <label>Финальный ответ</label>
            <textarea value={selected.final_answer || selected.draft_answer || ''} onChange={e=>setSelected({...selected, final_answer: e.target.value})} placeholder="Здесь появится черновик ответа" />
            <div className="actions">
              <button disabled={!selected} onClick={saveAnswer}>Сохранить локально</button>
              <button disabled={!selected || selected.operational_status !== 'needs_response'} className="primary" onClick={publish}><Send size={16}/> Опубликовать / dry-run</button>
              {section.type === 'reviews' && selected.has_answer && <button disabled={!selected} onClick={editPublishedReviewAnswer}>Редактировать ответ в WB / dry-run</button>}
            </div>
          </>}
        </div>
      </section>}
    </main>
  </div>
}

function RulesPanel({rules, setRules, saveRules, openaiStatus, rulesDirty, markRulesDirty}) {
  if (!rules) return <div className="empty">Загружаю правила...</div>;
  const update = (key, value) => {
    const next = {...rules, [key]: value};
    markRulesDirty && markRulesDirty();
    setRules(next);
    try { localStorage.setItem('karatov_rules_draft', JSON.stringify(next)); } catch (_) {}
  };
  const updateList = (key, value) => update(key, value.split('\n').map(x => x.trim()).filter(Boolean));
  const matrix = rules.autopublish_matrix || {WB:{reviews:false,questions:false}, OZON:{reviews:false,questions:false}, YM:{reviews:false,questions:false}};
  const updateMatrix = (platform, key, value) => update('autopublish_matrix', {...matrix, [platform]: {...(matrix[platform] || {}), [key]: value}});
  return <section className="settingsPanel">
    <div className="settingCard wide">
      <h3>Обучающий промпт ИИ</h3>
      <p className="meta">Этот блок подмешивается в системный промпт. Здесь можно описывать tone of voice, запреты, сценарии решений, правила по категориям и любые требования KARATOV.</p>
      <label>System prompt / инструкция бренда</label>
      <textarea className="largeText" value={rules.custom_system_prompt || ''} onChange={e=>update('custom_system_prompt', e.target.value)} />
      <label>Промпт для генерации ответов на отзывы</label>
      <textarea className="largeText" value={rules.review_prompt_template || ''} onChange={e=>update('review_prompt_template', e.target.value)} />
      <label>Промпт для генерации ответов на вопросы</label>
      <textarea className="largeText" value={rules.question_prompt_template || ''} onChange={e=>update('question_prompt_template', e.target.value)} />
    </div>

    <div className="settingCard wide">
      <h3>Правила локальных шаблонов KARATOV</h3>
      <p className="meta">Эти правила используются и OpenAI-генерацией, и локальными шаблонами, когда OpenAI недоступен.</p>
      <label>Общие правила шаблонов</label>
      <textarea className="largeText" value={rules.template_rules_text || ''} onChange={e=>update('template_rules_text', e.target.value)} />
      <label>Локальные шаблоны по категориям. Формат: категория: затем варианты строками</label>
      <textarea className="templateText" value={rules.local_templates_text || ''} onChange={e=>update('local_templates_text', e.target.value)} />
      <label>Подписи, по одной в строке. Они будут чередоваться в разных вариантах ответов</label>
      <textarea value={(rules.signatures || []).join('\n')} onChange={e=>updateList('signatures', e.target.value)} />
    </div>

    <div className="settingCard wide">
      <h3>Категории и теги</h3>
      <label>Расширенный список категорий отзывов, по одной в строке</label>
      <textarea value={(rules.expanded_review_categories || []).join('\n')} onChange={e=>updateList('expanded_review_categories', e.target.value)} />
      <label>Ключевые слова категорий</label>
      <textarea className="largeText" value={rules.category_keywords_text || ''} onChange={e=>update('category_keywords_text', e.target.value)} />
    </div>

    <div className="settingCard wide">
      <h3>Автопубликация по площадкам</h3>
      <p className="meta">Отзывы и вопросы включаются отдельно. Фоновый worker работает сам; ручная выгрузка не нужна, если включена синхронизация и публикация в .env.</p>
      <label className="check"><input type="checkbox" checked={!!rules.real_autopublish_enabled} onChange={e=>update('real_autopublish_enabled', e.target.checked)} /> Включить фоновую автопубликацию</label>
      <div className="matrixGrid">
        {['WB','OZON','YM'].map(p => <div className="matrixCard" key={p}>
          <b>{p === 'WB' ? 'Wildberries' : p === 'OZON' ? 'Ozon' : 'Яндекс Маркет'}</b>
          <label className="check"><input type="checkbox" checked={!!matrix[p]?.reviews} onChange={e=>updateMatrix(p, 'reviews', e.target.checked)} /> Отзывы</label>
          <label className="check"><input type="checkbox" checked={!!matrix[p]?.questions} onChange={e=>updateMatrix(p, 'questions', e.target.checked)} /> Вопросы</label>
        </div>)}
      </div>
      <label>Минимальная оценка для автоответа на отзыв</label>
      <input type="number" min="1" max="5" value={rules.positive_review_min_rating} onChange={e=>update('positive_review_min_rating', Number(e.target.value))} />
      <label>Максимум автоответов за один фоновый проход</label>
      <input type="number" min="1" max="100" value={rules.autopublish_max_per_run || 10} onChange={e=>update('autopublish_max_per_run', Number(e.target.value))} />
      <label>Интервал фоновой автопубликации, секунд</label>
      <input type="number" min="60" value={rules.autopublish_interval_seconds || 900} onChange={e=>update('autopublish_interval_seconds', Number(e.target.value))} />
      <p className="meta">Если ENABLE_MARKETPLACE_PUBLISHING=false, приложение подготовит ответ, но не отправит его на площадку.</p>
    </div>

    <div className="settingCard">
      <h3>ИИ и fallback</h3>
      <label className="check"><input type="checkbox" checked={!!rules.ai_generation_enabled} onChange={e=>update('ai_generation_enabled', e.target.checked)} /> Использовать OpenAI для генерации</label>
      <label className="check"><input type="checkbox" checked={!!rules.ai_fallback_to_local_templates} onChange={e=>update('ai_fallback_to_local_templates', e.target.checked)} /> Если OpenAI недоступен или закончились кредиты — использовать локальные шаблоны</label>
      <label className="check"><input type="checkbox" checked={!!rules.autopublish_local_templates} onChange={e=>update('autopublish_local_templates', e.target.checked)} /> Разрешить автопубликацию локальных шаблонов KARATOV</label>
      <p className="meta">OpenAI key: <b>{openaiStatus?.api_key_found ? 'найден' : 'не найден'}</b><br/>Модель: <b>{openaiStatus?.model || '—'}</b><br/>Публикация: <b>{openaiStatus?.publishing_enabled ? 'реальная' : 'dry-run / не отправляет на площадку'}</b></p>
    </div>
    <div className="settingCard">
      <h3>Что всегда на ручную проверку</h3>
      <label>Категории, по одной в строке</label>
      <textarea value={(rules.require_review_categories || []).join('\n')} onChange={e=>updateList('require_review_categories', e.target.value)} />
      <label>Уровни риска, по одному в строке</label>
      <textarea value={(rules.require_review_risk_levels || []).join('\n')} onChange={e=>updateList('require_review_risk_levels', e.target.value)} />
    </div>
    <div className="settingCard">
      <h3>Ограничители текста</h3>
      <label>Запрещенные фразы, по одной в строке</label>
      <textarea value={(rules.forbidden_phrases || []).join('\n')} onChange={e=>updateList('forbidden_phrases', e.target.value)} />
      <label>Максимальная длина автоответа</label>
      <input type="number" value={rules.max_auto_answer_chars} onChange={e=>update('max_auto_answer_chars', Number(e.target.value))} />
    </div>
    {rulesDirty && <div className="message">Есть несохраненные изменения в промптах/настройках. Автообновление их не сбросит, но нажми «Сохранить» после редактирования.</div>}
    <button className="primary" onClick={()=>saveRules(rules)}>Сохранить настройки ИИ и шаблонов</button>
  </section>
}


function MetricList({title, items, onPick}) {
  return <div className="settingCard">
    <h3>{title}</h3>
    {(!items || items.length===0) && <p className="meta">Пока нет данных</p>}
    {(items || []).map(x => <button className="metricRow metricButton" key={x.name || x.category} onClick={()=>onPick && onPick(x.name || x.category)}><span>{x.name || x.category}</span><b>{x.count}</b></button>)}
  </div>
}

function CxSummary({summary, onDrill, setProductFilter}) {
  const [openCategory, setOpenCategory] = useState(null);
  if (!summary) return <div className="empty">Загружаю саммари...</div>;
  const details = summary.category_details || [];
  const current = details.find(d => d.category === openCategory) || details[0];
  const drillReviews = (filters={}) => onDrill && onDrill('reviews-unanswered', filters);
  const drillQuestions = (filters={}) => onDrill && onDrill('questions-unanswered', filters);
  return <section className="settingsPanel">
    <div className="settingCard wide">
      <h3>Текстовая выжимка CX</h3>
      <pre className="reportText">{summary.textual_insights?.text || 'Пока недостаточно данных для выжимки.'}</pre>
      <div className="twoCols">
        <div>
          <h4>За что хвалят</h4>
          {(summary.textual_insights?.praise_examples || []).map(x => <div className="exampleBox clickable" key={x.id} onClick={()=>{setProductFilter && setProductFilter(x.sku || x.product_name || ''); onDrill && onDrill('reviews-answered', {});}}>
            <b>{x.rating}★</b> · <ProductLink sku={x.sku} productName={x.product_name || x.sku} url={x.product_url} platform={x.platform} />
            <p>{x.text}</p>
          </div>)}
        </div>
        <div>
          <h4>На что жалуются</h4>
          {(summary.textual_insights?.complaint_examples || []).map(x => <div className="exampleBox clickable" key={x.id} onClick={()=>{setProductFilter && setProductFilter(x.sku || x.product_name || ''); drillReviews({sentiment:'negative'});}}>
            <b>{x.rating}★</b> · <ProductLink sku={x.sku} productName={x.product_name || x.sku} url={x.product_url} platform={x.platform} />
            <p>{x.text}</p>
          </div>)}
        </div>
      </div>
    </div>
    <MetricList title="Категории отзывов" items={summary.review_categories} onPick={name=>{setOpenCategory(name); drillReviews({category:name});}} />
    <MetricList title="Тональность отзывов" items={summary.review_sentiments} onPick={name=>drillReviews({sentiment:name})} />
    <MetricList title="Риски отзывов" items={summary.review_risks} onPick={name=>drillReviews({risk:name})} />
    <MetricList title="Категории вопросов" items={summary.question_categories} onPick={name=>drillQuestions({category:name})} />
    <MetricList title="Риски вопросов" items={summary.question_risks} onPick={name=>drillQuestions({risk:name})} />

    <div className="settingCard wide">
      <h3>Рекомендации по товарам</h3>
      {(!(summary.recommendations?.products || []).length) && <p className="meta">Пока недостаточно проблемных сигналов для рекомендаций.</p>}
      {(summary.recommendations?.products || []).map(r => <div className="recommendationRow clickable" key={`${r.platform}-${r.sku}-${r.product_name}`} onClick={()=>{setProductFilter && setProductFilter(r.sku || r.product_name || ''); drillReviews({});}}>
        <div><b><ProductLink sku={r.sku} productName={r.product_name || r.sku} url={r.product_url} platform={r.platform} /></b><p>{r.recommendation}</p><div className="tags">{(r.problem_categories || []).map(c => <Badge key={c}>{c}</Badge>)}</div></div>
        <b>{r.negative}/{r.total} · {r.negative_share}% негатива · {r.rating_avg ?? '—'}★</b>
      </div>)}
    </div>

    <div className="settingCard wide">
      <h3>Рекомендации по группам проблем</h3>
      {(summary.recommendations?.groups || []).map(r => <div className="recommendationRow clickable" key={r.category} onClick={()=>{setOpenCategory(r.category); drillReviews({category:r.category});}}>
        <span><b>{r.category}</b><p>{r.recommendation}</p></span><b>{r.count}</b>
      </div>)}
    </div>

    <div className="settingCard wide">
      <h3>Детализация по категории</h3>
      {current ? <>
        <p><b>{current.category}</b> · {current.count} отзывов</p>
        <h4>Товары, где тема встречается чаще</h4>
        {(current.top_products || []).map(p => <button className="metricRow metricButton" key={`${p.sku}-${p.product_name}`} onClick={()=>{setProductFilter && setProductFilter(p.sku || p.product_name || ''); drillReviews({category:current.category});}}><span><ProductLink sku={p.sku} productName={p.product_name || p.sku} url={p.product_url} platform={p.platform} /></span><b>{p.count}</b></button>)}
        <h4>Примеры отзывов</h4>
        {(current.examples || []).map(x => <div className="exampleBox clickable" key={x.id} onClick={()=>{setProductFilter && setProductFilter(x.sku || x.product_name || ''); drillReviews({category:current.category});}}>
          <b>{x.rating || '—'}★</b> · <ProductLink sku={x.sku} productName={x.product_name || x.sku} url={x.product_url} platform={x.platform} />
          <p>{x.text}</p>
          <div className="tags">{(x.tags || []).map(t => <Badge key={t}>{t}</Badge>)}</div>
        </div>)}
      </> : <p className="meta">Выбери категорию слева.</p>}
    </div>
    <div className="settingCard wide">
      <h3>Проблемные SKU по негативу</h3>
      {(summary.sku_negative || []).slice(0,10).map(x => <button className="metricRow metricButton" key={`${x.sku}-${x.product_name}`} onClick={()=>{setProductFilter && setProductFilter(x.sku || x.product_name || ''); drillReviews({});}}><span><b>{x.sku}</b> <ProductLink sku={x.sku} productName={x.product_name || ''} url={x.product_url} platform={x.platform} /></span><b>{x.negative}/{x.total}</b></button>)}
    </div>
  </section>
}

function ProductsPanel({products, dynamicFilter='all', setDynamicFilter, setProductFilter}) {
  const deltaField = dynamicFilter === 'day' ? 'rating_delta_day' : dynamicFilter === 'week' ? 'rating_delta_week' : dynamicFilter === 'month' ? 'rating_delta_month' : null;
  const rows = deltaField ? (products || []).filter(p => p[deltaField] !== null && p[deltaField] !== undefined).sort((a,b)=>Math.abs(b[deltaField]||0)-Math.abs(a[deltaField]||0)) : (products || []);
  const fmtDelta = (v) => v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${v}`;
  return <section className="tablePanel">
    <div className="filtersBar">
      <label>Динамика рейтинга</label>
      <select value={dynamicFilter} onChange={e=>setDynamicFilter && setDynamicFilter(e.target.value)}>
        <option value="all">Все товары</option>
        <option value="day">День ко дню</option>
        <option value="week">Неделя к неделе</option>
        <option value="month">Месяц к месяцу</option>
      </select>
    </div>
    <table>
      <thead><tr><th>SKU/товар</th><th>Отзывы</th><th>Средний рейтинг</th><th>День</th><th>Неделя</th><th>Месяц</th><th>Негатив</th><th>Категории</th></tr></thead>
      <tbody>
        {rows.map(p => <tr key={p.product_key} className="clickableRow" onClick={()=>setProductFilter && setProductFilter(p.sku || p.product_name || p.product_key || '')}>
          <td><b>{p.sku || '—'}</b><br/><ProductLink sku={p.sku} productName={p.product_name || p.product_key} url={p.product_url} platform={p.platform} /></td>
          <td>{p.reviews_count}</td>
          <td>{p.rating_avg ?? '—'}</td>
          <td>{fmtDelta(p.rating_delta_day)}</td>
          <td>{fmtDelta(p.rating_delta_week)}</td>
          <td>{fmtDelta(p.rating_delta_month)}</td>
          <td>{p.negative_count}</td>
          <td>{(p.top_categories || []).map(c => <Badge key={c.name}>{c.name}: {c.count}</Badge>)}</td>
        </tr>)}
      </tbody>
    </table>
  </section>
}

function AnomaliesPanel({anomalies}) {
  return <section className="tablePanel">
    {anomalies.length===0 && <div className="empty">Аномалий пока не найдено. Они появятся при резком изменении рейтинга, росте негатива или рискованных категориях.</div>}
    <table>
      <thead><tr><th>Товар</th><th>Рейтинг</th><th>Отзывы</th><th>Оценка риска</th><th>Причины</th></tr></thead>
      <tbody>
        {anomalies.map(p => <tr key={p.product_key}>
          <td><b>{p.sku || '—'}</b><br/><ProductLink sku={p.sku} productName={p.product_name || p.product_key} url={p.product_url} platform={p.platform} /></td>
          <td>{p.rating_avg ?? '—'} {p.rating_delta_last_sync ? `(${p.rating_delta_last_sync > 0 ? '+' : ''}${p.rating_delta_last_sync})` : ''}</td>
          <td>{p.reviews_count} / негатив: {p.negative_count}</td>
          <td><Badge tone={p.anomaly_score >= 5 ? 'red' : 'yellow'}>{p.anomaly_score}</Badge></td>
          <td>{(p.reasons || []).map(r => <div key={r}>• {r}</div>)}</td>
        </tr>)}
      </tbody>
    </table>
  </section>
}



function ReportsPanel({reports}) {
  const daily = reports?.daily || [];
  const products = reports?.pivot?.products_dynamic || [];
  const categories = reports?.pivot?.categories_dynamic || [];
  return <section className="settingsPanel">
    <div className="settingCard wide">
      <h3>Текстовый отчет CX</h3>
      <pre className="reportText">{reports?.text || 'Отчет пока не сформирован'}</pre>
      <div className="actions">
        <a className="buttonLike" href={`${API}/reports/export/daily.csv`} target="_blank" rel="noreferrer">Выгрузить ежедневный отчет CSV</a>
        <a className="buttonLike" href={`${API}/reports/export/pivot.csv`} target="_blank" rel="noreferrer">Выгрузить потоварную динамику CSV</a>
      </div>
    </div>
    <div className="settingCard wide">
      <h3>Ежедневная операционная отчетность</h3>
      <table><thead><tr><th>Дата</th><th>Отзывы поступили</th><th>Вопросы поступили</th><th>Отзывы отвечены</th><th>Вопросы отвечены</th><th>Отзывы ≤1ч / &gt;1ч</th><th>Вопросы ≤15м / &gt;15м</th></tr></thead>
      <tbody>{daily.slice(0,30).map(r => <tr key={r.date}><td>{r.date}</td><td>{r.reviews_received}</td><td>{r.questions_received}</td><td>{r.reviews_answered}</td><td>{r.questions_answered}</td><td>{r.reviews_answered_within_1h} / {r.reviews_answered_over_1h}</td><td>{r.questions_answered_within_15m} / {r.questions_answered_over_15m}</td></tr>)}</tbody></table>
    </div>
    <div className="settingCard wide">
      <h3>Потоварная динамика отзывов</h3>
      <table><thead><tr><th>Дата</th><th>Товар</th><th>Отзывы</th><th>Позитив</th><th>Негатив</th><th>Средний рейтинг</th></tr></thead>
      <tbody>{products.slice(0,80).map((r,i) => <tr key={i}><td>{r.date}</td><td><ProductLink sku={r.sku} productName={r.product_name || r.product_key} url={r.product_url} platform={r.platform} /></td><td>{r.reviews}</td><td>{r.positive}</td><td>{r.negative}</td><td>{r.rating_avg ?? '—'}</td></tr>)}</tbody></table>
    </div>
    <div className="settingCard wide">
      <h3>Динамика категорий</h3>
      <table><thead><tr><th>Дата</th><th>Категория</th><th>Количество</th></tr></thead>
      <tbody>{categories.slice(0,80).map((r,i) => <tr key={i}><td>{r.date}</td><td>{r.category}</td><td>{r.count}</td></tr>)}</tbody></table>
    </div>
  </section>
}

function SyncDiagnostics({status, ozonStatus, platformFilter='all'}) {
  if (!status) return <div className="empty">Загружаю диагностику...</div>;
  const result = status.last_result || {};
  const d = result.diagnostics || {};
  const blocks = d.blocks || {};
  const blockRows = Object.entries(blocks);
  const showWB = platformFilter !== 'OZON';
  const showOzon = platformFilter !== 'WB';
  return <section className="settingsPanel">
    {showWB && <div className="settingCard wide">
      <h3>Статус синхронизации WB</h3>
      <div className="metricRow"><span>Сейчас выполняется</span><b>{status.running ? 'да' : 'нет'}</b></div>
      <div className="metricRow"><span>Режим</span><b>{status.sync_mode}</b></div>
      <div className="metricRow"><span>Последний успех</span><b>{status.last_success_at ? new Date(status.last_success_at).toLocaleString() : '—'}</b></div>
      <div className="metricRow"><span>Последняя ошибка</span><b>{status.last_error || '—'}</b></div>
      {status.progress && <p className="meta">Текущий/последний шаг: {status.progress.step}</p>}
    </div>}
    {showWB && <div className="settingCard wide">
      <h3>Очередь синхронизации WB</h3>
      <p className="meta">Автосинк запускает один блок за проход. Если WB вернул 429, блок ставится на cooldown и не мешает остальным.</p>
      {Object.entries(status.blocks_state || {}).map(([key,b]) => <div className="schedulerGrid" key={key}>
        <b>{key}</b>
        <span>{b.status}</span>
        <span>успех: {b.last_success_at ? new Date(b.last_success_at).toLocaleString() : '—'}</span>
        <span>повтор: {b.next_retry_at ? new Date(b.next_retry_at).toLocaleString() : '—'}</span>
        <span>{b.last_result?.diagnostics?.blocks?.[key]?.received ?? 0} получено</span>
        {b.last_error && <span className="errorText">{b.last_error}</span>}
      </div>)}
    </div>}
    {showOzon && ozonStatus && <div className="settingCard wide">
      <h3>Статус синхронизации Ozon</h3>
      <div className="metricRow"><span>Включен</span><b>{ozonStatus.enabled ? 'да' : 'нет'}</b></div>
      <div className="metricRow"><span>Есть Client ID / API Key</span><b>{ozonStatus.has_client_id ? 'Client ID есть' : 'Client ID нет'} / {ozonStatus.has_api_key ? 'API key есть' : 'API key нет'}</b></div>
      <div className="metricRow"><span>Последний успех</span><b>{ozonStatus.last_success_at ? new Date(ozonStatus.last_success_at).toLocaleString() : '—'}</b></div>
      <div className="metricRow"><span>Последняя ошибка</span><b>{ozonStatus.last_error || '—'}</b></div>
      {Object.entries(ozonStatus.blocks || {}).map(([name, b]) => <div className="schedulerGrid" key={name}>
        <b>{name}</b><span>{b.status}</span><span>успех: {b.last_success_at ? new Date(b.last_success_at).toLocaleString() : '—'}</span><span>{b.last_error || '—'}</span><span>получено: {b.last_result?.received ?? '—'}</span>
      </div>)}
    </div>}

    {showWB && <div className="settingCard wide">
      <h3>Что вернул WB по блокам</h3>
      {blockRows.length === 0 && <p className="meta">Пока нет результата. Дождись автосинхронизации или нажми “Синхронизировать WB”.</p>}
      {blockRows.map(([key, b]) => <div className="blockRow" key={key}>
        <b>{key}</b>
        <span>статус: {b.status}</span>
        <span>получено: {b.received}</span>
        <span>создано: {b.created}</span>
        <span>обновлено: {b.updated}</span>
        {b.error && <span className="errorText">ошибка: {b.error}</span>}
      </div>)}
    </div>}
    {showWB && <div className="settingCard wide">
      <h3>Предупреждения WB</h3>
      {(!d.warnings || d.warnings.length === 0) && <p className="meta">Предупреждений нет</p>}
      {(d.warnings || []).map(w => <div key={w} className="risk"><AlertTriangle size={16}/> {w}</div>)}
    </div>}
  </section>
}


function FboBookingPanel({state, setState, setMessage}) {
  const [form, setForm] = useState(state?.settings || null);
  const [excludedDate, setExcludedDate] = useState('');
  const [excludedReason, setExcludedReason] = useState('');
  const [targetEdits, setTargetEdits] = useState({});
  const [newTargetDate, setNewTargetDate] = useState('');
  const [newTargetComment, setNewTargetComment] = useState('');
  const [wbCabinet, setWbCabinet] = useState(null);
  const [wbApiBusy, setWbApiBusy] = useState(false);
  const [wbLive, setWbLive] = useState(null);
  const [liveOn, setLiveOn] = useState(true);
  useEffect(() => { if (state?.settings) setForm(state.settings); }, [state]);
  useEffect(() => {
    let alive = true;
    async function pullLive(){
      if (!liveOn) return;
      try {
        const r = await api('/fbo-booking/wb-cabinet/live-status');
        if (alive) setWbLive(r.live || null);
      } catch(e) {}
    }
    pullLive();
    const t = setInterval(pullLive, 2000);
    return () => { alive = false; clearInterval(t); };
  }, [liveOn]);
  if (!state || !form) return <div className="empty">Загружаю модуль автобронирования FBO...</div>;

  const targets = state.targets || [];
  const logs = state.logs || [];
  const notes = state.notifications || [];
  const caps = form.wb_api_capabilities || [];
  const runtime = state.runtime || {};
  const booked = targets.filter(t => t.status === 'booked').length;
  const found = targets.filter(t => t.status === 'found').length;
  const open = targets.filter(t => ['planned','searching','not_found','found','booking'].includes(t.status)).length;
  const skipped = targets.filter(t => t.status === 'skipped_holiday').length;
  const update = (patch) => setForm(prev => ({...prev, ...patch}));
  const toggleWarehouse = (name) => {
    const current = new Set(form.warehouses || []);
    if (current.has(name)) current.delete(name); else current.add(name);
    update({warehouses: Array.from(current)});
  };

  async function save() {
    setMessage('Сохраняю настройки ЛК WB-first FBO...');
    const next = await api('/fbo-booking/settings', {method:'PUT', body: JSON.stringify({...form, search_source_mode:'wb_cabinet', postavleno_enabled:false, wb_api_monitor_enabled:false})});
    setState(next); setMessage('Настройки FBO сохранены');
  }
  async function auditWbApi() {
    if (wbApiBusy) return;
    setWbApiBusy(true);
    setMessage('Проверяю возможности WB API по поставкам. Запросы идут бережно, с паузами...');
    try {
      const next = await api('/fbo-booking/wb-api/audit', {method:'POST'});
      setState(next); setMessage('WB API audit выполнен. Проверь таблицу возможностей.');
    } finally {
      setWbApiBusy(false);
    }
  }
  async function monitorWbApi() {
    if (wbApiBusy) return;
    setWbApiBusy(true);
    setMessage('Мониторю окна/коэффициенты через WB API. Повторные клики временно заблокированы...');
    try {
      const next = await api('/fbo-booking/wb-api/monitor', {method:'POST'});
      setState(next); setMessage('WB API мониторинг выполнен. Если найдено окно — дата отмечена в календаре.');
    } finally {
      setWbApiBusy(false);
    }
  }
  async function fullWbCycle() {
    setMessage('Запускаю автопилот через ЛК WB: кабинет → проверка дат → подготовка брони...');
    const next = await api('/fbo-booking/full-wb-cycle', {method:'POST'});
    setState(next); setWbCabinet(next.wb_cabinet || null); setMessage('Цикл ЛК WB выполнен. Проверь календарь, скриншот кабинета и журнал.');
  }
  async function startWbLogin() {
    const alias = form.wb_cabinet_profile_alias || 'default';
    setMessage('Открываю ЛК WB через Playwright. Если будет экран входа — авторизуйся и нажми “Проверить вход”.');
    const next = await api('/fbo-booking/wb-cabinet/start-login', {method:'POST', body: JSON.stringify({profile_alias: alias})});
    setState(next); setWbCabinet(next.wb_cabinet || null); setMessage('ЛК WB открыт. Статус: ' + (next.settings?.wb_cabinet_connection_status || '—'));
  }
  
  async function checkWbLogin() {
    const alias = form.wb_cabinet_profile_alias || 'default';
    setMessage('Проверяю авторизацию в ЛК WB...');
    const next = await api('/fbo-booking/wb-cabinet/check-login', {method:'POST', body: JSON.stringify({profile_alias: alias})});
    setState(next); setWbCabinet(next.wb_cabinet || null); setMessage('Статус ЛК WB: ' + (next.settings?.wb_cabinet_connection_status || '—'));
  }
  async function monitorWbCabinet() {
    const alias = form.wb_cabinet_profile_alias || 'default';
    setMessage('Проверяю окна через ЛК WB без обращения к API...');
    const next = await api('/fbo-booking/wb-cabinet/monitor', {method:'POST', body: JSON.stringify({profile_alias: alias})});
    setState(next); setWbCabinet(next.wb_cabinet || null); setMessage('Мониторинг ЛК WB выполнен. Проверь календарь, скриншот и журнал.');
  }
  async function prepareWbBooking() {
    const alias = form.wb_cabinet_profile_alias || 'default';
    setMessage('Готовлю маршрут создания поставки в ЛК WB. Live View обновляется каждые 2 секунды...');
    setLiveOn(true);
    const next = await api('/fbo-booking/wb-cabinet/book-next', {method:'POST', body: JSON.stringify({profile_alias: alias})});
    setState(next); setWbCabinet(next.wb_cabinet || null); setMessage('Маршрут ЛК WB завершен. Проверь Live View, журнал и календарь.');
  }
  async function stopWbRoute() {
    const r = await api('/fbo-booking/wb-cabinet/stop-route', {method:'POST'});
    setWbLive(r.live || null);
    setMessage('Отправила команду остановки маршрута ЛК WB');
  }
  async function runNow() {
    setMessage('Запускаю безопасную проверку FBO...');
    const next = await api('/fbo-booking/run-now', {method:'POST'});
    setState(next); setMessage('Проверка FBO выполнена.');
  }
  async function addHoliday() {
    if (!excludedDate) { setMessage('Укажи дату исключения.'); return; }
    const next = await api('/fbo-booking/excluded-dates', {method:'POST', body: JSON.stringify({date: excludedDate, reason: excludedReason})});
    setState(next); setExcludedDate(''); setExcludedReason(''); setMessage('Дата добавлена в исключения');
  }
  async function testTelegram() {
    const r = await api('/fbo-booking/test-telegram', {method:'POST'});
    setMessage(r.ok ? 'Тест Telegram отправлен' : `Telegram не отправлен: ${r.message}`);
  }
  async function testEmail() {
    const r = await api('/fbo-booking/test-email', {method:'POST'});
    setMessage(r.ok ? 'Тест email отправлен' : `Email не отправлен: ${r.message}`);
  }
  async function addCalendarTarget() {
    if (!newTargetDate) { setMessage('Укажи новую дату для календаря окон.'); return; }
    const next = await api('/fbo-booking/targets', {method:'POST', body: JSON.stringify({date: newTargetDate, comment: newTargetComment})});
    setState(next); setNewTargetDate(''); setNewTargetComment(''); setMessage('Новая дата добавлена в календарь FBO');
  }
  async function saveTargetDate(oldDate) {
    const edit = targetEdits[oldDate];
    if (!edit?.date) { setMessage('Укажи новую дату.'); return; }
    const next = await api(`/fbo-booking/targets/${oldDate}`, {method:'PATCH', body: JSON.stringify({new_date: edit.date, comment: edit.comment || ''})});
    setState(next); setTargetEdits(prev => { const copy = {...prev}; delete copy[oldDate]; return copy; }); setMessage('Дата в календаре FBO изменена');
  }
  const statusTone = (s) => s === 'booked' ? 'green' : s === 'found' ? 'green' : s === 'booking' ? 'yellow' : s === 'error' ? 'red' : s === 'skipped_holiday' ? 'yellow' : 'neutral';
  const capTone = (s) => s === 'ok' ? 'green' : s === 'warning' ? 'yellow' : s === 'error' ? 'red' : 'neutral';

  return <section className="settingsPanel fboPanel">
    <div className="fboHero">
      <div><h2>Автобронирование FBO — ЛК WB-first</h2><p>Первоисточник слотов — кабинет продавца WB. API больше не является основным мониторингом, поэтому 429 не останавливает автопилот.</p></div>
      <Badge tone={form.enabled ? 'green' : 'yellow'}>{form.enabled ? 'Активно' : 'Выключено'}</Badge>
    </div>
    <div className="fboCards">
      <div className="fboCard"><span>Старт</span><b>{formatDate(form.start_date)}</b></div>
      <div className="fboCard"><span>Открытых дат</span><b>{open}</b></div>
      <div className="fboCard"><span>Найдено WB</span><b>{found}</b></div>
      <div className="fboCard"><span>Забронировано</span><b>{booked}</b></div>
      <div className="fboCard"><span>Исключено</span><b>{skipped}</b></div>
    </div>

    <div className="settingCard wide autopilotStatus">
      <h3>Автопилот через ЛК WB</h3>
      <div className="metricRow"><span>Состояние</span><b>{runtime.running ? 'работает' : 'выключен'}</b></div>
      <div className="metricRow"><span>Последний цикл</span><b>{form.scheduler_last_finished_at ? formatDate(form.scheduler_last_finished_at) : '—'}</b></div>
      <div className="metricRow"><span>Следующий автоповтор</span><b>{form.scheduler_next_retry_at || runtime.next_retry_at ? formatDate(form.scheduler_next_retry_at || runtime.next_retry_at) : 'по интервалу'}</b></div>
      <p className="meta">{runtime.last_message || 'Автопоиск сам открывает ЛК WB в рабочее окно, проверяет целевые даты и перезапускается без ручного клика. WB API используется только для диагностики.'}</p>
      {runtime.last_error && <p className="errorText">{runtime.last_error}</p>}
    </div>

    <div className="fboGrid">
      <div className="settingCard wide">
        <h3>Правила FBO</h3>
        <div className="fboTwo">
          <label>Склады WB</label><div className="checks"><label><input type="checkbox" checked={(form.warehouses||[]).includes('Коледино')} onChange={()=>toggleWarehouse('Коледино')}/> Коледино</label><label><input type="checkbox" checked={(form.warehouses||[]).includes('Электросталь')} onChange={()=>toggleWarehouse('Электросталь')}/> Электросталь</label></div>
          <label>Тип поставки</label><input value={form.supply_type || 'Суперсейф'} onChange={e=>update({supply_type:e.target.value})}/>
          <label>Макс. коэффициент</label><input type="number" value={form.max_coefficient || 20} onChange={e=>update({max_coefficient:Number(e.target.value)})}/>
          <label>Дата старта</label><input type="date" value={form.start_date || ''} onChange={e=>update({start_date:e.target.value})}/>
          <label>Шаг, рабочих дней</label><input type="number" value={form.step_working_days || 3} onChange={e=>update({step_working_days:Number(e.target.value)})}/>
          <label>Горизонт, дней</label><input type="number" value={form.planning_horizon_days || 30} onChange={e=>update({planning_horizon_days:Number(e.target.value)})}/>
          <label>Поиск с</label><input type="time" value={form.search_from || '09:00'} onChange={e=>update({search_from:e.target.value})}/>
          <label>Поиск до</label><input type="time" value={form.search_to || '21:00'} onChange={e=>update({search_to:e.target.value})}/>
          <label>Интервал проверки ЛК WB, сек</label><input type="number" value={form.check_interval_seconds || 300} onChange={e=>update({check_interval_seconds:Number(e.target.value), wb_cabinet_check_interval_seconds:Number(e.target.value)})}/>
          <label>Пауза после ошибки ЛК WB, сек</label><input type="number" value={form.wb_cabinet_error_backoff_seconds || 120} onChange={e=>update({wb_cabinet_error_backoff_seconds:Number(e.target.value)})}/>
          <label>Профиль ЛК WB</label><input value={form.wb_cabinet_profile_alias || 'default'} onChange={e=>update({wb_cabinet_profile_alias:e.target.value})}/><label>Кол-во первого товара</label><input type="number" value={form.wb_cabinet_template_quantity || 1000} onChange={e=>update({wb_cabinet_template_quantity:Number(e.target.value)})}/><label>Склад по умолчанию для черновика</label><select value={form.wb_cabinet_default_warehouse || 'Электросталь'} onChange={e=>update({wb_cabinet_default_warehouse:e.target.value})}><option>Электросталь</option><option>Коледино</option></select><label>Режим бронирования</label><select value={form.wb_cabinet_real_booking_enabled && form.wb_cabinet_safe_mode === false ? 'real' : 'test'} onChange={e=>update({wb_cabinet_booking_mode:e.target.value, wb_cabinet_real_booking_enabled:e.target.value==='real', wb_cabinet_safe_mode:e.target.value!=='real'})}><option value="test">Тестовый: дойти до финального экрана, не создавать поставку</option><option value="real">Боевой: нажать “Создать поставку” и отправить уведомление</option></select>
          <label>Ожидаемый ЛК WB</label><input value={form.wb_cabinet_expected_company || 'ГОЛДСТАРТ ООО'} onChange={e=>update({wb_cabinet_expected_company:e.target.value})}/><label>Распознанный ЛК WB</label><input readOnly value={form.wb_cabinet_detected_company || '—'}/><label>Подтверждение боевого режима</label><input placeholder={'Создавать поставки в ' + (form.wb_cabinet_expected_company || 'ГОЛДСТАРТ ООО')} value={form.wb_cabinet_final_confirmation_text || ''} onChange={e=>update({wb_cabinet_final_confirmation_text:e.target.value})}/><label>URL ЛК WB</label><input value={form.wb_cabinet_url || 'https://seller.wildberries.ru'} onChange={e=>update({wb_cabinet_url:e.target.value})}/>
        </div>
        <label className="check"><input type="checkbox" checked={form.scheduler_enabled !== false} onChange={e=>update({scheduler_enabled:e.target.checked})}/> Автопилот включен: мониторить ЛК WB по расписанию и перезапускаться автоматически</label>
        <label className="check"><input type="checkbox" checked={form.auto_book !== false} onChange={e=>update({auto_book:e.target.checked})}/> Автоматически готовить черновик/бронь после найденного окна</label>
        <label className="check"><input type="checkbox" checked={form.wb_cabinet_real_booking_enabled === true && form.wb_cabinet_safe_mode === false} onChange={e=>update({wb_cabinet_real_booking_enabled:e.target.checked, wb_cabinet_safe_mode:!e.target.checked, wb_cabinet_booking_mode:e.target.checked?'real':'test'})}/> Боевой режим: нажимать “Создать поставку” после выбора даты</label>
        <label className="check"><input type="checkbox" checked={form.wb_cabinet_require_final_confirmation !== false} onChange={e=>update({wb_cabinet_require_final_confirmation:e.target.checked})}/> Перед финальной кнопкой требовать текстовое подтверждение ЛК</label>
        <label className="check"><input type="checkbox" checked={form.postavleno_enabled === true} onChange={e=>update({postavleno_enabled:e.target.checked})}/> POSTAVLENO только как ручной внешний сигнал</label>
        <div className="actions"><button className="primary" onClick={save}>Сохранить настройки</button><button onClick={runNow}>Пересобрать календарь</button></div>
      </div>

      <div className="settingCard">
        <h3>Уведомления</h3>
        <div className="metricRow"><span>Telegram</span><b>{form.telegram_enabled ? 'включен' : 'выключен'}</b></div>
        <div className="metricRow"><span>Email</span><b>{form.email_enabled ? 'включен' : 'выключен'}</b></div>
        <div className="actions vertical"><button onClick={testTelegram}>Отправить тест Telegram</button><button onClick={testEmail}>Отправить тест email</button></div>
        <h3>Праздники / ТК не возит</h3>
        <div className="holidayBox"><input type="date" value={excludedDate} onChange={e=>setExcludedDate(e.target.value)}/><input placeholder="Причина" value={excludedReason} onChange={e=>setExcludedReason(e.target.value)}/><button onClick={addHoliday}>Добавить</button></div>
        <div className="miniList">{(form.excluded_dates || []).length ? form.excluded_dates.map(d => <Badge tone="yellow" key={d}>{d}</Badge>) : <span className="meta">Исключений пока нет</span>}</div>
      </div>
    </div>

    <div className="fboGrid">
      <div className="settingCard wide">
        <h3>WB API: только диагностика</h3>
        <p className="meta">API больше не основной способ поиска окон. Основной мониторинг идет через ЛК WB, чтобы не зависеть от 429. Этот блок оставлен только для редкой диагностики токена/коэффициентов.</p>
        <p className="meta"><b>Бережный режим WB API:</b> пауза между запросами, кэш коэффициентов на 15 минут, складов на сутки, поставок на 5 минут. При 429 хаб ставит мониторинг на паузу.</p>
        <div className="actions"><button className="primary" disabled={wbApiBusy} onClick={auditWbApi}>{wbApiBusy ? 'WB API занят...' : 'Проверить возможности WB API'}</button><button className="primary" disabled={wbApiBusy} onClick={fullWbCycle}>Запустить цикл ЛК WB сейчас</button></div>
        <div className="metricRow"><span>Последний audit</span><b>{form.wb_api_last_audit_at ? formatDate(form.wb_api_last_audit_at) : '—'}</b></div>
        <div className="metricRow"><span>Последний мониторинг ЛК WB</span><b>{form.wb_cabinet_monitor_last_at ? formatDate(form.wb_cabinet_monitor_last_at) : '—'}</b></div>
        {form.wb_api_last_error && <p className="errorText">WB API: {form.wb_api_last_error}</p>}
        <table><thead><tr><th>Проверка</th><th>Статус</th><th>HTTP</th><th>Детали</th></tr></thead><tbody>{caps.map((c,i)=><tr key={i}><td><b>{c.name}</b><p className="meta">{c.description}</p></td><td><Badge tone={capTone(c.status)}>{c.status}</Badge></td><td>{c.status_code || '—'}</td><td className="apiDetails">{String(c.details || '').slice(0,360)}</td></tr>)}</tbody></table>
        {caps.length===0 && <p className="meta">Audit еще не запускался.</p>}
      </div>
      <div className="settingCard wide">
        <h3>ЛК WB / Playwright safe-mode</h3>
        <div className="metricRow"><span>Статус ЛК WB</span><b>{form.wb_cabinet_connection_status || 'not_connected'}</b></div>
        <div className="metricRow"><span>Профиль</span><b>{form.wb_cabinet_profile_alias || 'default'}</b></div>
        <p className="meta">Хаб открывает только домен ЛК WB. Маршрут: Черновики → Новая поставка → Вручную → первый товар, 1000 шт → склад/Суперсейф → дата → пропуск упаковки/ШК → финал. В тестовом режиме финальная кнопка не нажимается.</p>
        <div className="actions vertical"><button onClick={startWbLogin}>1. Открыть ЛК WB</button><button onClick={checkWbLogin}>2. Проверить вход</button><button onClick={monitorWbCabinet}>3. Мониторить окна через ЛК WB</button><button className="primary" onClick={prepareWbBooking}>4. Запустить маршрут создания поставки</button><button className="dangerBtn" onClick={stopWbRoute}>Остановить маршрут</button></div>
        <div className="wbLiveBox"><div className="wbLiveHeader"><b>Live View ЛК WB</b><Badge tone={(wbLive?.running) ? 'yellow' : 'neutral'}>{wbLive?.running ? 'выполняется' : 'ожидает'}</Badge></div><div className="metricRow"><span>Текущий шаг</span><b>{wbLive?.step || form.wb_cabinet_last_route_step || '—'}</b></div><div className="metricRow"><span>Прогресс</span><b>{wbLive?.step_index || 0}/{wbLive?.total_steps || 15}</b></div><div className="metricRow"><span>ЛК WB</span><b>{wbLive?.detected_company || form.wb_cabinet_detected_company || 'не распознан'}</b></div>{wbLive?.error && <p className="errorText">{wbLive.error}</p>}{(wbLive?.screenshot_base64 || wbCabinet?.screenshot_base64 || form.wb_cabinet_last_screenshot) && <div className="wbLiveShot"><img src={'data:image/png;base64,' + (wbLive?.screenshot_base64 || wbCabinet?.screenshot_base64 || form.wb_cabinet_last_screenshot)} alt="WB cabinet live screenshot" /></div>}</div>
      </div>
    </div>

    <div className="settingCard wide">
      <h3>Календарь окон на месяц вперед</h3>
      <div className="holidayBox calendarAddRow"><input type="date" value={newTargetDate} onChange={e=>setNewTargetDate(e.target.value)}/><input placeholder="Комментарий к новой дате" value={newTargetComment} onChange={e=>setNewTargetComment(e.target.value)}/><button onClick={addCalendarTarget}>Добавить дату</button></div>
      <p className="meta">Дату можно изменить прямо в строке. Старую дату хаб добавит в исключения, чтобы она не вернулась автоматическим правилом.</p>
      <table><thead><tr><th>Дата</th><th>WB статус</th><th>Склад</th><th>Время</th><th>Коэффициент</th><th>Черновик</th><th>Комментарий</th><th></th></tr></thead>
      <tbody>{targets.map(t => { const edit = targetEdits[t.date] || {date:t.date, comment:''}; const changed = edit.date !== t.date || !!edit.comment; return <tr key={t.date}>
        <td><input className="inlineDate" type="date" value={edit.date} onChange={e=>setTargetEdits(prev=>({...prev, [t.date]: {...(prev[t.date] || {date:t.date, comment:''}), date:e.target.value}}))}/>{t.manual && <Badge tone="neutral">ручная</Badge>}</td>
        <td><Badge tone={statusTone(t.status)}>{t.status_label || t.status}</Badge></td><td>{t.warehouse || '—'}</td><td>{t.slot_time || '—'}</td><td>{t.coefficient ?? '—'}</td><td>{t.draft_id || '—'}</td>
        <td><div>{t.message || '—'}</div><input className="inlineComment" placeholder="Комментарий к изменению" value={edit.comment || ''} onChange={e=>setTargetEdits(prev=>({...prev, [t.date]: {...(prev[t.date] || {date:t.date, comment:''}), comment:e.target.value}}))}/></td>
        <td>{changed && <button className="primary" onClick={()=>saveTargetDate(t.date)}>сохранить дату</button>}</td>
      </tr> })}</tbody></table>
    </div>

    <div className="fboGrid">
      <div className="settingCard"><h3>Внутренние уведомления</h3>{(notes || []).slice(0,8).map((n,i) => <div className="notificationRow" key={i}><Badge tone={n.level === 'success' ? 'green' : n.level === 'error' ? 'red' : 'neutral'}>{n.level}</Badge><b>{n.title}</b><p>{n.body}</p><span>{formatDate(n.created_at)}</span></div>)}{(!notes || notes.length===0) && <p className="meta">Уведомлений пока нет.</p>}</div>
      <div className="settingCard"><h3>Журнал действий</h3><table><thead><tr><th>Дата</th><th>Действие</th><th>Результат</th><th>Детали</th></tr></thead><tbody>{logs.slice(0,10).map((l,i)=><tr key={i}><td>{formatDate(l.created_at)}</td><td>{l.action}</td><td>{l.result}</td><td>{l.details}</td></tr>)}</tbody></table></div>
    </div>
  </section>
}

createRoot(document.getElementById('root')).render(<App/>);
