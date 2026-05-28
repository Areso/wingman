from pathlib import Path
import requests


secret_path = Path("/Users/areso/.wingman/weather")

try:
    api_key = secret_path.read_text().strip()
except FileNotFoundError:
    print(f"Error: File not found {secret_path}")
    exit(1)

url = f"https://api.openweathermap.org/data/2.5/forecast?q=Limassol,cy&APPID={api_key}&units=metric"

try:
    response = requests.get(url)
    response.raise_for_status()  # Fail if Code is not 200
    data = response.json()
except requests.exceptions.RequestException as e:
    print(f"API returns error: {e}")
    exit(1)

forecast_list = data.get("list", [])

forecast = ""

for item in forecast_list[:6]:
    dt_txt = item.get("dt_txt")
    temp = item.get("main", {}).get("temp")
    weather_list = item.get("weather", [])
    weather_main = weather_list[0].get("main") if weather_list else "No data"
    forecast_3h = f"{dt_txt} | Temp: {temp}°C | Weather: {weather_main}"
    if forecast == "":
        forecast += forecast_3h
    else:
        forecast += "\n"+forecast_3h

print(forecast)