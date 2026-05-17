import os
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from html import escape

import requests
from bs4 import BeautifulSoup


TRENDING_URL = "https://github.com/trending?since=daily"


SMTP_CONFIG_BY_DOMAIN = {
    "gmail.com": ("smtp.gmail.com", 465),
    "outlook.com": ("smtp.office365.com", 587),
    "hotmail.com": ("smtp.office365.com", 587),
    "live.com": ("smtp.office365.com", 587),
    "qq.com": ("smtp.qq.com", 465),
    "163.com": ("smtp.163.com", 465),
    "126.com": ("smtp.126.com", 465),
}


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def fetch_trending_projects(limit: int = 10) -> list[dict[str, str]]:
    response = requests.get(
        TRENDING_URL,
        headers={
            "Accept": "text/html",
            "User-Agent": "github-trending-daily-mailer/1.0",
        },
        timeout=30,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    projects = []

    for article in soup.select("article.Box-row")[:limit]:
        title_link = article.select_one("h2 a")
        if not title_link:
            continue

        repo_path = " ".join(title_link.get_text(" ", strip=True).split())
        repo_path = repo_path.replace(" / ", "/")
        repo_url = f"https://github.com{title_link.get('href', '').strip()}"

        description_tag = article.select_one("p")
        description = (
            description_tag.get_text(" ", strip=True)
            if description_tag
            else "No description provided."
        )

        stars = "N/A"
        for link in article.select("a.Link--muted, a.muted-link"):
            href = link.get("href", "")
            if href.endswith("/stargazers"):
                stars = link.get_text(" ", strip=True)
                break

        projects.append(
            {
                "name": repo_path,
                "url": repo_url,
                "description": description,
                "stars": stars,
            }
        )

    if not projects:
        raise RuntimeError("No GitHub Trending projects were found.")

    return projects


def build_email_html(projects: list[dict[str, str]]) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    items = []

    for index, project in enumerate(projects, start=1):
        items.append(
            f"""
            <li style="margin-bottom: 18px;">
              <div>
                <strong>{index}. <a href="{escape(project["url"])}">{escape(project["name"])}</a></strong>
              </div>
              <div>Stars: {escape(project["stars"])}</div>
              <div>{escape(project["description"])}</div>
            </li>
            """
        )

    return f"""
    <html>
      <body>
        <h2>GitHub Trending Daily - {today}</h2>
        <ol>
          {''.join(items)}
        </ol>
      </body>
    </html>
    """


def get_smtp_config(email_user: str) -> tuple[str, int]:
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = os.getenv("SMTP_PORT")
    if smtp_host and smtp_port:
        return smtp_host, int(smtp_port)

    domain = email_user.split("@")[-1].lower()
    if domain in SMTP_CONFIG_BY_DOMAIN:
        return SMTP_CONFIG_BY_DOMAIN[domain]

    raise RuntimeError(
        "Unable to infer SMTP server. Add SMTP_HOST and SMTP_PORT as repository secrets."
    )


def send_email(projects: list[dict[str, str]]) -> None:
    email_user = get_required_env("EMAIL_USER")
    email_pass = get_required_env("EMAIL_PASS")
    email_to = get_required_env("EMAIL_TO")
    smtp_host, smtp_port = get_smtp_config(email_user)

    message = MIMEText(build_email_html(projects), "html", "utf-8")
    message["Subject"] = "GitHub Trending Daily"
    message["From"] = email_user
    message["To"] = email_to

    if smtp_port == 465:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as server:
            server.login(email_user, email_pass)
            server.send_message(message)
    else:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.starttls()
            server.login(email_user, email_pass)
            server.send_message(message)


def main() -> None:
    projects = fetch_trending_projects()
    send_email(projects)
    print(f"Sent {len(projects)} GitHub Trending projects.")


if __name__ == "__main__":
    main()
