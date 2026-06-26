from __future__ import annotations
import asyncio, os
from datetime import datetime, timedelta, timezone
from typing import Any
import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.config import settings
from app.models import MarketplaceOperation
from app.services.ops_common import dumps, now_utc, parse_dt, get, items, short_hash, message_time

def _classify_wb_document(item: dict[str, Any]) -> str:
    raw = ' '.join(str(get(item, k, default='') or '') for k in ['name','title','category','serviceName','fileName']).lower()
    if any(x in raw for x in ['обезлич','anonym']): return 'anonymized_item'
    if any(x in raw for x in ['излиш','surplus','excess']): return 'surplus'
    if any(x in raw for x in ['недостач','shortage']): return 'shortage'
    if any(x in raw for x in ['расхожд','discrep']): return 'discrepancy'
    if any(x in raw for x in ['акт','act','income','acceptance']): return 'act'
    return 'document'

def _ext(item: dict[str, Any], prefix: str) -> str:
    return str(get(item, 'id','operation_id','operationId','act_id','actId','posting_number','postingNumber','return_id','returnId','serviceName','number','docNumber','document_number','srid', default='') or f'{prefix}-{short_hash(item)}')

def _upsert(db: Session, data: dict[str, Any]) -> str:
    row = db.query(MarketplaceOperation).filter(MarketplaceOperation.platform == data['platform'], MarketplaceOperation.operation_type == data['operation_type'], MarketplaceOperation.external_id == data['external_id']).first()
    if not row:
        db.add(MarketplaceOperation(**data)); db.commit(); return 'created'
    for k, v in data.items():
        if k != 'external_id': setattr(row, k, v)
    row.updated_at = now_utc(); db.commit(); return 'updated'

class OperationsSyncService:
    def __init__(self, db: Session):
        self.db = db
        self.timeout = httpx.Timeout(float(os.getenv('OPERATIONS_REQUEST_TIMEOUT','25')), connect=10.0)
        self.max_pages = int(os.getenv('OPERATIONS_MAX_PAGES','4') or '4')
        self.max_rows = int(os.getenv('OPERATIONS_MAX_ROWS','500') or '500')
        self.ozon_act_chunk_days = int(os.getenv('OZON_ACT_CHUNK_DAYS','7') or '7')
        self.ozon_act_limit = int(os.getenv('OZON_ACT_LIMIT','500') or '500')
    def _ensure_raw(self):
        dialect = self.db.bind.dialect.name; id_type = 'SERIAL PRIMARY KEY' if dialect == 'postgresql' else 'INTEGER PRIMARY KEY AUTOINCREMENT'
        self.db.execute(text(f"CREATE TABLE IF NOT EXISTS marketplace_raw_events (id {id_type}, platform VARCHAR(32) NOT NULL, block VARCHAR(128) NOT NULL, external_id VARCHAR(256), status VARCHAR(64) DEFAULT 'received', error TEXT, raw TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")); self.db.commit()
    def _raw(self, platform, block, status, raw, error=None, external_id=None):
        self._ensure_raw(); self.db.execute(text('INSERT INTO marketplace_raw_events(platform, block, external_id, status, error, raw, created_at, updated_at) VALUES (:p,:b,:e,:s,:err,:r,:c,:u)'), {'p':platform,'b':block,'e':external_id,'s':status,'err':error,'r':dumps(raw),'c':now_utc(),'u':now_utc()}); self.db.commit()
    async def _request(self, platform, block, method, url, *, headers, params=None, json_body=None):
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.request(method, url, headers=headers, params=params, json=json_body)
            if resp.status_code == 204:
                self._raw(platform, block, 'empty', {'url':url,'params':params,'body':json_body}, '204 No data'); return True, [], None
            try: data = resp.json()
            except Exception: data = {'text': resp.text}
            if resp.status_code >= 400:
                err = f'HTTP {resp.status_code}: {str(data)[:900]}'; self._raw(platform, block, 'failed', {'url':url,'params':params,'body':json_body,'response':data}, err); return False, data, err
            self._raw(platform, block, 'success', {'url':url,'params':params,'body':json_body,'response':data}); return True, data, None
        except Exception as exc:
            err = str(exc); self._raw(platform, block, 'failed', {'url':url,'params':params,'body':json_body}, err); return False, None, err
    async def sync(self, platform='ALL', days=31):
        platform = (platform or 'ALL').upper(); results = []
        if platform in {'ALL','WB'}: results.append(await self.sync_wb(days=days))
        if platform in {'ALL','OZON'}: results.append(await self.sync_ozon(days=days))
        return {'ok': any(r.get('ok', True) for r in results), 'platform': platform, 'received': sum(int(r.get('received',0) or 0) for r in results), 'created': sum(int(r.get('created',0) or 0) for r in results), 'updated': sum(int(r.get('updated',0) or 0) for r in results), 'results': results, 'message': 'Operations sync завершен. Недоступные API пишутся в Диагностику API без фейковых строк.'}
    async def sync_wb(self, days=31):
        token = getattr(settings, 'wb_api_token', '') or getattr(settings, 'wb_api_key', '')
        res = {'platform':'WB','ok':True,'received':0,'created':0,'updated':0,'blocks':[]}
        if not token:
            err='WB_API_KEY/WB_API_TOKEN не заполнен'; self._raw('WB','operations','failed',{},err); return {**res,'ok':False,'error':err}
        headers = {'Authorization': token}; date_to = datetime.now(timezone.utc).date(); date_from = date_to - timedelta(days=min(max(days,1),31))
        ok, data, err = await self._request('WB','operations_returns_goods','GET','https://seller-analytics-api.wildberries.ru/api/v1/analytics/goods-return',headers=headers,params={'dateFrom':date_from.isoformat(),'dateTo':date_to.isoformat()})
        block={'operation_type':'return','endpoint':'goods-return','received':0,'created':0,'updated':0}
        if ok:
            got = items(data, ['returns','items']); block['received']=len(got)
            for it in got[:self.max_rows]:
                st=self._upsert_wb_item('return', it); block['created' if st=='created' else 'updated']+=1
        else: block['error']=err
        res['blocks'].append(block)
        ok, cats, cerr = await self._request('WB','documents_categories','GET','https://documents-api.wildberries.ru/api/v1/documents/categories',headers=headers,params={'locale':'ru'})
        res['blocks'].append({'operation_type':'document_categories','endpoint':'documents_categories','received':len(items(cats,['categories'])) if ok else 0,'created':0,'updated':0,'error':cerr if not ok else None})
        await asyncio.sleep(1.1)
        offset=created=updated=received=0; errors=[]
        for _ in range(self.max_pages):
            ok, docs, derr = await self._request('WB','documents_list','GET','https://documents-api.wildberries.ru/api/v1/documents/list',headers=headers,params={'locale':'ru','beginTime':date_from.isoformat(),'endTime':date_to.isoformat(),'limit':50,'offset':offset,'sort':'date','order':'desc'})
            if not ok: errors.append(derr); break
            got = items(docs, ['documents'])
            if not got: break
            received += len(got)
            for doc in got:
                op = _classify_wb_document(doc)
                if op == 'document': continue
                st = self._upsert_wb_document(op, doc); created += st=='created'; updated += st!='created'
            offset += 50; await asyncio.sleep(1.1)
        res['blocks'].append({'operation_type':'documents','endpoint':'documents_list','received':received,'created':int(created),'updated':int(updated),'errors':errors})
        res['received']=sum(int(b.get('received',0) or 0) for b in res['blocks']); res['created']=sum(int(b.get('created',0) or 0) for b in res['blocks']); res['updated']=sum(int(b.get('updated',0) or 0) for b in res['blocks']); return res
    def _upsert_wb_item(self, op_type, item):
        ext=_ext(item,f'wb-{op_type}'); sku=str(get(item,'nmId','nmID','nm_id','sku','supplierArticle','article',default='') or '')
        data={'platform':'WB','operation_type':op_type,'external_id':ext,'document_number':str(get(item,'docNumber','document_number','returnId','srid',default='') or ext),'sku':sku or None,'product_name':get(item,'subject','product_name','name','goodsName','title'), 'warehouse':get(item,'warehouseName','warehouse','officeName'), 'amount':str(get(item,'price','retailPrice','amount','sum',default='') or '') or None,'quantity':int(get(item,'quantity','qty','count',default=1) or 1),'reason':get(item,'reason','returnReason','comment','status'),'status':'synced','marketplace_status':str(get(item,'status','state','operation_status','operationStatus',default='synced') or 'synced'),'cx_workflow_status':'new_to_review','raw':item,'occurred_at':message_time(item, get(item,'date','returnDate','createdAt','lastChangeDate','creationTime')) or now_utc()}
        return _upsert(self.db,data)
    def _upsert_wb_document(self, op_type, doc):
        ext=_ext(doc,'wb-doc')
        data={'platform':'WB','operation_type':op_type,'external_id':ext,'document_number':str(get(doc,'serviceName','name','title','fileName',default='') or ext),'sku':None,'product_name':get(doc,'title','category','name'), 'warehouse':None,'amount':None,'quantity':1,'reason':get(doc,'category','name','title'),'status':'synced','marketplace_status':'document_available','cx_workflow_status':'new_to_review','raw':doc,'occurred_at':parse_dt(get(doc,'creationTime','date','createdAt')) or now_utc()}
        return _upsert(self.db,data)
    async def sync_ozon(self, days=31):
        if not settings.ozon_client_id or not settings.ozon_api_key:
            err='OZON_CLIENT_ID/OZON_API_KEY не заполнены'; self._raw('OZON','operations','failed',{},err); return {'platform':'OZON','ok':False,'received':0,'created':0,'updated':0,'error':err}
        headers={'Client-Id':settings.ozon_client_id,'Api-Key':settings.ozon_api_key,'Content-Type':'application/json'}; res={'platform':'OZON','ok':True,'received':0,'created':0,'updated':0,'blocks':[]}; to=datetime.now(timezone.utc); frm=to-timedelta(days=max(1,min(days,31)))
        cr=up=rec=0; errors=[]; start=frm
        while start < to:
            end=min(start+timedelta(days=self.ozon_act_chunk_days), to); payload={'limit':min(self.ozon_act_limit,1000),'filter':{'date_from':start.isoformat(),'date_to':end.isoformat()}}
            ok,data,err=await self._request('OZON','acts_fbs_list','POST','https://api-seller.ozon.ru/v2/posting/fbs/act/list',headers=headers,json_body=payload)
            if ok:
                got=items(data,['acts']); rec+=len(got)
                for it in got[:self.max_rows]:
                    st=self._upsert_ozon('act',it); cr+=st=='created'; up+=st!='created'
            else: errors.append(err)
            start=end; await asyncio.sleep(0.8)
        res['blocks'].append({'operation_type':'act','endpoint':'/v2/posting/fbs/act/list','received':rec,'created':int(cr),'updated':int(up),'errors':errors})
        cr=up=rec=0; errors=[]
        for path, schema in [('/v3/returns/company/fbs','fbs'),('/v3/returns/company/fbo','fbo')]:
            last_id=0
            for _ in range(self.max_pages):
                ok,data,err=await self._request('OZON',f'returns_{schema}','POST',f'https://api-seller.ozon.ru{path}',headers=headers,json_body={'filter':{},'last_id':last_id,'limit':min(100,self.max_rows)})
                if not ok: errors.append(err); break
                got=items(data,['returns']); rec+=len(got)
                for it in got:
                    st=self._upsert_ozon('return',it); cr+=st=='created'; up+=st!='created'
                next_id=get(data,'last_id','lastId')
                if not got or not next_id or str(next_id)==str(last_id): break
                last_id=next_id; await asyncio.sleep(0.8)
        res['blocks'].append({'operation_type':'return','endpoint':'/v3/returns/company/fbs+fbo','received':rec,'created':int(cr),'updated':int(up),'errors':errors})
        payload={'dir':'DESC','filter':{'since':frm.isoformat(),'to':to.isoformat()},'limit':min(100,self.max_rows),'offset':0,'with':{'analytics_data':True,'financial_data':False}}
        ok,data,err=await self._request('OZON','postings_fbs_list','POST','https://api-seller.ozon.ru/v3/posting/fbs/list',headers=headers,json_body=payload)
        cr=up=0; got=[] if not ok else items(data,['postings'])
        for it in got:
            status=str(get(it,'status',default='') or '')
            if status in {'awaiting_packaging','awaiting_deliver','arbitration','cancelled','delivering'}:
                st=self._upsert_ozon('shipment_issue',it); cr+=st=='created'; up+=st!='created'
        res['blocks'].append({'operation_type':'shipment_issue','endpoint':'/v3/posting/fbs/list','received':len(got),'created':int(cr),'updated':int(up),'errors':[err] if err else []})
        res['received']=sum(int(b.get('received',0) or 0) for b in res['blocks']); res['created']=sum(int(b.get('created',0) or 0) for b in res['blocks']); res['updated']=sum(int(b.get('updated',0) or 0) for b in res['blocks']); return res
    def _upsert_ozon(self, op_type, item):
        ext=_ext(item,f'ozon-{op_type}'); sku=str(get(item,'sku','product_id','productId','offer_id','offerId',default='') or '')
        data={'platform':'OZON','operation_type':op_type,'external_id':ext,'document_number':str(get(item,'number','act_id','actId','posting_number','postingNumber','return_id','returnId',default='') or ext),'sku':sku or None,'product_name':get(item,'name','product_name','productName'), 'warehouse':get(item,'warehouse','warehouse_name','warehouseName','delivery_method_name'), 'amount':str(get(item,'amount','price','total_price','totalPrice',default='') or '') or None,'quantity':int(get(item,'quantity','qty','count',default=1) or 1),'reason':get(item,'reason','return_reason_name','status','state'),'status':'synced','marketplace_status':str(get(item,'status','state','operation_status','operationStatus',default='synced') or 'synced'),'cx_workflow_status':'new_to_review','raw':item,'occurred_at':message_time(item, get(item,'created_at','createdAt','date','act_date','actDate','return_date','returnDate')) or now_utc()}
        return _upsert(self.db,data)
