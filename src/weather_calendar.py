import logging
import argparse
import pandas as pd
from pathlib import Path
from typing import Any, cast

import openmeteo_requests
import requests

from retry_requests import retry
from icalendar import Calendar, Event

BASE_PARAMS = {
    # Added "precipitation" to fetch hourly rainfall data in mm
    "hourly": [
        "temperature_2m",
        "precipitation",
        "wind_speed_10m",
        "wind_direction_10m",
    ],
    "wind_speed_unit": "ms",
    "precipitation_unit": "mm",  # Ensures precipitation is returned in millimeters
    "forecast_days": 5,
}


def wind_direction_arrow(direction_degrees: float) -> str:
    arrows = ["↑", "↗", "→", "↘", "↓", "↙", "←", "↖"]
    index = int((direction_degrees + 22.5) % 360 // 45)
    return arrows[index]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate an iCalendar file with hourly weather forecasts for a location."
    )
    parser.add_argument(
        "--location",
        default="Leuven, Belgium",
        help="Location name to geocode and fetch weather for (default: Leuven, Belgium)",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)5s] %(message)s",
        datefmt="%Y%m%dT%H%M%S",
    )
    logger = logging.getLogger(__name__)

    # 1. Setup the Open-Meteo API client with retry capabilities
    session = requests.Session()
    retry_session = retry(session, retries=5, backoff_factor=0.2)
    openmeteo = openmeteo_requests.Client(session=cast(Any, retry_session))
    logger.debug("Open-Meteo client initialised (retries: 5)")

    # 2. Resolve the requested location to coordinates using Open-Meteo geocoding
    geocode_url = "https://geocoding-api.open-meteo.com/v1/search"
    geocode_params = {"name": args.location, "count": 1, "language": "en", "format": "json"}

    logger.info("Resolving location: %s", args.location)
    geocode_response = retry_session.get(geocode_url, params=geocode_params, timeout=15)
    geocode_response.raise_for_status()
    geocode_data = geocode_response.json()
    geocode_results = geocode_data.get("results") or []
    if not geocode_results:
        raise RuntimeError(f"Could not resolve location '{args.location}' via Open-Meteo geocoding.")

    top_result = geocode_results[0]
    latitude = top_result["latitude"]
    longitude = top_result["longitude"]
    geocoded_name = top_result.get("name", args.location)
    geocoded_country = top_result.get("country")
    display_location = f"{geocoded_name}, {geocoded_country}" if geocoded_country else geocoded_name
    geocoded_timezone = top_result.get("timezone")

    params = {
        **BASE_PARAMS,
        "latitude": latitude,
        "longitude": longitude,
        "timezone": geocoded_timezone or "auto",
    }

    logger.info(
        "Resolved location '%s' -> %.4f, %.4f (%s)",
        display_location,
        latitude,
        longitude,
        params["timezone"],
    )

    # 3. Define Open-Meteo endpoint and configurations for the selected location
    url = "https://api.open-meteo.com/v1/forecast"

    logger.info("Fetching weather data from Open-Meteo...")
    logger.debug("Request params: %s", params)
    responses = openmeteo.weather_api(url, params=params)
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

    # 4. Extract the hourly variables
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

    # 5. Initialize the iCalendar object
    cal = Calendar()
    cal.add("prodid", f"-//{display_location} Weather and Rain Calendar//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", f"{display_location} Weather Forecast")  # Calendar display name

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

    # 6. Build one full-day event per forecast day with an hourly table in the details
    for _, day_df in forecast_df.groupby("date", sort=True):
        day_date = day_df["timestamp"].iloc[0].date()
        event = Event()

        min_temp = day_df["temp"].min()
        max_temp = day_df["temp"].max()
        total_precip = day_df["precip"].sum()

        summary = f"🌡️ {min_temp:.0f}–{max_temp:.0f}°C | 🌧️ {total_precip:.1f}mm"
        event.add("summary", summary)

        # Full-day event: add only date-based DTSTART (no time component).
        event.add("dtstart", day_date)

        hourly_lines = [
            (
                f"{ts.strftime('%H:%M')}  | "
                f"🌡️ {temp_value:.0f}°C  | "
                f"💨 {wind_direction_arrow(float(dir_value))} {speed_value:.0f} m/s  | "
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
            f"{display_location} Weather Forecast (hourly details): {day_date.isoformat()}",
            *hourly_lines,
            "\nData source: Open-Meteo (https://open-meteo.com/)",
        ]
        event.add("description", "\n".join(description_lines))

        # Unique daily ID lets calendar clients overwrite the same day on refresh.
        safe_location = "-".join(args.location.lower().replace(",", " ").split())
        event.add("uid", f"{safe_location}-weather-{day_date.strftime('%Y%m%d')}@myscript")

        cal.add_component(event)
        logger.debug("Daily event added: %s — %s", day_date.isoformat(), summary)

    # 7. Save data into an iCalendar file
    safe_location = "-".join(args.location.lower().replace(",", " ").split())
    output_path = Path(f"{safe_location}_weather.ics")
    output_path.write_bytes(cal.to_ical())
    logger.debug("Wrote %d bytes to %s", output_path.stat().st_size, output_path)
    logger.info("Finished! '%s' successfully created with rain details.", output_path)
