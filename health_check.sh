#!/bin/bash

echo ""
echo "PRECISION KART AUTOMATION HEALTH CHECK"
echo "======================================"
echo ""

echo "1. LOCAL FILES"
echo "--------------"
for f in app.py generate_customs_declaration.py generate_invoice.py drive_upload.py country_router.py shopify_fulfill.py fedex_ship.py fedex_rates.py fedex_auth.py check_metafields.py; do
    if [ -f "$f" ]; then
        size=$(wc -c < "$f")
        echo "  OK  $f ($size bytes)"
    else
        echo "  MISSING  $f"
    fi
done

echo ""
echo "2. APP.PY CUSTOMS WIRING"
echo "------------------------"
customs_lines=$(grep -c "customs" app.py)
echo "  Customs references in app.py: $customs_lines"
if [ "$customs_lines" -gt 10 ]; then
    echo "  OK  Customs auto-generation wired in"
else
    echo "  WARNING  Customs wiring incomplete"
fi

echo ""
echo "3. LOCAL ENV VARS"
echo "-----------------"
for var in SHOPIFY_STORE_DOMAIN SHOPIFY_CLIENT_ID SHOPIFY_CLIENT_SECRET SHOPIFY_WEBHOOK_SECRET GOOGLE_DRIVE_FOLDER_ID GOOGLE_DRIVE_CUSTOMS_FOLDER_ID FEDEX_CLIENT_ID FEDEX_CLIENT_SECRET FEDEX_ACCOUNT_NUMBER FEDEX_TRACK_CLIENT_ID; do
    if grep -q "^$var=" .env 2>/dev/null; then
        echo "  OK  $var"
    else
        echo "  MISSING  $var"
    fi
done

echo ""
echo "4. GIT STATUS"
echo "-------------"
local_commit=$(git rev-parse HEAD)
remote_commit=$(git ls-remote origin HEAD 2>/dev/null | awk '{print $1}')
echo "  Local HEAD:  $local_commit"
echo "  Remote HEAD: $remote_commit"
if [ "$local_commit" = "$remote_commit" ]; then
    echo "  OK  Local and remote in sync"
else
    echo "  WARNING  Local and remote out of sync"
fi
echo ""
echo "  Last 3 commits:"
git log --oneline -3 | sed 's/^/    /'

echo ""
echo "5. RAILWAY ENDPOINT REACHABLE"
echo "-----------------------------"
railway_status=$(curl -s -o /dev/null -w "%{http_code}" https://web-production-bc61b.up.railway.app/ 2>/dev/null)
echo "  HTTPS status code: $railway_status"
if [ "$railway_status" = "200" ] || [ "$railway_status" = "404" ] || [ "$railway_status" = "405" ]; then
    echo "  OK  Railway is reachable"
else
    echo "  WARNING  Railway returned: $railway_status"
fi

echo ""
echo "6. LIVE CONNECTIVITY TEST (Shopify + Drive)"
echo "-------------------------------------------"
echo "  Generating customs PDF for order 04125-SHP..."
python generate_customs_declaration.py 04125-SHP 2>&1 | grep -E "OK|Uploaded|ERROR|WARNING|Folder:" | sed 's/^/  /'

echo ""
echo "7. RECENT US ORDERS"
echo "-------------------"
python audit_recent_orders.py 2>/dev/null | grep -E "US|invoice" | sed 's/^/  /' | head -10

echo ""
echo "HEALTH CHECK COMPLETE"
echo "====================="
