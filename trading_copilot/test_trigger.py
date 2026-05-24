import requests
print("Triggering Option Map...")
resp = requests.post("http://127.0.0.1:8000/api/map-option-tokens")
print(resp.json())
