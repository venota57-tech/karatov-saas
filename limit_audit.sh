#!/bin/bash

echo "===== FRONTEND LIMITS ====="
grep -R -n "limit=[0-9]\+\|limit: [0-9]\+\|limit=[{]\|slice(0,\|\.slice(0" backend/frontend/src backend/frontend 2>/dev/null

echo ""
echo "===== BACKEND ROUTE LIMITS ====="
grep -R -n "limit: int =\|limit =\|\.limit(\|min(max(limit\|offset\|page_size\|per_page" backend/app/routes backend/app/services 2>/dev/null

echo ""
echo "===== HARD NUMERIC CAPS ====="
grep -R -n "1000\|500\|2000\|100\|limit=100\|limit=500\|limit=1000\|limit=2000" backend/app backend/frontend/src 2>/dev/null

echo ""
echo "===== SYNC TAKE / PAGES ====="
grep -R -n "sync_take\|pages_per_block_run\|max_pages\|take =" backend/app backend/services 2>/dev/null

echo ""
echo "===== PRODUCT / OPERATIONS / RETURNS ====="
grep -R -n "product-summary\|operations\|returns\|возврат\|return\|limit" backend/app/routes backend/frontend/src/main.jsx 2>/dev/null
