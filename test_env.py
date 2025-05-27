from dotenv import load_dotenv
import os

load_dotenv()  # Make sure this is called before reading variables

print("V1 Key:", os.getenv("SHIPSTATION_V1_KEY"))
print("V1 Secret:", os.getenv("SHIPSTATION_V1_SECRET"))
print("V2 Key:", os.getenv("SHIPSTATION_V2_KEY"))

