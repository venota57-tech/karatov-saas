import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .database import Base, engine, run_lightweight_migrations
from .routes import reviews, questions, sync, ozon_sync, summary, analytics, reports, settings as settings_routes, autopublish_settings, fbo_booking
from .services.sync_service import wb_auto_sync_loop
from .services.ozon_sync_service import ozon_auto_sync_loop
import asyncio
from app.engine.engine import engine_loop

Base.metadata.create_all(bind=engine)
run_lightweight_migrations()

app = FastAPI(title='KARATOV Marketplace CX Hub', version='3.1.0')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['http://localhost:5173', 'http://127.0.0.1:5173'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(sync.router)
app.include_router(ozon_sync.router)
app.include_router(reviews.router)
app.include_router(questions.router)
app.include_router(summary.router)
app.include_router(analytics.router)
app.include_router(settings_routes.router)
app.include_router(autopublish_settings.router)
app.include_router(reports.router)
app.include_router(fbo_booking.router)

@app.on_event('startup')
async def startup_event():
    asyncio.create_task(wb_auto_sync_loop())
    asyncio.create_task(ozon_auto_sync_loop())

    try:
        from .services.autopublish_service import autopublish_loop
        asyncio.create_task(autopublish_loop())
    except Exception as e:
        print(f'[autopublish] failed to start: {e}')

    try:
        from .services.fbo_booking_service import fbo_booking_loop
        asyncio.create_task(fbo_booking_loop())
    except Exception as e:
        print(f'[fbo-booking] failed to start: {e}')

@app.get('/health')
def health():
    return {'status': 'ok'}
    @app.on_event("startup")
async def start_engine():
    asyncio.create_task(engine_loop())
