import asyncio
import csv
import json
import os
import smtplib
import sys
import time
import urllib.request
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"
FIELDNAMES = ["Index Name", "DATE", "pe", "pb", "divYield", "pe*pb"]
PERIODS = [20, 40, 60, 120, 250, 500, 750, 1000, 2000, 3000, 4000, 5000]

INDEX_CONFIG = {
    "NIFTY 50": {
        "csv_path": DATA_DIR / "df_nifty50.csv",
        "display_name": "Nifty 50",
    },
    "NIFTY BANK": {
        "csv_path": DATA_DIR / "df_niftybank.csv",
        "display_name": "Nifty Bank",
    },
}


def parse_date(value: str | None) -> str:
    """Convert the NSE API date to the same DD-MM-YYYY format used by the CSV."""
    if not value:
        return datetime.now().strftime("%d-%m-%Y")

    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).strftime("%d-%m-%Y")
        except ValueError:
            continue

    return datetime.now().strftime("%d-%m-%Y")


async def fetch_index_metrics(index_key: str, display_name: str) -> dict[str, Any]:
    """Fetch the latest metrics for a given index from the NSE API."""
    url = "https://www.nseindia.com/api/allIndices"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Referer": "https://www.nseindia.com/",
    }

    req = urllib.request.Request(url, headers=headers)

    def _download() -> str:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.read().decode("utf-8")

    payload_text = await asyncio.to_thread(_download)
    payload = json.loads(payload_text)

    for item in payload.get("data", []):
        if str(item.get("index", "")).upper() == index_key:
            pe = float(item.get("pe", 0))
            pb = float(item.get("pb", 0))
            div_yield = float(item.get("dy", 0))
            return {
                "Index Name": display_name,
                "DATE": parse_date(item.get("previousDay") or item.get("date")),
                "pe": f"{pe:.2f}",
                "pb": f"{pb:.2f}",
                "divYield": f"{div_yield:.2f}",
                "pe*pb": f"{pe * pb:.4f}",
            }

    raise ValueError(f"{index_key} data was not found in the NSE response")


def update_csv_with_latest_row(csv_path: Path, new_row: dict[str, Any]) -> None:
    """Append or update the latest row in the target CSV file."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []

    if csv_path.exists():
        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

    updated = False
    for row in rows:
        if row.get("Index Name") == new_row["Index Name"] and row.get("DATE") == new_row["DATE"]:
            row.update(new_row)
            updated = True
            break

    if not updated:
        rows.append(new_row)

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def analyze_rolling_averages(csv_path: Path) -> dict[str, Any]:
    """Build a rolling-average style report from the CSV data."""
    rows: list[dict[str, str]] = []
    if csv_path.exists():
        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

    records: list[tuple[datetime, float]] = []
    for row in rows:
        try:
            dt = datetime.strptime(row["DATE"], "%d-%m-%Y")
            value = float(row["pe*pb"])
        except (KeyError, ValueError):
            continue
        records.append((dt, value))

    records.sort(key=lambda item: item[0])

    if not records:
        return {"latest_date": None, "current": None, "averages": {}, "deviations": {}}

    current = records[-1][1]
    latest_date = records[-1][0].strftime("%d-%m-%Y")

    averages: dict[Any, float] = {}
    deviations: dict[Any, float | None] = {}
    for period in PERIODS:
        if len(records) >= period:
            window = [value for _, value in records[-period:]]
            avg = sum(window) / len(window)
            averages[period] = round(avg, 2)
            deviations[period] = round(((current - avg) / avg) * 100, 2) if avg else None

    all_time_avg = sum(value for _, value in records) / len(records)
    averages["all_time"] = round(all_time_avg, 2)
    deviations["all_time"] = round(((current - all_time_avg) / all_time_avg) * 100, 2) if all_time_avg else None

    return {
        "latest_date": latest_date,
        "current": round(current, 2),
        "averages": averages,
        "deviations": deviations,
    }


def build_report_message(csv_path: Path, title: str) -> str:
    data = analyze_rolling_averages(csv_path)
    latest_date = data["latest_date"] or "N/A"
    current = data["current"]
    averages = data["averages"]
    deviations = data["deviations"]

    lines = [
        f"📊 {title} Analysis Report",
        f"📅 Date: {latest_date}",
        f"Current PE*PB: {current}",
        "",
        "Rolling Averages:",
    ]

    for period in [20, 40, 60, 120, 250, 500, 750, 1000, 2000, 3000, 4000, 5000, "all_time"]:
        if period in averages:
            avg = averages[period]
            dev = deviations.get(period)
            suffix = f" ({dev:+.2f}%)" if dev is not None else ""
            lines.append(f"- {period} days: {avg}{suffix}")

    return "\n".join(lines)


def build_combined_report() -> str:
    sections = [build_report_message(config["csv_path"], config["display_name"]) for config in INDEX_CONFIG.values()]
    return "\n\n".join(sections)


def send_email(subject: str, html_body: str) -> None:
    email = os.environ.get("EMAIL_ADDRESS")
    password = os.environ.get("EMAIL_PASSWORD")
    to_email = os.environ.get("TO_EMAIL", email)

    if not email or not password:
        print("Email credentials not found. Skipping email send.")
        return

    msg = MIMEText(html_body, "html")
    msg["Subject"] = subject
    msg["From"] = email
    msg["To"] = to_email or email

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(email, password)
        server.send_message(msg)

    print(f"Email sent to {to_email or email}")


def text_to_html(report_text: str) -> str:
    """Convert plain text report into a styled HTML email body."""
    now = datetime.now().strftime("%d %B %Y, %I:%M %p")
    sections = [section.strip() for section in report_text.split("\n\n") if section.strip()]
    cards = []

    for section in sections:
        lines = [line.strip() for line in section.splitlines() if line.strip()]
        if not lines:
            continue

        title = lines[0] if lines else ""
        subtitle = lines[1] if len(lines) > 1 else ""
        pepb_line = lines[2] if len(lines) > 2 else ""
        rows = ""

        for line in lines[3:]:
            if line.startswith("Rolling Averages:"):
                continue
            if not line.startswith("- "):
                continue

            label, value = line[2:].split(":", 1)
            color = "#cccccc"
            if "(" in value and "%" in value:
                pct_text = value[value.index("(") + 1 : value.index(")")]
                try:
                    pct_val = float(pct_text.replace("%", ""))
                    color = "#ff4d4d" if pct_val > 0 else "#4dff88"
                except ValueError:
                    pass

            rows += (
                f'<tr><td style="padding:6px 12px;color:#aaa;">{label.strip()}</td>'
                f'<td style="padding:6px 12px;color:{color};font-weight:bold;">{value.strip()}</td></tr>'
            )

        card = f"""
        <div style="background:#1a1a2e;border-radius:10px;padding:20px;margin-bottom:16px;border:1px solid #333;">
            <div style="color:#00d4ff;font-size:17px;font-weight:bold;margin-bottom:4px;">{title}</div>
            <div style="color:#888;font-size:13px;margin-bottom:12px;">{subtitle}</div>
            <div style="color:#fff;font-size:15px;margin-bottom:14px;">{pepb_line}</div>
            <table style="width:100%;border-collapse:collapse;font-size:14px;font-family:monospace;">
                {rows}
            </table>
        </div>"""
        cards.append(card)

    return f"""
    <html>
    <body style="background:#0f0f23;padding:20px;font-family:Arial,sans-serif;">
        <h1 style="color:#fff;text-align:center;">📊 NSE PE*PB Daily Report</h1>
        <p style="color:#888;text-align:center;">Generated: {now}</p>
        {''.join(cards)}
        <p style="color:#555;text-align:center;font-size:12px;margin-top:20px;">Auto-generated via GitHub Actions</p>
    </body>
    </html>"""


async def main() -> None:
    retry_after_minutes = 30
    try:
        for index_key, config in INDEX_CONFIG.items():
            latest_row = await fetch_index_metrics(index_key, config["display_name"])
            update_csv_with_latest_row(config["csv_path"], latest_row)

        report_text = build_combined_report()
        print(report_text)

        today = datetime.now().strftime("%d %b %Y")
        send_email(
            subject=f"NSE PE*PB Report - {today}",
            html_body=text_to_html(report_text),
        )
    except Exception as exc:
        print(f"Report generation failed: {exc}")
        print(f"Retrying in {retry_after_minutes} minutes...")
        time.sleep(retry_after_minutes * 60)
        raise


if __name__ == "__main__":
    asyncio.run(main())
