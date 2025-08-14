import os
from pathlib import Path
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth
import requests
from collections import defaultdict

# Load .env explicitly from project directory
env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(dotenv_path=env_path)

API_KEY = os.getenv("SHIPSTATION_API_KEY")
API_SECRET = os.getenv("SHIPSTATION_API_SECRET")

print("API_KEY:", API_KEY[:4] + "..." if API_KEY else "Not loaded")
print("API_SECRET:", API_SECRET[:4] + "..." if API_SECRET else "Not loaded")

if not API_KEY or not API_SECRET:
    raise Exception("API credentials not loaded. Check your .env file.")

BASE_URL = "https://ssapi.shipstation.com"
AUTH = HTTPBasicAuth(API_KEY, API_SECRET)
HEADERS = {"Content-Type": "application/json"}

# Store IDs
store_ids = {
    "HPD": 427093,
    "HPS": 427096
}

SPLIT_TAG_ID = 142954

def get_orders_by_store(store_id):
    orders = []
    page = 1
    while True:
        response = requests.get(
            f"{BASE_URL}/orders",
            headers=HEADERS,
            auth=AUTH,
            params={
                "orderStatus": "awaiting_shipment",
                "storeId": store_id,
                "pageSize": 500,
                "page": page
            }
        )

        try:
            data = response.json()
        except Exception as e:
            print("Error parsing JSON:", e)
            print("Raw response text:")
            print(response.text)
            break

        if "orders" not in data:
            print("Unexpected response:", data)
            break

        orders.extend(data["orders"])
        if page >= data.get("pages", 1):
            break
        page += 1
    return orders


# New ShipStation API version to assign a tag using orders/createorder endpoint
def assign_order_tag(order_id, tag_id):
    """
    Assigns a tag to an order via ShipStation API (works by updating tagIds).
    """
    # Fetch the current order object
    url_get = f"{BASE_URL}/orders/{order_id}"
    resp_get = requests.get(url_get, headers=HEADERS, auth=(API_KEY, API_SECRET))
    if resp_get.status_code != 200:
        print(f"❌ Failed to fetch order {order_id}: {resp_get.status_code} - {resp_get.text}")
        return resp_get
    order = resp_get.json()
    tag_ids = set(order.get('tagIds', []))
    tag_ids.add(tag_id)
    minimal_order = {
        "orderId": order['orderId'],
        "tagIds": list(tag_ids),
    }
    url_update = f"{BASE_URL}/orders/createorder"
    resp_update = requests.put(url_update, headers=HEADERS, auth=(API_KEY, API_SECRET), json=minimal_order)
    if resp_update.status_code == 200:
        print(f"🏷 Tag {tag_id} applied to order {order_id}")
    else:
        print(f"❌ Failed to tag order {order_id}: {resp_update.status_code} - {resp_update.text}")
    return resp_update

def remove_order_tag(order_id, tag_id):
    url = f"{BASE_URL}/orders/removetag"
    payload = {
        "orderId": order_id,
        "tagId": tag_id
    }
    response = requests.post(url, headers=HEADERS, auth=AUTH, json=payload)
    return response

# Step 1: Fetch all orders
all_ready_orders = []
for name, store_id in store_ids.items():
    print(f"📦 Fetching orders for {name} (ID {store_id})")
    all_ready_orders.extend(get_orders_by_store(store_id))

print(f"Total orders fetched: {len(all_ready_orders)}")

# Step 2: Group by shipTo
print("\n🔍 Identifying duplicates by shipping address...")
grouped = defaultdict(list)
for order in all_ready_orders:
    key = tuple(sorted(order['shipTo'].items()))
    grouped[key].append(order)

duplicates = [o for group in grouped.values() if len(group) > 1 for o in group]
print(f"🔁 Found {len(duplicates)} orders with duplicate shipping addresses")

# Step 3: Untag incorrect ones
for order in all_ready_orders:
    current_tags = order.get("tagIds", [])
    if SPLIT_TAG_ID in current_tags and order not in duplicates:
        print(f"🪩 Unassigning tag from {order['orderNumber']}")
        resp = remove_order_tag(order['orderId'], SPLIT_TAG_ID)
        if resp.status_code != 200:
            print(f"❌ Failed to unassign tag from order {order['orderNumber']}: {resp.status_code} - {resp.text}")
        else:
            print(f"✅ Unassigned tag from order {order['orderNumber']}")

    if "Note: Your order" in (order.get("customerNotes") or "") and SPLIT_TAG_ID in current_tags:
        print(f"🪩 Removing tag (has note) {order['orderNumber']}")
        resp = remove_order_tag(order['orderId'], SPLIT_TAG_ID)
        if resp.status_code != 200:
            print(f"❌ Failed to unassign tag from order {order['orderNumber']}: {resp.status_code} - {resp.text}")
        else:
            print(f"✅ Unassigned tag from order {order['orderNumber']}")

# Step 4: Tag duplicates
for order in duplicates:
    current_tags = order.get("tagIds", [])
    if SPLIT_TAG_ID not in current_tags and "Note: Your order" not in (order.get("customerNotes") or ""):
        print(f"🏷 Tagging {order['orderNumber']} as Split Shipment")
        resp = assign_order_tag(order['orderId'], SPLIT_TAG_ID)
        if resp.status_code != 200:
            print(f"❌ Failed to tag order {order['orderNumber']}: {resp.status_code} - {resp.text}")
        else:
            print(f"✅ Tagged order {order['orderNumber']} successfully.")
