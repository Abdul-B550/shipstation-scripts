import os
import requests
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth

load_dotenv()  # Load credentials from .env file

# V1 Credentials
API_KEY = os.getenv("SHIPSTATION_V1_KEY")
API_SECRET = os.getenv("SHIPSTATION_V1_SECRET")
BASE_URL = "https://ssapi.shipstation.com"

def get_orders(store_id, order_status="awaiting_shipment", page_size=500):
    """Fetches all orders for a given store with optional status"""
    all_orders = []
    current_page = 1

    while True:
        url = f"{BASE_URL}/orders"
        params = {
            "storeId": store_id,
            "orderStatus": order_status,
            "pageSize": page_size,
            "page": current_page
        }

        response = requests.get(url, auth=HTTPBasicAuth(API_KEY, API_SECRET), params=params)

        if response.status_code != 200:
            raise Exception(f"Error fetching orders: {response.status_code} - {response.text}")

        data = response.json()
        all_orders.extend(data.get("orders", []))

        if current_page >= data.get("pages", 1):
            break
        current_page += 1

    return all_orders
