import openmeteo_requests
import requests_cache
import pandas as pd
from pathlib import Path
from retry_requests import retry
from icalendar import Calendar, Event
import datetime

# 1. Setup the Open-Meteo API client with cache and retry capabilities
cache_session = requests_cache.CachedSession(Path(".cache"), expire_after=3600)
retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
openmeteo = openmeteo_requests.Client(session=retry_session)

# 2. Define Open-Meteo endpoint and configurations for Leuven, Belgium
url = "https://open-meteo.com"
params = {
    "latitude": 50.8792,
    "longitude": 4.7012,
    # Added "precipitation" to fetch hourly rainfall data in mm
    "hourly": ["temperature_2m", "precipitation", "wind_speed_10m", "wind_direction_10m"],
    "wind_speed_unit": "ms",
    "precipitation_unit": "mm",  # Ensures precipitation is returned in millimeters
    "timezone": "Europe/Brussels",  # Matches the local time zone of Leuven
    "forecast_days": 5,
}

print("Fetching weather data from Open-Meteo...")
responses = openmeteo.weather_api(url, params=params)
response = responses[0]

# 3. Extract the hourly variables
hourly = response.Hourly()
temp = hourly.Variables(0).ValuesAsNumpy()
precip = hourly.Variables(1).ValuesAsNumpy()  # Hourly precipitation in mm
wind_spd = hourly.Variables(2).ValuesAsNumpy()
wind_dir = hourly.Variables(3).ValuesAsNumpy()

# Convert API time integers directly to a localized pandas DatetimeIndex
time_index = pd.to_datetime(hourly.Time(), unit="s", utc=True).tz_convert(response.Timezone())

# 4. Initialize the iCalendar object
cal = Calendar()
cal.add("prodid", "-//Leuven Weather and Rain Calendar//EN")
cal.add("version", "2.0")
cal.add("x-wr-calname", "Leuven Weather Forecast")  # Calendar display name

print("Generating .ics calendar file...")
# 5. Loop through hours and generate events
for i, ts in enumerate(time_index):
    event = Event()

    # Dynamically change the emoji based on whether it is raining
    rain_text = f"🌧️ {precip[i]:.1f}mm" if precip[i] > 0 else "☀️ 0mm"

    # Event title format: Temp | Rain | Wind
    summary = f"{temp[i]:.1f}°C | {rain_text} | 💨 {wind_spd[i]:.1f}m/s"
    event.add("summary", summary)

    # Timings for the 1-hour block
    start_time = ts.to_pydatetime()
    end_time = start_time + pd.Timedelta(hours=1)
    event.add("dtstart", start_time)
    event.add("dtend", end_time)

    # Detailed text description when clicking the event
    description = (
        f"Temperature: {temp[i]:.1f}°C\n"
        f"Precipitation: {precip[i]:.1f} mm\n"
        f"Wind Speed: {wind_spd[i]:.1f} m/s\n"
        f"Wind Direction: {wind_dir[i]:.0f}°"
    )
    event.add("description", description)

    # Unique ID mapping for Google Calendar to track overwrites
    event.add("uid", f"leuven-weather-{ts.strftime('%Y%md%H%M%S')}@myscript")

    cal.add_component(event)

# 6. Save data into an iCalendar file
Path("leuven_weather.ics").write_bytes(cal.to_ical())

print("Finished! 'leuven_weather.ics' successfully created with rain details.")
