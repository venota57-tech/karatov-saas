from __future__ import annotations
import asyncio, hashlib, json, os
from datetime import datetime, timedelta, timezone
from typing import Any
import httpx
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from app.config import settings


def now(): return datetime.now(timezone.utc).replace(tzinfo=None)
def dumps(v):
    try: return json.dumps(v, ensure_ascii=False, default=str)
    except Exception: return json.dumps({"raw": str(v)}, ensure_ascii=False)
def loads(v):
    if v in (None, ""): return None
    if isinstance(v, (dict, list)): return v
    try: return json.loads(v)
    except Exception: return v
def sid(v): return hashlib.sha1(dumps(v).encode()).hexdigest()[:32]
def pdt(v):
    if v in (None, ""): return None
    if isinstance(v, datetime): return v.replace(tzinfo=None) if v.tzinfo else v
    if isinstance(v, (int, float)):
        try:
            x=float(v); x=x/1000 if x>100000000000 else x
            return datetime.fromtimestamp(x, tz=timezone.utc).replace(tzinfo=None)
        except Exception: return None
    s=str(v).strip()
    if s.isdigit(): return pdt(int(s))
    try: return datetime.fromisoformat(s.replace('Z','+00:00')).replace(tzinfo=None)
    except Exception: return None
def walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values(): yield from walk(v)
    elif isinstance(o, list):
        for v in o: yield from walk(v)
def get(o,*ks,default=None):
    if isinstance(o, dict):
        for k in ks:
            if k in o and o.get(k) not in (None, ""): return o.get(k)
        for v in o.values():
            r=get(v,*ks,default=None)
            if r not in (None, ""): return r
    elif isinstance(o, list):
        for v in o:
            r=get(v,*ks,default=None)
            if r not in (None, ""): return r
    return default
def arr(data, pref=None):
    pref=pref or []; data=loads(data) or data
    if isinstance(data, list): return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict): return []
    roots=[data]
    for k in ('data','result','items'):
        if isinstance(data.get(k),(list,dict)): roots.insert(0,data[k])
    for root in roots:
        if isinstance(root, list): return [x for x in root if isinstance(x, dict)]
        if not isinstance(root, dict): continue
        for k in pref+['feedbacks','questions','reviews','items','list','data','result','comments','messages','chats','events','returns','claims','acts','postings','documents']:
            v=root.get(k)
            if isinstance(v, list): return [x for x in v if isinstance(x, dict)]
            if isinstance(v, dict):
                a=arr(v,pref)
                if a: return a
    return []
def media(o):
    o=loads(o) or o; out=[]; seen=set()
    def add(kind,url=None,ext=None,name=None,raw=None):
        if not url and not ext: return
        key=f"{kind}:{url or ext}"
        if key in seen: return
        seen.add(key); out.append({'media_type':kind or 'file','url':url,'preview_url':url if kind=='image' else None,'external_media_id':ext,'filename':name,'raw_payload':raw,'source':'marketplace','visibility':'marketplace_visible'})
    for n in walk(o):
        for k,v in n.items():
            lk=str(k).lower()
            if isinstance(v,str) and v.startswith('http') and any(x in lk for x in ['photo','image','video','file','media','url','link','preview']):
                low=v.lower(); kind='image' if any(x in low for x in ['.jpg','.jpeg','.png','.webp','image','photo']) else ('video' if any(x in low for x in ['.mp4','.mov','video']) else 'file')
                add(kind,v,name=str(get(n,'filename','fileName','name','title',default='') or ''),raw=n)
            if isinstance(v,list) and any(x in lk for x in ['photo','image','video','file','media','attachments']):
                for it in v:
                    if isinstance(it,str) and it.startswith('http'): add('image' if any(x in it.lower() for x in ['.jpg','.png','.webp']) else 'file',it,raw={k:it})
                    if isinstance(it,dict):
                        u=get(it,'url','link','src','fileUrl','file_url','preview','previewUrl','miniSize','fullSize',default=None); ext=get(it,'downloadID','downloadId','id','fileId','mediaId',default=None)
                        ct=str(get(it,'type','contentType','mime','mimeType',default='') or '').lower(); kind='image' if 'image' in ct or 'photo' in ct else ('video' if 'video' in ct else 'file')
                        if u and any(x in str(u).lower() for x in ['.jpg','.jpeg','.png','.webp','image']): kind='image'
                        if u and any(x in str(u).lower() for x in ['.mp4','.mov','video']): kind='video'
                        add(kind,str(u) if u else None,str(ext) if ext else None,str(get(it,'filename','fileName','name','title',default='') or ''),it)
    return out[:50]
def body_text(o):
    if isinstance(o,str): return o
    if not isinstance(o,dict): return ''
    v=get(o,'text','reviewText','question','message','body','content','comment','description',default='')
    return body_text(v) if isinstance(v,dict) else str(v or '')
def ans(o):
    a=get(o,'answer','sellerAnswer','published_answer','response',default=None)
    if isinstance(a,str): return a, pdt(get(o,'answerDate','answeredAt',default=None)), a
    if isinstance(a,dict): return str(get(a,'text','body','message','comment',default='') or '') or None, pdt(get(a,'createDate','createdDate','createdAt','date','updatedAt',default=None)), a
    cs=arr(o,['comments']); sellers=[]
    for c in cs:
        if any(x in dumps(c).lower() for x in ['seller','merchant','official','company','продав']): sellers.append(c)
    if sellers:
        c=sellers[-1]; return body_text(c) or None, pdt(get(c,'created_at','createdAt','date','publishedAt',default=None)), c
    return None,None,None


def operation_semantics(platform, typ, raw):
    raw_text = dumps(raw).lower()
    label = typ
    meaning = "Операция маркетплейса. Нужна проверка raw-данных, потому что API не передал достаточно признаков."
    source = "unknown"
    confidence = "medium"
    if typ in ("return_request", "return"):
        label = "Возврат"
        if platform == "WB" and any(x in raw_text for x in ["goods-return", "goods_return", "analytics"]):
            meaning = "WB goods-return / аналитика возврата товара: логистическое движение товара обратно к продавцу/складу, часто связано с невыкупом, возвратом после выкупа или возвратной логистикой. Это не обязательно продавец-инициированный возврат."
            source = "WB goods-return analytics / returns"
        elif platform == "WB":
            meaning = "WB заявка/claim на возврат: обращение или возвратная заявка покупателя/маркетплейса. Не считать продавец-инициированным без дополнительного статуса в raw."
            source = "WB returns/claims"
        elif platform == "OZON":
            meaning = "Ozon заявка на возврат покупателя или возвратный кейс, если доступный returns API вернул данные. Не является FBS-отгрузкой."
            source = "Ozon returns API"
        confidence = "high"
    elif typ in ("posting", "shipment", "shipment_issue"):
        label = "Отгрузка"
        meaning = "Ozon FBS posting: заказ/отправление FBS из /v3/posting/fbs/list. Это не возврат; это рабочая отгрузка/заказ. В Operations Hub нужна для контроля статусов отгрузки и проблемных состояний."
        if typ == "shipment_issue":
            meaning = "Проблемная Ozon FBS отгрузка: posting из /v3/posting/fbs/list со статусом, требующим внимания. Это не возврат, а FBS-заказ/отправление."
        source = "Ozon /v3/posting/fbs/list"
        confidence = "high"
    elif typ == "act":
        label = "Акт"
        meaning = "Акт/документ маркетплейса. Для Ozon это FBS act list, для WB — документ из Documents API, классифицированный по названию/категории."
        source = "Ozon acts / WB Documents"
        confidence = "high"
    elif typ == "surplus":
        label = "Излишек"
        meaning = "Документ/операция, классифицированная как излишек по WB Documents API или признакам raw."
        source = "WB Documents"
    elif typ == "shortage":
        label = "Недостача"
        meaning = "Документ/операция, классифицированная как недостача по WB Documents API или признакам raw."
        source = "WB Documents"
    elif typ == "anonymized_item":
        label = "Обезличка"
        meaning = "Документ/операция, классифицированная как обезличенный товар по WB Documents API или признакам raw."
        source = "WB Documents"
    elif typ == "discrepancy":
        label = "Расхождение"
        meaning = "Документ/операция, классифицированная как расхождение по WB Documents API или признакам raw."
        source = "WB Documents"
    return {"operation_label": label, "operation_description": meaning, "operation_source": source, "operation_confidence": confidence}


def simple_topic(text, rating=None, raw=None):
    t = ((text or "") + " " + dumps(raw or "")).lower()
    if not t.strip() or t.strip() in {"без текста", "нет текста"}:
        return "Без текста"
    if any(x in t for x in ["размер", "мал", "больш", "не подош", "подош"]):
        return "Размер / посадка"
    if any(x in t for x in ["камень", "вставк", "фианит", "бриллиант", "изумруд", "сапфир", "топаз"]):
        return "Камни / вставки"
    if any(x in t for x in ["качество", "брак", "слом", "погнул", "царап", "потемн", "золото", "серебро"]):
        return "Качество изделия"
    if any(x in t for x in ["достав", "курьер", "срок", "получ", "пункт", "упаков"]):
        return "Доставка / упаковка"
    if any(x in t for x in ["возврат", "деньги", "отказ", "не выкуп"]):
        return "Возврат / отказ"
    try:
        if rating is not None and int(rating) >= 4:
            return "Позитив"
        if rating is not None and int(rating) <= 3:
            return "Негатив / претензия"
    except Exception:
        pass
    return "Прочее"


def fix_text(value):
    if not isinstance(value, str):
        return value
    if not any(marker in value for marker in ["Р", "С", "Ð", "Ñ"]):
        return value
    candidates = []
    for enc in ("cp1251", "latin1"):
        try:
            candidates.append(value.encode(enc, errors="ignore").decode("utf-8", errors="ignore"))
        except Exception:
            pass
    for repaired in candidates:
        if repaired and any(("а" <= ch.lower() <= "я") or ch.lower() == "ё" for ch in repaired):
            return repaired
    return value


def fix_tree(value):
    if isinstance(value, str):
        return fix_text(value)
    if isinstance(value, list):
        return [fix_tree(x) for x in value]
    if isinstance(value, dict):
        return {k: fix_tree(v) for k, v in value.items()}
    return value


def operation_semantics(platform, typ, raw):
    raw_text = dumps(raw).lower()
    label = typ or "operation"
    meaning = "Операция маркетплейса. Нужна проверка raw-данных, потому что API не передал достаточно признаков."
    source = "unknown"
    confidence = "medium"
    if typ in ("return_request", "return"):
        label = "Возврат"
        if platform == "WB" and any(x in raw_text for x in ["goods-return", "goods_return", "analytics"]):
            meaning = "WB goods-return / аналитика возврата товара: логистическое движение товара обратно к продавцу/складу, часто связано с невыкупом, возвратом после выкупа или возвратной логистикой. Это не обязательно продавец-инициированный возврат."
            source = "WB goods-return analytics / returns"
        elif platform == "WB":
            meaning = "WB заявка/claim на возврат: обращение или возвратная заявка покупателя/маркетплейса. Не считать продавец-инициированным без дополнительного статуса в raw."
            source = "WB returns/claims"
        elif platform == "OZON":
            meaning = "Ozon заявка на возврат покупателя или возвратный кейс, если доступный returns API вернул данные. Не является FBS-отгрузкой."
            source = "Ozon returns API"
        confidence = "high"
    elif typ in ("posting", "shipment", "shipment_issue"):
        label = "Отгрузка"
        meaning = "Ozon FBS posting: заказ/отправление FBS из /v3/posting/fbs/list. Это не возврат; это рабочая отгрузка/заказ. В Operations Hub нужна для контроля статусов отгрузки и проблемных состояний."
        if typ == "shipment_issue":
            meaning = "Проблемная Ozon FBS отгрузка: posting из /v3/posting/fbs/list со статусом, требующим внимания. Это не возврат, а FBS-заказ/отправление."
        source = "Ozon /v3/posting/fbs/list"
        confidence = "high"
    elif typ == "act":
        label = "Акт"
        meaning = "Акт/документ маркетплейса. Для Ozon это FBS act list, для WB — документ из Documents API, классифицированный по названию/категории."
        source = "Ozon acts / WB Documents"
        confidence = "high"
    elif typ == "surplus":
        label = "Излишек"; meaning = "Документ/операция, классифицированная как излишек по WB Documents API или признакам raw."; source = "WB Documents"
    elif typ == "shortage":
        label = "Недостача"; meaning = "Документ/операция, классифицированная как недостача по WB Documents API или признакам raw."; source = "WB Documents"
    elif typ == "anonymized_item":
        label = "Обезличка"; meaning = "Документ/операция, классифицированная как обезличенный товар по WB Documents API или признакам raw."; source = "WB Documents"
    elif typ == "discrepancy":
        label = "Расхождение"; meaning = "Документ/операция, классифицированная как расхождение по WB Documents API или признакам raw."; source = "WB Documents"
    return fix_tree({"operation_label": label, "operation_description": meaning, "operation_source": source, "operation_confidence": confidence})


def simple_topic(text, rating=None, raw=None):
    t = ((text or "") + " " + dumps(raw or "")).lower()
    if not t.strip() or t.strip() in {"без текста", "нет текста"}:
        return "Без текста"
    if any(x in t for x in ["размер", "мал", "больш", "не подош", "подош"]):
        return "Размер / посадка"
    if any(x in t for x in ["камень", "вставк", "фианит", "бриллиант", "изумруд", "сапфир", "топаз"]):
        return "Камни / вставки"
    if any(x in t for x in ["качество", "брак", "слом", "погнул", "царап", "потемн", "золото", "серебро"]):
        return "Качество изделия"
    if any(x in t for x in ["достав", "курьер", "срок", "получ", "пункт", "упаков"]):
        return "Доставка / упаковка"
    if any(x in t for x in ["возврат", "деньги", "отказ", "не выкуп"]):
        return "Возврат / отказ"
    try:
        if rating is not None and int(rating) >= 4:
            return "Позитив"
        if rating is not None and int(rating) <= 3:
            return "Негатив / претензия"
    except Exception:
        pass
    return "Прочее"


def first_product(raw):
    raw = loads(raw) or raw or {}
    products = raw.get("products") if isinstance(raw, dict) else None
    if isinstance(products, list) and products and isinstance(products[0], dict):
        return products[0]
    product = get(raw, "product", "productDetails", "goodCard", default={}) if isinstance(raw, dict) else {}
    return product if isinstance(product, dict) else {}

class RecoveryV5:
    def __init__(self, db: Session):
        self.db=db; self.timeout=httpx.Timeout(float(os.getenv('RECOVERY_REQUEST_TIMEOUT','24')), connect=8)
        self.hot_pages=int(os.getenv('RECOVERY_HOT_PAGES','3')); self.deep_pages=int(os.getenv('RECOVERY_DEEP_PAGES','12'))
        self.take=int(os.getenv('RECOVERY_TAKE','200')); self.chat_limit=int(os.getenv('RECOVERY_CHAT_LIMIT','250')); self.sleep=float(os.getenv('RECOVERY_SLEEP_SECONDS','0.45'))
    def tables(self):
        try: return set(inspect(self.db.bind).get_table_names())
        except Exception: self.db.rollback(); return set()
    def cols(self,t):
        try: return {c['name'] for c in inspect(self.db.bind).get_columns(t)}
        except Exception: self.db.rollback(); return set()
    def ensure(self):
        try: self.db.rollback()
        except Exception: pass
        idt='SERIAL PRIMARY KEY' if self.db.bind.dialect.name=='postgresql' else 'INTEGER PRIMARY KEY AUTOINCREMENT'
        bt='BOOLEAN DEFAULT FALSE' if self.db.bind.dialect.name=='postgresql' else 'BOOLEAN DEFAULT 0'
        ddls=[
        f"""CREATE TABLE IF NOT EXISTS marketplace_raw_events(id {idt}, platform VARCHAR(32), block VARCHAR(128), external_id VARCHAR(256), status VARCHAR(64), error TEXT, raw TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
        f"""CREATE TABLE IF NOT EXISTS recovery_communications(id {idt}, entity_type VARCHAR(64), platform VARCHAR(32), external_id VARCHAR(256), source_block VARCHAR(128), status VARCHAR(128), product_name TEXT, sku VARCHAR(128), order_number VARCHAR(256), client_name VARCHAR(256), rating INTEGER, text TEXT, answer_text TEXT, created_at_marketplace TIMESTAMP, answered_at_marketplace TIMESTAMP, has_media {bt}, raw TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
        f"""CREATE TABLE IF NOT EXISTS communication_media(id {idt}, entity_type VARCHAR(64), entity_id VARCHAR(256), platform VARCHAR(32), external_media_id VARCHAR(256), media_type VARCHAR(32), url TEXT, preview_url TEXT, filename TEXT, mime_type VARCHAR(128), size_bytes INTEGER, source VARCHAR(64), visibility VARCHAR(64), send_status VARCHAR(64), content_base64 TEXT, raw_payload TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
        f"""CREATE TABLE IF NOT EXISTS response_sla_metrics(id {idt}, entity_type VARCHAR(64), platform VARCHAR(32), external_id VARCHAR(256), source_block VARCHAR(128), created_at_marketplace TIMESTAMP, answered_at_marketplace TIMESTAMP, response_minutes INTEGER, sla_status VARCHAR(64), answer_source VARCHAR(64), raw TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
        f"""CREATE TABLE IF NOT EXISTS buyer_chats(id {idt}, platform VARCHAR(32), external_chat_id VARCHAR(256), chat_type VARCHAR(64), marketplace_status VARCHAR(128), internal_status VARCHAR(64) DEFAULT 'new', unread_count INTEGER DEFAULT 0, needs_response {bt}, buyer_name VARCHAR(256), buyer_id VARCHAR(128), order_number VARCHAR(256), posting_number VARCHAR(256), sku VARCHAR(128), product_name TEXT, product_url TEXT, product_image TEXT, assigned_to VARCHAR(128), operator_comment TEXT, reply_sign TEXT, first_customer_message_at TIMESTAMP, first_seller_response_at TIMESTAMP, last_message_at TIMESTAMP, response_minutes INTEGER, response_sla_status VARCHAR(64), raw TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
        f"""CREATE TABLE IF NOT EXISTS buyer_chat_messages(id {idt}, platform VARCHAR(32), external_chat_id VARCHAR(256), external_message_id VARCHAR(256), direction VARCHAR(32), author_name VARCHAR(256), text TEXT, message_type VARCHAR(64), sent_at TIMESTAMP, media TEXT, raw TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
        f"""CREATE TABLE IF NOT EXISTS buyer_returns(id {idt}, platform VARCHAR(32), external_return_id VARCHAR(256), order_id VARCHAR(256), posting_number VARCHAR(256), sku VARCHAR(128), product_name TEXT, product_url TEXT, reason TEXT, marketplace_status VARCHAR(128), internal_status VARCHAR(64) DEFAULT 'new', assigned_to VARCHAR(128), operator_comment TEXT, amount VARCHAR(64), quantity INTEGER, created_at_marketplace TIMESTAMP, updated_at_marketplace TIMESTAMP, raw TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
        f"""CREATE TABLE IF NOT EXISTS marketplace_operations(id {idt}, platform VARCHAR(32), operation_type VARCHAR(64), external_id VARCHAR(256), document_number VARCHAR(256), sku VARCHAR(128), product_name TEXT, warehouse TEXT, amount VARCHAR(64), quantity INTEGER, reason TEXT, status VARCHAR(64), marketplace_status VARCHAR(128), cx_workflow_status VARCHAR(64), responsible VARCHAR(128), comment TEXT, raw TEXT, occurred_at TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
        ]
        for d in ddls:
            try: self.db.execute(text(d)); self.db.commit()
            except Exception as e: self.db.rollback(); print('[recovery-v5] ddl failed',e)
        for idx in [
            'CREATE INDEX IF NOT EXISTS ix_raw_v5 ON marketplace_raw_events(platform, block, created_at)',
            'CREATE UNIQUE INDEX IF NOT EXISTS uq_rec_comm_v5 ON recovery_communications(entity_type, platform, external_id)',
            'CREATE UNIQUE INDEX IF NOT EXISTS uq_media_v5 ON communication_media(entity_type, entity_id, platform, external_media_id)',
            'CREATE UNIQUE INDEX IF NOT EXISTS uq_sla_v5 ON response_sla_metrics(entity_type, platform, external_id)',
            'CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_v5 ON buyer_chats(platform, external_chat_id)',
            'CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_msg_v5 ON buyer_chat_messages(platform, external_message_id)',
            'CREATE UNIQUE INDEX IF NOT EXISTS uq_return_v5 ON buyer_returns(platform, external_return_id)',
            'CREATE UNIQUE INDEX IF NOT EXISTS uq_ops_v5 ON marketplace_operations(platform, operation_type, external_id)']:
            try: self.db.execute(text(idx)); self.db.commit()
            except Exception as e: self.db.rollback(); print('[recovery-v5] index failed',e)
    def raw(self, platform, block, status, raw, error=None, external_id=None):
        try:
            self.db.execute(text('INSERT INTO marketplace_raw_events(platform,block,external_id,status,error,raw,created_at,updated_at) VALUES (:p,:b,:e,:s,:er,:r,:c,:u)'), {'p':platform,'b':block,'e':external_id,'s':status,'er':error,'r':dumps(raw),'c':now(),'u':now()}); self.db.commit()
        except Exception as e: self.db.rollback(); print('[recovery-v5] raw failed',e)
    async def req(self, platform, block, method, url, headers, params=None, body=None, log_ok=True):
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c: r=await c.request(method,url,headers=headers,params=params,json=body)
            try: data=r.json()
            except Exception: data={'text':r.text}
            if r.status_code>=400:
                er=f'HTTP {r.status_code}: {str(data)[:900]}'; self.raw(platform,block,'failed',{'url':url,'params':params,'body':body,'response':data},er); return False,data,er
            if log_ok: self.raw(platform,block,'success',{'url':url,'params':params,'body':body,'response':data})
            return True,data,None
        except Exception as e:
            er=str(e); self.raw(platform,block,'failed',{'url':url,'params':params,'body':body},er); return False,None,er
    def up_media(self, entity_type, entity_id, platform, medias):
        for m in medias or []:
            ext=str(m.get('external_media_id') or m.get('url') or sid(m)); vals={'t':entity_type,'id':str(entity_id),'p':platform,'ext':ext,'mt':m.get('media_type') or 'file','url':m.get('url'),'pr':m.get('preview_url'),'fn':m.get('filename'),'raw':dumps(m.get('raw_payload') or m),'c':now(),'u':now()}
            try:
                ex=self.db.execute(text('SELECT id FROM communication_media WHERE entity_type=:t AND entity_id=:id AND platform=:p AND external_media_id=:ext'),vals).first()
                if ex: self.db.execute(text('UPDATE communication_media SET media_type=:mt,url=COALESCE(:url,url),preview_url=COALESCE(:pr,preview_url),filename=COALESCE(:fn,filename),raw_payload=:raw,updated_at=:u WHERE id=:rid'),{**vals,'rid':ex[0]})
                else: self.db.execute(text('INSERT INTO communication_media(entity_type,entity_id,platform,external_media_id,media_type,url,preview_url,filename,source,visibility,send_status,raw_payload,created_at,updated_at) VALUES (:t,:id,:p,:ext,:mt,:url,:pr,:fn,\'marketplace\',\'marketplace_visible\',\'received\',:raw,:c,:u)'),vals)
                self.db.commit()
            except Exception as e: self.db.rollback(); self.raw(platform,'media_upsert','failed',m,str(e),ext)
    def sync_sla(self, etype, platform, ext, block, created, answered, raw):
        mins=None; status='unknown_time'
        if created and answered: mins=max(0,int((answered-created).total_seconds()//60)); status='in_sla' if mins<=60 else 'late'
        elif created: status='unanswered'
        vals={'t':etype,'p':platform,'e':ext,'b':block,'cr':created,'an':answered,'m':mins,'s':status,'raw':dumps(raw),'c':now(),'u':now()}
        try:
            ex=self.db.execute(text('SELECT id FROM response_sla_metrics WHERE entity_type=:t AND platform=:p AND external_id=:e'),vals).first()
            if ex: self.db.execute(text('UPDATE response_sla_metrics SET source_block=:b,created_at_marketplace=COALESCE(:cr,created_at_marketplace),answered_at_marketplace=COALESCE(:an,answered_at_marketplace),response_minutes=:m,sla_status=:s,raw=:raw,updated_at=:u WHERE id=:id'),{**vals,'id':ex[0]})
            else: self.db.execute(text('INSERT INTO response_sla_metrics(entity_type,platform,external_id,source_block,created_at_marketplace,answered_at_marketplace,response_minutes,sla_status,answer_source,raw,created_at,updated_at) VALUES (:t,:p,:e,:b,:cr,:an,:m,:s,\'marketplace\',:raw,:c,:u)'),vals)
            self.db.commit()
        except Exception as e: self.db.rollback(); self.raw(platform,'sla_upsert','failed',raw,str(e),ext)
    def mirror_existing(self, kind, platform, ext, raw, norm):
        names=['reviews','marketplace_reviews'] if kind=='review' else ['questions','marketplace_questions']
        t=next((x for x in names if x in self.tables()),None)
        if not t: return
        cols=self.cols(t); extcol=next((c for c in ['external_id','external_review_id','external_question_id'] if c in cols),None)
        if not extcol: return
        ans_txt,ans_dt,_=ans(raw)
        vals={'platform':platform,'external_id':ext,'external_review_id':ext,'external_question_id':ext,'text':norm.get('text'),'question':norm.get('text'),'rating':norm.get('rating'),'product_name':norm.get('product_name'),'sku':norm.get('sku'),'status':norm.get('status'),'state':norm.get('status'),'source':norm.get('source_block'),'answer_text':ans_txt,'response_text':ans_txt,'seller_answer':ans_txt,'published_answer':ans_txt,'created_at_marketplace':norm.get('created_at_marketplace'),'answered_at_marketplace':ans_dt,'raw':dumps(raw),'raw_payload':dumps(raw),'updated_at':now(),'created_at':now()}
        vals={k:v for k,v in vals.items() if k in cols and v is not None}
        try:
            ex=self.db.execute(text(f'SELECT id FROM {t} WHERE platform=:p AND {extcol}=:e LIMIT 1'),{'p':platform,'e':ext}).first()
            if ex:
                uv={k:v for k,v in vals.items() if k not in {'created_at','platform',extcol}}
                if uv: self.db.execute(text(f'UPDATE {t} SET '+', '.join([f'{k}=:{k}' for k in uv])+' WHERE id=:id'),{**uv,'id':ex[0]})
            else:
                vals.setdefault(extcol,ext); vals.setdefault('platform',platform)
                self.db.execute(text(f'INSERT INTO {t}('+','.join(vals)+') VALUES ('+','.join([f':{k}' for k in vals])+')'),vals)
            self.db.commit()
        except Exception as e: self.db.rollback(); self.raw(platform,f'{kind}_mirror_{t}','failed',raw,str(e),ext)
    def up_comm(self, kind, platform, ext, block, raw):
        a_txt,a_dt,_=ans(raw); product=get(raw,'productDetails','product','goodCard',default={}) or {}; created=pdt(get(raw,'createdDate','created_at','createdAt','published_at','publishedAt','date',default=None)); md=media(raw)
        norm={'entity_type':kind,'platform':platform,'external_id':ext,'source_block':block,'status':str(get(raw,'state','status','review_status',default='') or ''),'product_name':str(get(raw,'productName','product_name','name','title',default='') or get(product,'productName','name',default='') or ''),'sku':str(get(raw,'nmId','nmID','sku','offer_id','product_id',default='') or get(product,'nmId','nmID','sku','supplierArticle',default='') or ''),'order_number':str(get(raw,'orderNumber','posting_number','postingNumber','rid','srid',default='') or ''),'client_name':str(get(raw,'userName','clientName','buyerName','customer_name','author',default='') or ''),'rating':get(raw,'productValuation','rating','mark',default=None),'text':body_text(raw),'answer_text':a_txt,'created_at_marketplace':created,'answered_at_marketplace':a_dt,'has_media':bool(md),'raw':dumps(raw),'created_at':now(),'updated_at':now()}
        try:
            ex=self.db.execute(text('SELECT id FROM recovery_communications WHERE entity_type=:entity_type AND platform=:platform AND external_id=:external_id'),norm).first()
            if ex: self.db.execute(text('UPDATE recovery_communications SET source_block=:source_block,status=:status,product_name=:product_name,sku=:sku,order_number=:order_number,client_name=:client_name,rating=:rating,text=:text,answer_text=:answer_text,created_at_marketplace=COALESCE(:created_at_marketplace,created_at_marketplace),answered_at_marketplace=COALESCE(:answered_at_marketplace,answered_at_marketplace),has_media=:has_media,raw=:raw,updated_at=:updated_at WHERE id=:id'),{**norm,'id':ex[0]})
            else: self.db.execute(text('INSERT INTO recovery_communications(entity_type,platform,external_id,source_block,status,product_name,sku,order_number,client_name,rating,text,answer_text,created_at_marketplace,answered_at_marketplace,has_media,raw,created_at,updated_at) VALUES (:entity_type,:platform,:external_id,:source_block,:status,:product_name,:sku,:order_number,:client_name,:rating,:text,:answer_text,:created_at_marketplace,:answered_at_marketplace,:has_media,:raw,:created_at,:updated_at)'),norm)
            self.db.commit()
        except Exception as e: self.db.rollback(); self.raw(platform,f'{kind}_upsert','failed',raw,str(e),ext)
        self.up_media(kind,ext,platform,md); self.sync_sla(kind,platform,ext,block,created,a_dt,raw); self.mirror_existing(kind,platform,ext,raw,norm)
    async def wb_reviews_questions(self, deep=False):
        token=getattr(settings,'wb_api_token','') or getattr(settings,'wb_api_key',''); res={'platform':'WB','block':'reviews_questions','received':0,'errors':[]}
        if not token: self.raw('WB','reviews_questions','failed',{},'WB token missing'); res['errors'].append('WB token missing'); return res
        h={'Authorization':token}; pages=self.deep_pages if deep else self.hot_pages
        for answered in [False,True]:
            for page in range(pages):
                ok,d,e=await self.req('WB',f'feedbacks_{answered}','GET','https://feedbacks-api.wildberries.ru/api/v1/feedbacks',h,params={'isAnswered':str(answered).lower(),'take':min(self.take,500),'skip':page*min(self.take,500),'order':'dateDesc'})
                if not ok: res['errors'].append(e); break
                rows=arr(d,['feedbacks']); res['received']+=len(rows)
                if not rows: break
                for it in rows: self.up_comm('review','WB',str(get(it,'id','feedbackId',default='') or sid(it)),f'feedbacks_{answered}',it)
                await asyncio.sleep(self.sleep)
        for page in range(2 if not deep else pages):
            ok,d,e=await self.req('WB','feedbacks_archive','GET','https://feedbacks-api.wildberries.ru/api/v1/feedbacks/archive',h,params={'take':min(self.take,500),'skip':page*min(self.take,500),'order':'dateDesc'})
            if not ok: res['errors'].append(e); break
            rows=arr(d,['feedbacks']); res['received']+=len(rows)
            if not rows: break
            for it in rows: self.up_comm('review','WB',str(get(it,'id','feedbackId',default='') or sid(it)),'feedbacks_archive',it)
            await asyncio.sleep(self.sleep)
        for answered in [False,True]:
            for page in range(pages):
                ok,d,e=await self.req('WB',f'questions_{answered}','GET','https://feedbacks-api.wildberries.ru/api/v1/questions',h,params={'isAnswered':str(answered).lower(),'take':min(self.take,500),'skip':page*min(self.take,500),'order':'dateDesc'})
                if not ok: res['errors'].append(e); break
                rows=arr(d,['questions']); res['received']+=len(rows)
                if not rows: break
                for it in rows: self.up_comm('question','WB',str(get(it,'id','questionId',default='') or sid(it)),f'questions_{answered}',it)
                await asyncio.sleep(self.sleep)
        return res
    async def ozon_reviews_questions(self, deep=False):
        cid=getattr(settings,'ozon_client_id',''); key=getattr(settings,'ozon_api_key',''); res={'platform':'OZON','block':'reviews_questions','received':0,'errors':[]}
        if not cid or not key: self.raw('OZON','reviews_questions','failed',{},'Ozon credentials missing'); res['errors'].append('Ozon credentials missing'); return res
        h={'Client-Id':cid,'Api-Key':key,'Content-Type':'application/json'}; pages=self.deep_pages if deep else self.hot_pages; last=None
        for _ in range(pages):
            body={'limit':min(self.take,100),'sort_dir':'DESC'}
            if last: body['last_id']=last
            ok,d,e=await self.req('OZON','review_list','POST','https://api-seller.ozon.ru/v1/review/list',h,body=body)
            if not ok: res['errors'].append(e); break
            rows=arr(d,['reviews']); res['received']+=len(rows)
            if not rows: break
            for it in rows:
                rid=str(get(it,'id','review_id','reviewId',default='') or sid(it)); full=it
                oi,info,ie=await self.req('OZON','review_info','POST','https://api-seller.ozon.ru/v1/review/info',h,body={'review_id':rid},log_ok=False)
                if oi and isinstance(info,dict): full={**it, **(info.get('result') if isinstance(info.get('result'),dict) else info)}
                self.up_comm('review','OZON',rid,'review_list',full); await asyncio.sleep(0.12)
            last=get(d,'last_id','lastId',default=None)
            if not last: break
            await asyncio.sleep(self.sleep)
        ok,d,e=await self.req('OZON','question_list','POST','https://api-seller.ozon.ru/v1/question/list',h,body={'limit':min(self.take,100),'sort_dir':'DESC'})
        if ok:
            for it in arr(d,['questions']): self.up_comm('question','OZON',str(get(it,'id','question_id','questionId',default='') or sid(it)),'question_list',it)
        elif e: res['errors'].append(e)
        return res
    def up_chat(self, platform, item):
        cid=str(get(item,'chat_id','chatId','chatID','id',default='') or sid(item)); product=get(item,'product','goodCard','productDetails',default={}) or {}; sku=str(get(item,'sku','offer_id','product_id','nmID','nmId',default='') or get(product,'sku','offer_id','nmId','nmID',default='') or ''); md=media(item); image=next((m.get('preview_url') or m.get('url') for m in md if m.get('media_type')=='image'),None)
        vals={'p':platform,'cid':cid,'typ':str(get(item,'type','chat_type',default='buyer') or 'buyer'),'st':str(get(item,'status','state',default='') or ''),'unread':int(get(item,'unread_count','unreadCount',default=0) or 0),'need':bool(get(item,'needAnswer','needs_response','unanswered',default=False)),'buyer':str(get(item,'buyer_name','buyerName','clientName','customer_name',default='') or ''),'bid':str(get(item,'buyer_id','buyerId','clientID','customer_id',default='') or ''),'ord':str(get(item,'order_number','orderNumber','order_id','rid','srid',default='') or ''),'post':str(get(item,'posting_number','postingNumber',default='') or ''),'sku':sku,'prod':str(get(item,'product_name','productName','name','title',default='') or get(product,'productName','name','title',default='') or ''),'url':f'https://www.wildberries.ru/catalog/{sku}/detail.aspx' if platform=='WB' and sku.isdigit() else None,'img':image,'reply':str(get(item,'replySign','reply_sign',default='') or ''),'last':pdt(get(item,'last_message_at','lastMessageAt','updated_at','updatedAt','created_at','createdAt',default=None)),'raw':dumps(item),'c':now(),'u':now()}
        try:
            ex=self.db.execute(text('SELECT id FROM buyer_chats WHERE platform=:p AND external_chat_id=:cid'),vals).first()
            if ex: self.db.execute(text('UPDATE buyer_chats SET chat_type=:typ,marketplace_status=:st,unread_count=:unread,needs_response=:need,buyer_name=COALESCE(NULLIF(:buyer,\'\'),buyer_name),buyer_id=COALESCE(NULLIF(:bid,\'\'),buyer_id),order_number=COALESCE(NULLIF(:ord,\'\'),order_number),posting_number=COALESCE(NULLIF(:post,\'\'),posting_number),sku=COALESCE(NULLIF(:sku,\'\'),sku),product_name=COALESCE(NULLIF(:prod,\'\'),product_name),product_url=COALESCE(:url,product_url),product_image=COALESCE(:img,product_image),reply_sign=COALESCE(NULLIF(:reply,\'\'),reply_sign),last_message_at=COALESCE(:last,last_message_at),raw=:raw,updated_at=:u WHERE id=:id'),{**vals,'id':ex[0]})
            else: self.db.execute(text('INSERT INTO buyer_chats(platform,external_chat_id,chat_type,marketplace_status,internal_status,unread_count,needs_response,buyer_name,buyer_id,order_number,posting_number,sku,product_name,product_url,product_image,reply_sign,last_message_at,raw,created_at,updated_at) VALUES (:p,:cid,:typ,:st,\'new\',:unread,:need,:buyer,:bid,:ord,:post,:sku,:prod,:url,:img,:reply,:last,:raw,:c,:u)'),vals)
            self.db.commit()
        except Exception as e: self.db.rollback(); self.raw(platform,'chat_upsert','failed',item,str(e),cid)
        self.up_media('chat',cid,platform,md); return cid
    def up_msg(self, platform, chat_id, it):
        mid=str(get(it,'message_id','messageId','event_id','eventId','id','uuid',default='') or f'{chat_id}:{sid(it)}'); raw=dumps(it).lower(); direction='seller' if any(x in raw for x in ['seller','merchant','operator','продав','оператор']) else 'customer'; md=media(it); sent=pdt(get(it,'sent_at','sentAt','created_at','createdAt','date','timestamp','time','eventTime',default=None)) or now(); vals={'p':platform,'cid':chat_id,'mid':mid,'dir':direction,'author':str(get(it,'author','author_name','senderName','name',default='') or ''),'txt':body_text(it) or ('[медиа]' if md else ''),'typ':str(get(it,'type','eventType','message_type',default='message') or 'message'),'sent':sent,'media':dumps(md),'raw':dumps(it),'c':now(),'u':now()}
        try:
            ex=self.db.execute(text('SELECT id FROM buyer_chat_messages WHERE platform=:p AND external_message_id=:mid'),vals).first()
            if ex: self.db.execute(text('UPDATE buyer_chat_messages SET external_chat_id=:cid,direction=:dir,author_name=:author,text=COALESCE(NULLIF(:txt,\'\'),text),message_type=:typ,sent_at=COALESCE(:sent,sent_at),media=:media,raw=:raw,updated_at=:u WHERE id=:id'),{**vals,'id':ex[0]})
            else: self.db.execute(text('INSERT INTO buyer_chat_messages(platform,external_chat_id,external_message_id,direction,author_name,text,message_type,sent_at,media,raw,created_at,updated_at) VALUES (:p,:cid,:mid,:dir,:author,:txt,:typ,:sent,:media,:raw,:c,:u)'),vals)
            self.db.commit()
        except Exception as e: self.db.rollback(); self.raw(platform,'chat_msg_upsert','failed',it,str(e),mid)
        self.up_media('chat_message',mid,platform,md); return mid
    async def chats(self, platform='ALL'):
        platform=platform.upper(); res={'platform':platform,'block':'chats','received':0,'errors':[]}
        if platform in ('ALL','OZON'):
            cid=getattr(settings,'ozon_client_id',''); key=getattr(settings,'ozon_api_key','')
            if cid and key:
                h={'Client-Id':cid,'Api-Key':key,'Content-Type':'application/json'}; ok,d,e=await self.req('OZON','chats_list','POST','https://api-seller.ozon.ru/v3/chat/list',h,body={'filter':{},'limit':min(self.chat_limit,100),'offset':0})
                if ok:
                    for ch in arr(d,['chats']):
                        chat_id=self.up_chat('OZON',ch); res['received']+=1
                        oh,hist,he=await self.req('OZON','chat_history','POST','https://api-seller.ozon.ru/v3/chat/history',h,body={'chat_id':chat_id,'limit':100},log_ok=False)
                        if oh:
                            for m in arr(hist,['messages']): self.up_msg('OZON',chat_id,m)
                            self.raw('OZON','chat_history','success',{'chat_id':chat_id,'messages':len(arr(hist,['messages']))},None,chat_id)
                        elif he: res['errors'].append(he)
                        await asyncio.sleep(0.1)
                else: res['errors'].append(e)
        if platform in ('ALL','WB'):
            token=getattr(settings,'wb_api_token','') or getattr(settings,'wb_api_key','')
            if token:
                h={'Authorization':token}; ok,d,e=await self.req('WB','chats_list','GET','https://buyer-chat-api.wildberries.ru/api/v1/seller/chats',h,params={'limit':min(self.chat_limit,100)})
                if ok:
                    for ch in arr(d,['chats']):
                        cid=self.up_chat('WB',ch); res['received']+=1
                        last=ch.get('lastMessage') if isinstance(ch.get('lastMessage'),dict) else None
                        if last: self.up_msg('WB',cid,{**last,'chatID':cid})
                else: res['errors'].append(e)
                nxt=None
                for _ in range(3):
                    params={'next':nxt} if nxt else None; ok,d,e=await self.req('WB','chat_events','GET','https://buyer-chat-api.wildberries.ru/api/v1/seller/events',h,params=params)
                    if not ok: res['errors'].append(e); break
                    evs=arr(d,['events']); res['received']+=len(evs)
                    for ev in evs:
                        cid=str(get(ev,'chatID','chatId','chat_id',default='') or '')
                        if cid: self.up_chat('WB',{'chatID':cid,'lastMessage':ev}); self.up_msg('WB',cid,ev.get('message') if isinstance(ev.get('message'),dict) else ev)
                    nxt=get(d,'next',default=None); total=int(get(d,'totalEvents',default=0) or 0)
                    if not nxt or total==0: break
                    await asyncio.sleep(self.sleep)
        return res
    def up_return(self, platform, it):
        ext=str(get(it,'id','claimID','claimId','return_id','returnId','posting_number','postingNumber','srid','rid',default='') or sid(it)); self.up_operation(platform,'return_request',ext,it); self.up_media('return_request',ext,platform,media(it)); return ext
    def up_operation(self, platform, typ, ext, it):
        products=it.get('products') if isinstance(it.get('products'),list) else []; fp=products[0] if products and isinstance(products[0],dict) else {}; delivery=it.get('delivery_method') if isinstance(it.get('delivery_method'),dict) else {}; sku=str(get(fp,'sku','product_id','offer_id',default='') or get(it,'sku','nmID','nmId','offer_id','product_id',default='') or ''); vals={'p':platform,'typ':typ,'ext':ext,'doc':str(get(it,'number','act_id','actId','posting_number','postingNumber','claimID','returnId','docNumber',default='') or ext),'sku':sku or None,'prod':get(fp,'name','productName','product_name',default=None) or get(it,'productName','product_name','subject','name','title',default=None),'wh':get(delivery,'warehouse','warehouse_name','warehouseName',default=None) or get(it,'warehouseName','warehouse',default=None),'amt':str(get(fp,'price','amount',default='') or get(it,'amount','price','total','sum',default='') or '') or None,'qty':int(get(fp,'quantity','qty','count',default=None) or get(it,'quantity','qty','count',default=1) or 1),'reason':get(it,'reason','returnReason','status','state','category',default=None),'st':'synced','mp':str(get(it,'status','state',default='synced') or 'synced'),'raw':dumps(it),'occ':pdt(get(it,'date','created_at','createdAt','shipment_date','returnDate',default=None)) or now(),'c':now(),'u':now()}
        try:
            ex=self.db.execute(text('SELECT id FROM marketplace_operations WHERE platform=:p AND operation_type=:typ AND external_id=:ext'),vals).first()
            if ex: self.db.execute(text('UPDATE marketplace_operations SET document_number=:doc,sku=:sku,product_name=:prod,warehouse=:wh,amount=:amt,quantity=:qty,reason=:reason,status=:st,marketplace_status=:mp,raw=:raw,occurred_at=COALESCE(:occ,occurred_at),updated_at=:u WHERE id=:id'),{**vals,'id':ex[0]})
            else: self.db.execute(text('INSERT INTO marketplace_operations(platform,operation_type,external_id,document_number,sku,product_name,warehouse,amount,quantity,reason,status,marketplace_status,cx_workflow_status,raw,occurred_at,created_at,updated_at) VALUES (:p,:typ,:ext,:doc,:sku,:prod,:wh,:amt,:qty,:reason,:st,:mp,\'new_to_review\',:raw,:occ,:c,:u)'),vals)
            self.db.commit()
        except Exception as e: self.db.rollback(); self.raw(platform,'operation_upsert','failed',it,str(e),ext)
    async def operations(self, platform='ALL'):
        platform=platform.upper(); res={'platform':platform,'block':'operations','received':0,'errors':[]}
        if platform in ('ALL','OZON'):
            cid=getattr(settings,'ozon_client_id',''); key=getattr(settings,'ozon_api_key','')
            if cid and key:
                h={'Client-Id':cid,'Api-Key':key,'Content-Type':'application/json'}; d2=now().date(); d1=d2-timedelta(days=31); cur=d1
                while cur<d2:
                    nx=min(cur+timedelta(days=7),d2); ok,d,e=await self.req('OZON','acts_fbs_list','POST','https://api-seller.ozon.ru/v2/posting/fbs/act/list',h,body={'filter':{'date_from':cur.isoformat(),'date_to':nx.isoformat()},'limit':50})
                    if ok:
                        for it in arr(d,['acts']): self.up_operation('OZON','act',str(get(it,'id','act_id','actId','number',default='') or sid(it)),it); res['received']+=1
                    elif e: res['errors'].append(e)
                    cur=nx; await asyncio.sleep(self.sleep)
                ok,d,e=await self.req('OZON','postings_fbs_list','POST','https://api-seller.ozon.ru/v3/posting/fbs/list',h,body={'dir':'DESC','filter':{'since':(now()-timedelta(days=14)).isoformat(),'to':now().isoformat()},'limit':100,'offset':0,'with':{'analytics_data':True,'financial_data':False}})
                if ok:
                    for it in arr(d,['postings']): self.up_operation('OZON','posting',str(get(it,'posting_number','postingNumber',default='') or sid(it)),it); res['received']+=1
                elif e: res['errors'].append(e)
                for path in ['/v4/returns/company/fbs','/v4/returns/company/fbo']:
                    ok,d,e=await self.req('OZON','returns_list','POST',f'https://api-seller.ozon.ru{path}',h,body={'filter':{},'last_id':0,'limit':100})
                    if ok:
                        for it in arr(d,['returns']): self.up_return('OZON',it); res['received']+=1
                    elif e and 'obsolete method' not in str(e).lower(): res['errors'].append(e)
        if platform in ('ALL','WB'):
            token=getattr(settings,'wb_api_token','') or getattr(settings,'wb_api_key','')
            if token:
                h={'Authorization':token}; d2=now().date(); d1=d2-timedelta(days=31)
                for archive in [False,True]:
                    for url in ['https://returns-api.wildberries.ru/api/v1/claims','https://returns-api.wildberries.ru/api/v1/claims/openapi-portal']:
                        ok,d,e=await self.req('WB','returns_list','GET',url,h,params={'is_archive':str(archive).lower(),'limit':150})
                        if ok:
                            for it in arr(d,['claims','returns']): self.up_return('WB',it); res['received']+=1
                            break
                        elif e: res['errors'].append(e)
                    await asyncio.sleep(self.sleep)
                for block,url,params in [('documents_categories','https://documents-api.wildberries.ru/api/v1/documents/categories',{'locale':'ru'}),('documents_list','https://documents-api.wildberries.ru/api/v1/documents/list',{'locale':'ru','beginTime':d1.isoformat(),'endTime':d2.isoformat(),'limit':50,'offset':0}),('goods_return','https://seller-analytics-api.wildberries.ru/api/v1/analytics/goods-return',{'dateFrom':d1.isoformat(),'dateTo':d2.isoformat()})]:
                    ok,d,e=await self.req('WB',block,'GET',url,h,params=params)
                    if ok:
                        for it in arr(d,['documents','categories','returns','items']):
                            raw=dumps(it).lower(); typ='return' if block=='goods_return' else 'document'
                            if 'акт' in raw or 'act' in raw: typ='act'
                            if 'излиш' in raw or 'surplus' in raw or 'excess' in raw: typ='surplus'
                            if 'недостач' in raw or 'shortage' in raw: typ='shortage'
                            if 'обезлич' in raw or 'anonym' in raw: typ='anonymized_item'
                            self.up_operation('WB',typ,str(get(it,'id','serviceName','name','title','docNumber',default='') or sid(it)),it); res['received']+=1
                    elif e: res['errors'].append(e)
                    await asyncio.sleep(self.sleep)
        return res
    async def all(self, platform='ALL', deep=False):
        self.ensure(); parts=[]; p=platform.upper()
        if p in ('ALL','WB'): parts.append(await self.wb_reviews_questions(deep))
        if p in ('ALL','OZON'): parts.append(await self.ozon_reviews_questions(deep))
        parts.append(await self.chats(p)); parts.append(await self.operations(p))
        return {'ok':any(not x.get('errors') for x in parts),'platform':p,'deep':deep,'received':sum(int(x.get('received',0) or 0) for x in parts),'parts':parts}


    def communications(self, platform='ALL', limit=20000, entity_type='ALL', topic=None):
        self.ensure()
        p = platform.upper()
        limit = min(max(int(limit), 1), 50000)
        items = []

        if "recovery_communications" in self.tables():
            where = ["(:p='ALL' OR platform=:p)"]
            params = {"p": p, "l": limit}
            if entity_type and entity_type.upper() != "ALL":
                where.append("entity_type=:et")
                params["et"] = entity_type
            sql = "SELECT * FROM recovery_communications WHERE " + " AND ".join(where) + " ORDER BY COALESCE(created_at_marketplace,updated_at,created_at) DESC,id DESC LIMIT :l"
            try:
                rows = self.db.execute(text(sql), params).mappings().all()
            except Exception:
                self.db.rollback()
                rows = []
            for r in rows:
                d = fix_tree(dict(r))
                raw = fix_tree(loads(d.get("raw")) or {})
                d["raw"] = raw
                d["topic"] = simple_topic(d.get("text"), d.get("rating"), raw)
                if topic and d["topic"] != topic:
                    continue
                try:
                    mids = self.db.execute(text("SELECT media_type,url,preview_url,filename,source,visibility,send_status FROM communication_media WHERE platform=:p AND entity_type=:et AND entity_id=:eid ORDER BY id DESC LIMIT 50"), {"p": d.get("platform"), "et": d.get("entity_type"), "eid": str(d.get("external_id"))}).mappings().all()
                    d["media"] = [fix_tree(dict(m)) for m in mids]
                except Exception:
                    self.db.rollback()
                    d["media"] = extract_media(raw)
                for k, v in list(d.items()):
                    if hasattr(v, "isoformat"):
                        d[k] = v.isoformat()
                items.append(d)

        if not items:
            fallback_tables = [("reviews", "review"), ("marketplace_reviews", "review"), ("questions", "question"), ("marketplace_questions", "question")]
            for table_name, etype in fallback_tables:
                if table_name not in self.tables():
                    continue
                cols = self.cols(table_name)
                order_cols = [c for c in ["created_at_marketplace", "updated_at", "created_at"] if c in cols]
                order_expr = "COALESCE(" + ",".join(order_cols) + ")" if len(order_cols) > 1 else (order_cols[0] if order_cols else "id")
                where = "WHERE (:p='ALL' OR platform=:p)" if "platform" in cols else ""
                try:
                    rows = self.db.execute(text(f"SELECT * FROM {table_name} {where} ORDER BY {order_expr} DESC, id DESC LIMIT :l"), {"p": p, "l": limit}).mappings().all()
                except Exception:
                    self.db.rollback()
                    rows = []
                for r in rows:
                    d0 = dict(r)
                    raw = fix_tree(loads(d0.get("raw") or d0.get("raw_payload")) or d0)
                    external_id = str(d0.get("external_id") or d0.get("external_review_id") or d0.get("external_question_id") or d0.get("id"))
                    rating = d0.get("rating") or d0.get("product_valuation") or get(raw, "rating", "productValuation", default=None)
                    txt = d0.get("text") or d0.get("question") or text_value(raw)
                    item = {
                        "entity_type": etype,
                        "platform": str(d0.get("platform") or p).upper(),
                        "external_id": external_id,
                        "product_name": fix_text(d0.get("product_name") or get(raw, "productName", "product_name", "subject", "name", default="")),
                        "sku": str(d0.get("sku") or get(raw, "sku", "nmID", "nmId", "offer_id", default="") or ""),
                        "rating": rating,
                        "text": fix_text(txt),
                        "created_at_marketplace": d0.get("created_at_marketplace") or d0.get("created_at"),
                        "answer_text": d0.get("answer_text") or d0.get("response_text") or get(raw, "answer", "sellerAnswer", default=None),
                        "media": extract_media(raw),
                        "raw": raw,
                    }
                    item["topic"] = simple_topic(item["text"], rating, raw)
                    if topic and item["topic"] != topic:
                        continue
                    for k, v in list(item.items()):
                        if hasattr(v, "isoformat"):
                            item[k] = v.isoformat()
                    items.append(item)

        return {"ok": True, "platform": p, "count": len(items), "items": items[:limit]}

    def operations_items(self, platform='ALL', limit=10000, operation_type='ALL'):
        self.ensure()
        p = platform.upper()
        limit = min(max(int(limit), 1), 50000)
        where = ["(:p='ALL' OR platform=:p)"]
        params = {"p": p, "l": limit}
        if operation_type and operation_type.upper() != "ALL":
            where.append("operation_type=:ot")
            params["ot"] = operation_type
        sql = "SELECT * FROM marketplace_operations WHERE " + " AND ".join(where) + " ORDER BY COALESCE(occurred_at,updated_at,created_at) DESC,id DESC LIMIT :l"
        try:
            rows = self.db.execute(text(sql), params).mappings().all()
        except Exception as e:
            self.db.rollback()
            return {"ok": False, "items": [], "error": str(e)}
        out = []
        for r in rows:
            d = fix_tree(dict(r))
            raw = fix_tree(loads(d.get("raw")) or {})
            d["raw"] = raw
            fp = fix_tree(first_product(raw))
            if isinstance(fp, dict) and fp:
                d["product_name"] = fix_text(fp.get("name") or fp.get("productName") or fp.get("product_name") or d.get("product_name"))
                d["sku"] = str(fp.get("sku") or fp.get("product_id") or fp.get("offer_id") or d.get("sku") or "")
                d["amount"] = str(fp.get("price") or fp.get("amount") or d.get("amount") or "")
                d["quantity"] = int(fp.get("quantity") or fp.get("qty") or d.get("quantity") or 1)
            delivery = raw.get("delivery_method") if isinstance(raw, dict) and isinstance(raw.get("delivery_method"), dict) else {}
            if delivery and not d.get("warehouse"):
                d["warehouse"] = fix_text(delivery.get("warehouse") or delivery.get("warehouse_name") or "")
            d.update(operation_semantics(d.get("platform"), d.get("operation_type"), raw))
            for k, v in list(d.items()):
                if hasattr(v, "isoformat"):
                    d[k] = v.isoformat()
            out.append(d)
        return {"ok": True, "platform": p, "count": len(out), "items": out}

    def topics(self, platform='ALL'):
        data = self.communications(platform=platform, limit=50000)
        if not data.get("ok"):
            return data
        out = {}
        for item in data.get("items", []):
            key = item.get("topic") or "Прочее"
            o = out.setdefault(key, {"topic": key, "total": 0, "reviews": 0, "questions": 0, "chats": 0, "returns": 0, "with_media": 0})
            o["total"] += 1
            et = item.get("entity_type")
            if et == "review":
                o["reviews"] += 1
            elif et == "question":
                o["questions"] += 1
            elif et == "chat":
                o["chats"] += 1
            elif et == "return_request":
                o["returns"] += 1
            if item.get("media"):
                o["with_media"] += 1
        return {"ok": True, "platform": platform.upper(), "items": sorted(out.values(), key=lambda x: x["total"], reverse=True)}

    def scheduler(self):
        from pathlib import Path
        root = Path.cwd().parent if Path.cwd().name == "backend" else Path.cwd()
        wf = root / ".github" / "workflows"
        files = sorted(wf.glob("*.yml")) if wf.exists() else []
        workflows = []
        for f in files:
            txt = f.read_text(encoding="utf-8", errors="ignore")
            workflows.append({"file": f.name, "has_schedule": "schedule:" in txt, "has_workflow_dispatch": "workflow_dispatch:" in txt, "cron": [line.strip() for line in txt.splitlines() if "cron:" in line][:5], "uses_recovery_v5": "app.recovery_v5_entry" in txt})
        expected = [
            {"file": "marketplace-hot-sync.yml", "expected_schedule": True, "expected_kind": "reviews_questions"},
            {"file": "marketplace-customer-ops-sync.yml", "expected_schedule": True, "expected_kind": "customer_ops"},
            {"file": "marketplace-operations-sync.yml", "expected_schedule": True, "expected_kind": "operations"},
            {"file": "marketplace-nightly-deep-sync.yml", "expected_schedule": True, "expected_kind": "all/deep"},
            {"file": "marketplace-scheduler-heartbeat.yml", "expected_schedule": True, "expected_kind": "scheduler_heartbeat"},
        ]
        if not workflows:
            workflows = [{**x, "runtime_file_visible": False, "note": "Render runtime usually does not contain .github/workflows; use heartbeat and GitHub Actions UI as source of truth."} for x in expected]
        hb = []
        try:
            hb = [dict(r) for r in self.db.execute(text("SELECT platform,block,status,error,created_at FROM marketplace_raw_events WHERE block='scheduler_heartbeat' ORDER BY created_at DESC LIMIT 20")).mappings().all()]
            for r in hb:
                for k, v in list(r.items()):
                    if hasattr(v, "isoformat"):
                        r[k] = v.isoformat()
        except Exception as e:
            self.db.rollback()
            hb = [{"error": str(e)}]
        return {"ok": True, "workflow_dir": str(wf), "workflows": workflows, "heartbeats": hb, "heartbeat_count": len(hb)}

    def counts(self):
        self.ensure()
        out = {}
        for table_name in [
            "reviews", "questions", "marketplace_reviews", "marketplace_questions",
            "recovery_communications", "response_sla_metrics", "communication_media",
            "buyer_chats", "buyer_chat_messages", "buyer_returns", "marketplace_operations", "marketplace_raw_events"
        ]:
            if table_name not in self.tables():
                continue
            try:
                out[table_name] = self.db.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()
            except Exception as e:
                self.db.rollback()
                out[table_name] = f"error: {e}"
        return {"ok": True, "tables": out}
    def diagnostics(self, platform='ALL', limit=200):
        self.ensure(); p=platform.upper()
        try: rows=self.db.execute(text('SELECT platform,block,status,error,created_at FROM marketplace_raw_events WHERE (:p=\'ALL\' OR platform=:p) ORDER BY created_at DESC LIMIT :l'),{'p':p,'l':min(max(int(limit),1),500)}).mappings().all(); return {'ok':True,'items':[dict(r) for r in rows]}
        except Exception as e: self.db.rollback(); return {'ok':False,'error':str(e),'items':[]}

    def sla(self, platform='ALL', days=30):
        self.ensure()
        p = platform.upper()
        since = now() - timedelta(days=max(1, min(int(days), 365)))
        try:
            rows = self.db.execute(text("SELECT entity_type,platform,response_minutes,sla_status FROM response_sla_metrics WHERE (:p='ALL' OR platform=:p) AND COALESCE(created_at_marketplace,created_at)>=:since"), {"p": p, "since": since}).mappings().all()
        except Exception:
            self.db.rollback()
            rows = []

        if not rows:
            comms = self.communications(platform=p, limit=50000).get("items", [])
            generated = []
            for item in comms:
                created = pdt(item.get("created_at_marketplace") or item.get("created_at"))
                raw = item.get("raw") or {}
                ans_text, ans_dt, _ans_raw = answer_data(raw if isinstance(raw, dict) else {})
                if not ans_dt:
                    ans_dt = pdt(item.get("answered_at_marketplace"))
                minutes = None
                status = "unanswered"
                if created and ans_dt:
                    minutes = max(0, int((ans_dt - created).total_seconds() // 60))
                    status = "in_sla" if minutes <= 60 else "late"
                elif not created:
                    status = "unknown_time"
                generated.append({"entity_type": item.get("entity_type"), "platform": item.get("platform"), "response_minutes": minutes, "sla_status": status})
            rows = generated

        out = {}
        for r in rows:
            k = f"{r['platform']}:{r['entity_type']}"
            o = out.setdefault(k, {"total": 0, "answered": 0, "unanswered": 0, "late": 0, "avg_minutes": 0, "sum": 0})
            o["total"] += 1
            if r["response_minutes"] is None:
                o["unanswered"] += 1
            else:
                o["answered"] += 1
                o["sum"] += int(r["response_minutes"])
                if r["sla_status"] == "late":
                    o["late"] += 1
        for o in out.values():
            o["avg_minutes"] = round(o["sum"] / o["answered"], 2) if o["answered"] else 0
            o.pop("sum", None)
        return {"ok": True, "by_type": out}
    def chat_messages(self, chat_id:int, limit=500):
        self.ensure()
        try:
            ch=self.db.execute(text('SELECT * FROM buyer_chats WHERE id=:id'),{'id':chat_id}).mappings().first()
            if not ch: return {'chat':None,'items':[],'error':'chat not found'}
            ch=dict(ch); rows=self.db.execute(text('SELECT * FROM buyer_chat_messages WHERE platform=:p AND external_chat_id=:cid ORDER BY COALESCE(sent_at,created_at) ASC,id ASC LIMIT :l'),{'p':ch['platform'],'cid':ch['external_chat_id'],'l':min(max(int(limit),1),1000)}).mappings().all(); items=[]
            for r in rows:
                d=dict(r); d['media']=loads(d.get('media')) or []
                for k,v in list(d.items()):
                    if hasattr(v,'isoformat'): d[k]=v.isoformat()
                items.append(d)
            for k,v in list(ch.items()):
                if hasattr(v,'isoformat'): ch[k]=v.isoformat()
            return {'chat':ch,'items':items}
        except Exception as e: self.db.rollback(); self.raw('ALL','chat_messages_route','failed',{'chat_id':chat_id},str(e),str(chat_id)); return {'chat':None,'items':[],'error':str(e)}
