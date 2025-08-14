import os
import json
import requests
import time
from dotenv import load_dotenv
from pathlib import Path
from collections import defaultdict
import hashlib
from shipstation_client import assign_tag

"""
ShipStation Order Processor (Python rewrite)
-------------------------------------------

This script connects to ShipStation, fetches orders for the **House Plant Shop (HPS)** store (ID 427096),
filters out orders that shouldn’t be touched (e.g. Wayfair / Public Goods), and performs **batch tagging**
and **product type tagging** on the rest, based on SKU logic.

Key updates in this revision:
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* Removed the *Split Shipment* logic entirely
* Added **Batch Tagging** phase
* Added **Product Type Tagging** phase
* Custom handling for bundles and replacements
* Clean log output uses tag names, not IDs
* Re-usable tagging helpers preserved
"""

# ---------------------------------------------------------------------------
# 🏧  Environment setup
# ---------------------------------------------------------------------------

env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)

API_KEY = os.getenv("SHIPSTATION_V1_KEY")
API_SECRET = os.getenv("SHIPSTATION_V1_SECRET")

if not API_KEY or not API_SECRET:
    raise RuntimeError("API credentials not loaded – check your .env file")

BASE_URL = "https://ssapi.shipstation.com"
HEADERS  = {"Content-Type": "application/json"}

# ---------------------------------------------------------------------------
# 🍿  Helpful tag → name mapping
# ---------------------------------------------------------------------------

TAG_NAMES = {
    151644: "Wayfair",
    147485: "Public Goods",
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
    100783: "Product Type - Air Plants",
    100784: "Product Type - Potted Plants",
    112302: "Product Type - Accessories",
    124699: "Product Type - Cuttings",
    118141: "Product Type - Planter",
    100785: "Product Type - Potted Variety",
    119141: "Product Type - Pre Potted",
    111473: "Bundle",
}

def tag_name(tag_id: int) -> str:
    return TAG_NAMES.get(tag_id, str(tag_id))

# ---------------------------------------------------------------------------
# 🔍  Phase 1 – list stores
# ---------------------------------------------------------------------------

print("✅ API connection successful. Here are some store names:")
resp = requests.get(f"{BASE_URL}/stores", headers=HEADERS, auth=(API_KEY, API_SECRET))
resp.raise_for_status()
for store in resp.json():
    print(f" – {store['storeName']} (ID: {store['storeId']})")

# ---------------------------------------------------------------------------
# 📦  Phase 2 – fetch all orders
# ---------------------------------------------------------------------------

STORE_IDS = [427096]  # HPS only
EXCLUDED_TAG_IDS = {151644, 147485}

def fetch_all_orders(store_id: int):
    print(f"\n🔀 Fetching orders for store ID {store_id} …")
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
            print(f" ❌ Page {page} failed: {r.text}")
            break
        payload = r.json()
        batch = payload.get("orders", [])
        orders.extend(batch)
        print(f"   • Page {page} → {len(batch)} orders")
        if page >= payload.get("pages", 1):
            break
        page += 1
        time.sleep(0.2)
    return orders

all_orders = []
for sid in STORE_IDS:
    all_orders.extend(fetch_all_orders(sid))

print(f"\n✅ Total orders fetched: {len(all_orders)}")

eligible_orders = [o for o in all_orders if not set(o.get("tagIds", [])) & EXCLUDED_TAG_IDS]
print(f"✅ Eligible for processing (after tag exclusions): {len(eligible_orders)}")

def is_edge_case(order):
    order_number = order.get('orderNumber')

    if has_edge_tag(order):
        print(f"⚠️  {order_number}: Already marked as edge case")
        return True

    if has_processed_tag(order):
        print(f"⏩ {order_number}: Already processed")
        return False

    if is_merged(order):
        print(f"🚫 {order_number}: Merged order – edge case")
        assign_tag(order['orderId'], EDGE_CASE_TAG)
        return True

    if has_no_location(order):
        print(f"🚫 {order_number}: No location assigned – edge case")
        assign_tag(order['orderId'], EDGE_CASE_TAG)
        return True

    if has_no_shipping_settings(order):
        print(f"🚫 {order_number}: Missing shipping settings – edge case")
        assign_tag(order['orderId'], EDGE_CASE_TAG)
        return True

    if has_new_item(order):
        print(f"🚫 {order_number}: Contains new SKU – edge case")
        assign_tag(order['orderId'], EDGE_CASE_TAG)
        return True

    return False


orders_to_process = [
    order for order in eligible_orders
    if not is_edge_case(order) and not has_processed_tag(order)
]

print(f"🚀 Beginning processing of {len(orders_to_process)} orders...\n")

for order in orders_to_process:
    order_number = order.get("orderNumber")
    print(f"🔍 Processing order {order_number}")

    # 🚚 Determine cheapest shipping
    set_shipping_service(order)

    # 💳 Set appropriate billing account
    assign_shipping_account(order)

    # 🏷 Tag as processed
    assign_tag(order["orderId"], PROCESSED_TAG)

    print(f"✅ Order {order_number} fully processed.\n")

        
# ---------------------------------------------------------------------------
# 🚚  Shipping logic and account selection
# ---------------------------------------------------------------------------

def assign_shipping_account(order):
    """
    Assigns shipping account based on carrier and known tag associations.
    """
    if order.get("carrierCode") == "ups":
        # HPS UPS account
        order["advancedOptions"] = order.get("advancedOptions", {})
        order["advancedOptions"].update({
            "billToParty": "my_account",
            "billToCountryCode": "US",
            "billToAccount": "8X8Y09"
        })
    elif order.get("carrierCode") == "fedex":
        order["advancedOptions"] = order.get("advancedOptions", {})
        order["advancedOptions"].update({
            "billToParty": "my_account",
            "billToCountryCode": "US",
            "billToAccount": "696231770"
        })
    elif order.get("carrierCode") == "stamps_com":
        order["advancedOptions"] = order.get("advancedOptions", {})
        order["advancedOptions"].update({
            "billToParty": "my_account",
            "billToCountryCode": "US",
            "billToAccount": "PPCInt-01"
        })

def set_shipping_service(order):
    """
    Apply simplified shipping logic:
    - USPS First Class if weight < 16oz
    - FedEx Home Delivery for residential
    - FedEx Ground for commercial
    """
    weight = order.get("weight", {}).get("value", 0.0)
    if weight < 16:
        order.update({
            "carrierCode": "stamps_com",
            "serviceCode": "usps_first_class_mail",
            "packageCode": "package"
        })
    else:
        is_residential = order.get("shipTo", {}).get("residential", True)
        order.update({
            "carrierCode": "fedex",
            "serviceCode": "fedex_home_delivery" if is_residential else "fedex_ground",
            "packageCode": "package"
        })
    assign_shipping_account(order)


# ---------------------------------------------------------------------------
# 📦  Phase 3 – cache all products
# ---------------------------------------------------------------------------

print("\n🔀 Fetching product catalogue …")
product_lookup = {}
page = 1
while True:
    params = {"pageSize": 500, "page": page}
    r = requests.get(f"{BASE_URL}/products", headers=HEADERS, params=params, auth=(API_KEY, API_SECRET))
    if r.status_code != 200:
        print(f" ❌ Product page {page} failed: {r.status_code} – {r.text[:120]}")
        break
    data = r.json()
    for p in data.get("products", []):
        product_lookup[p["sku"]] = p
    if page >= data.get("pages", 1):
        break
    page += 1
print(f"✅ Cached {len(product_lookup)} products\n")

# ---------------------------------------------------------------------------
# 🧐  Phase 4 – tagging logic
# ---------------------------------------------------------------------------

for order in eligible_orders:
    skus = [item.get("sku") for item in order.get("items", []) if item.get("sku")]
    lower_skus = [sku.lower() for sku in skus if sku]
    tags_to_apply = set()

    carrier_code = order.get("carrierCode")
    if carrier_code and carrier_code.lower() in {"fedex", "ups"}:
        tags_to_apply.add(126500)

    for sku in lower_skus:
        if "bundle" in sku:
            tags_to_apply.add(112296)
        if "4in" in sku:
            if lower_skus.count(sku) == 1:
                tags_to_apply.add(112293)
            elif lower_skus.count(sku) >= 2:
                tags_to_apply.add(112294)
        if "6in" in sku:
            tags_to_apply.add(112295)
        if "8in" in sku or "10in" in sku:
            tags_to_apply.add(126425)
        if "cut" in sku:
            tags_to_apply.add(126427)

        product = product_lookup.get(sku)
        if product:
            pname = (product.get("name") or "").lower()
            if "air plant" in pname:
                tags_to_apply.add(100783)
            if "potted plant" in pname:
                tags_to_apply.add(100784)
            if "accessor" in pname:
                tags_to_apply.add(112302)
            if "cutting" in pname:
                tags_to_apply.add(124699)
            if "planter" in pname:
                tags_to_apply.add(118141)
            if "variety" in pname:
                tags_to_apply.add(100785)
            if "pre pot" in pname:
                tags_to_apply.add(119141)
            if "bundle" in pname:
                tags_to_apply.add(111473)

    existing = set(order.get("tagIds", []))
    for tag_id in tags_to_apply:
        if tag_id not in existing:
            print(f"🏷 Tagging {order['orderNumber']} as {tag_name(tag_id)}")
            assign_tag(order["orderId"], tag_id)

print("\n✅ Tagging complete.")


# ---------------------------------------------------------------------------
# 🧪  Edge Case Detection
# ---------------------------------------------------------------------------

EDGE_CASE_TAG = 145681
PROCESSED_TAG = 145844
NEW_PRODUCT_SKUS = set()

EDGE_CASE_REASONS = {
    "merged": "Merged order",
    "no_location": "No location assigned",
    "missing_shipping": "Missing shipping settings",
    "new_sku": "Contains new SKU",
    "already_tagged": "Already marked edge case",
    "already_processed": "Already processed"
}

def get_skus(order):
    return [item['sku'] for item in order.get('items', []) if item['sku'] != 'total-discount']

def is_light(order):
    return order.get('weight', {}).get('value', 0.0) < 16

def has_edge_tag(order):
    return EDGE_CASE_TAG in order.get('tagIds', [])

def has_processed_tag(order):
    return PROCESSED_TAG in order.get('tagIds', [])

def is_merged(order):
    return order.get('advancedOptions', {}).get('mergedOrSplit', False)

def has_no_location(order):
    return order.get('advancedOptions', {}).get('customField2') in [None, '', 'No Location']

def has_no_shipping_settings(order):
    return (
        order.get('weight', {}).get('value', 0.0) == 0.0 or
        order.get('carrierCode') is None or
        order.get('dimensions') is None
    )

def has_new_item(order):
    return any(sku in NEW_PRODUCT_SKUS for sku in get_skus(order))

def mark_edge_case(order, reason="unknown"):
    order_number = order.get("orderNumber")
    print(f"Evaluating order {order_number} → Edge case reason: {reason}")
    if EDGE_CASE_TAG not in order['tagIds']:
        assign_tag(order['orderId'], EDGE_CASE_TAG)
        print(f"   ✅ Edge case tag applied to {order_number}")
    else:
        print(f"   ⚠️ Edge case tag already exists on {order_number}")


def is_edge_case(order):
    try:
        order_number = order.get('orderNumber')

        if has_edge_tag(order):
            mark_edge_case(order, "already_tagged")
            return True

        if has_processed_tag(order):
            print(f"{order_number}: {EDGE_CASE_REASONS['already_processed']}")
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

    except Exception as e:
        print(f"Edge case check failed for {order.get('orderNumber')}: {e}")
        return True

print("\n✅ Edge case module ready.")

print("\n🔍 Evaluating orders for edge cases...")
for order in eligible_orders:
    is_edge_case(order)