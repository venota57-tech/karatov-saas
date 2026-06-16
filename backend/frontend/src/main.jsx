import React, { useEffect, useMemo, useState } from "react";
import ReactDOM from "react-dom/client";
import "./style.css";

const API = "";

const NAV = [
  ["dashboard", "Дашборд"],
  ["reviews", "Отзывы"],
  ["questions", "Вопросы"],
  ["reports", "Отчеты"],
  ["summary", "Саммари CX"],
  ["settings", "Настройки"],
  ["autopublish", "Автопубликация"],
  ["sync", "Синхронизация"],
];

async function api(path, options = {}) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  const text = await res.text();
  let data;

  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }

  if (!res.ok) {
    throw new Error(
      typeof data === "object" ? data.error || JSON.stringify(data) : data
    );
  }

  return data;
}

function normalizeList(data) {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.items)) return data.items;
  if (Array.isArray(data?.reviews)) return data.reviews;
  if (Array.isArray(data?.questions)) return data.questions;
  if (Array.isArray(data?.data)) return data.data;
  return [];
}

function Badge({ children, type = "" }) {
  return <span className={`badge ${type}`}>{children}</span>;
}

function App() {
  const [section, setSection] = useState("dashboard");
  const [health, setHealth] = useState(null);
  const [reviews, setReviews] = useState([]);
  const [questions, setQuestions] = useState([]);
  const [selected, setSelected] = useState(null);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [platform, setPlatform] = useState("WB");
  const [report, setReport] = useState("");
  const [settings, setSettings] = useState({});
  const [draft, setDraft] = useState("");

  const currentItems = section === "questions" ? questions : reviews;

  const metrics = useMemo(() => {
    const all = [...reviews, ...questions];
    return {
      reviews: reviews.length,
      questions: questions.length,
      total: all.length,
      noAnswer: all.filter((x) => !x.has_answer && !x.final_answer).length,
      ready: all.filter((x) => x.status === "ready_to_publish").length,
      risk: all.filter((x) => x.ai_risk_level === "high").length,
    };
  }, [reviews, questions]);

  useEffect(() => {
    checkHealth();
  }, []);

  async function checkHealth() {
    try {
      const data = await api("/health");
      setHealth(data);
    } catch (e) {
      setHealth({ status: "error", error: e.message });
    }
  }

  async function loadReviews() {
    setLoading(true);
    setMessage("Загружаю отзывы...");
    try {
      const data = await api("/reviews");
      const list = normalizeList(data);
      setReviews(list);
      setSelected(list[0] || null);
      setMessage(`Отзывы загружены: ${list.length}`);
    } catch (e) {
      setMessage(`Ошибка загрузки отзывов: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }

  async function loadQuestions() {
    setLoading(true);
    setMessage("Загружаю вопросы...");
    try {
      const data = await api("/questions");
      const list = normalizeList(data);
      setQuestions(list);
      setSelected(list[0] || null);
      setMessage(`Вопросы загружены: ${list.length}`);
    } catch (e) {
      setMessage(`Ошибка загрузки вопросов: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }

  async function generateAnswer(item) {
    setLoading(true);
    setMessage("Генерирую ответ...");
    try {
      const data = await api("/generate", {
        method: "POST",
        body: JSON.stringify(item),
      });

      const answer = data.answer_text || data.answer || JSON.stringify(data, null, 2);
      setDraft(answer);
      setSelected({ ...item, final_answer: answer, draft_answer: answer });
      setMessage("Ответ сгенерирован");
    } catch (e) {
      setMessage(`Ошибка генерации: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }

  async function runAutopublish() {
    setLoading(true);
    setMessage("Запускаю автопубликацию...");
    try {
      const data = await api("/autopublish", { method: "POST" });
      setMessage(`Автопубликация: ${JSON.stringify(data)}`);
    } catch (e) {
      setMessage(`Ошибка автопубликации: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }

  async function loadReport(type = "summary") {
    setLoading(true);
    setMessage("Формирую отчет...");
    try {
      const paths = [`/reports/${type}`, `/reports`, `/summary`];

      let data = null;
      let lastError = null;

      for (const path of paths) {
        try {
          data = await api(path);
          break;
        } catch (e) {
          lastError = e;
        }
      }

      if (!data && lastError) throw lastError;

      setReport(
        typeof data === "string" ? data : JSON.stringify(data, null, 2)
      );
      setMessage("Отчет сформирован");
    } catch (e) {
      setMessage(`Ошибка отчета: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }

  async function loadSettings() {
    setLoading(true);
    setMessage("Загружаю настройки...");
    try {
      const paths = ["/autopublish-settings", "/settings"];
      let data = {};

      for (const path of paths) {
        try {
          data = await api(path);
          break;
        } catch {}
      }

      setSettings(data || {});
      setMessage("Настройки загружены");
    } catch (e) {
      setMessage(`Ошибка настроек: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }

  async function saveSettings() {
    setLoading(true);
    setMessage("Сохраняю настройки...");
    try {
      const body = JSON.stringify(settings);
      let data;

      try {
        data = await api("/autopublish-settings", {
          method: "POST",
          body,
        });
      } catch {
        data = await api("/settings", {
          method: "POST",
          body,
        });
      }

      setSettings(data || settings);
      setMessage("Настройки сохранены");
    } catch (e) {
      setMessage(`Ошибка сохранения настроек: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }

  async function runSync(target) {
    setLoading(true);
    setMessage(`Запускаю синхронизацию ${target}...`);

    const variants = [
      `/sync/${target.toLowerCase()}`,
      `/${target.toLowerCase()}_sync`,
      `/${target.toLowerCase()}-sync`,
      "/sync",
    ];

    try {
      let result = null;
      let lastError = null;

      for (const path of variants) {
        try {
          result = await api(path, { method: "POST" });
          break;
        } catch (e) {
          lastError = e;
        }
      }

      if (!result && lastError) throw lastError;
      setMessage(`Синхронизация ${target}: ${JSON.stringify(result)}`);
    } catch (e) {
      setMessage(`Ошибка синхронизации: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }

  function renderDashboard() {
    return (
      <>
        <div className="top">
          <div>
            <h2>Операционный кабинет KARATOV CX Hub</h2>
            <p>Отзывы, вопросы, AI-ответы, отчеты, настройки и автопубликация</p>
          </div>

          <div className="cards">
            <button onClick={() => setSection("reviews")}>
              <b>{metrics.reviews}</b>
              <span>Отзывы</span>
            </button>
            <button onClick={() => setSection("questions")}>
              <b>{metrics.questions}</b>
              <span>Вопросы</span>
            </button>
            <button onClick={() => setSection("autopublish")}>
              <b>{metrics.ready}</b>
              <span>Готово к публикации</span>
            </button>
            <button onClick={() => setSection("reports")}>
              <b>{metrics.risk}</b>
              <span>Риски</span>
            </button>
          </div>
        </div>

        <div className="settingsPanel">
          <div className="settingCard">
            <h3>Статус backend</h3>
            <div className="metricRow">
              <span>Health</span>
              <b>{health?.status || "..."}</b>
            </div>
            <button onClick={checkHealth}>Проверить</button>
          </div>

          <div className="settingCard">
            <h3>Быстрые действия</h3>
            <div className="blockButtons">
              <button onClick={loadReviews}>Загрузить отзывы</button>
              <button onClick={loadQuestions}>Загрузить вопросы</button>
              <button onClick={runAutopublish}>Запустить автопубликацию</button>
            </div>
          </div>

          <div className="settingCard">
            <h3>Площадки</h3>
            <div className="platformSwitch">
              {["WB", "OZON", "YM"].map((p) => (
                <button
                  key={p}
                  className={platform === p ? "active" : ""}
                  onClick={() => setPlatform(p)}
                >
                  {p}
                </button>
              ))}
            </div>
            <p className="meta">Текущая площадка: {platform}</p>
          </div>
        </div>
      </>
    );
  }

  function renderList(type) {
    const isQuestions = type === "questions";
    return (
      <>
        <div className="top">
          <div>
            <h2>{isQuestions ? "Вопросы покупателей" : "Отзывы покупателей"}</h2>
            <p>Очередь обработки, генерация ответа, проверка качества</p>
          </div>
          <div className="actions">
            <button className="primary" onClick={isQuestions ? loadQuestions : loadReviews}>
              {loading ? "Загрузка..." : "Обновить"}
            </button>
          </div>
        </div>

        <div className="workspace">
          <div className="list">
            {currentItems.length === 0 ? (
              <div className="empty">Данных пока нет. Нажми «Обновить».</div>
            ) : (
              currentItems.map((item, i) => (
                <div
                  key={item.id || i}
                  className={`row ${selected === item ? "selected" : ""}`}
                  onClick={() => {
                    setSelected(item);
                    setDraft(item.final_answer || item.draft_answer || "");
                  }}
                >
                  <div className="rowhead">
                    <b>{item.product_name || item.sku || `Запись ${i + 1}`}</b>
                    <Badge>{item.platform || platform}</Badge>
                  </div>
                  <div className="dateMeta">
                    {item.rating && <span>⭐ <b>{item.rating}</b></span>}
                    {item.status && <span>{item.status}</span>}
                  </div>
                  <div className="text">{item.text || item.question || "Без текста"}</div>
                  <div className="tags">
                    {item.ai_risk_level && <Badge type="red">Риск: {item.ai_risk_level}</Badge>}
                    {item.ai_category && <Badge type="yellow">{item.ai_category}</Badge>}
                    {item.has_answer ? <Badge type="green">С ответом</Badge> : <Badge>Без ответа</Badge>}
                  </div>
                </div>
              ))
            )}
          </div>

          <div className="detail">
            {!selected ? (
              <div className="empty">Выбери запись слева</div>
            ) : (
              <>
                <div className="detailhead">
                  <div>
                    <h3>{selected.product_name || selected.sku || "Карточка"}</h3>
                    <p className="meta">{selected.platform || platform}</p>
                  </div>
                  {selected.product_url && (
                    <a className="buttonLike" href={selected.product_url} target="_blank" rel="noreferrer">
                      Открыть товар
                    </a>
                  )}
                </div>

                <div className="clientText">
                  {selected.text || selected.question || "Нет текста"}
                </div>

                {selected.ai_reason && (
                  <div className="risk">
                    <b>AI:</b> {selected.ai_reason}
                  </div>
                )}

                <label>Ответ</label>
                <textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder="Здесь будет ответ AI или ручной ответ"
                />

                <div className="actions">
                  <button className="primary" onClick={() => generateAnswer(selected)}>
                    Сгенерировать ответ
                  </button>
                  <button onClick={() => navigator.clipboard.writeText(draft || "")}>
                    Скопировать
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </>
    );
  }

  function renderReports() {
    return (
      <>
        <div className="top">
          <div>
            <h2>Отчеты и аналитика</h2>
            <p>NPS/CX, категории жалоб, динамика, риски, рекомендации</p>
          </div>
          <div className="actions">
            <button onClick={() => loadReport("summary")}>Сформировать отчет</button>
          </div>
        </div>

        <div className="settingsPanel">
          <div className="settingCard">
            <h3>Ключевые метрики</h3>
            <div className="metricRow"><span>Отзывы</span><b>{metrics.reviews}</b></div>
            <div className="metricRow"><span>Вопросы</span><b>{metrics.questions}</b></div>
            <div className="metricRow"><span>Без ответа</span><b>{metrics.noAnswer}</b></div>
            <div className="metricRow"><span>Риски</span><b>{metrics.risk}</b></div>
          </div>

          <div className="settingCard wide">
            <h3>Отчет</h3>
            <pre className="reportText">
              {report || "Нажми «Сформировать отчет». Если backend-отчет еще не подключен, здесь появится диагностическая ошибка endpoint’а."}
            </pre>
          </div>
        </div>
      </>
    );
  }

  function renderSettings() {
    return (
      <>
        <div className="top">
          <div>
            <h2>Настройки</h2>
            <p>Промты, подпись, режимы публикации, лимиты, quality gate</p>
          </div>
          <div className="actions">
            <button onClick={loadSettings}>Загрузить</button>
            <button className="primary" onClick={saveSettings}>Сохранить</button>
          </div>
        </div>

        <div className="settingsPanel">
          <div className="settingCard">
            <h3>AI</h3>
            <label className="check">
              <input
                type="checkbox"
                checked={Boolean(settings.ai_generation_enabled)}
                onChange={(e) => setSettings({ ...settings, ai_generation_enabled: e.target.checked })}
              />
              Генерация AI включена
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={Boolean(settings.ai_fallback_to_local_templates)}
                onChange={(e) => setSettings({ ...settings, ai_fallback_to_local_templates: e.target.checked })}
              />
              Fallback на локальные шаблоны
            </label>
          </div>

          <div className="settingCard">
            <h3>Автопубликация</h3>
            <label className="check">
              <input
                type="checkbox"
                checked={Boolean(settings.real_autopublish_enabled)}
                onChange={(e) => setSettings({ ...settings, real_autopublish_enabled: e.target.checked })}
              />
              Реальная автопубликация включена
            </label>
            <label>Лимит за запуск</label>
            <input
              type="number"
              value={settings.autopublish_max_per_run || 10}
              onChange={(e) => setSettings({ ...settings, autopublish_max_per_run: Number(e.target.value) })}
            />
          </div>

          <div className="settingCard">
            <h3>Подпись</h3>
            <textarea
              value={settings.signature || "С уважением, команда KARATOV"}
              onChange={(e) => setSettings({ ...settings, signature: e.target.value })}
            />
          </div>

          <div className="settingCard wide">
            <h3>Промт / правила</h3>
            <textarea
              className="templateText"
              value={settings.prompt || ""}
              onChange={(e) => setSettings({ ...settings, prompt: e.target.value })}
              placeholder="Здесь можно хранить рабочий промт, правила tone of voice, запреты и обязательные формулировки"
            />
          </div>
        </div>
      </>
    );
  }

  function renderAutopublish() {
    return (
      <>
        <div className="top">
          <div>
            <h2>Автопубликация</h2>
            <p>Матрица разрешений WB / Ozon / Яндекс Маркет</p>
          </div>
          <div className="actions">
            <button onClick={loadSettings}>Загрузить настройки</button>
            <button className="primary" onClick={runAutopublish}>
              Запустить сейчас
            </button>
          </div>
        </div>

        <div className="matrixGrid">
          {["WB", "OZON", "YM"].map((p) => (
            <div className="matrixCard" key={p}>
              <b>{p}</b>
              <label className="check">
                <input
                  type="checkbox"
                  checked={Boolean(settings.autopublish_matrix?.[p]?.reviews)}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      autopublish_matrix: {
                        ...(settings.autopublish_matrix || {}),
                        [p]: {
                          ...(settings.autopublish_matrix?.[p] || {}),
                          reviews: e.target.checked,
                        },
                      },
                    })
                  }
                />
                Отзывы
              </label>
              <label className="check">
                <input
                  type="checkbox"
                  checked={Boolean(settings.autopublish_matrix?.[p]?.questions)}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      autopublish_matrix: {
                        ...(settings.autopublish_matrix || {}),
                        [p]: {
                          ...(settings.autopublish_matrix?.[p] || {}),
                          questions: e.target.checked,
                        },
                      },
                    })
                  }
                />
                Вопросы
              </label>
            </div>
          ))}
        </div>

        <button className="primary" onClick={saveSettings}>Сохранить матрицу</button>
      </>
    );
  }

  function renderSync() {
    return (
      <>
        <div className="top">
          <div>
            <h2>Синхронизация</h2>
            <p>Загрузка отзывов, вопросов и архивов из маркетплейсов</p>
          </div>
        </div>

        <div className="settingsPanel">
          {["WB", "OZON", "YM"].map((p) => (
            <div className="settingCard" key={p}>
              <h3>{p}</h3>
              <div className="blockButtons">
                <button onClick={() => runSync(p)}>Запустить синхронизацию</button>
                <button onClick={p === "WB" ? loadReviews : loadQuestions}>
                  Обновить данные в интерфейсе
                </button>
              </div>
            </div>
          ))}
        </div>
      </>
    );
  }

  function renderContent() {
    if (section === "dashboard") return renderDashboard();
    if (section === "reviews") return renderList("reviews");
    if (section === "questions") return renderList("questions");
    if (section === "reports") return renderReports();
    if (section === "summary") return renderReports();
    if (section === "settings") return renderSettings();
    if (section === "autopublish") return renderAutopublish();
    if (section === "sync") return renderSync();
    return renderDashboard();
  }

  return (
    <div className="app">
      <aside>
        <h1>KARATOV<br />CX Hub</h1>

        <div className={`currentPlatform ${platform}`}>{platform}</div>

        {NAV.map(([id, title]) => (
          <button
            key={id}
            className={section === id ? "active" : ""}
            onClick={() => setSection(id)}
          >
            {title}
            {id === "reviews" && <span className="navCount">{metrics.reviews}</span>}
            {id === "questions" && <span className="navCount">{metrics.questions}</span>}
          </button>
        ))}

        <div className="syncMini">
          Backend: {health?.status || "не проверен"}
          <br />
          {loading ? "Выполняется операция..." : "Готово"}
        </div>

        <div className="hint">
          Боевой интерфейс поверх текущего backend. Если какой-то endpoint еще не подключен — интерфейс покажет ошибку без падения приложения.
        </div>
      </aside>

      <main>
        {message && <div className="message">{message}</div>}
        {renderContent()}
      </main>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);