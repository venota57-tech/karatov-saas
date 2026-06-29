from __future__ import annotations
import asyncio, os
from app.database import SessionLocal
from app.services.recovery_v5 import RecoveryV5
async def main():
    kind=(os.getenv('RECOVERY_V5_KIND') or os.getenv('GITHUB_SYNC_KIND') or 'all').lower(); platform=(os.getenv('RECOVERY_V5_PLATFORM') or os.getenv('GITHUB_SYNC_PLATFORM') or 'ALL').upper(); deep=(os.getenv('RECOVERY_V5_DEEP') or '').lower() in {'1','true','yes','deep'} or kind=='nightly'
    db=SessionLocal()
    try:
        svc=RecoveryV5(db); svc.ensure()
        if kind in {'scheduler_heartbeat','heartbeat'}:
            svc.raw('ALL','scheduler_heartbeat','success',{'kind':kind,'platform':platform,'source':'github_actions'})
            res={'ok':True,'heartbeat':'saved','platform':platform}
        elif kind in {'customer_ops','chats'}: res=await svc.chats(platform)
        elif kind in {'operations','ops'}: res=await svc.operations(platform)
        elif kind in {'reviews','answers','questions','reviews_questions'}:
            parts=[]
            if platform in {'ALL','WB'}: parts.append(await svc.wb_reviews_questions(deep))
            if platform in {'ALL','OZON'}: parts.append(await svc.ozon_reviews_questions(deep))
            res={'ok':True,'platform':platform,'parts':parts}
        else: res=await svc.all(platform, deep)
        print(res)
    finally: db.close()
if __name__=='__main__': asyncio.run(main())
