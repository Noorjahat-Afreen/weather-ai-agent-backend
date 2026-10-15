
import os
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)

API_KEY = os.environ.get("ACCUWEATHER_API", "").strip()
BASE_URL = "https://dataservice.accuweather.com"

frontend = os.environ.get("FRONTEND_ORIGIN", "").rstrip("/")

if frontend:
    CORS(app, resources={r"/api/*": {"origins": [frontend]}})


def api_get(path, params=None):
    if not API_KEY:
        raise RuntimeError("ACCUWEATHER_API is not configured")

    response = requests.get(
        BASE_URL + path,
        params={**(params or {}), "apikey": API_KEY},
        timeout=20
    )

    if response.status_code in (401, 403, 429):
        raise RuntimeError(
            f"AccuWeather returned HTTP {response.status_code}"
        )

    response.raise_for_status()
    return response.json()


def make_tips(current, forecast):
    tips = []
    temp = current.get("temperature")
    humidity = current.get("humidity")
    wind = current.get("wind_kmh") or 0

    if temp is not None:
        if temp >= 35:
            tips.append("Very hot! Drink water and avoid peak afternoon heat.")
        elif temp >= 30:
            tips.append("Warm weather. Carry water and wear light clothes.")
        elif temp <= 15:
            tips.append("Cool weather. Consider carrying a jacket.")
        else:
            tips.append("Check local conditions before outdoor activities.")

    if any((d.get("rain_chance") or 0) >= 40 for d in forecast):
        tips.append("Rain is possible. Consider carrying an umbrella.")

    if humidity is not None and humidity >= 75:
        tips.append("Humidity is high. Stay hydrated.")

    if wind >= 30:
        tips.append("Wind may be strong. Take care outdoors.")

    return tips or ["Check the forecast before travelling."]


@app.get("/")
def home():
    return jsonify({"status": "running", "agent": "Weather AI Agent"})


@app.get("/api/cities")
def cities():
    q = request.args.get("q", "").strip()

    if len(q) < 2:
        return jsonify([])

    try:
        results = api_get(
            "/locations/v1/cities/autocomplete",
            {"q": q}
        )

        return jsonify([
            {
                "name": x.get("LocalizedName", ""),
                "region": x.get("AdministrativeArea", {}).get(
                    "LocalizedName", ""
                ),
                "country": x.get("Country", {}).get(
                    "LocalizedName", ""
                ),
                "key": x.get("Key", "")
            }
            for x in results[:8]
        ])

    except (requests.RequestException, RuntimeError) as e:
        app.logger.warning("City search failed: %s", e)
        return jsonify({"error": "City suggestions unavailable."}), 502


@app.get("/api/weather")
def weather():
    city = request.args.get("city", "").strip()

    if not city:
        return jsonify({"error": "Enter a city name."}), 400

    try:
        locations = api_get(
            "/locations/v1/cities/search",
            {"q": city, "language": "en-us"}
        )

        if not locations:
            return jsonify({"error": "City not found."}), 404

        location = locations[0]
        key = location["Key"]

        conditions = api_get(
            f"/currentconditions/v1/{key}",
            {"details": "true", "language": "en-us"}
        )

        if not conditions:
            return jsonify({"error": "Current weather unavailable."}), 502

        c = conditions[0]
        temp = c.get("Temperature", {}).get("Metric", {})
        feels = c.get("RealFeelTemperature", {}).get("Metric", {})
        wind = c.get("Wind", {}).get("Speed", {}).get("Metric", {})

        current = {
            "temperature": temp.get("Value"),
            "unit": temp.get("Unit", "C"),
            "condition": c.get("WeatherText", "Unknown"),
            "icon": c.get("WeatherIcon"),
            "humidity": c.get("RelativeHumidity"),
            "feels_like": feels.get("Value"),
            "wind_kmh": wind.get("Value", 0),
            "is_day": c.get("IsDayTime")
        }

        data = api_get(
            f"/forecasts/v1/daily/5day/{key}",
            {"metric": "true", "details": "true", "language": "en-us"}
        )

        forecast = []
        for d in data.get("DailyForecasts", []):
            day = d.get("Day", {})
            night = d.get("Night", {})
            temps = d.get("Temperature", {})

            forecast.append({
                "date": d.get("Date", "")[:10],
                "min": temps.get("Minimum", {}).get("Value"),
                "max": temps.get("Maximum", {}).get("Value"),
                "condition": day.get("IconPhrase", "Forecast"),
                "icon": day.get("Icon"),
                "rain_chance": max(
                    day.get("PrecipitationProbability", 0) or 0,
                    night.get("PrecipitationProbability", 0) or 0
                )
            })

        return jsonify({
            "city": location.get("LocalizedName", city),
            "region": location.get("AdministrativeArea", {}).get(
                "LocalizedName", ""
            ),
            "country": location.get("Country", {}).get(
                "LocalizedName", ""
            ),
            "current": current,
            "forecast": forecast,
            "suggestions": make_tips(current, forecast),
            "headline": data.get("Headline", {}).get("Text", ""),
            "source": "AccuWeather"
        })

    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else 502
        app.logger.warning("Weather provider HTTP error: %s", code)
        return jsonify({"error": f"Weather provider HTTP {code}."}), 502

    except (requests.RequestException, RuntimeError) as e:
        app.logger.warning("Weather request failed: %s", e)
        return jsonify({"error": str(e)}), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
