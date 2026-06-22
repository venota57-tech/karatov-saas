
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
