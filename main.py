import base64
import os
import re
import smtplib
from dataclasses import dataclass, field
from datetime import datetime
from email.mime.text import MIMEText
from html import escape
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


TRENDING_URL = "https://github.com/trending?since=daily"
GITHUB_API_URL = "https://api.github.com"
PROJECT_LIMIT = 8
REQUEST_TIMEOUT = 20


SMTP_CONFIG_BY_DOMAIN = {
    "gmail.com": ("smtp.gmail.com", 465),
    "outlook.com": ("smtp.office365.com", 587),
    "hotmail.com": ("smtp.office365.com", 587),
    "live.com": ("smtp.office365.com", 587),
    "qq.com": ("smtp.qq.com", 465),
    "163.com": ("smtp.163.com", 465),
    "126.com": ("smtp.126.com", 465),
}


KEYWORD_RULES = [
    {
        "category": "AI",
        "keywords": [
            "ai",
            "agent",
            "llm",
            "rag",
            "machine learning",
            "deep learning",
            "neural",
            "model",
            "transformer",
            "diffusion",
            "inference",
            "pytorch",
            "tensorflow",
            "cuda",
        ],
        "audiences": ["AI 初学者", "深度学习学习者"],
        "learning": ["模型部署", "API 调用", "开源项目组织方式"],
    },
    {
        "category": "Python",
        "keywords": ["python", "fastapi", "django", "flask", "pandas", "numpy", "cli"],
        "audiences": ["Python 开发者"],
        "learning": ["Python 工程化", "项目结构", "自动化脚本"],
    },
    {
        "category": "前端",
        "keywords": [
            "react",
            "vue",
            "next.js",
            "nextjs",
            "typescript",
            "javascript",
            "css",
            "ui",
            "frontend",
            "tailwind",
        ],
        "audiences": ["前端开发者"],
        "learning": ["前端 UI", "项目结构", "README 写法"],
    },
    {
        "category": "后端",
        "keywords": ["api", "server", "database", "postgres", "redis", "go", "rust", "java"],
        "audiences": ["后端开发者"],
        "learning": ["API 调用", "项目结构", "开源项目组织方式"],
    },
    {
        "category": "自动化",
        "keywords": ["automation", "workflow", "devops", "docker", "kubernetes", "ci", "deploy"],
        "audiences": ["DevOps / 自动化", "工具党"],
        "learning": ["自动化脚本", "模型部署", "开源项目组织方式"],
    },
    {
        "category": "工具",
        "keywords": ["tool", "cli", "terminal", "editor", "productivity", "desktop", "app"],
        "audiences": ["工具党", "Python 开发者"],
        "learning": ["项目结构", "README 写法", "开源项目组织方式"],
    },
]


@dataclass
class Project:
    name: str
    url: str
    stars: str = "N/A"
    description: str = "暂无简介"
    language: str = "未知"
    topics: list[str] = field(default_factory=list)
    readme_excerpt: str = ""
    one_sentence: str = "信息不足，建议先看 README。"
    problem: str = "信息不足，建议先看 README，判断它具体解决的问题。"
    audiences: list[str] = field(default_factory=lambda: ["工具党"])
    learning_points: list[str] = field(default_factory=lambda: ["README 写法", "开源项目组织方式"])
    reading_advice: str = "先看 README，重点关注安装、快速开始和 examples。"
    recommendation: str = "可以收藏"
    categories: list[str] = field(default_factory=list)


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def clean_text(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    return value


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/vnd.github+json, text/html",
            "User-Agent": "github-trending-daily-mailer/2.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    )
    return session


def request_json(session: requests.Session, url: str) -> dict | None:
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.RequestException:
        return None


def fetch_trending_projects(session: requests.Session, limit: int = PROJECT_LIMIT) -> tuple[list[Project], str | None]:
    try:
        response = session.get(TRENDING_URL, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        return [], f"今日获取失败：无法访问 GitHub Trending。原因：{exc.__class__.__name__}"

    soup = BeautifulSoup(response.text, "html.parser")
    projects = []

    for article in soup.select("article.Box-row")[:limit]:
        title_link = article.select_one("h2 a")
        if not title_link:
            continue

        repo_path = clean_text(title_link.get_text(" ", strip=True)).replace(" / ", "/")
        href = title_link.get("href", "").strip()
        if not repo_path or not href:
            continue

        description_tag = article.select_one("p")
        language_tag = article.select_one("[itemprop='programmingLanguage']")
        stars = "N/A"

        for link in article.select("a.Link--muted, a.muted-link"):
            if link.get("href", "").endswith("/stargazers"):
                stars = clean_text(link.get_text(" ", strip=True))
                break

        projects.append(
            Project(
                name=repo_path,
                url=f"https://github.com{href}",
                stars=stars,
                description=clean_text(description_tag.get_text(" ", strip=True)) if description_tag else "暂无简介",
                language=clean_text(language_tag.get_text(" ", strip=True)) if language_tag else "未知",
            )
        )

    if not projects:
        return [], "今日获取失败：GitHub Trending 页面没有解析到项目。"

    return projects, None


def fetch_readme_excerpt(session: requests.Session, repo_name: str) -> str:
    readme_data = request_json(session, f"{GITHUB_API_URL}/repos/{repo_name}/readme")
    if not readme_data or "content" not in readme_data:
        return ""

    try:
        markdown = base64.b64decode(readme_data["content"]).decode("utf-8", errors="ignore")
    except (ValueError, TypeError):
        return ""

    lines = []
    for line in markdown.splitlines():
        line = clean_text(re.sub(r"!\[[^\]]*\]\([^)]+\)", "", line))
        if not line or line.startswith(("#", "[!", "<", "---")):
            continue
        if line.startswith(("```", "|")):
            continue
        lines.append(line)
        if len(lines) >= 4:
            break

    return clean_text(" ".join(lines))[:900]


def enrich_project(session: requests.Session, project: Project) -> Project:
    repo_data = request_json(session, f"{GITHUB_API_URL}/repos/{project.name}")
    if repo_data:
        project.description = repo_data.get("description") or project.description
        project.language = repo_data.get("language") or project.language
        project.stars = f"{repo_data.get('stargazers_count', project.stars):,}" if isinstance(repo_data.get("stargazers_count"), int) else project.stars
        project.topics = repo_data.get("topics") or []

    project.readme_excerpt = fetch_readme_excerpt(session, project.name)
    analyze_project(project)
    return project


def analyze_project(project: Project) -> None:
    text = " ".join(
        [
            project.name,
            project.description,
            project.language,
            " ".join(project.topics),
            project.readme_excerpt,
        ]
    ).lower()

    matched_rules = [
        rule
        for rule in KEYWORD_RULES
        if any(keyword in text for keyword in rule["keywords"])
    ]

    if matched_rules:
        project.categories = unique([rule["category"] for rule in matched_rules])
        project.audiences = unique(item for rule in matched_rules for item in rule["audiences"])
        project.learning_points = unique(item for rule in matched_rules for item in rule["learning"])
    else:
        project.categories = ["工具"]
        project.audiences = ["工具党", "暂时不适合新手"]
        project.learning_points = ["README 写法", "开源项目组织方式"]

    project.one_sentence = make_one_sentence(project)
    project.problem = make_problem_explanation(project)
    project.reading_advice = make_reading_advice(project)
    project.recommendation = make_recommendation(project)


def unique(values) -> list[str]:
    result = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def make_one_sentence(project: Project) -> str:
    description = project.description if project.description != "暂无简介" else project.readme_excerpt
    if not description:
        return "信息不足，建议先看 README。"

    if "AI" in project.categories:
        return f"这是一个偏 AI / 模型方向的开源项目，主要用途是：{description[:120]}。"
    if "前端" in project.categories:
        return f"这是一个偏前端或 UI 的项目，主要用途是：{description[:120]}。"
    if "自动化" in project.categories:
        return f"这是一个偏自动化或部署效率的工具，主要用途是：{description[:120]}。"
    if "Python" in project.categories:
        return f"这是一个 Python 相关项目，主要用途是：{description[:120]}。"
    return f"这是一个开源工具或框架，主要用途是：{description[:120]}。"


def make_problem_explanation(project: Project) -> str:
    if "AI" in project.categories:
        return "它通常是在降低模型使用、推理、构建 AI 应用或处理数据的门槛。对学习 AI 的同学，可以重点关注它如何组织模型、数据和接口。"
    if "前端" in project.categories:
        return "它主要解决界面构建、交互体验或前端工程效率问题。适合观察组件组织、状态管理和项目文档。"
    if "后端" in project.categories:
        return "它主要解决服务端开发、数据处理或 API 构建中的实际工程问题。可以留意它的接口设计和目录结构。"
    if "自动化" in project.categories:
        return "它主要解决重复操作、部署流程或工程协作中的自动化问题。适合学习脚本化思维和工具链设计。"
    if project.description and project.description != "暂无简介":
        return f"从简介看，它关注的问题是：{project.description[:160]}。如果想进一步确认，建议读 README 的快速开始部分。"
    return "信息不足，建议先看 README。"


def make_reading_advice(project: Project) -> str:
    text = " ".join(project.topics).lower()
    if "AI" in project.categories or project.language in {"Python", "Jupyter Notebook"}:
        return "先看 README 的安装和快速开始，再找 examples、demo 或 notebooks，不建议一上来精读全部源码。"
    if "前端" in project.categories:
        return "先看 README 的截图、在线 demo 和 examples，再看组件目录。"
    if "自动化" in project.categories:
        return "可以重点看配置文件、命令行参数和 CI / Docker 相关说明。"
    if any(word in text for word in ["compiler", "database", "kernel", "runtime"]):
        return "门槛可能偏高，先收藏并读 README，不建议现在精读。"
    return "先看 README，重点关注安装、快速开始和项目结构。"


def make_recommendation(project: Project) -> str:
    text = " ".join([project.language, " ".join(project.topics), project.description]).lower()
    if "AI" in project.categories and any(word in text for word in ["python", "pytorch", "llm", "agent", "rag"]):
        return "值得精读"
    if "Python" in project.categories or "自动化" in project.categories:
        return "可以收藏"
    if "暂时不适合新手" in project.audiences:
        return "暂时略过"
    return "可以收藏"


def build_summary(projects: list[Project], fetch_error: str | None) -> str:
    if fetch_error:
        return f"<p><strong>{escape(fetch_error)}</strong></p>"

    categories = unique(category for project in projects for category in project.categories)
    top_projects = sorted(projects, key=lambda item: recommendation_rank(item.recommendation), reverse=True)[:2]
    beginner_projects = [
        project
        for project in projects
        if "暂时不适合新手" not in project.audiences
    ][:2]

    return f"""
    <p>今天 GitHub Trending 主要包含：{escape(' / '.join(categories) or '工具')} 等方向。</p>
    <ul>
      <li>最值得关注：{escape('、'.join(project.name for project in top_projects) or '信息不足')}</li>
      <li>对初学者较友好：{escape('、'.join(project.name for project in beginner_projects) or '建议先收藏阅读')}</li>
    </ul>
    """


def recommendation_rank(value: str) -> int:
    return {"值得精读": 3, "可以收藏": 2, "暂时略过": 1}.get(value, 0)


def build_email_html(projects: list[Project], fetch_error: str | None = None) -> str:
    today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    cards = []

    for index, project in enumerate(projects, start=1):
        cards.append(
            f"""
            <section style="border:1px solid #e5e7eb;border-radius:8px;padding:18px;margin:18px 0;background:#ffffff;">
              <h3 style="margin:0 0 10px;">{index}. 【{escape(project.name)}】</h3>
              <p><strong>链接：</strong><a href="{escape(project.url)}">{escape(project.url)}</a></p>
              <p><strong>Stars：</strong>{escape(project.stars)}</p>
              <p><strong>语言：</strong>{escape(project.language)}</p>
              <p><strong>Topics：</strong>{escape(', '.join(project.topics) or '暂无')}</p>
              <p><strong>一句话解释：</strong>{escape(project.one_sentence)}</p>
              <p><strong>它解决什么问题：</strong>{escape(project.problem)}</p>
              <p><strong>适合谁看：</strong>{escape('、'.join(project.audiences))}</p>
              <p><strong>我能学到什么：</strong>{escape('、'.join(project.learning_points))}</p>
              <p><strong>阅读建议：</strong>{escape(project.reading_advice)}</p>
              <p><strong>推荐程度：</strong>{escape(project.recommendation)}</p>
            </section>
            """
        )

    if fetch_error and not projects:
        cards.append(
            """
            <section style="border:1px solid #f3caca;border-radius:8px;padding:18px;margin:18px 0;background:#fff7f7;">
              <h3 style="margin:0 0 10px;">今日获取失败</h3>
              <p>GitHub Trending 暂时无法获取。建议稍后手动重新运行 workflow。</p>
            </section>
            """
        )

    return f"""
    <html>
      <body style="margin:0;background:#f6f8fa;color:#24292f;font-family:Arial,'Microsoft YaHei',sans-serif;line-height:1.6;">
        <main style="max-width:860px;margin:0 auto;padding:24px;">
          <h1 style="margin:0 0 16px;">GitHub 技术日报解读版 - {today}</h1>
          <section style="border:1px solid #d0d7de;border-radius:8px;padding:18px;background:#ffffff;">
            <h2 style="margin:0 0 10px;">今日总结</h2>
            {build_summary(projects, fetch_error)}
          </section>
          {''.join(cards)}
        </main>
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

    raise RuntimeError("Unable to infer SMTP server. Add SMTP_HOST and SMTP_PORT as repository secrets.")


def send_email(projects: list[Project], fetch_error: str | None = None) -> None:
    email_user = get_required_env("EMAIL_USER")
    email_pass = get_required_env("EMAIL_PASS")
    email_to = get_required_env("EMAIL_TO")
    smtp_host, smtp_port = get_smtp_config(email_user)
    today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")

    message = MIMEText(build_email_html(projects, fetch_error), "html", "utf-8")
    message["Subject"] = f"GitHub 技术日报解读版 - {today}"
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
    session = make_session()
    projects, fetch_error = fetch_trending_projects(session)
    enriched_projects = []

    for project in projects[:PROJECT_LIMIT]:
        try:
            enriched_projects.append(enrich_project(session, project))
        except Exception:
            analyze_project(project)
            enriched_projects.append(project)

    send_email(enriched_projects, fetch_error)
    print(f"Sent {len(enriched_projects)} GitHub Trending projects.")


if __name__ == "__main__":
    main()
