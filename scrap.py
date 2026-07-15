import os
import json
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

BASE_URL = "https://www.csk.gov.in/"
ALERTS_URL = urljoin(BASE_URL, "alerts.html")

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

OUTPUT_DIR = "output"
IMAGE_DIR = os.path.join(OUTPUT_DIR, "images")

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(IMAGE_DIR, exist_ok=True)


def get_soup(url):
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, "lxml")


def get_alert_links():
    soup = get_soup(ALERTS_URL)

    alerts = []

    for link in soup.find_all("a", href=True):

        href = link["href"]

        if ".html" not in href:
            continue

        if "alerts.html" in href:
            continue

        full_url = urljoin(BASE_URL, href)

        title = link.get_text(strip=True)

        if not title:
            continue

        alerts.append({
            "title": title,
            "url": full_url
        })

    # remove duplicates
    seen = set()
    unique = []

    for alert in alerts:
        if alert["url"] not in seen:
            seen.add(alert["url"])
            unique.append(alert)

    return unique


def download_images(soup, threat_name):

    images = []

    folder = os.path.join(IMAGE_DIR, threat_name.replace(" ", "_"))
    os.makedirs(folder, exist_ok=True)

    for img in soup.find_all("img"):

        src = img.get("src")

        if not src:
            continue

        image_url = urljoin(BASE_URL, src)

        try:

            r = requests.get(image_url, headers=HEADERS, timeout=30)

            filename = os.path.basename(urlparse(image_url).path)

            if filename == "":
                continue

            path = os.path.join(folder, filename)

            with open(path, "wb") as f:
                f.write(r.content)

            images.append(path)

        except Exception:
            pass

    return images


def parse_advisory(alert):

    soup = get_soup(alert["url"])

    text = soup.get_text(separator="\n", strip=True)

    references = []

    for a in soup.find_all("a", href=True):

        href = urljoin(BASE_URL, a["href"])

        references.append(href)

    images = download_images(soup, alert["title"])

    return {

        "title": alert["title"],

        "url": alert["url"],

        "text": text,

        "references": list(set(references)),

        "images": images

    }


def save_json(data):

    filename = data["title"].replace("/", "_").replace(" ", "_")

    with open(
        os.path.join(OUTPUT_DIR, f"{filename}.json"),
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(data, f, indent=4, ensure_ascii=False)


def main():

    alerts = get_alert_links()

    print(f"Found {len(alerts)} advisories")

    for alert in alerts:

        print(f"Scraping: {alert['title']}")

        try:

            threat = parse_advisory(alert)

            save_json(threat)

        except Exception as e:

            print(e)


if __name__ == "__main__":
    main()