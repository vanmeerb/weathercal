import logging
import pandas as pd

from pathlib import Path

import openmeteo_requests
import requests_cache

from retry_requests import retry
from icalendar import Calendar, Event

## adding argparse for future CLI usage, but not implemented yet
## add a --debug CLI flag, to enable DEBUG logging mode
import argparse


def wind_direction_arrow(direction_degrees: float) -> str:
    arrows = ["↑", "↗", "→", "↘", "↓", "↙", "←", "↖"]
    index = int((direction_degrees + 22.5) % 360 // 45)
    return arrows[index]


parser = argparse.ArgumentParser(
    description="Generate an iCalendar file with hourly weather forecasts for Leuven, Belgium."
)
parser.add_argument("--debug", action="store_true", help="Enable debug logging")
args = parser.parse_args()

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)5s] %(message)s",
        datefmt="%Y%m%dT%H%M%S",
    )
    logger = logging.getLogger(__name__)

    # 1. Setup the Open-Meteo API client with cache and retry capabilities
    cache_session = requests_cache.CachedSession(Path(".cache"), expire_after=3600)
    retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
    openmeteo = openmeteo_requests.Client(session=retry_session)
    logger.debug("Open-Meteo client initialised (cache: .cache, retries: 5)")

    # 2. Define Open-Meteo endpoint and configurations for Leuven, Belgium
    url = "https://api.open-meteo.com/v1/forecast"
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

    logger.info("Fetching weather data from Open-Meteo...")
    logger.debug("Request params: %s", params)
    responses = openmeteo.weather_api(url, params=params)
    response = responses[0]
    timezone = response.Timezone().decode() if isinstance(response.Timezone(), bytes) else response.Timezone()

    logger.debug(
        "Response received — coordinates: %.4f°N %.4f°E, timezone: %s",
        response.Latitude(),
        response.Longitude(),
        timezone,
    )

    # 3. Extract the hourly variables
    hourly = response.Hourly()
    temp = hourly.Variables(0).ValuesAsNumpy()
    precip = hourly.Variables(1).ValuesAsNumpy()  # Hourly precipitation in mm
    wind_spd = hourly.Variables(2).ValuesAsNumpy()
    wind_dir = hourly.Variables(3).ValuesAsNumpy()

    # Convert the API start/end/interval values into a localized hourly DatetimeIndex
    time_index = pd.date_range(
        start=pd.to_datetime(hourly.Time(), unit="s", utc=True),
        end=pd.to_datetime(hourly.TimeEnd(), unit="s", utc=True),
        freq=pd.Timedelta(seconds=hourly.Interval()),
        inclusive="left",
    ).tz_convert(timezone)
    logger.debug("Hourly time range: %s → %s (%d slots)", time_index[0], time_index[-1], len(time_index))

    # 4. Initialize the iCalendar object
    cal = Calendar()
    cal.add("prodid", "-//Leuven Weather and Rain Calendar//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Leuven Weather Forecast")  # Calendar display name

    logger.info("Generating .ics calendar file (%d events)...", len(time_index))
    # 5. Loop through hours and generate events
    for i, ts in enumerate(time_index):
        event = Event()
        wind_arrow = wind_direction_arrow(float(wind_dir[i]))

        # Dynamically change the emoji based on whether it is raining
        rain_text = f"🌧️ {precip[i]:.1f}mm" if precip[i] > 0 else "☀️ 0mm"

        # Event title format: Temperature | Rain | Wind speed | Wind direction
        summary = f"🌡️ {temp[i]:.0f}°C | 💨 {wind_arrow} {wind_spd[i]:.0f} m/s | {rain_text}"
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
            f"Wind Direction: {wind_arrow}"
        )
        event.add("description", description)

        # Unique ID mapping for Google Calendar to track overwrites
        event.add("uid", f"leuven-weather-{ts.strftime('%Y%md%H%M%S')}@myscript")

        cal.add_component(event)
        logger.debug("Event added: %s — %s", start_time.isoformat(), summary)

    # 6. Save data into an iCalendar file
    output_path = Path("leuven_weather.ics")
    output_path.write_bytes(cal.to_ical())
    logger.debug("Wrote %d bytes to %s", output_path.stat().st_size, output_path)
    logger.info("Finished! '%s' successfully created with rain details.", output_path)
