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
        description="Generate an iCalendar file with hourly weather forecasts for a location.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-l", "--location", default="Leuven", help="Location name to fetch weather for")
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug logging")
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

    logger.info(f"Resolving location: {args.location}")
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
    safe_location = "_".join(geocoded_name.lower().split())
    geocoded_timezone = top_result.get("timezone")

    params = {
        **BASE_PARAMS,
        "latitude": latitude,
        "longitude": longitude,
        "timezone": geocoded_timezone or "auto",
    }

    logger.info(
        f"Resolved location '{display_location}' -> {latitude:.4f}, {longitude:.4f} ({params['timezone']})"
    )

    # 3. Define Open-Meteo endpoint and configurations for the selected location
    url = "https://api.open-meteo.com/v1/forecast"

    logger.info("Fetching weather data from Open-Meteo...")
    logger.debug(f"Request params: {params}")
    responses = openmeteo.weather_api(url, params=params)
    if not responses:
        raise RuntimeError("Open-Meteo returned no forecast responses.")

    response = responses[0]
    timezone_raw = response.Timezone()
    if timezone_raw is None:
        raise RuntimeError("Open-Meteo response did not include a timezone.")
    timezone = timezone_raw.decode() if isinstance(timezone_raw, bytes) else timezone_raw

    logger.debug(
        "Response received - coordinates: "
        f"{response.Latitude():.4f}°N {response.Longitude():.4f}°E, "
        f"timezone: {timezone}"
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

    logger.debug(f"Hourly time range: {time_index[0]} → {time_index[-1]} ({len(time_index)} slots)")

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
    logger.info(f"Generating .ics calendar file ({daily_event_count} daily events)...")

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

        hourly_lines = []
        for ts, temp_value, precip_value, speed_value, dir_value in zip(
            day_df["timestamp"],
            day_df["temp"],
            day_df["precip"],
            day_df["wind_spd"],
            day_df["wind_dir"],
        ):
            time_label = ts.strftime("%Hu")
            wind_arrow = wind_direction_arrow(float(dir_value))
            wind_speed_kmh = float(speed_value) * 3.6
            rain_text = f"{precip_value:.1f} mm"
            rain_emoji = "🌧️" if precip_value > 0 else "☀️"

            hourly_lines.append(
                f"{time_label}  | "
                f"🌡️ {temp_value:.0f}°  | "
                f"💨 {wind_arrow} {wind_speed_kmh:.0f} kmh  | "
                f"{rain_emoji} {rain_text}"
            )

        updated_at = pd.Timestamp.now(tz=timezone).strftime("%Y-%m-%d %H:%M:%S %Z")

        description_lines = [
            f"{display_location} Weather Forecast (hourly details): {day_date.isoformat()}",
            *hourly_lines,
            f"\nUpdated at: {updated_at}",
            "\nData source: Open-Meteo (https://open-meteo.com/)",
        ]
        event.add("description", "\n".join(description_lines))

        # Unique daily ID lets calendar clients overwrite the same day on refresh.
        event.add("uid", f"{safe_location}-weather-{day_date.strftime('%Y%m%d')}@myscript")

        cal.add_component(event)
        logger.debug(f"Daily event added: {day_date.isoformat()} — {summary}")

    # 7. Save data into an iCalendar file
    output_path = Path(f"{safe_location}_weather.ics")
    output_path.write_bytes(cal.to_ical())
    logger.debug(f"Wrote {output_path.stat().st_size} bytes to {output_path}")
    logger.info(f"Finished! '{output_path}' successfully created with rain details.")
