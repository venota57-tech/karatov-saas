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

const PLATFORMS = ["ALL", "WB", "OZON", "YM"];
const ANSWER_STATES = [
  ["all", "Все"],
  ["unanswered", "Без ответа"],
  ["answered", "С ответом"],
  ["drafts", "Черновики"],
  ["published", "Опубликовано"],
  ["stale", "Устаревшие"],
];

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  const text = await res.text();
  let data = null;

  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }

  if (!res.ok) {
    throw new Error(
      typeof data === "object"
        ? data.detail || data.error || JSON.stringify(data)
        : data
    );
  }

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

function pretty(x) {
  return typeof x === "string" ? x : JSON.stringify(x, null, 2);
}

function boolText(v) {
  return v ? "да" : "нет";
}

function dt(v) {
  return v ? String(v).replace("T", " ").slice(0, 19) : "—";
}

function toDateKey(v) {
  if (!v) return "Без даты";
  return String(v).slice(0, 10);
}

function toMonthKey(v) {
  if (!v) return "Без месяца";
  return String(v).slice(0, 7);
}

function toWeekKey(v) {
  if (!v) return "Без недели";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return "Без недели";
  const oneJan = new Date(d.getFullYear(), 0, 1);
  const week = Math.ceil((((d - oneJan) / 86400000) + oneJan.getDay() + 1) / 7);
  return `${d.getFullYear()}-W${String(week).padStart(2, "0")}`;
}

function Badge({ children, type = "" }) {
  return <span className={`badge ${type}`}>{children}</span>;
}

function PlatformBadge({ p }) {
  return <span className={`platformBadge ${p || ""}`}>{p || "—"}</span>;
}

function groupCount(items, getKey) {
  const map = {};
  items.forEach((x) => {
    const key = getKey(x);
    map[key] = (map[key] || 0) + 1;
  });
  return Object.entries(map)
    .sort(([a], [b]) => String(b).localeCompare(String(a)))
    .slice(0, 20);
}

function diffMinutes(start, end) {
  if (!start || !end) return null;
  const a = new Date(start).getTime();
  const b = new Date(end).getTime();
  if (Number.isNaN(a) || Number.isNaN(b)) return null;
  return Math.max(0, Math.round((b - a) / 60000));
}

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
  const [draft, setDraft] = useState("");

  const [rules, setRules] = useState({});
  const [diagnostics, setDiagnostics] = useState(null);
  const [syncStatus, setSyncStatus] = useState(null);
  const [ozonStatus, setOzonStatus] = useState(null);
  const [summary, setSummary] = useState("");
  const [booking, setBooking] = useState(null);
  const [lastRefreshAt, setLastRefreshAt] = useState(null);

  function setOk(text) {
    setMessage(text);
  }

  function setErr(prefix, e) {
    setMessage(`${prefix}: ${e.message}`);
  }

  function itemMatchesFilters(item) {
    const p = String(item.platform || "").toUpperCase();

    if (platform !== "ALL" && p !== platform) return false;

    if (answerState === "unanswered") {
      if (!(item.operational_status === "needs_response" || item.has_answer === false)) return false;
    }

    if (answerState === "answered") {
      if (!(item.has_answer === true || item.response_origin === "seller_cabinet")) return false;
    }

    if (answerState === "drafts") {
      if (!(item.final_answer || item.draft_answer || item.status === "ready_to_review" || item.status === "ready_to_publish")) return false;
    }

    if (answerState === "published") {
      if (!(item.status === "published" || item.status === "auto_published" || item.response_origin === "auto_app")) return false;
    }

    if (answerState === "stale") {
      if (!String(item.operational_status || "").includes("stale")) return false;
    }

    const q = product.trim().toLowerCase();
    if (q) {
      const haystack = [
        item.sku,
        item.product_name,
        item.external_id,
        item.text,
        item.pros,
        item.cons,
      ].join(" ").toLowerCase();
      if (!haystack.includes(q)) return false;
    }

    if (category.trim() && String(item.ai_category || "") !== category.trim()) return false;
    if (risk.trim() && String(item.ai_risk_level || "") !== risk.trim()) return false;

    return true;
  }

  const reviews = useMemo(
    () => rawReviews.filter(itemMatchesFilters),
    [rawReviews, platform, answerState, product, category, risk]
  );

  const questions = useMemo(
    () => rawQuestions.filter(itemMatchesFilters),
    [rawQuestions, platform, answerState, product, category, risk]
  );

  const allItems = useMemo(() => [...rawReviews, ...rawQuestions], [rawReviews, rawQuestions]);
  const visibleItems = useMemo(() => [...reviews, ...questions], [reviews, questions]);

  const metrics = useMemo(() => {
    const needs = allItems.filter((x) => x.operational_status === "needs_response" || x.has_answer === false);
    const ready = allItems.filter((x) => x.status === "ready_to_publish" || x.status === "ready_to_review" || x.final_answer);
    const high = allItems.filter((x) => x.ai_risk_level === "high");

    return {
      reviews: rawReviews.length,
      questions: rawQuestions.length,
      noAnswer: needs.length,
      ready: ready.length,
      risk: high.length,
      wb: allItems.filter((x) => x.platform === "WB").length,
      ozon: allItems.filter((x) => x.platform === "OZON").length,
    };
  }, [allItems, rawReviews, rawQuestions]);

  useEffect(() => {
    bootstrap();

    const timer = setInterval(() => {
      refreshAll(false);
    }, 60000);

    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    if (section === "reviews" && reviews[0] && (!selected || selected.type !== "review")) {
      setSelected({ type: "review", item: reviews[0] });
      setDraft(reviews[0].final_answer || reviews[0].draft_answer || "");
    }

    if (section === "questions" && questions[0] && (!selected || selected.type !== "question")) {
      setSelected({ type: "question", item: questions[0] });
      setDraft(questions[0].final_answer || questions[0].draft_answer || "");
    }
  }, [section, reviews, questions]);

  async function bootstrap() {
    await Promise.allSettled([
      refreshAll(false),
      loadDiagnostics(false),
      loadRules(false),
      loadSyncStatus(false),
      loadBooking(false),
    ]);
  }

  async function refreshAll(show = true) {
    if (show) setLoading(true);
    if (show) setOk("Обновляю данные из базы...");

    try {
      const [reviewsData, questionsData] = await Promise.all([
        api("/reviews?limit=500"),
        api("/questions?limit=500"),
      ]);

      setRawReviews(asList(reviewsData));
      setRawQuestions(asList(questionsData));
      setLastRefreshAt(new Date().toISOString());

      if (show) setOk("Данные обновлены");
    } catch (e) {
      setErr("Ошибка обновления данных", e);
    } finally {
      if (show) setLoading(false);
    }
  }

  async function generateSelected() {
    if (!selected?.item?.id) return;

    setLoading(true);
    setOk("Генерирую ответ через AI / локальные шаблоны с quality gate 10/10...");

    try {
      const base = selected.type === "question" ? "/questions" : "/reviews";
      const fresh = await api(`${base}/${selected.item.id}/generate`, { method: "POST" });

      setSelected({ ...selected, item: fresh });
      setDraft(fresh.final_answer || fresh.draft_answer || "");
      setOk(fresh.final_answer ? "Ответ готов к проверке" : "Ответ не прошел quality gate 10/10");

      await refreshAll(false);
    } catch (e) {
      setErr("Ошибка генерации", e);
    } finally {
      setLoading(false);
    }
  }

  async function saveAnswer() {
    if (!selected?.item?.id) return;

    setLoading(true);
    setOk("Сохраняю ответ...");

    try {
      const base = selected.type === "question" ? "/questions" : "/reviews";
      const fresh = await api(`${base}/${selected.item.id}/answer`, {
        method: "PATCH",
        body: JSON.stringify({ final_answer: draft }),
      });

      setSelected({ ...selected, item: fresh });
      setOk("Ответ сохранен");
      await refreshAll(false);
    } catch (e) {
      setErr("Ошибка сохранения", e);
    } finally {
      setLoading(false);
    }
  }

  async function publishSelected() {
    if (!selected?.item?.id) return;

    setLoading(true);
    setOk("Публикую ответ...");

    try {
      const base = selected.type === "question" ? "/questions" : "/reviews";
      const result = await api(`${base}/${selected.item.id}/publish`, { method: "POST" });

      setOk(`Результат публикации: ${pretty(result)}`);
      await refreshAll(false);
      await loadDiagnostics(false);
    } catch (e) {
      setErr("Ошибка публикации", e);
    } finally {
      setLoading(false);
    }
  }

  async function loadRules(show = true) {
    if (show) setOk("Загружаю AI-правила и шаблоны...");

    try {
      const data = await api("/settings/automation-rules");
      setRules(data || {});
      if (show) setOk("Правила загружены");
    } catch (e) {
      setErr("Ошибка загрузки правил", e);
    }
  }

  async function saveRules() {
    setLoading(true);
    setOk("Сохраняю AI-правила...");

    try {
      const payload = { ...rules };
      delete payload.updated_at;

      const data = await api("/settings/automation-rules", {
        method: "PUT",
        body: JSON.stringify(payload),
      });

      setRules(data || payload);
      await api("/autopublish-settings/api", {
        method: "POST",
        body: JSON.stringify(payload),
      }).catch(() => null);

      setOk("AI-правила, шаблоны и автопубликация сохранены");
    } catch (e) {
      setErr("Ошибка сохранения правил", e);
    } finally {
      setLoading(false);
    }
  }

  async function runAutopublish() {
    setLoading(true);
    setOk("Запускаю один проход автопубликации...");

    try {
      setOk(`Автопубликация: ${pretty(await api("/autopublish", { method: "POST" }))}`);
      await refreshAll(false);
    } catch (e) {
      setErr("Ошибка автопубликации", e);
    } finally {
      setLoading(false);
    }
  }

  async function loadDiagnostics(show = true) {
    if (show) setOk("Загружаю диагностику...");

    try {
      const data = await api("/system/diagnostics").catch(() => api("/system/status"));
      setDiagnostics(data);
      if (data?.rules) setRules(data.rules);
      if (show) setOk("Диагностика обновлена");
    } catch (e) {
      setErr("Ошибка диагностики", e);
    }
  }

  async function loadSyncStatus(show = true) {
    try {
      const wb = await api("/sync/status").catch(() => null);
      const oz = await api("/sync/ozon/status").catch(() => null);

      setSyncStatus(wb);
      setOzonStatus(oz);

      if (show) setOk("Статусы синхронизации обновлены");
    } catch (e) {
      if (show) setErr("Ошибка статуса синхронизации", e);
    }
  }

  async function runSync(path, label) {
    setLoading(true);
    setOk(`Запускаю ${label}...`);

    try {
      const data = await api(path, { method: "POST" });
      setOk(`${label}: ${pretty(data)}`);

      await Promise.allSettled([
        refreshAll(false),
        loadSyncStatus(false),
        loadDiagnostics(false),
      ]);
    } catch (e) {
      setErr(`Ошибка ${label}`, e);
    } finally {
      setLoading(false);
    }
  }

  async function loadSummary() {
    setLoading(true);
    setOk("Формирую CX-саммари...");

    try {
      const paths = ["/summary", "/summary/cx", "/reports/summary"];
      let data = null;
      let err = null;

      for (const p of paths) {
        try {
          data = await api(p);
          break;
        } catch (e) {
          err = e;
        }
      }

      if (!data && err) throw err;

      setSummary(pretty(data));
      setOk("Саммари сформировано");
    } catch (e) {
      setErr("Ошибка саммари", e);
    } finally {
      setLoading(false);
    }
  }

  async function loadBooking(show = true) {
    try {
      const data = await api("/wb-booking/status");
      setBooking(data);
      if (show) setOk("Статус WB FBO слотов обновлен");
    } catch (e) {
      if (show) setErr("Ошибка WB FBO модуля", e);
    }
  }

  async function toggleBooking(enabled) {
    setLoading(true);

    try {
      const data = await api(enabled ? "/wb-booking/start" : "/wb-booking/stop", {
        method: "POST",
        body: JSON.stringify(booking || {}),
      });

      setBooking(data);
      setOk(enabled ? "Мониторинг WB FBO слотов включен" : "Мониторинг WB FBO слотов остановлен");
    } catch (e) {
      setErr("Ошибка WB FBO", e);
    } finally {
      setLoading(false);
    }
  }

  function updateRule(key, value) {
    setRules((prev) => ({ ...prev, [key]: value }));
  }

  function updateMatrix(p, kind, value) {
    setRules((prev) => ({
      ...prev,
      autopublish_matrix: {
        ...(prev.autopublish_matrix || {}),
        [p]: {
          ...(prev.autopublish_matrix?.[p] || {}),
          [kind]: value,
        },
      },
    }));
  }

  function top(title, subtitle, actions = null) {
    return (
      <div className="top">
        <div>
          <h2>{title}</h2>
          <p>{subtitle}</p>
        </div>
        <div className="actions">{actions}</div>
      </div>
    );
  }

  function filters() {
    return (
      <div className="sectionFilters">
        <div className="productFilterBox inline">
          <label>Площадка</label>
          <select value={platform} onChange={(e) => setPlatform(e.target.value)}>
            {PLATFORMS.map((p) => (
              <option key={p} value={p}>{p === "ALL" ? "Все" : p}</option>
            ))}
          </select>
        </div>

        <div className="productFilterBox inline">
          <label>Состояние</label>
          <select value={answerState} onChange={(e) => setAnswerState(e.target.value)}>
            {ANSWER_STATES.map(([v, t]) => (
              <option key={v} value={v}>{t}</option>
            ))}
          </select>
        </div>

        <div className="productFilterBox inline">
          <label>Товар / SKU</label>
          <input
            value={product}
            onChange={(e) => setProduct(e.target.value)}
            placeholder="артикул, название, id"
          />
        </div>

        <button onClick={() => refreshAll(true)}>Обновить сейчас</button>
      </div>
    );
  }

  function renderDashboard() {
    return (
      <>
        {top(
          "Операционный кабинет KARATOV CX Hub",
          "Данные обновляются автоматически. Операторы видят реальные отзывы, вопросы, AI-черновики и статусы публикации.",
          <button className="primary" onClick={() => refreshAll(true)}>Обновить сейчас</button>
        )}

        <div className="cards">
          <button onClick={() => setSection("reviews")}><b>{metrics.reviews}</b><span>Отзывы всего</span></button>
          <button onClick={() => setSection("questions")}><b>{metrics.questions}</b><span>Вопросы всего</span></button>
          <button onClick={() => setSection("reviews")}><b>{metrics.noAnswer}</b><span>Требуют ответа</span></button>
          <button onClick={() => setSection("autopublish")}><b>{metrics.ready}</b><span>Есть черновики</span></button>
          <button onClick={() => setSection("reports")}><b>{metrics.risk}</b><span>Высокий риск</span></button>
          <button onClick={() => { setPlatform("WB"); setSection("reviews"); }}><b>{metrics.wb}</b><span>WB</span></button>
          <button onClick={() => { setPlatform("OZON"); setSection("reviews"); }}><b>{metrics.ozon}</b><span>Ozon</span></button>
        </div>

        <div className="settingsPanel">
          <div className="settingCard">
            <h3>Статус данных</h3>
            <div className="metricRow"><span>Последнее обновление UI</span><b>{dt(lastRefreshAt)}</b></div>
            <div className="metricRow"><span>Отзывы в базе UI</span><b>{rawReviews.length}</b></div>
            <div className="metricRow"><span>Вопросы в базе UI</span><b>{rawQuestions.length}</b></div>
            <button onClick={() => refreshAll(true)}>Обновить</button>
          </div>

          <div className="settingCard">
            <h3>API-ключи</h3>
            {diagnostics?.keys ? Object.entries(diagnostics.keys).map(([k, v]) => (
              <div className="metricRow" key={k}><span>{k}</span><b>{boolText(v)}</b></div>
            )) : <p>Нажми «Обновить диагностику»</p>}
            <button onClick={() => loadDiagnostics(true)}>Обновить диагностику</button>
          </div>

          <div className="settingCard">
            <h3>Синхронизация</h3>
            <div className="metricRow"><span>WB auto</span><b>{boolText(syncStatus?.auto_sync_enabled)}</b></div>
            <div className="metricRow"><span>WB running</span><b>{boolText(syncStatus?.running)}</b></div>
            <div className="metricRow"><span>WB mode</span><b>{syncStatus?.sync_mode || "—"}</b></div>
            <button onClick={() => loadSyncStatus(true)}>Обновить статус</button>
          </div>
        </div>
      </>
    );
  }

  function listPane(type) {
    const items = type === "question" ? questions : reviews;

    return (
      <div className="list">
        {items.length === 0 ? (
          <div className="empty">Данных по выбранным фильтрам нет.</div>
        ) : items.map((item, i) => (
          <div
            key={`${type}-${item.id || i}`}
            className={`row ${selected?.type === type && selected?.item?.id === item.id ? "selected" : ""}`}
            onClick={() => {
              setSelected({ type, item });
              setDraft(item.final_answer || item.draft_answer || "");
            }}
          >
            <div className="rowhead">
              <b>{item.product_name || item.sku || `Запись ${item.id || i + 1}`}</b>
              <PlatformBadge p={item.platform} />
            </div>

            <div className="dateMeta">
              {item.rating && <span>⭐ <b>{item.rating}</b></span>}
              <span>{dt(item.created_at_marketplace)}</span>
              <span>{item.source_status || "—"}</span>
            </div>

            <div className="text">{item.text || item.pros || item.cons || "Без текста"}</div>

            <div className="tags">
              <Badge>{item.status || "new"}</Badge>
              {item.ai_category && <Badge type="yellow">{item.ai_category}</Badge>}
              {item.ai_risk_level && <Badge type={item.ai_risk_level === "high" ? "red" : ""}>{item.ai_risk_level}</Badge>}
              {item.final_answer && <Badge type="green">черновик готов</Badge>}
            </div>
          </div>
        ))}
      </div>
    );
  }

  function detailPane() {
    const item = selected?.item;

    if (!item) {
      return <div className="detail"><div className="empty">Выбери запись слева</div></div>;
    }

    return (
      <div className="detail">
        <div className="detailhead">
          <div>
            <h3>{item.product_name || item.sku || "Карточка"}</h3>
            <p className="meta">{selected.type === "question" ? "Вопрос" : "Отзыв"} · {item.platform} · {item.source_status}</p>
          </div>
          {item.product_url && (
            <a className="buttonLike" href={item.product_url} target="_blank" rel="noreferrer">Открыть товар</a>
          )}
        </div>

        <div className="clientText">{item.text || item.pros || item.cons || "Нет текста"}</div>

        <div className="twoCols">
          <div className="exampleBox"><b>AI-категория</b><p>{item.ai_category || "—"}</p></div>
          <div className="exampleBox"><b>Quality / причина</b><p>{item.ai_reason || item.publish_blocked_reason || "—"}</p></div>
        </div>

        <label>Финальный ответ</label>
        <textarea value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Сгенерируй или введи ответ" />

        <div className="actions">
          <button className="primary" onClick={generateSelected}>Сгенерировать 10/10</button>
          <button onClick={saveAnswer}>Сохранить</button>
          <button onClick={publishSelected}>Опубликовать</button>
          <button onClick={() => navigator.clipboard.writeText(draft || "")}>Скопировать</button>
        </div>
      </div>
    );
  }

  function renderWork(type) {
    return (
      <>
        {top(
          type === "question" ? "Вопросы покупателей" : "Отзывы покупателей",
          "Список обновляется автоматически. Фильтры работают без ручной перезагрузки страницы.",
          <button className="primary" onClick={() => refreshAll(true)}>{loading ? "Загрузка..." : "Обновить сейчас"}</button>
        )}
        {filters()}
        <div className="workspace">{listPane(type)}{detailPane()}</div>
      </>
    );
  }

  function renderReports() {
    const reviewsByDay = groupCount(rawReviews, (x) => toDateKey(x.created_at_marketplace));
    const reviewsByWeek = groupCount(rawReviews, (x) => toWeekKey(x.created_at_marketplace));
    const reviewsByMonth = groupCount(rawReviews, (x) => toMonthKey(x.created_at_marketplace));

    const questionsByDay = groupCount(rawQuestions, (x) => toDateKey(x.created_at_marketplace));
    const questionsByWeek = groupCount(rawQuestions, (x) => toWeekKey(x.created_at_marketplace));
    const questionsByMonth = groupCount(rawQuestions, (x) => toMonthKey(x.created_at_marketplace));

    const answeredReviews = rawReviews.filter((x) => x.has_answer || x.response_origin || String(x.status || "").includes("published"));
    const answeredQuestions = rawQuestions.filter((x) => x.has_answer || x.response_origin || String(x.status || "").includes("published"));

    const reviewsUnder60 = answeredReviews.filter((x) => {
      const m = diffMinutes(x.created_at_marketplace, x.updated_at);
      return m !== null && m <= 60;
    }).length;

    const reviewsOver60 = answeredReviews.filter((x) => {
      const m = diffMinutes(x.created_at_marketplace, x.updated_at);
      return m !== null && m > 60;
    }).length;

    const questionsUnder15 = answeredQuestions.filter((x) => {
      const m = diffMinutes(x.created_at_marketplace, x.updated_at);
      return m !== null && m <= 15;
    }).length;

    const questionsOver15 = answeredQuestions.filter((x) => {
      const m = diffMinutes(x.created_at_marketplace, x.updated_at);
      return m !== null && m > 15;
    }).length;

    function table(title, rows) {
      return (
        <div className="settingCard">
          <h3>{title}</h3>
          <table>
            <thead><tr><th>Период</th><th>Кол-во</th></tr></thead>
            <tbody>{rows.map(([k, v]) => <tr key={k}><td>{k}</td><td>{v}</td></tr>)}</tbody>
          </table>
        </div>
      );
    }

    return (
      <>
        {top("Отчеты", "Ключевые метрики по отзывам, вопросам, SLA и динамике", <button onClick={() => refreshAll(true)}>Обновить данные</button>)}

        <div className="cards">
          <button><b>{rawReviews.length}</b><span>Отзывы total</span></button>
          <button><b>{rawQuestions.length}</b><span>Вопросы total</span></button>
          <button><b>{reviewsUnder60}</b><span>Отзывы ≤ 1 часа</span></button>
          <button><b>{reviewsOver60}</b><span>Отзывы &gt; 1 часа</span></button>
          <button><b>{questionsUnder15}</b><span>Вопросы ≤ 15 минут</span></button>
          <button><b>{questionsOver15}</b><span>Вопросы &gt; 15 минут</span></button>
          <button><b>{metrics.risk}</b><span>High risk</span></button>
        </div>

        <div className="settingsPanel">
          {table("Отзывы день к дню", reviewsByDay)}
          {table("Отзывы неделя к неделе", reviewsByWeek)}
          {table("Отзывы месяц к месяцу", reviewsByMonth)}
          {table("Вопросы день к дню", questionsByDay)}
          {table("Вопросы неделя к неделе", questionsByWeek)}
          {table("Вопросы месяц к месяцу", questionsByMonth)}
        </div>

        <div className="settingCard wide">
          <h3>Важно по SLA</h3>
          <p>
            Сейчас SLA считается по доступным полям created_at_marketplace → updated_at.
            Для точного SLA публикации нужно, чтобы backend сохранял отдельное поле published_at / answered_at после реальной отправки ответа в WB/Ozon.
          </p>
        </div>
      </>
    );
  }

  function renderSettings() {
    return (
      <>
        {top("AI / шаблоны / quality gate", "Промты, локальные шаблоны, fallback, подписи, запреты и правила качества", (
          <>
            <button onClick={() => loadRules(true)}>Загрузить</button>
            <button className="primary" onClick={saveRules}>Сохранить</button>
          </>
        ))}

        <div className="settingsPanel">
          <div className="settingCard">
            <h3>AI</h3>
            <label className="check"><input type="checkbox" checked={!!rules.ai_generation_enabled} onChange={(e) => updateRule("ai_generation_enabled", e.target.checked)} /> Генерация AI включена</label>
            <label className="check"><input type="checkbox" checked={!!rules.ai_fallback_to_local_templates} onChange={(e) => updateRule("ai_fallback_to_local_templates", e.target.checked)} /> Fallback на локальные шаблоны</label>
            <label className="check"><input type="checkbox" checked={!!rules.auto_generate_on_sync} onChange={(e) => updateRule("auto_generate_on_sync", e.target.checked)} /> Автогенерация при синхронизации</label>
          </div>

          <div className="settingCard">
            <h3>Quality gate</h3>
            <div className="metricRow"><span>Публикация только если</span><b>10/10</b></div>
            <label>Макс. длина ответа</label>
            <input type="number" value={rules.max_auto_answer_chars || 900} onChange={(e) => updateRule("max_auto_answer_chars", Number(e.target.value))} />
          </div>

          <div className="settingCard wide">
            <h3>Системный промт KARATOV</h3>
            <textarea className="templateText" value={rules.custom_system_prompt || ""} onChange={(e) => updateRule("custom_system_prompt", e.target.value)} />
          </div>

          <div className="settingCard wide">
            <h3>Промт для отзывов</h3>
            <textarea className="largeText" value={rules.review_prompt_template || ""} onChange={(e) => updateRule("review_prompt_template", e.target.value)} />
          </div>

          <div className="settingCard wide">
            <h3>Промт для вопросов</h3>
            <textarea className="largeText" value={rules.question_prompt_template || ""} onChange={(e) => updateRule("question_prompt_template", e.target.value)} />
          </div>

          <div className="settingCard wide">
            <h3>Правила использования шаблонов</h3>
            <textarea className="templateText" value={rules.template_rules_text || ""} onChange={(e) => updateRule("template_rules_text", e.target.value)} />
          </div>

          <div className="settingCard wide">
            <h3>Локальные шаблоны fallback</h3>
            <textarea className="templateText" value={rules.local_templates_text || ""} onChange={(e) => updateRule("local_templates_text", e.target.value)} />
          </div>
        </div>
      </>
    );
  }

  function renderAutopublish() {
    return (
      <>
        {top("Автопубликация", "Матрица площадок, лимиты, dry-run/real publish и ручные ограничения", (
          <>
            <button onClick={() => loadRules(true)}>Загрузить</button>
            <button className="primary" onClick={saveRules}>Сохранить</button>
            <button onClick={runAutopublish}>Запустить сейчас</button>
          </>
        ))}

        <div className="settingsPanel">
          <div className="settingCard">
            <h3>Главный переключатель</h3>
            <label className="check">
              <input type="checkbox" checked={!!rules.real_autopublish_enabled} onChange={(e) => updateRule("real_autopublish_enabled", e.target.checked)} />
              Автопубликация разрешена в правилах
            </label>
            <p className="meta">Реальная отправка дополнительно требует ENABLE_MARKETPLACE_PUBLISHING=true на Render.</p>
          </div>

          <div className="settingCard">
            <h3>Лимиты</h3>
            <label>Макс. за запуск</label>
            <input type="number" value={rules.autopublish_max_per_run || 10} onChange={(e) => updateRule("autopublish_max_per_run", Number(e.target.value))} />
            <label>Пауза между публикациями, сек</label>
            <input type="number" value={rules.autopublish_pause_between_items_seconds || 8} onChange={(e) => updateRule("autopublish_pause_between_items_seconds", Number(e.target.value))} />
          </div>
        </div>

        <div className="matrixGrid">
          {["WB", "OZON", "YM"].map((p) => (
            <div className="matrixCard" key={p}>
              <b>{p}</b>
              <label className="check"><input type="checkbox" checked={!!rules.autopublish_matrix?.[p]?.reviews} onChange={(e) => updateMatrix(p, "reviews", e.target.checked)} />Отзывы</label>
              <label className="check"><input type="checkbox" checked={!!rules.autopublish_matrix?.[p]?.questions} onChange={(e) => updateMatrix(p, "questions", e.target.checked)} />Вопросы</label>
            </div>
          ))}
        </div>
      </>
    );
  }

  function renderSync() {
    return (
      <>
        {top("Синхронизация", "WB/Ozon обновляются автоматически; ручные кнопки только для аварийной проверки", <button onClick={() => loadSyncStatus(true)}>Обновить статус</button>)}

        <div className="settingsPanel">
          <div className="settingCard">
            <h3>WB</h3>
            <div className="blockButtons">
              <button onClick={() => runSync("/sync/wb", "WB next block")}>Следующий безопасный блок</button>
              <button onClick={() => runSync("/sync/wb/operational/next", "WB operational")}>Очередь без ответа</button>
              <button onClick={() => runSync("/sync/wb/backfill/next", "WB archive backfill")}>Дозагрузка архива</button>
            </div>
          </div>

          <div className="settingCard">
            <h3>Ozon</h3>
            <div className="blockButtons">
              <button onClick={() => runSync("/sync/ozon", "Ozon all")}>Все блоки</button>
              <button onClick={() => runSync("/sync/ozon/block/reviews_unanswered", "Ozon reviews unanswered")}>Отзывы без ответа</button>
              <button onClick={() => runSync("/sync/ozon/block/questions_unanswered", "Ozon questions unanswered")}>Вопросы без ответа</button>
            </div>
          </div>

          <div className="settingCard">
            <h3>Статус автообновления</h3>
            <pre className="reportText smallPre">{pretty({ wb: syncStatus, ozon: ozonStatus })}</pre>
          </div>
        </div>
      </>
    );
  }

  function renderSummary() {
    return (
      <>
        {top("Саммари CX", "Сводка по отзывам, вопросам, рискам и тематикам", <button onClick={loadSummary}>Сформировать</button>)}
        <div className="settingCard wide"><pre className="reportText">{summary || "Нажми «Сформировать»"}</pre></div>
      </>
    );
  }

  function renderBooking() {
    return (
      <>
        {top("WB FBO слоты", "Модуль управления автобронированием поставок", (
          <>
            <button onClick={() => loadBooking(true)}>Обновить</button>
            <button className="primary" onClick={() => toggleBooking(true)}>Включить</button>
            <button onClick={() => toggleBooking(false)}>Остановить</button>
          </>
        ))}
        <div className="settingCard wide"><pre className="reportText">{pretty(booking || "Нет статуса")}</pre></div>
      </>
    );
  }

  function renderSystem() {
    return (
      <>
        {top("Диагностика", "Ключи не раскрываются, показывается только наличие и статусы модулей", <button onClick={() => loadDiagnostics(true)}>Обновить</button>)}
        <div className="settingCard wide"><pre className="reportText">{pretty(diagnostics || "Нет диагностики")}</pre></div>
      </>
    );
  }

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

  return (
    <div className="app">
      <aside>
        <h1>KARATOV<br />CX Hub</h1>
        <div className={`currentPlatform ${platform}`}>{platform === "ALL" ? "Все площадки" : platform}</div>

        {NAV.map(([id, title]) => (
          <button key={id} className={section === id ? "active" : ""} onClick={() => setSection(id)}>
            {title}
            {id === "reviews" && <span className="navCount">{metrics.reviews}</span>}
            {id === "questions" && <span className="navCount">{metrics.questions}</span>}
          </button>
        ))}

        <div className="syncMini">
          Backend: {diagnostics?.status || "ok"}<br />
          UI refresh: {dt(lastRefreshAt)}<br />
          {loading ? "Выполняется..." : "Готово"}
        </div>

        <div className="hint">Данные обновляются автоматически. Кнопки — только для ручной проверки.</div>
      </aside>

      <main>
        {message && <div className="message">{message}</div>}
        {content()}
      </main>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);