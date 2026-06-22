
## RC1.6.1 checks
- [ ] `/sync/backfill-marketplace-answers` returns updated counts.
- [ ] `/ops/product-summary?platform=ALL` returns `total`, `offset`, `limit`, `items`.
- [ ] `/ops/product-summary?platform=ALL&limit=0` does not cap products at 500.
- [ ] `/operations?platform=ALL&limit=20` returns paginated payload.
- [ ] Operations unsupported types are `not_connected`, not silent zero.
- [ ] Frontend counters remain non-zero after release.

## RC1.6.2 release checks
- [ ] `/system/dashboard` returns non-zero reviews/questions/products when DB has data.
- [ ] Control Tower does not become zero if `/reviews` or `/questions` fail.
- [ ] Sidebar counters use server dashboard counts.
- [ ] `/operations/sync?platform=ALL` returns quickly and does not hang the browser.
- [ ] `/operations/sync/status` shows running/completed/error state.
- [ ] `/ops/product-summary?platform=ALL&limit=0` returns all grouped products with total.
- [ ] `python3 -m compileall backend/app` passes.
- [ ] `npm run build` passes.
- [ ] `import app.main` passes.

## RC1.6.5 Web/Worker Foundation
- FastAPI lifespan must not start WB/Ozon/autopublish/booking/dashboard infinite loops.
- /health must stay DB-free.
- /system/status and /system/diagnostics must stay lightweight.
- /system/dashboard returns real fast DB counters and never starts sync jobs.
- Heavy WB/Ozon/Operations/Product Summary refresh must run through worker/queue.
- Worker command: python -m app.worker.

## RC1.6.5 Data Restore
- Restored priority /reviews and /questions routes before included routers.
- Fixed dashboard product count to use real reviews/questions product keys.
- Raised Product Summary frontend load from 500 to 5000 for current full catalog visibility.
- Fixed startup dashboard request that referenced requestedPlatform outside refreshAll.

## RC1.6.5 Data Restore - no false ceilings
- Restored priority reviews/questions endpoints with correct platform=ALL behavior.
- Product Summary UI now loads by server-reported total instead of fixed 500/5000 ceilings.
- Dashboard product count now uses the same reviews/questions source as Product Summary.
- Removed stale dashboard-count preservation when switching marketplace tabs.
