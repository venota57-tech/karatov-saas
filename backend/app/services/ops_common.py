from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from typing import Any

def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

def dumps(v: Any) -> str:
    try: return json.dumps(v, ensure_ascii=False, default=str)
    except Exception: return json.dumps({"raw": str(v)}, ensure_ascii=False)

def loads(v: Any) -> Any:
    if v in (None, ""): return None
    if isinstance(v, (dict, list)): return v
    try: return json.loads(v)
    except Exception: return v

def short_hash(v: Any) -> str:
    return hashlib.sha1(dumps(v).encode('utf-8')).hexdigest()[:24]

def parse_dt(v: Any) -> datetime | None:
    if v in (None, ""): return None
    if isinstance(v, datetime): return v.replace(tzinfo=None) if v.tzinfo else v
    if isinstance(v, (int, float)):
        try:
            ts = float(v)
            if ts > 100000000000: ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
        except Exception: return None
    raw = str(v).strip().replace('Z', '+00:00')
    if not raw: return None
    if raw.isdigit(): return parse_dt(int(raw))
    try:
        dt = datetime.fromisoformat(raw)
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    except Exception: return None

def walk(o: Any):
    if isinstance(o, dict):
        yield o
        for vv in o.values(): yield from walk(vv)
    elif isinstance(o, list):
        for vv in o: yield from walk(vv)

def get(o: Any, *keys: str, default: Any = None) -> Any:
    if isinstance(o, dict):
        for k in keys:
            if k in o and o.get(k) not in (None, ''): return o.get(k)
        for vv in o.values():
            found = get(vv, *keys, default=None)
            if found not in (None, ''): return found
    elif isinstance(o, list):
        for vv in o:
            found = get(vv, *keys, default=None)
            if found not in (None, ''): return found
    return default

def items(data: Any, preferred: list[str] | None = None) -> list[dict[str, Any]]:
    preferred = preferred or []
    if isinstance(data, list): return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict): return []
    roots = [data]
    if isinstance(data.get('result'), (dict, list)): roots.insert(0, data['result'])
    if isinstance(data.get('data'), (dict, list)): roots.insert(0, data['data'])
    for root in roots:
        if isinstance(root, list): return [x for x in root if isinstance(x, dict)]
        if not isinstance(root, dict): continue
        for key in preferred + ['items','chats','events','messages','returns','claims','operations','postings','acts','documents','categories','list','data','rows','result']:
            val = root.get(key)
            if isinstance(val, list): return [x for x in val if isinstance(x, dict)]
            if isinstance(val, dict):
                nested = items(val, preferred)
                if nested: return nested
    return []

def extract_media(o: Any) -> list[dict[str, Any]]:
    out, seen = [], set()
    for node in walk(o):
        for k, v in node.items():
            lk = str(k).lower()
            if isinstance(v, str) and v.strip().startswith('http') and any(x in lk for x in ['url','link','file','image','photo','video','attachment','media']):
                raw = v.strip(); kind = 'file'
                if any(x in raw.lower() for x in ['.jpg','.jpeg','.png','.webp','/image','image']): kind = 'image'
                if any(x in raw.lower() for x in ['.mp4','.mov','/video','video']): kind = 'video'
                sid = f'{kind}:{raw}'
                if sid not in seen:
                    out.append({'type': kind, 'url': raw, 'name': str(get(node, 'name','fileName','filename','title', default='') or '')}); seen.add(sid)
    return out[:20]

def message_text(item: dict[str, Any]) -> str:
    direct = get(item, 'text','message_text','messageText','body','content','value','comment','caption','description')
    if isinstance(direct, str) and direct.strip(): return direct.strip()
    if isinstance(direct, (int,float)): return str(direct)
    msg = item.get('message') if isinstance(item, dict) else None
    if isinstance(msg, str) and msg.strip(): return msg.strip()
    if isinstance(msg, dict):
        nested = message_text(msg)
        if nested: return nested
    for key in ['payload','data','event','message','body','content','lastMessage']:
        v = item.get(key) if isinstance(item, dict) else None
        if isinstance(v, dict):
            nested = message_text(v)
            if nested: return nested
    if extract_media(item): return '[медиа]'
    return ''

def message_time(item: dict[str, Any], fallback: Any = None) -> datetime | None:
    for key in ['sent_at','sentAt','created_at','createdAt','addTimestamp','add_timestamp','date','timestamp','time','event_time','eventTime','messageCreatedAt','lastMessageAt','updated_at','updatedAt']:
        dt = parse_dt(get(item, key))
        if dt: return dt
    return parse_dt(fallback)

def product_link(platform: str, sku: Any, raw: Any = None) -> str | None:
    url = get(raw or {}, 'product_url','productUrl','url','link','productLink')
    if url: return str(url)
    if not sku: return None
    if platform.upper() == 'WB':
        digits = ''.join(ch for ch in str(sku) if ch.isdigit())
        return f'https://www.wildberries.ru/catalog/{digits}/detail.aspx' if digits else None
    return None
