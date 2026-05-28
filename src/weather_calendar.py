import logging
import argparse
import pandas as pd
from pathlib import Path
from typing import Any, cast

import openmeteo_requests
import requests_cache

from retry_requests import retry
from icalendar import Calendar, Event

PARAMS = {
    "latitude": 50.8792,
    "longitude": 4.7012,
    # Added "precipitation" to fetch hourly rainfall data in mm
    "hourly": [
        "temperature_2m",
        "precipitation",
        "wind_speed_10m",
        "wind_direction_10m",
    ],
    "wind_speed_unit": "ms",
    "precipitation_unit": "mm",  # Ensures precipitation is returned in millimeters
    "timezone": "Europe/Brussels",  # Matches the local time zone of Leuven
    "forecast_days": 5,
}


def wind_direction_arrow(direction_degrees: float) -> str:
    arrows = ["↑", "↗", "→", "↘", "↓", "↙", "←", "↖"]
    index = int((direction_degrees + 22.5) % 360 // 45)
    return arrows[index]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate an iCalendar file with hourly weather forecasts for Leuven, Belgium."
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)5s] %(message)s",
        datefmt="%Y%m%dT%H%M%S",
    )
    logger = logging.getLogger(__name__)

    # 1. Setup the Open-Meteo API client with cache and retry capabilities
    cache_session = requests_cache.CachedSession(Path(".cache"), expire_after=3600)
    retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
    openmeteo = openmeteo_requests.Client(session=cast(Any, retry_session))
    logger.debug("Open-Meteo client initialised (cache: .cache, retries: 5)")

    # 2. Define Open-Meteo endpoint and configurations for Leuven, Belgium
    url = "https://api.open-meteo.com/v1/forecast"

    logger.info("Fetching weather data from Open-Meteo...")
    logger.debug("Request params: %s", PARAMS)
    responses = openmeteo.weather_api(url, params=PARAMS)
    if not responses:
        raise RuntimeError("Open-Meteo returned no forecast responses.")

    response = responses[0]
    timezone_raw = response.Timezone()
    if timezone_raw is None:
        raise RuntimeError("Open-Meteo response did not include a timezone.")
    timezone = timezone_raw.decode() if isinstance(timezone_raw, bytes) else timezone_raw

    logger.debug(
        "Response received — coordinates: %.4f°N %.4f°E, timezone: %s",
        response.Latitude(),
        response.Longitude(),
        timezone,
    )

    # 3. Extract the hourly variables
    hourly = response.Hourly()
    if hourly is None:
        raise RuntimeError("Open-Meteo response did not include hourly forecast data.")
    hourly_data = hourly

    def hourly_values(variable_index: int):
        variable = hourly_data.Variables(variable_index)
        if variable is None:
            raise RuntimeError(f"Hourly variable at index {variable_index} is missing.")
        return variable.ValuesAsNumpy()

    temp = hourly_values(0)
    precip = hourly_values(1)  # Hourly precipitation in mm
    wind_spd = hourly_values(2)
    wind_dir = hourly_values(3)

    # Convert the API start/end/interval values into a localized hourly DatetimeIndex
    time_index = pd.date_range(
        start=pd.to_datetime(hourly_data.Time(), unit="s", utc=True),
        end=pd.to_datetime(hourly_data.TimeEnd(), unit="s", utc=True),
        freq=pd.Timedelta(seconds=hourly_data.Interval()),
        inclusive="left",
    ).tz_convert(timezone)

    hourly_lengths = {len(time_index), len(temp), len(precip), len(wind_spd), len(wind_dir)}
    if len(hourly_lengths) != 1:
        raise RuntimeError("Hourly forecast arrays have inconsistent lengths; cannot build calendar safely.")

    logger.debug("Hourly time range: %s → %s (%d slots)", time_index[0], time_index[-1], len(time_index))

    # 4. Initialize the iCalendar object
    cal = Calendar()
    cal.add("prodid", "-//Leuven Weather and Rain Calendar//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Leuven Weather Forecast")  # Calendar display name

    forecast_df = pd.DataFrame({
        "timestamp": time_index,
        "temp": temp,
        "precip": precip,
        "wind_spd": wind_spd,
        "wind_dir": wind_dir,
    })
    forecast_df["date"] = forecast_df["timestamp"].dt.date

    daily_event_count = forecast_df["date"].nunique()
    logger.info("Generating .ics calendar file (%d daily events)...", daily_event_count)

    # 5. Build one full-day event per forecast day with an hourly table in the details
    for _, day_df in forecast_df.groupby("date", sort=True):
        day_date = day_df["timestamp"].iloc[0].date()
        event = Event()

        min_temp = day_df["temp"].min()
        max_temp = day_df["temp"].max()
        total_precip = day_df["precip"].sum()

        summary = f"Leuven forecast | 🌡️ {min_temp:.0f}–{max_temp:.0f}°C | 🌧️ {total_precip:.1f}mm"
        event.add("summary", summary)

        # Full-day event: add only date-based DTSTART (no time component).
        event.add("dtstart", day_date)

        hourly_lines = [
            (
                f"{ts.strftime('%H:%M')} | "
                f"🌡️ {temp_value:.0f}°C | "
                f"💨 {wind_direction_arrow(float(dir_value))} {speed_value:.0f} m/s | "
                f"{'🌧️ ' + format(precip_value, '.1f') + 'mm' if precip_value > 0 else '☀️ 0.0mm'}"
            )
            for ts, temp_value, precip_value, speed_value, dir_value in zip(
                day_df["timestamp"],
                day_df["temp"],
                day_df["precip"],
                day_df["wind_spd"],
                day_df["wind_dir"],
            )
        ]

        description_lines = [
            f"Hourly forecast for {day_date.isoformat()} (local time)",
            "Time  | Temp   | Wind             | Rain",
            "--------------------------------------------",
            *hourly_lines,
        ]
        event.add("description", "\n".join(description_lines))

        # Unique daily ID lets calendar clients overwrite the same day on refresh.
        event.add("uid", f"leuven-weather-{day_date.strftime('%Y%m%d')}@myscript")

        cal.add_component(event)
        logger.debug("Daily event added: %s — %s", day_date.isoformat(), summary)

    # 6. Save data into an iCalendar file
    output_path = Path("leuven_weather.ics")
    output_path.write_bytes(cal.to_ical())
    logger.debug("Wrote %d bytes to %s", output_path.stat().st_size, output_path)
    logger.info("Finished! '%s' successfully created with rain details.", output_path)
