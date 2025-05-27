from api.shipstation_client import get_orders
import os
from dotenv import load_dotenv

load_dotenv()

# Excluded tag IDs (e.g., Wayfair, Public Goods)
EXCLUDED_TAG_IDS = {151644, 147485}  # Use a set for fast lookup

def filter_orders(orders):
    """Remove orders that have unwanted tags"""
    filtered = []
    for order in orders:
        tag_ids = order.get("tagIds", [])
        if any(tag in EXCLUDED_TAG_IDS for tag in tag_ids):
            continue  # skip excluded orders
        filtered.append(order)
    return filtered

if __name__ == "__main__":
    store_ids = [427096]  # Your target store
    all_ready_orders = []

    for store_id in store_ids:
        print(f"Fetching orders for store {store_id}...")
        orders = get_orders(store_id)
        print(f"Fetched {len(orders)} orders.")

        filtered_orders = filter_orders(orders)
        print(f"{len(filtered_orders)} orders remaining after filtering excluded tags.")

        all_ready_orders.extend(filtered_orders)

    print(f"\n✅ Final Order Count: {len(all_ready_orders)}")
