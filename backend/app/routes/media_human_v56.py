from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.media_human_v56 import HumanCommsV56

router = APIRouter(tags=["media-human-v56"])


@router.get("/ops-v56", response_class=HTMLResponse)
def ops_v56_page():
    return HTMLResponse("""
<!doctype html><html lang="ru"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>KARATOV Ops v5.6</title>
<style>
body{margin:0;background:#f7f3ee;font:15px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#2b2420}header{padding:22px 28px;border-bottom:1px solid #eadfd3;background:#fffaf3;display:flex;gap:18px;align-items:center;justify-content:space-between}h1{margin:0;font-size:28px}.sub,.meta,.tech{color:#786e68}.toolbar{display:flex;gap:10px;align-items:center}.toolbar select,.toolbar button{border:1px solid #eadfd3;background:white;border-radius:12px;padding:10px 13px;font-weight:700}button.primary{background:#2f251f;color:white}main{padding:22px 28px;display:grid;grid-template-columns:minmax(360px,520px) 1fr;gap:18px}.panel{background:white;border:1px solid #eadfd3;border-radius:22px;box-shadow:0 10px 30px #2b1d1010;padding:18px}.tabs{display:flex;gap:10px;margin-bottom:14px}.tab{border:1px solid #eadfd3;border-radius:14px;padding:10px 14px;background:#f4eee7;cursor:pointer;font-weight:800}.tab.active{background:#b99155;color:white}.item{border-bottom:1px solid #eadfd3;padding:13px 4px;cursor:pointer}.item:hover{background:#fff8ef}.title{font-weight:900}.meta{font-size:13px;margin-top:3px}.tech{font-size:11px;margin-top:2px}.badge{display:inline-flex;align-items:center;border:1px solid #eadfd3;border-radius:999px;padding:2px 8px;font-size:12px;margin-right:5px;background:#fbf7f0}a{color:#7b5523;font-weight:800;text-decoration:none}.copy{font-size:12px;border:0;background:#eee0cd;border-radius:8px;padding:4px 7px;margin-left:6px;cursor:pointer}.msg{margin:10px 0;padding:12px 14px;border-radius:16px;max-width:78%;background:#f5f1eb}.msg.seller{margin-left:auto;background:#fff0cf}.msg .who{font-weight:900;margin-bottom:4px}.msg .time{font-size:12px;color:#786e68;margin-top:5px}.media{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}.media img{width:110px;height:110px;object-fit:cover;border-radius:12px;border:1px solid #eadfd3}.media video{width:180px;max-height:160px;border-radius:12px}.file{border:1px solid #eadfd3;border-radius:10px;padding:8px;background:white}.kv{display:grid;grid-template-columns:150px 1fr;gap:8px;margin:10px 0}.empty{color:#786e68;padding:30px;text-align:center}
</style></head><body>
<header><div><h1>KARATOV Ops v5.6</h1><div class="sub">Человеческие чаты, отзывы, вопросы и медиа из ЛК маркетплейсов</div></div><div class="toolbar"><select id="platform"><option>ALL</option><option>WB</option><option>OZON</option></select><button onclick="syncMedia()">Починить текст и медиа из raw</button><button class="primary" onclick="loadList()">Обновить</button></div></header>
<main><section class="panel"><div class="tabs"><button class="tab active" data-tab="chats" onclick="setTab('chats')">Чаты</button><button class="tab" data-tab="review" onclick="setTab('review')">Отзывы</button><button class="tab" data-tab="question" onclick="setTab('question')">Вопросы</button></div><div id="list">Загрузка...</div></section><section class="panel" id="detail"><div class="empty">Выбери строку слева</div></section></main>
<script>
let tab='chats';const $=id=>document.getElementById(id);function esc(s){return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}function copyText(t){navigator.clipboard?.writeText(String(t||''));}
function mediaHtml(items){if(!items||!items.length)return '';return `<div class="media">${items.map(m=>{const url=m.preview_url||m.url;if(m.media_type==='image'&&url)return `<a href="${esc(m.url||url)}" target="_blank"><img src="${esc(url)}"/></a>`;if(m.media_type==='video'&&url)return `<video src="${esc(url)}" controls></video>`;if(url)return `<a class="file" href="${esc(url)}" target="_blank">📎 ${esc(m.filename||m.media_type||'файл')}</a>`;return `<span class="file">📎 ${esc(m.filename||m.media_type||'медиа')} ${m.external_media_id?`<span class="tech">${esc(m.external_media_id)}</span>`:''}</span>`}).join('')}</div>`}
function productHtml(x){if(!x.product_name&&!x.sku)return '—';const sku=x.sku?`<span class="badge">арт. ${esc(x.sku)}</span>`:'';const name=x.product_url?`<a href="${esc(x.product_url)}" target="_blank">${esc(x.product_name||'товар')}</a>`:esc(x.product_name||'товар');return `${name} ${sku}`}
function orderHtml(x){if(!x.order_number)return '—';const num=x.order_url?`<a href="${esc(x.order_url)}" target="_blank">${esc(x.order_number)}</a>`:esc(x.order_number);return `${num}<button class="copy" onclick="copyText('${esc(x.order_number)}')">копировать</button><span class="tech">${esc(x.order_kind||'')}</span>`}
async function api(path){const r=await fetch(path);const t=await r.text();try{return JSON.parse(t)}catch(e){return{ok:false,error:t}}}
function setTab(t){tab=t;document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active',b.dataset.tab===t));$('detail').innerHTML='<div class="empty">Выбери строку слева</div>';loadList()}
async function syncMedia(){$('list').innerHTML='Сканирую raw и сохраняю медиа...';const d=await api('/ops-v56/api/media/sync-existing?limit=100000');$('list').innerHTML=`<pre>${esc(JSON.stringify(d,null,2))}</pre>`}
async function loadList(){const p=$('platform').value;$('list').innerHTML='Загрузка...';let data=tab==='chats'?await api(`/ops-v56/api/chats?platform=${p}&limit=5000`):await api(`/ops-v56/api/communications?entity_type=${tab}&platform=${p}&limit=20000`);if(!data.ok){$('list').innerHTML=`<div class="empty">${esc(data.error||'Ошибка')}</div>`;return}const items=data.items||[];window.currentItems=items;$('list').innerHTML=items.length?items.map((x,i)=>`<div class="item" onclick="openItem(${i})"><div class="title">${esc(x.title||x.client_name||x.product_name||x.text||'Без названия')}</div><div class="meta">${esc(x.platform||'')} · ${esc(x.last_message_at||x.created_at_marketplace||'')} ${x.media?.length?`· 📎 ${x.media.length}`:''}</div><div class="meta">${productHtml(x)}</div></div>`).join(''):'<div class="empty">Нет данных</div>'}
async function openItem(i){const x=window.currentItems[i];if(tab==='chats'){$('detail').innerHTML='<div class="empty">Загрузка истории...</div>';const data=await api(`/ops-v56/api/chats/${encodeURIComponent(x.platform)}/${encodeURIComponent(x.technical_chat_id)}/messages?limit=1000`);const chat=data.chat||x,msgs=data.items||[];$('detail').innerHTML=`<h2>${esc(chat.title||'Чат')}</h2><div class="kv"><b>Клиент</b><div>${esc(chat.client_name||'—')}</div><b>Товар</b><div>${productHtml(chat)}</div><b>Заказ</b><div>${orderHtml(chat)}</div><b>Площадка</b><div>${esc(chat.platform)}</div></div><details class="tech"><summary>технический ID</summary>${esc(chat.technical_chat_id||x.technical_chat_id)}</details>${mediaHtml(chat.media)}<hr/>${msgs.length?msgs.map(m=>`<div class="msg ${m.direction==='seller'?'seller':'customer'}"><div class="who">${m.direction==='seller'?'Продавец/оператор':'Клиент'}</div><div>${esc(m.text||'')}</div>${mediaHtml(m.media)}<div class="time">${esc(m.sent_at||'')}</div></div>`).join(''):'<div class="empty">Сообщений нет</div>'}`}else{$('detail').innerHTML=`<h2>${tab==='review'?'Отзыв':'Вопрос'}</h2><div class="kv"><b>Площадка</b><div>${esc(x.platform)}</div><b>Товар</b><div>${productHtml(x)}</div><b>Заказ</b><div>${orderHtml(x)}</div><b>Оценка</b><div>${esc(x.rating||'—')}</div><b>Дата</b><div>${esc(x.created_at_marketplace||'—')}</div></div><p>${esc(x.text||'')}</p>${x.answer_text?`<h3>Ответ продавца</h3><p>${esc(x.answer_text)}</p>`:''}${mediaHtml(x.media)}<details><summary>raw</summary><pre>${esc(JSON.stringify(x.raw,null,2))}</pre></details>`}}
loadList();
</script></body></html>
""")


@router.get("/ops-v56/api/chats")
def api_chats(platform: str = "ALL", limit: int = 5000, db: Session = Depends(get_db)):
    return HumanCommsV56(db).chats(platform=platform, limit=limit)


@router.get("/ops-v56/api/chats/{platform}/{external_chat_id}/messages")
def api_chat_messages(platform: str, external_chat_id: str, limit: int = 1000, db: Session = Depends(get_db)):
    return HumanCommsV56(db).chat_messages(platform=platform, external_chat_id=external_chat_id, limit=limit)


@router.get("/ops-v56/api/communications")
def api_communications(entity_type: str = "review", platform: str = "ALL", limit: int = 20000, db: Session = Depends(get_db)):
    return HumanCommsV56(db).communications(entity_type=entity_type, platform=platform, limit=limit)


@router.get("/ops-v56/api/media/sync-existing")
def api_sync_existing_media(limit: int = 100000, db: Session = Depends(get_db)):
    return HumanCommsV56(db).sync_existing_media(limit=limit)
