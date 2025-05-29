import os
from pathlib import Path
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth
import requests
from collections import defaultdict  #  Add this line


# Load .env explicitly from project directory
env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(dotenv_path=env_path)


# Load environment variables

load_dotenv()

API_KEY = os.getenv("SHIPSTATION_API_KEY")
API_SECRET = os.getenv("SHIPSTATION_API_SECRET")


print("🔑 API_KEY:", API_KEY[:4] + "..." if API_KEY else "Not loaded")
print("🔐 API_SECRET:", API_SECRET[:4] + "..." if API_SECRET else "Not loaded")

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

# Step 1: Fetch all orders
all_ready_orders = []
for name, store_id in store_ids.items():
    print(f"📦 Fetching orders for {name} (ID {store_id})")
    all_ready_orders.extend(get_orders_by_store(store_id))

print(f"✅ Total orders fetched: {len(all_ready_orders)}")

# Step 2: Group by shipTo
grouped = defaultdict(list)
for order in all_ready_orders:
    key = tuple(sorted(order['shipTo'].items()))
    grouped[key].append(order)

duplicates = [o for group in grouped.values() if len(group) > 1 for o in group]

# Step 3: Untag incorrect ones
for order in all_ready_orders:
    if SPLIT_TAG_ID in order.get("tagIds", []) and order not in duplicates:
        print(f"🧹 Unassigning tag from {order['orderNumber']}")
        requests.post(
            f"{BASE_URL}/orders/removetag",
            auth=AUTH,
            json={"orderId": order["orderId"], "tagId": SPLIT_TAG_ID}
        )
    if "Note: Your order" in (order.get("customerNotes") or ""):
        print(f"🧹 Removing tag (has note) {order['orderNumber']}")
        requests.post(
            f"{BASE_URL}/orders/removetag",
            auth=AUTH,
            json={"orderId": order["orderId"], "tagId": SPLIT_TAG_ID}
        )

# Step 4: Tag duplicates
for order in duplicates:
    if SPLIT_TAG_ID not in order.get("tagIds", []) and "Note: Your order" not in (order.get("customerNotes") or ""):
        print(f"🏷️ Tagging {order['orderNumber']} as split")
        requests.post(
            f"{BASE_URL}/orders/assigntag",
            auth=AUTH,
            json={"orderId": order["orderId"], "tagId": SPLIT_TAG_ID}
        )
