import os
import json
import requests
import time
from dotenv import load_dotenv
from pathlib import Path
from collections import defaultdict
import hashlib
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
# 🚚  STUB: Set Shipping Service
# ---------------------------------------------------------------------------
def set_shipping_service(order):
    """
    Fetches available rates via ShipStation API and sets the cheapest one.
    """
    shipment = {
        "carrierCode": None,  # All rates
        "fromPostalCode": order.get("shipFrom", {}).get("postalCode", "92821"),  # Default warehouse ZIP if missing
        "toCountryCode": order.get("shipTo", {}).get("country", "US"),
        "toPostalCode": order.get("shipTo", {}).get("postalCode"),
        "toState": order.get("shipTo", {}).get("state"),
        "weight": order.get("weight", {"value": 16, "units": "ounces"}),
        "dimensions": order.get("dimensions", {"length": 10, "width": 8, "height": 6, "units": "inches"}),
        "confirmation": "none",
        "residential": order.get("shipTo", {}).get("residential", False),
    }
    url = f"{BASE_URL}/shipments/getrates"
    resp = requests.post(url, headers=HEADERS, auth=(API_KEY, API_SECRET), json=shipment)
    if resp.status_code != 200:
        print(f"❌ Failed to fetch rates for {order.get('orderNumber')}: {resp.text}")
        return

    rates = resp.json()
    if not rates:
        print(f"❌ No rates found for {order.get('orderNumber')}")
        return

    # Pick the cheapest available rate
    best_rate = min(rates, key=lambda r: r['shipmentCost'])
    order['carrierCode'] = best_rate['carrierCode']
    order['serviceCode'] = best_rate['serviceCode']

    print(f"Selected {best_rate['carrierCode']} {best_rate['serviceCode']} for {order.get('orderNumber')} at ${best_rate['shipmentCost']:.2f}")
# ---------------------------------------------------------------------------

# 🚚  STUB: Assign Shipping Account
# ---------------------------------------------------------------------------
def assign_shipping_account(order):
    """
    Placeholder for assigning shipping account.
    Implement your logic here for selecting/assigning the right account.
    """
    print(f"[stub] Would assign shipping account for order {order.get('orderNumber')}")
    # Actual implementation would update the order or assign an account.


# ---------------------------------------------------------------------------
# 🏷️  STUB: Assign Tag to Order
# ---------------------------------------------------------------------------
def assign_tag(order_id, tag_id):
    """
    Assigns a tag to an order via ShipStation API using the correct POST /orders/addtag endpoint.
    """
    url = f"{BASE_URL}/orders/addtag"
    payload = {
        "orderId": int(order_id),  # Ensure numeric type
        "tagId": int(tag_id)
    }
    resp = requests.post(url, headers=HEADERS, auth=(API_KEY, API_SECRET), json=payload)
    print(f"POST {url} with {payload}")
    print(f"Response: {resp.status_code} - {resp.text}")
    if resp.status_code == 200:
        print(f"🏷 Tag {tag_id} applied to order {order_id}")
    else:
        print(f"❌ Failed to tag order {order_id}: {resp.status_code} - {resp.text}")

# ---------------------------------------------------------------------------


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
# 🧪  Edge Case Detection Setup
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 📦  Weight and Dimension Assignment Logic
# ---------------------------------------------------------------------------

# Example SKU weight mapping
SKU_WEIGHT_MAP = {
    "4IN-PLANT": 16.0,  # 16 oz (1 lb)
    "6IN-PLANT": 40.0,  # 40 oz (2.5 lb)
    "8IN-PLANT": 64.0,  # 64 oz (4 lb)
    "BUNDLE": 56.0,     # 56 oz (3.5 lb)
    # Add more SKUs as needed...
}
DEFAULT_WEIGHT = 16.0  # in ounces (1 lb)

# Box sizes, from smallest to largest, with max_items
BOX_SIZES = [
    {"length": 8, "width": 8, "height": 8, "max_items": 1},
    {"length": 10, "width": 8, "height": 6, "max_items": 2},
    {"length": 12, "width": 10, "height": 8, "max_items": 4},
    {"length": 16, "width": 12, "height": 10, "max_items": 8},
    {"length": 20, "width": 14, "height": 12, "max_items": 16},
]


def assign_weight_and_dimensions(order):
    items = order.get('items', [])
    total_weight = 0
    total_items = 0
    large_item_present = False

    for item in items:
        sku = item.get('sku', '').upper()
        qty = item.get('quantity', 1)
        total_items += qty

        # Use your weight lookup (or default)
        weight = SKU_WEIGHT_MAP.get(sku, DEFAULT_WEIGHT)
        total_weight += weight * qty

        # Example: If an 8IN-PLANT or BUNDLE is present, mark as large
        if sku.startswith("8IN") or sku == "BUNDLE":
            large_item_present = True

    # Decide on a box
    if large_item_present:
        # Force a bigger box
        chosen_box = BOX_SIZES[-2]  # Second biggest
    else:
        # Find the smallest box that fits
        chosen_box = BOX_SIZES[-1]  # Default to largest
        for box in BOX_SIZES:
            if total_items <= box["max_items"]:
                chosen_box = box
                break

    order['weight'] = {'value': total_weight, 'units': 'ounces'}
    order['dimensions'] = {
        'length': chosen_box['length'],
        'width': chosen_box['width'],
        'height': chosen_box['height'],
        'units': 'inches'
    }

    print(f"Assigned weight {total_weight} oz and box ({chosen_box['length']}x{chosen_box['width']}x{chosen_box['height']}) to order {order.get('orderNumber')}")

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
    """
    Tag the order with the edge case tag (145681), even if already tagged, and log the reason.
    """
    order_number = order.get("orderNumber")
    assign_tag(order['orderId'], EDGE_CASE_TAG)
    print(f"   ✅ Edge case tag ({EDGE_CASE_TAG}) applied to {order_number} - {reason}")


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

    # 📦 Assign weight and dimensions FIRST
    assign_weight_and_dimensions(order)

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
orders_to_process = [
    order for order in eligible_orders
    if not is_edge_case(order) and not has_processed_tag(order)
]

print(f"🚀 Beginning processing of {len(orders_to_process)} orders...\n")

for order in orders_to_process:
    order_number = order.get("orderNumber")
    print(f"🔍 Processing order {order_number}")

    # 📦 Assign weight and dimensions FIRST
    assign_weight_and_dimensions(order)

    # 🚚 Determine cheapest shipping
    set_shipping_service(order)


    # 💳 Set appropriate billing account
    assign_shipping_account(order)

    # 🏷 Tag as processed
    assign_tag(order["orderId"], PROCESSED_TAG)

    print(f"✅ Order {order_number} fully processed.\n")
    


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
# 🧐  Phase 4 – tagging logic (Product-In-Type Batch grouping & real tag assignment)
# ---------------------------------------------------------------------------

# Map SKUs to batch tag IDs (expand as needed)
PRODUCT_TYPE_TO_BATCH_TAG = {
    "4IN-PLANT": 112293,   # Batch #2 - 4 Inch (1)
    "6IN-PLANT": 112295,   # Batch #4 - 6 Inch
    "BUNDLE":    112296,   # Batch #6 - Bundle/Variety
    # Add more as needed...
}

def get_primary_product_type(order):
    for item in order.get("items", []):
        sku = item.get("sku", "").upper()
        if sku in PRODUCT_TYPE_TO_BATCH_TAG:
            return sku
    return None

# Group and batch-tag orders by product type
batch_groups = {}
for order in eligible_orders:
    product_type = get_primary_product_type(order)
    if not product_type:
        continue
    batch_groups.setdefault(product_type, []).append(order)

for product_type, orders in batch_groups.items():
    batch_tag_id = PRODUCT_TYPE_TO_BATCH_TAG[product_type]
    for order in orders:
        assign_tag(order["orderId"], batch_tag_id)
        print(f"Order {order['orderNumber']} tagged as batch {product_type}")

print("✅ Tagging complete.")

