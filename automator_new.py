import os
import json
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

"""
ShipStation Order Processor (Python rewrite)
-------------------------------------------
- Loads orders for HPS (storeId 427096)
- Skips excluded-tag orders (Wayfair/Public Goods)
- Applies edge-case tagging (Ruby parity, incl. PITB logic)
- Assigns weight/dimensions
- Rate shops with Ruby-style preferences
- (Stub) Assigns shipping account
- Tags processed orders

Notes:
- Uses POST /orders/addtag for tagging (works)
- Fixes rate-shopping payload keys (uses toCountry; omits carrierCode)
- Keeps prints on single lines; consistent indentation
"""

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

API_KEY = os.getenv("SHIPSTATION_V1_KEY")
API_SECRET = os.getenv("SHIPSTATION_V1_SECRET")
if not API_KEY or not API_SECRET:
    raise RuntimeError("API credentials not loaded – check your .env file (SHIPSTATION_V1_KEY / SHIPSTATION_V1_SECRET)")

BASE_URL = "https://ssapi.shipstation.com"
HEADERS = {"Content-Type": "application/json"}

# ---------------------------------------------------------------------------
# Tag constants & names
# ---------------------------------------------------------------------------
EDGE_CASE_TAG = 145681
PROCESSED_TAG = 145844
PITB_TAG = 117278  # PITB orders bypass edge-case checks unless critical shipping data is missing

TAG_NAMES = {
    151644: "Wayfair",
    147485: "Public Goods",
    126500: "BATCH #1 - Expedited",
    126428: "BATCH #10 - w/ Planter",
    112301: "BATCH #11 - w/ Accessories",
    112299: "BATCH #12 - w/ Air Plants",
    145490: "BATCH #13 - Replacement",
    112293: "BATCH #2 - 4 Inch (1)",
    112294: "BATCH #3 - 4 Inch (2+)",
    112295: "BATCH #4 - 6 Inch",
    126425: "BATCH #5 - 8-10 Inch",
    112296: "BATCH #6 - Bundle/Variety (Potted Plants)",
    112298: "BATCH #7 - 4+6 Inch",
    126426: "BATCH #8 - w/ Pre-Pot",
    126427: "BATCH #9 - w/ Cuttings",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def tag_name(tag_id: int) -> str:
    return TAG_NAMES.get(tag_id, str(tag_id))

# POST /orders/addtag — official way that works in prod
def assign_tag(order_id: int, tag_id: int) -> None:
    url = f"{BASE_URL}/orders/addtag"
    data = {"orderId": int(order_id), "tagId": int(tag_id)}
    resp = requests.post(url, headers=HEADERS, auth=(API_KEY, API_SECRET), json=data)
    print(f"POST {url} with {data}")
    print(f"Response: {resp.status_code} - {resp.text}")
    if resp.status_code == 200:
        print(f"🏷 Tag {tag_id} applied to order {order_id}")
    else:
        print(f"❌ Failed to tag order {order_id}: {resp.status_code} - {resp.text}")

# Weight / box rules
SKU_WEIGHT_MAP = {
    "4IN-PLANT": 16.0,  # oz
    "6IN-PLANT": 40.0,
    "8IN-PLANT": 64.0,
    "BUNDLE": 56.0,
}
DEFAULT_WEIGHT_OZ = 16.0
BOX_SIZES = [
    {"length": 8,  "width": 8,  "height": 8,  "max_items": 1},
    {"length": 10, "width": 8,  "height": 6,  "max_items": 2},
    {"length": 12, "width": 10, "height": 8,  "max_items": 4},
    {"length": 16, "width": 12, "height": 10, "max_items": 8},
    {"length": 20, "width": 14, "height": 12, "max_items": 16},
]

def assign_weight_and_dimensions(order: dict) -> None:
    items = order.get("items", [])
    total_weight = 0.0
    total_items = 0
    large_item_present = False

    for it in items:
        sku = (it.get("sku") or "").upper()
        qty = int(it.get("quantity") or 1)
        total_items += qty
        total_weight += SKU_WEIGHT_MAP.get(sku, DEFAULT_WEIGHT_OZ) * qty
        if sku.startswith("8IN") or sku == "BUNDLE":
            large_item_present = True

    if large_item_present:
        chosen_box = BOX_SIZES[-2]
    else:
        chosen_box = BOX_SIZES[-1]
        for b in BOX_SIZES:
            if total_items <= b["max_items"]:
                chosen_box = b
                break

    order["weight"] = {"value": total_weight, "units": "ounces"}
    order["dimensions"] = {
        "length": chosen_box["length"],
        "width": chosen_box["width"],
        "height": chosen_box["height"],
        "units": "inches",
    }
    print(f"Assigned weight {total_weight} oz and box ({chosen_box['length']}x{chosen_box['width']}x{chosen_box['height']}) to order {order.get('orderNumber')}")

# Ruby-style rate shopping
def set_shipping_service(order: dict) -> None:
    """
    Rate shop in a ShipStation-compatible way and tag edge cases if rates fail.

    Fixes the error by:
    - Querying /shipments/getrates per carrier (stamps_com, ups, fedex) with carrierCode set.
    - Merging rates and applying preferences; falling back to cheapest.
    - If no rates found, tag as EDGE CASE via POST /orders/addtag.
    """
    def _order_has_tag(o, tag_id):
        try:
            return tag_id in (o.get("tagIds") or [])
        except Exception:
            return False

    def _choose_by_keywords(rates_list, keywords):
        if not rates_list:
            return None
        cands = []
        for r in rates_list:
            sc = (r.get("serviceCode") or "").lower()
            sn = (r.get("serviceName") or "").lower()
            cc = (r.get("carrierCode") or "").lower()
            if any(k in sc or k in sn or k in cc for k in keywords):
                cands.append(r)
        return min(cands, key=lambda rr: rr.get("shipmentCost", float("inf"))) if cands else None

    # Build base shipment; we add carrierCode per request.
    base_shipment = {
        "fromPostalCode": order.get("shipFrom", {}).get("postalCode", "92821"),
        "toCountry":      order.get("shipTo", {}).get("country", "US"),
        "toPostalCode":   order.get("shipTo", {}).get("postalCode"),
        "toState":        order.get("shipTo", {}).get("state"),
        "weight":         order.get("weight", {"value": 16, "units": "ounces"}),
        "dimensions":     order.get("dimensions", {"length": 10, "width": 8, "height": 6, "units": "inches"}),
        "confirmation":   "none",
        "residential":    order.get("shipTo", {}).get("residential", False),
    }
    base_shipment = {k: v for k, v in base_shipment.items() if v is not None}

    carriers = ["stamps_com", "ups", "fedex"]
    all_rates = []
    url = f"{BASE_URL}/shipments/getrates"

    for carrier in carriers:
        payload = dict(base_shipment)
        payload["carrierCode"] = carrier
        resp = requests.post(url, headers=HEADERS, auth=(API_KEY, API_SECRET), json=payload)
        if resp.status_code == 200:
            rates = resp.json() or []
            if isinstance(rates, list):
                all_rates.extend(rates)
        else:
            print(f"⚠️ Rates error for {carrier} on {order.get('orderNumber')}: {resp.status_code} {resp.text}")

    # If we still have no rates, tag as EDGE CASE and stop
    if not all_rates:
        print(f"❌ No rates found for {order.get('orderNumber')} (after polling carriers); tagging as edge case")
        assign_tag(order["orderId"], EDGE_CASE_TAG)  # uses POST /orders/addtag under the hood
        return

    # Preference logic – similar to your Ruby approach
    all_rates.sort(key=lambda r: r.get("shipmentCost", float("inf")))

    to_country = (base_shipment.get("toCountry") or "US").upper()
    is_domestic = to_country in {"US", "USA"}
    try:
        weight_oz = float((base_shipment.get("weight") or {}).get("value") or 0.0)
    except Exception:
        weight_oz = 0.0

    expedited = _order_has_tag(order, 126500)  # expedited batch tag

    chosen = None
    if expedited:
        chosen = _choose_by_keywords(all_rates, [
            "2day", "2-day", "two day", "express", "expedited",
            "priority_overnight", "ups_2nd_day", "ups second", "fedex_2day", "fedex 2 day",
        ])

    if not chosen and is_domestic and weight_oz > 0:
        if weight_oz < 16.0:
            chosen = _choose_by_keywords(all_rates, ["first_class", "usps_first", "stamps_first_class", "ground_advantage", "ground advantage"])
        if not chosen:
            chosen = _choose_by_keywords(all_rates, [
                "usps_priority", "priority_mail", "priority mail",
                "ups_ground", "surepost",
                "fedex_ground", "home_delivery",
            ])

    if not chosen and not is_domestic:
        chosen = _choose_by_keywords(all_rates, [
            "ups_worldwide", "worldwide saver", "worldwide expedited",
            "priority_mail_international", "usps_priority_mail_international",
            "fedex_international", "international economy", "international priority",
        ])

    if not chosen:
        chosen = all_rates[0]

    order["carrierCode"] = chosen.get("carrierCode")
    order["serviceCode"] = chosen.get("serviceCode")
    print(
        f"Selected {order['carrierCode']} {order['serviceCode']} for {order.get('orderNumber')} "
        f"at ${chosen.get('shipmentCost', 0.0):.2f} (domestic={is_domestic}, weight_oz={weight_oz}, expedited={expedited})"
    )


# (Stub) choose billing account
def assign_shipping_account(order: dict) -> None:
    print(f"[stub] Would assign shipping account for order {order.get('orderNumber')}")

# ---------------------------------------------------------------------------
# Edge-case detection
# ---------------------------------------------------------------------------
NEW_PRODUCT_SKUS = set()

def get_skus(order):
    return [it['sku'] for it in order.get('items', []) if it.get('sku') and it['sku'] != 'total-discount']

def has_edge_tag(order):
    return EDGE_CASE_TAG in (order.get('tagIds') or [])

def has_processed_tag(order):
    return PROCESSED_TAG in (order.get('tagIds') or [])

def is_merged(order):
    return (order.get('advancedOptions', {}) or {}).get('mergedOrSplit', False)

def has_no_location(order):
    return (order.get('advancedOptions', {}) or {}).get('customField2') in [None, '', 'No Location']

def has_no_shipping_settings(order):
    return (
        (order.get('weight', {}) or {}).get('value', 0.0) == 0.0 or
        order.get('carrierCode') is None or
        order.get('dimensions') is None
    )

def has_new_item(order):
    return any(sku in NEW_PRODUCT_SKUS for sku in get_skus(order))

# PITB helper

def is_pitb(order):
    """Return True if order has the PITB tag (117278)."""
    return PITB_TAG in (order.get('tagIds') or [])


def mark_edge_case(order, reason="unknown"):
    order_num = order.get("orderNumber")
    assign_tag(order["orderId"], EDGE_CASE_TAG)
    print(f"   ✅ Edge case tag ({EDGE_CASE_TAG}) applied to {order_num} - {reason}")

# ---------------------------------------------------------------------------
# Fetch stores & orders
# ---------------------------------------------------------------------------
print("✅ API connection successful. Here are some store names:")
resp = requests.get(f"{BASE_URL}/stores", headers=HEADERS, auth=(API_KEY, API_SECRET))
resp.raise_for_status()
for store in resp.json():
    print(f" – {store['storeName']} (ID: {store['storeId']})")

STORE_IDS = [427096]  # HPS only
EXCLUDED_TAG_IDS = {151644, 147485}  # Wayfair, Public Goods

def fetch_all_orders(store_id: int):
    print(f"🔀 Fetching orders for store ID {store_id} …")
    orders, page = [], 1
    while True:
        params = {
            "storeId": store_id,
            "orderStatus": "awaiting_shipment",
            "pageSize": 500,
            "page": page,
        }
        r = requests.get(f"{BASE_URL}/orders", headers=HEADERS, params=params, auth=(API_KEY, API_SECRET))
        if r.status_code != 200:
            print(f" ❌ Page {page} failed: {r.text}")
            break
        payload = r.json()
        batch = payload.get("orders", [])
        orders.extend(batch)
        print(f"   • Page {page} → {len(batch)} orders")
        if page >= payload.get("pages", 1):
            break
        page += 1
        time.sleep(0.2)
    return orders

all_orders = []
for sid in STORE_IDS:
    all_orders.extend(fetch_all_orders(sid))

print(f"✅ Total orders fetched: {len(all_orders)}")

eligible_orders = [o for o in all_orders if not set(o.get("tagIds", [])) & EXCLUDED_TAG_IDS]
print(f"✅ Eligible for processing (after tag exclusions): {len(eligible_orders)}")

# ---------------------------------------------------------------------------
# Decide edge cases (PITB-aware)
# ---------------------------------------------------------------------------

def is_edge_case(order):
    order_number = order.get('orderNumber')

    # --- PITB logic (mirror Ruby) ---
    if is_pitb(order):
        weight = (order.get('weight') or {})
        dims = order.get('dimensions')
        weight_val = (weight or {}).get('value')
        missing_crit = dims is None or weight is None or weight_val in (None, 0, 0.0)
        if missing_crit:
            mark_edge_case(order, "pitb_missing_critical_shipping_data")
            return True
        print(f"⏩ {order_number}: PITB — skip edge-case checks (has shipping data)")
        return False
    # --- end PITB logic ---

    if has_edge_tag(order):
        mark_edge_case(order, "already_tagged")
        return True

    if has_processed_tag(order):
        print(f"⏩ {order_number}: Already processed")
        return False

    if is_merged(order):
        mark_edge_case(order, "merged")
        return True

    if has_no_location(order):
        mark_edge_case(order, "no_location")
        return True

    if has_no_shipping_settings(order):
        mark_edge_case(order, "missing_shipping")
        return True

    if has_new_item(order):
        mark_edge_case(order, "new_sku")
        return True

    return False

# ---------------------------------------------------------------------------
# Process orders
# ---------------------------------------------------------------------------
orders_to_process = [o for o in eligible_orders if not is_edge_case(o) and not has_processed_tag(o)]
print(f"🚀 Beginning processing of {len(orders_to_process)} orders..")

for order in orders_to_process:
    onum = order.get("orderNumber")
    print(f"🔍 Processing order {onum}")

    assign_weight_and_dimensions(order)
    set_shipping_service(order)
    assign_shipping_account(order)

    assign_tag(order["orderId"], PROCESSED_TAG)
    print(f"✅ Order {onum} fully processed.")

# ---------------------------------------------------------------------------
# (Optional) cache products & batch-tag by product type
# ---------------------------------------------------------------------------
print("🔀 Fetching product catalogue …")
product_lookup = {}
page = 1
while True:
    params = {"pageSize": 500, "page": page}
    r = requests.get(f"{BASE_URL}/products", headers=HEADERS, params=params, auth=(API_KEY, API_SECRET))
    if r.status_code != 200:
        print(f" ❌ Product page {page} failed: {r.status_code} – {r.text[:120]}")
        break
    data = r.json()
    for p in data.get("products", []):
        product_lookup[p["sku"]] = p
    if page >= data.get("pages", 1):
        break
    page += 1
print(f"✅ Cached {len(product_lookup)} products")

PRODUCT_TYPE_TO_BATCH_TAG = {
    "4IN-PLANT": 112293,
    "6IN-PLANT": 112295,
    "BUNDLE": 112296,
}

def get_primary_product_type(order):
    for item in order.get("items", []):
        sku = (item.get("sku") or "").upper()
        if sku in PRODUCT_TYPE_TO_BATCH_TAG:
            return sku
    return None

batch_groups = {}
for order in eligible_orders:
    ptype = get_primary_product_type(order)
    if not ptype:
        continue
    batch_groups.setdefault(ptype, []).append(order)

for ptype, orders in batch_groups.items():
    btag = PRODUCT_TYPE_TO_BATCH_TAG[ptype]
    for order in orders:
        assign_tag(order["orderId"], btag)
        print(f"Order {order['orderNumber']} tagged as batch {ptype}")

print("✅ Tagging complete.")
