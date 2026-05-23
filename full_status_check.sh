#!/bin/bash

echo ""
echo "PRECISION KART FULL STATUS CHECK"
echo "================================="
echo ""

# 1. FILES PRESENT
echo "1. CORE FILES"
echo "-------------"
for f in app.py generate_invoice.py generate_customs_declaration.py drive_upload.py country_router.py shopify_fulfill.py fedex_ship.py fedex_rates.py fedex_auth.py fedex_upload_docs.py ship_real_order.py box_chooser.py shipping_log.py poll_tracking.py alert_unscanned.py; do
    if [ -f "$f" ]; then
        size=$(wc -c < "$f")
        printf "  OK   %-40s %8d bytes\n" "$f" "$size"
    else
        printf "  MISS %-40s\n" "$f"
    fi
done

# 2. APP.PY WIRING
echo ""
echo "2. APP.PY WIRING"
echo "----------------"
customs_lines=$(grep -c "customs" app.py)
ship_lines=$(grep -c "ship_order\|create_shipment" app.py)
dry_run=$(grep -c "FEDEX_DRY_RUN" app.py)
echo "  Customs declaration wired: $customs_lines references"
echo "  Ship API wired:            $ship_lines references"
echo "  FEDEX_DRY_RUN handling:    $dry_run references"

# 3. ENV VARS
echo ""
echo "3. LOCAL ENV VARS"
echo "-----------------"
for var in SHOPIFY_STORE_DOMAIN SHOPIFY_CLIENT_ID SHOPIFY_CLIENT_SECRET SHOPIFY_WEBHOOK_SECRET GOOGLE_DRIVE_FOLDER_ID GOOGLE_DRIVE_CUSTOMS_FOLDER_ID GOOGLE_OAUTH_CLIENT_JSON GOOGLE_TOKEN_JSON FEDEX_CLIENT_ID FEDEX_CLIENT_SECRET FEDEX_ACCOUNT_NUMBER FEDEX_ENVIRONMENT FEDEX_TRACK_CLIENT_ID FEDEX_TRACK_CLIENT_SECRET FEDEX_TRACK_ACCOUNT_NUMBER SHIPPING_LOG_SHEET_ID FEDEX_DRY_RUN; do
    if grep -q "^$var=" .env 2>/dev/null; then
        echo "  OK   $var"
    else
        echo "  MISS $var"
    fi
done

# 4. ACCOUNT NUMBER CHECK
echo ""
echo "4. FEDEX ACCOUNT NUMBER"
echo "-----------------------"
account=$(grep "^FEDEX_ACCOUNT_NUMBER=" .env | cut -d'=' -f2)
echo "  Local .env: $account"
if [ "$account" = "207751841" ]; then
    echo "  STATUS: Correct production account"
elif [ "$account" = "802255209" ]; then
    echo "  WARNING: Still using old sandbox account number"
else
    echo "  UNKNOWN: Verify against FedEx portal"
fi

# 5. ENVIRONMENT CHECK
echo ""
echo "5. FEDEX ENVIRONMENT"
echo "--------------------"
env=$(grep "^FEDEX_ENVIRONMENT=" .env | cut -d'=' -f2)
echo "  Local: $env"

# 6. SHIPPER ADDRESS
echo ""
echo "6. SHIPPER ADDRESS IN ship_real_order.py"
echo "----------------------------------------"
grep -A 14 '^SHIPPER = {' ship_real_order.py | head -16

# 7. GIT STATUS
echo ""
echo "7. GIT STATUS"
echo "-------------"
local_commit=$(git rev-parse HEAD)
remote_commit=$(git ls-remote origin HEAD 2>/dev/null | awk '{print $1}')
echo "  Local HEAD:  $local_commit"
echo "  Remote HEAD: $remote_commit"
if [ "$local_commit" = "$remote_commit" ]; then
    echo "  STATUS: In sync"
else
    echo "  WARNING: Out of sync - need to push"
fi
echo ""
echo "  Last 5 commits:"
git log --oneline -5 | sed 's/^/    /'

# 8. WORKING TREE
echo ""
echo "8. WORKING TREE"
echo "---------------"
git status --short
if [ -z "$(git status --short)" ]; then
    echo "  Clean (nothing uncommitted)"
fi

# 9. RAILWAY REACHABLE
echo ""
echo "9. RAILWAY ENDPOINT"
echo "-------------------"
railway_status=$(curl -s -o /dev/null -w "%{http_code}" https://web-production-bc61b.up.railway.app/ 2>/dev/null)
echo "  HTTPS status: $railway_status"

# 10. RATES API CHECK (live test)
echo ""
echo "10. LIVE PRODUCTION RATES API TEST"
echo "----------------------------------"
python3 -c "
import os, sys
from dotenv import load_dotenv
load_dotenv('.env')

from fedex_rates import get_rates
try:
    rates = get_rates({
        'shipper': {'contact': {'personName':'Precision Kart','phoneNumber':'+447000000000','companyName':'Precision Kart','emailAddress':'info@precisionkart.co.uk'},'address':{'streetLines':['Hendall Gate Farm'],'city':'Uckfield','stateOrProvinceCode':'GB','postalCode':'TN225LX','countryCode':'GB'}},
        'recipient': {'contact': {'personName':'Test','phoneNumber':'+12125551234'},'address':{'streetLines':['123 Main St'],'city':'New York','stateOrProvinceCode':'NY','postalCode':'10001','countryCode':'US'}},
        'package': {'weight_kg':2.0,'length_cm':30,'width_cm':20,'height_cm':15,'currency':'GBP','declared_value':150.0},
        'line_items': [{'description':'TEST','hs_code':'87089935','country_of_origin':'GB','unit_value':150.0,'quantity':1,'currency':'GBP','weight_kg':2.0}]
    })
    if rates:
        cheapest = rates[0]
        print(f'  STATUS: Working - cheapest = {cheapest.get(\"service_name\")} @ {cheapest.get(\"price\")} {cheapest.get(\"currency\")}')
    else:
        print('  STATUS: No rates returned')
except Exception as e:
    print(f'  STATUS: Failed - {str(e)[:120]}')
" 2>&1 | grep -v "Warning\|warn(" | grep -v "^$" | head -3

# 11. SHIP API SANITY CHECK
echo ""
echo "11. ship_real_order.py STRUCTURE"
echo "--------------------------------"
grep "^def " ship_real_order.py | sed 's/^/  /'

echo ""
echo "STATUS CHECK COMPLETE"
echo "====================="
