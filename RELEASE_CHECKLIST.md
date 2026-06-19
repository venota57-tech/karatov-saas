
## RC1.6.1 checks
- [ ] `/sync/backfill-marketplace-answers` returns updated counts.
- [ ] `/ops/product-summary?platform=ALL` returns `total`, `offset`, `limit`, `items`.
- [ ] `/ops/product-summary?platform=ALL&limit=0` does not cap products at 500.
- [ ] `/operations?platform=ALL&limit=20` returns paginated payload.
- [ ] Operations unsupported types are `not_connected`, not silent zero.
- [ ] Frontend counters remain non-zero after release.
