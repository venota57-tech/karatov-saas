from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..services.autopublish_service import get_autopublish_rules, save_autopublish_rules, autopublish_once

router = APIRouter(prefix="/autopublish-settings", tags=["autopublish-settings"])


@router.get("", response_class=HTMLResponse)
def page():
    return HTMLResponse("""
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>KARATOV — настройки автопубликации</title>
  <style>
    body { font-family: Arial, sans-serif; max-width: 980px; margin: 32px auto; padding: 0 20px; color: #222; }
    h1 { margin-bottom: 8px; }
    .muted { color: #666; margin-bottom: 24px; }
    .card { border: 1px solid #ddd; border-radius: 14px; padding: 18px; margin: 16px 0; box-shadow: 0 2px 10px rgba(0,0,0,.04); }
    label { display: block; margin: 10px 0; }
    input[type="number"] { width: 120px; padding: 8px; }
    button { padding: 10px 16px; border-radius: 10px; border: 0; cursor: pointer; margin-right: 8px; }
    .primary { background: #111; color: #fff; }
    .secondary { background: #eee; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    textarea { width: 100%; min-height: 90px; padding: 8px; }
    pre { background: #f6f6f6; padding: 12px; border-radius: 10px; overflow: auto; }
  </style>
</head>
<body>
  <h1>Настройки автопубликации</h1>
  <div class="muted">Меняй правила здесь, без правки кода и базы. Настройки применяются в следующий цикл автопубликации.</div>

  <div class="card">
    <label><input id="enabled" type="checkbox"> Включить автопубликацию отзывов</label>
    <label><input id="wb" type="checkbox"> WB</label>
    <label><input id="ozon" type="checkbox"> Ozon</label>
  </div>

  <div class="grid">
    <div class="card">
      <h3>Какие отзывы публиковать</h3>
      <label>Минимальная оценка:
        <input id="minRating" type="number" min="1" max="5">
      </label>
      <label><input id="requireAi" type="checkbox"> Публиковать только если AI разрешил автопубликацию</label>
      <label><input id="localTemplates" type="checkbox"> Если нет AI-ответа, использовать безопасный локальный шаблон</label>
    </div>

    <div class="card">
      <h3>Лимиты</h3>
      <label>Максимум публикаций за проход:
        <input id="maxPerRun" type="number" min="1" max="100">
      </label>
      <label>Интервал автопубликации, секунд:
        <input id="interval" type="number" min="60">
      </label>
      <label>Пауза между публикациями, секунд:
        <input id="pause" type="number" min="5">
      </label>
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <h3>Исключенные категории</h3>
      <p class="muted">Одна категория на строку. Такие отзывы не будут улетать автоматически.</p>
      <textarea id="categories"></textarea>
    </div>

    <div class="card">
      <h3>Исключенные риски</h3>
      <p class="muted">Обычно high. Можно добавить medium, если хочешь осторожнее.</p>
      <textarea id="risks"></textarea>
    </div>
  </div>

  <div class="card">
    <button class="primary" onclick="save()">Сохранить настройки</button>
    <button class="secondary" onclick="runOnce()">Запустить один проход сейчас</button>
    <button class="secondary" onclick="load()">Обновить</button>
  </div>

  <pre id="result"></pre>

<script>
async function load() {
  const res = await fetch('/autopublish-settings/api');
  const data = await res.json();

  enabled.checked = !!data.autopublish_reviews_enabled;
  wb.checked = (data.autopublish_platforms || []).includes('WB');
  ozon.checked = (data.autopublish_platforms || []).includes('OZON');
  minRating.value = data.autopublish_min_rating ?? 5;
  requireAi.checked = !!data.autopublish_require_ai_can_autopublish;
  localTemplates.checked = !!data.autopublish_local_templates;
  maxPerRun.value = data.autopublish_max_per_run ?? 10;
  interval.value = data.autopublish_interval_seconds ?? 900;
  pause.value = data.autopublish_pause_between_items_seconds ?? 8;
  categories.value = (data.autopublish_excluded_categories || []).join('\\n');
  risks.value = (data.autopublish_excluded_risk_levels || []).join('\\n');

  result.textContent = JSON.stringify(data, null, 2);
}

async function save() {
  const platforms = [];
  if (wb.checked) platforms.push('WB');
  if (ozon.checked) platforms.push('OZON');

  const payload = {
    real_autopublish_enabled: true,
    autopublish_reviews_enabled: enabled.checked,
    autopublish_platforms: platforms,
    autopublish_min_rating: Number(minRating.value),
    autopublish_require_ai_can_autopublish: requireAi.checked,
    autopublish_local_templates: localTemplates.checked,
    autopublish_max_per_run: Number(maxPerRun.value),
    autopublish_interval_seconds: Number(interval.value),
    autopublish_pause_between_items_seconds: Number(pause.value),
    autopublish_excluded_categories: categories.value.split('\\n').map(x => x.trim()).filter(Boolean),
    autopublish_excluded_risk_levels: risks.value.split('\\n').map(x => x.trim()).filter(Boolean)
  };

  const res = await fetch('/autopublish-settings/api', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });

  const data = await res.json();
  result.textContent = JSON.stringify(data, null, 2);
  alert('Настройки сохранены');
}

async function runOnce() {
  const res = await fetch('/autopublish-settings/run-once', {method: 'POST'});
  const data = await res.json();
  result.textContent = JSON.stringify(data, null, 2);
}

load();
</script>
</body>
</html>
""")


@router.get("/api")
def get_rules(db: Session = Depends(get_db)):
    return get_autopublish_rules(db)


@router.post("/api")
def save_rules(payload: dict, db: Session = Depends(get_db)):
    return save_autopublish_rules(db, payload)


@router.post("/run-once")
async def run_once():
    return await autopublish_once()
