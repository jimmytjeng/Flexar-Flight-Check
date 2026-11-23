from datetime import datetime, timedelta, timezone
import os
import re
from icalendar import Calendar
import requests

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID", "YOUR_CHAT_ID")
ICS_URL = os.getenv("ICS_URL", "YOUR_SECRET_ICAL_URL")

RATES_PER_MIN = {
    "saver": 0.50,
    "weekday": 0.60,
    "weekend": 0.80,
}

DEFAULT_RENTAL_MINUTES = 35
FREE_MINUTES = 5
PLATFORM_FEE = 0.99
EXTRA_SURCHARGES = 0.99
LEAD_MINUTES_BEFORE_EVENT = 75  # Adjust if calendar time is STD rather than report time

TARGET_MINUTES_BEFORE = 75
WINDOW_HALF_WIDTH = 7  # Triggers roughly between 68 and 82 minutes before the event
FORCE_SEND = os.getenv("FORCE_SEND", "false").lower() == "true"


def get_next_sin_flight(ics_url: str) -> tuple[datetime, str] | None:
    resp = requests.get(ics_url, timeout=15)
    resp.raise_for_status()

    cal = Calendar.from_ical(resp.content)
    now_utc = datetime.now(timezone.utc)
    flights = []

    for component in cal.walk("VEVENT"):
        summary = str(component.get("SUMMARY", "")).strip()
        dtstart = component.get("DTSTART")
        if not dtstart:
            continue

        start_val = dtstart.dt
        if not isinstance(start_val, datetime):
            continue

        start_dt = start_val if start_val.tzinfo else start_val.replace(tzinfo=timezone.utc)
        if start_dt < now_utc:
            continue

        if re.search(r"\bSIN-[A-Z]{3}\b", summary):
            # Convert to local system timezone (ensure runner TZ is set to Asia/Singapore)
            local_naive = start_dt.astimezone().replace(tzinfo=None)
            flights.append((local_naive, summary))

    flights.sort(key=lambda item: item[0])
    return flights[0] if flights else None


def get_flexar_tier(start_dt: datetime) -> str:
    weekday = start_dt.weekday()  # 0=Mon .. 4=Fri, 5=Sat, 6=Sun
    hour = start_dt.hour

    if 0 <= hour <= 5:
        return "saver"
    if 6 <= hour <= 8:
        return "weekday" if weekday <= 4 else "saver"
    if hour == 9:
        return "saver"
    if 10 <= hour <= 16:
        return "saver" if weekday <= 4 else "weekend"
    return "weekday" if weekday <= 3 else "weekend"


def estimate_trip(event_dt: datetime) -> dict:
    pickup_dt = event_dt - timedelta(minutes=LEAD_MINUTES_BEFORE_EVENT)
    tier = get_flexar_tier(pickup_dt)
    billed_minutes = max(0, DEFAULT_RENTAL_MINUTES - FREE_MINUTES)
    rate = RATES_PER_MIN[tier]
    total = round((billed_minutes * rate) + PLATFORM_FEE + EXTRA_SURCHARGES, 2)

    return {
        "pickup_dt": pickup_dt,
        "tier": tier,
        "rate_per_min": rate,
        "total": total,
    }


def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": CHAT_ID, "text": text},
        timeout=10,
    )
    resp.raise_for_status()


if __name__ == "__main__":
    next_flight = get_next_sin_flight(ICS_URL)

    if not next_flight:
        print("No upcoming SIN-departing flights found.")
    else:
        flight_dt, summary = next_flight
        now_local = datetime.now()
        minutes_until = (flight_dt - now_local).total_seconds() / 60.0

        in_window = (
            (TARGET_MINUTES_BEFORE - WINDOW_HALF_WIDTH)
            <= minutes_until
            <= (TARGET_MINUTES_BEFORE + WINDOW_HALF_WIDTH)
        )

        est = estimate_trip(flight_dt)
        msg = (
            f"Next Flight: {summary}\n"
            f"Scheduled: {flight_dt.strftime('%a %d %b %H:%M')} "
            f"(in ~{int(round(minutes_until))}m)\n"
            f"Est. Flexar Pickup: {est['pickup_dt'].strftime('%a %d %b %H:%M')}\n"
            f"Rate Tier: {est['tier'].title()} (S${est['rate_per_min']:.2f}/min)\n"
            f"Est. Fare ({DEFAULT_RENTAL_MINUTES} min): S${est['total']:.2f}"
        )

        print(msg)
        if FORCE_SEND or in_window:
            send_telegram(msg)
            print("Telegram alert sent.")
        else:
            print("Outside target window; skipped sending Telegram alert.")