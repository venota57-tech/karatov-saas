from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Body
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.services.stable_marketplace_os import StableMarketplaceOS, loads

router = APIRouter(prefix="/stable-os", tags=["stable-os"])

_STATE: dict[str, Any] = {"customer_ops": {"running": False}, "operations": {"running": False}}
_TASKS: dict[str, asyncio.Task] = {}


def iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _run(kind: str, platform: str, run_id: str):
    db = SessionLocal()
    try:
        osvc = StableMarketplaceOS(db)
        result = await (osvc.sync_customer_ops(platform) if kind == "customer_ops" else osvc.sync_operations(platform))
        _STATE[kind].update({"running": False, "run_id": run_id, "finished_at": iso(), "last_success_at": iso(), "last_error": None, "last_result": result})
    except Exception as exc:
        _STATE[kind].update({"running": False, "run_id": run_id, "finished_at": iso(), "last_error": str(exc)})
    finally:
        db.close()


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def stable_os_page():
    return HTMLResponse("""<!doctype html><html lang="ru"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/><title>KARATOV Stable Marketplace OS</title>
<style>
body{font-family:Inter,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;background:#f8f4ee;color:#30261f;margin:0}header{position:sticky;top:0;background:#fff8;backdrop-filter:blur(14px);border-bottom:1px solid #eadfce;z-index:10;padding:18px 26px;display:flex;justify-content:space-between;gap:16px;align-items:center}main{padding:24px;max-width:1500px;margin:auto}.grid{display:grid;grid-template-columns:1.1fr 1fr;gap:18px}.panel{background:#fff;border:1px solid #eadfce;border-radius:22px;padding:18px;box-shadow:0 12px 34px #0000000a}.toolbar{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.btn,button,select,input{border:1px solid #d7c6ad;background:#fff;border-radius:12px;padding:10px 12px}button.primary{background:#30261f;color:white;border-color:#30261f}.cards{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:12px;margin:18px 0}.card{background:#fff;border:1px solid #eadfce;border-radius:18px;padding:14px}.card b{font-size:28px}.list{display:flex;flex-direction:column;gap:10px;max-height:620px;overflow:auto}.item{border:1px solid #eadfce;border-radius:16px;padding:12px;background:#fff;cursor:pointer}.item:hover{box-shadow:0 8px 24px #0001}.meta{opacity:.65;font-size:13px}.badge{display:inline-block;border-radius:999px;background:#f0e6d6;padding:4px 8px;margin-right:6px;font-size:12px}.media{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}.media img{width:86px;height:86px;object-fit:cover;border-radius:12px;border:1px solid #ddd}.sticky{position:sticky;top:88px;align-self:start}.row{display:grid;grid-template-columns:120px 90px 140px 1fr 160px;gap:8px;border-bottom:1px solid #f0e6d6;padding:8px 0}.log{font-family:ui-monospace,monospace;font-size:12px;white-space:pre-wrap;background:#2a221d;color:#fff;border-radius:14px;padding:12px;max-height:420px;overflow:auto}.tabs button.active{background:#30261f;color:#fff}textarea{width:100%;min-height:80px;border-radius:14px;border:1px solid #d7c6ad;padding:10px}
</style></head><body><header><div><h2 style="margin:0">KARATOV Stable Marketplace OS v4</h2><div class="meta">Стабильный контур: sync jobs → raw events → normalized data → media layer → last-good UI</div></div><div class="toolbar"><select id="platform"><option>ALL</option><option>OZON</option><option>WB</option></select><button class="primary" onclick="refreshAll()">Обновить</button><button onclick="startSync('customer_ops')">Sync Customer Ops</button><button onclick="startSync('operations')">Sync Operations</button></div></header><main><div id="msg" class="meta"></div><div class="cards"><div class="card">Коммуникации<br><b id="c1">—</b></div><div class="card">Операции<br><b id="c2">—</b></div><div class="card">Ошибки API<br><b id="c3">—</b></div><div class="card">Sync<br><b id="c4">—</b></div></div><div class="grid"><section class="panel"><h3>Коммуникации + медиа</h3><div class="tabs toolbar"><button class="active" onclick="tab='communications';paint()">Коммуникации</button><button onclick="tab='operations';paint()">Операции</button><button onclick="tab='diagnostics';paint()">Диагностика API</button><button onclick="tab='scheduler';paint()">Scheduler</button></div><div id="list" class="list"></div></section><section class="panel sticky"><h3>Карточка</h3><div id="detail" class="meta">Выбери строку слева</div><hr><h4>Внутреннее вложение</h4><input type="file" id="file"/><button onclick="upload()">Прикрепить к выбранной карточке</button><div class="meta">Файл сохранится внутри CX Hub. В маркетплейс отправляется только там, где API явно поддерживает вложения.</div></section></div></main>
<script>
let data={communications:[],operations:[],diagnostics:[],scheduler:{}}, selected=null, tab='communications';
const $=id=>document.getElementById(id); const platform=()=>$('platform').value;
function dt(x){return x?new Date(x).toLocaleString('ru-RU'):'—'} function esc(x){return String(x??'').replace(/[&<>]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]))}
async function api(path, opts={}){const r=await fetch(path,opts); if(!r.ok) throw new Error(await r.text()); return r.json()}
function mediaHtml(arr=[]){return `<div class="media">${arr.map(m=>m.preview_url||m.url?`<a target="_blank" href="${esc(m.url||m.preview_url)}"><img src="${esc(m.preview_url||m.url)}"/></a>`:`<span class="badge">${esc(m.filename||m.media_type||'file')}</span>`).join('')}</div>`}
async function refreshAll(){ $('msg').textContent='Обновляю…'; try{const p=platform(); const [c,o,d,s]=await Promise.all([api(`/stable-os/api/communications?platform=${p}`),api(`/stable-os/api/operations?platform=${p}`),api(`/stable-os/api/diagnostics?platform=${p}`),api('/stable-os/api/scheduler')]); data={communications:c.items||[],operations:o.items||[],diagnostics:d.items||[],scheduler:s}; $('c1').textContent=data.communications.length; $('c2').textContent=data.operations.length; $('c3').textContent=data.diagnostics.filter(x=>x.status==='failed').length; $('c4').textContent=s.ok?'ok':'—'; paint(); $('msg').textContent='Обновлено. Ошибки API не обнуляют данные.'}catch(e){$('msg').textContent='Ошибка обновления: '+e.message+' — последние данные оставлены на экране.'}}
async function startSync(kind){$('msg').textContent='Запускаю '+kind+'…'; try{const r=await api(`/stable-os/api/sync/start?kind=${kind}&platform=${platform()}`,{method:'POST'}); $('msg').textContent=r.already_running?'Уже выполняется':'Запущено в фоне'; setTimeout(refreshAll,8000); setTimeout(refreshAll,24000)}catch(e){$('msg').textContent='Ошибка запуска: '+e.message}}
function paint(){const list=$('list'); document.querySelectorAll('.tabs button').forEach(b=>b.classList.remove('active')); if(tab==='communications'){list.innerHTML=data.communications.map((x,i)=>`<div class="item" onclick="selectItem('communications',${i})"><span class="badge">${esc(x.platform)}</span><span class="badge">${esc(x.entity_type)}</span><b>${esc(x.client_name||x.product_name||x.sku||x.external_id||'карточка')}</b><div class="meta">${dt(x.created_at)} · ${esc(x.order_number||'заказ не передан API')}</div><p>${esc(x.text||'')}</p>${mediaHtml(x.media||[])}</div>`).join('')||'Нет данных';}
else if(tab==='operations'){list.innerHTML=data.operations.map((x,i)=>`<div class="item" onclick="selectItem('operations',${i})"><span class="badge">${esc(x.platform)}</span><span class="badge">${esc(x.operation_type)}</span><b>${esc(x.document_number||x.external_id)}</b><div class="meta">${dt(x.occurred_at||x.created_at)} · ${esc(x.marketplace_status||'')}</div><p>${esc(x.product_name||x.sku||x.reason||'')}</p></div>`).join('')||'Нет операций';}
else if(tab==='diagnostics'){list.innerHTML=`<div class="log">${esc(data.diagnostics.map(x=>`${dt(x.created_at)}\\t${x.platform}\\t${x.block}\\t${x.status}\\t${x.error||''}`).join('\\n'))}</div>`}
else {list.innerHTML=`<div class="log">${esc(JSON.stringify(data.scheduler,null,2))}</div>`}}
function selectItem(kind,i){selected={kind,item:data[kind][i]}; const x=selected.item; $('detail').innerHTML=`<div><span class="badge">${esc(x.platform)}</span><span class="badge">${esc(x.entity_type||x.operation_type)}</span></div><h3>${esc(x.client_name||x.product_name||x.document_number||x.external_id||'карточка')}</h3><p class="meta">${dt(x.created_at||x.occurred_at)} · ${esc(x.order_number||x.posting_number||'')}</p><p>${esc(x.text||x.reason||x.marketplace_status||'')}</p>${mediaHtml(x.media||[])}<pre class="log">${esc(JSON.stringify(x.raw||x,null,2).slice(0,3000))}</pre>`}
async function upload(){if(!selected){alert('Сначала выбери карточку');return} const f=$('file').files[0]; if(!f){alert('Выбери файл');return} const b64=await new Promise(res=>{const r=new FileReader();r.onload=()=>res(r.result);r.readAsDataURL(f)}); const entity=selected.item.entity_type||selected.item.operation_type||selected.kind; const id=selected.item.external_id||selected.item.id; const r=await api('/stable-os/api/media/upload-json',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({entity_type:entity,entity_id:id,platform:selected.item.platform,filename:f.name,mime_type:f.type,content_base64:b64})}); $('msg').textContent=r.message; await refreshAll()}
refreshAll();
</script></body></html>""")


@router.get("/api/status")
def status():
    return {"ok": True, "state": _STATE}


@router.post("/api/sync/start")
async def sync_start(kind: str, platform: str = "ALL"):
    kind = (kind or "").lower()
    if kind not in {"customer_ops", "operations"}:
        return {"ok": False, "error": "kind must be customer_ops or operations"}
    task = _TASKS.get(kind)
    if task and not task.done():
        return {"ok": True, "already_running": True, "status": _STATE[kind]}
    rid = str(uuid4())
    _STATE[kind] = {"running": True, "run_id": rid, "platform": platform.upper(), "started_at": iso(), "last_error": None}
    _TASKS[kind] = asyncio.create_task(_run(kind, platform.upper(), rid))
    return {"ok": True, "started": True, "job_id": rid}


@router.get("/api/communications")

def communications(platform: str = "ALL", limit: int = 120, db: Session = Depends(get_db)):
    try:
        return StableMarketplaceOS(db).communications(platform=platform, limit=limit)
    except Exception as exc:
        try:
            StableMarketplaceOS(db).raw((platform or "ALL").upper(), "stable_os_api_communications", "failed", {}, str(exc))
        except Exception:
            pass
        return {"ok": False, "platform": (platform or "ALL").upper(), "items": [], "error": str(exc)}

@router.get("/api/operations")

def operations(platform: str = "ALL", limit: int = 200, db: Session = Depends(get_db)):
    try:
        return StableMarketplaceOS(db).operations(platform=platform, limit=limit)
    except Exception as exc:
        try:
            StableMarketplaceOS(db).raw((platform or "ALL").upper(), "stable_os_api_operations", "failed", {}, str(exc))
        except Exception:
            pass
        return {"ok": False, "platform": (platform or "ALL").upper(), "items": [], "error": str(exc)}

@router.get("/api/diagnostics")

def diagnostics(platform: str = "ALL", limit: int = 120, db: Session = Depends(get_db)):
    try:
        return StableMarketplaceOS(db).diagnostics(platform=platform, limit=limit)
    except Exception as exc:
        return {"ok": False, "platform": (platform or "ALL").upper(), "items": [], "error": str(exc)}

@router.post("/api/media/upload-json")
def media_upload_json(payload: dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    return StableMarketplaceOS(db).upload_media_json(payload)


@router.get("/api/media/{media_id}/download")
def media_download(media_id: int, db: Session = Depends(get_db)):
    StableMarketplaceOS(db).ensure_schema()
    row = db.execute(text("SELECT filename, mime_type, content_base64 FROM communication_media WHERE id=:id"), {"id": media_id}).mappings().first()
    if not row or not row["content_base64"]:
        return JSONResponse({"ok": False, "error": "media not found"}, status_code=404)
    import base64
    raw = base64.b64decode(str(row["content_base64"]).split(",", 1)[-1])
    return Response(raw, media_type=row["mime_type"] or "application/octet-stream", headers={"Content-Disposition": f"attachment; filename={row['filename'] or 'attachment'}"})


@router.get("/api/scheduler")

def scheduler():
    try:
        root = Path.cwd().parent if Path.cwd().name == "backend" else Path.cwd()
        wf_dir = root / ".github" / "workflows"
        files = sorted(wf_dir.glob("*.yml")) if wf_dir.exists() else []
        workflows = []
        for f in files:
            txt = f.read_text(encoding="utf-8", errors="ignore")
            workflows.append({"file": f.name, "has_schedule": "schedule:" in txt, "has_workflow_dispatch": "workflow_dispatch:" in txt, "cron": [line.strip() for line in txt.splitlines() if "cron:" in line][:5]})
        return {"ok": True, "workflow_dir": str(wf_dir), "workflows": workflows}
    except Exception as exc:
        return {"ok": False, "workflow_dir": None, "workflows": [], "error": str(exc)}
