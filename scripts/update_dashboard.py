#!/usr/bin/env python3
"""Refresh dynamic README sections from public GitHub, Medium, and Hugging Face data."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

USERNAME = "simeetnayan81"
HF_USER = "simeetnayan"
PROFILE_REPOS = {USERNAME, f"{USERNAME}.github.io"}
HIDDEN_REPOS = {"mpeft"}
ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def request_json(url: str, headers: dict[str, str] | None = None, timeout: int = 30):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "simeetnayan81-dashboard",
            "Accept": "application/json",
            **(headers or {}),
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def github(path: str, params: dict[str, str] | None = None):
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    headers = {"Accept": "application/vnd.github+json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    return request_json(f"https://api.github.com{path}{query}", headers)


def replace_section(text: str, name: str, body: str) -> str:
    start, end = f"<!--START:{name}-->", f"<!--END:{name}-->"
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    if not pattern.search(text):
        return text
    return pattern.sub(f"{start}\n{body.rstrip()}\n{end}", text)


def short_date(value: str | None) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.strftime("%b %d, %Y")
    except ValueError:
        return value[:10]


def owned_repos():
    repos = github(
        f"/users/{USERNAME}/repos",
        {"per_page": "100", "type": "owner", "sort": "pushed"},
    )
    return [
        repo
        for repo in repos
        if not repo.get("fork")
        and repo.get("name") not in PROFILE_REPOS
        and repo.get("name") not in HIDDEN_REPOS
    ]


def clean_description(name: str, description: str | None) -> str:
    desc = (description or "No description yet.").strip().rstrip(".")
    prefixes = (f"{name} is a ", f"{name} is an ", f"{name} is ")
    for prefix in prefixes:
        if desc.lower().startswith(prefix.lower()):
            desc = desc[len(prefix):]
            if desc:
                desc = desc[0].upper() + desc[1:]
            break
    return desc


def format_repo_line(repo: dict, html: bool = False) -> str:
    name = repo["name"]
    url = repo["html_url"]
    desc = clean_description(name, repo.get("description"))
    lang = repo.get("language") or "multi"
    stars = repo.get("stargazers_count") or 0
    pushed = short_date(repo.get("pushed_at"))
    star_bit = f" · {stars}★" if stars else ""
    if html:
        return (
            f'        <li><a href="{url}"><b>{name}</b></a> — {desc} '
            f"<i>({lang}{star_bit} · {pushed})</i></li>"
        )
    return f"- **[{name}]({url})** — {desc} · _{lang}{star_bit} · updated {pushed}_"


def event_line(event: dict) -> str | None:
    kind = event.get("type")
    repo = (event.get("repo") or {}).get("name") or ""
    repo_url = f"https://github.com/{repo}" if repo else ""
    payload = event.get("payload") or {}
    when = short_date(event.get("created_at"))

    if kind in {"WatchEvent", "ForkEvent", "DeleteEvent"}:
        return None
    if kind == "PushEvent":
        commits = payload.get("size") or len(payload.get("commits") or [])
        if not commits:
            return None
        noun = "commit" if commits == 1 else "commits"
        return f"- Pushed {commits} {noun} to [{repo}]({repo_url}) · {when}"
    if kind == "PullRequestEvent":
        action = payload.get("action")
        pr = payload.get("pull_request") or {}
        title = pr.get("title")
        if not title:
            return None
        url = pr.get("html_url") or repo_url
        return f"- {action.capitalize() if action else 'Updated'} PR [{title}]({url}) in {repo} · {when}"
    if kind == "IssuesEvent":
        action = payload.get("action")
        issue = payload.get("issue") or {}
        title = issue.get("title") or "issue"
        url = issue.get("html_url") or repo_url
        return f"- {action.capitalize() if action else 'Updated'} issue [{title}]({url}) · {when}"
    if kind == "CreateEvent" and payload.get("ref_type") == "repository":
        return f"- Created [{repo}]({repo_url}) · {when}"
    if kind == "ReleaseEvent":
        release = payload.get("release") or {}
        tag = release.get("tag_name") or "release"
        url = release.get("html_url") or repo_url
        return f"- Published [{tag}]({url}) in {repo} · {when}"
    if kind == "ForkEvent":
        return f"- Forked [{repo}]({repo_url}) · {when}"
    if kind == "WatchEvent":
        return f"- Starred [{repo}]({repo_url}) · {when}"
    if kind == "IssueCommentEvent":
        issue = payload.get("issue") or {}
        title = issue.get("title") or "discussion"
        url = (payload.get("comment") or {}).get("html_url") or issue.get("html_url") or repo_url
        return f"- Commented on [{title}]({url}) · {when}"
    return None


def medium_posts(limit: int = 4) -> list[str]:
    req = urllib.request.Request(
        f"https://medium.com/feed/@{USERNAME}",
        headers={"User-Agent": "simeetnayan81-dashboard"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            root = ET.fromstring(response.read())
    except (urllib.error.URLError, ET.ParseError):
        return ["- Medium feed is temporarily unavailable."]

    lines = []
    for item in root.findall("./channel/item")[:limit]:
        title = (item.findtext("title") or "Untitled").strip()
        link = (item.findtext("link") or "").split("?")[0]
        pub = item.findtext("pubDate") or ""
        date = ""
        if pub:
            try:
                date = datetime.strptime(pub[:16], "%a, %d %b %Y").strftime("%b %Y")
            except ValueError:
                date = pub[:16]
        suffix = f" · _{date}_" if date else ""
        lines.append(f"- [{title}]({link}){suffix}")
    return lines or ["- No public Medium posts found."]


def repo_from_url(url: str) -> str:
    parts = (url or "").rstrip("/").split("/")
    if "github.com" in parts:
        idx = parts.index("github.com")
        if len(parts) >= idx + 3:
            return f"{parts[idx + 1]}/{parts[idx + 2]}"
    if "repos" in parts:
        idx = parts.index("repos")
        if len(parts) >= idx + 3:
            return f"{parts[idx + 1]}/{parts[idx + 2]}"
    return ""


def recent_contributions(events: list[dict], limit_repos: int = 8, limit_each: int = 5) -> str:
    groups: dict[str, list[tuple[str, str]]] = {}

    def add(repo: str, when: str, line: str) -> None:
        if not repo or not line:
            return
        bucket = groups.setdefault(repo, [])
        if line not in {item[1] for item in bucket}:
            bucket.append((when, line))

    try:
        data = github(
            "/search/issues",
            {"q": f"author:{USERNAME} type:pr", "sort": "updated", "order": "desc", "per_page": "20"},
        )
        for item in data.get("items") or []:
            repo = repo_from_url(item.get("repository_url") or item.get("html_url") or "")
            title = item.get("title") or "pull request"
            url = item.get("html_url") or ""
            state = "merged" if (item.get("pull_request") or {}).get("merged_at") else item.get("state")
            when = item.get("updated_at") or ""
            add(repo, when, f"- [{title}]({url}) · {state} · {short_date(when)}")
    except urllib.error.HTTPError:
        pass

    for event in events:
        repo = (event.get("repo") or {}).get("name") or ""
        when = event.get("created_at") or ""
        line = event_line(event)
        if line:
            add(repo, when, line)

    if not groups:
        return "- No recent public contributions."

    ranked = sorted(
        groups.items(),
        key=lambda item: max((stamp for stamp, _ in item[1]), default=""),
        reverse=True,
    )[:limit_repos]

    blocks = []
    for repo, items in ranked:
        items = sorted(items, key=lambda item: item[0], reverse=True)[:limit_each]
        org = repo.split("/")[0]
        repo_url = f"https://github.com/{repo}"
        org_url = f"https://github.com/{org}"
        noun = "item" if len(items) == 1 else "items"
        blocks.append(
            f"<details>\n"
            f"<summary><b><a href=\"{repo_url}\">{repo}</a></b> · "
            f"<a href=\"{org_url}\">{org}</a> · {len(items)} {noun}</summary>\n\n"
            + "\n".join(line for _, line in items)
            + "\n</details>"
        )
    return "\n\n".join(blocks)


def contribution_weeks() -> list[list[dict]]:
    query = """
    query($login: String!) {
      user(login: $login) {
        contributionsCollection {
          contributionCalendar {
            weeks {
              contributionDays { date contributionCount }
            }
          }
        }
      }
    }
    """
    payload = json.dumps({"query": query, "variables": {"login": USERNAME}}).encode()
    headers = {
        "User-Agent": "simeetnayan81-dashboard",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request("https://api.github.com/graphql", data=payload, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        data = json.load(response)
    weeks = (
        data.get("data", {})
        .get("user", {})
        .get("contributionsCollection", {})
        .get("contributionCalendar", {})
        .get("weeks", [])
    )
    return weeks[-52:]


def write_activity_svg(path: Path) -> None:
    weeks = contribution_weeks()
    if not weeks:
        return
    cell, gap, pad = 11, 3, 16
    width = pad * 2 + len(weeks) * (cell + gap) - gap
    height = pad * 2 + 7 * (cell + gap) - gap + 8
    levels = ("#161B22", "#0D2F4A", "#1F4E79", "#388BFD", "#58A6FF")

    def color(count: int) -> str:
        if count <= 0:
            return levels[0]
        if count == 1:
            return levels[1]
        if count <= 3:
            return levels[2]
        if count <= 6:
            return levels[3]
        return levels[4]

    rects = []
    for wi, week in enumerate(weeks):
        for day in week.get("contributionDays") or []:
            try:
                weekday = datetime.fromisoformat(day["date"]).weekday()
            except (KeyError, ValueError):
                continue
            # GitHub weeks start Sunday; Python weekday() is Monday=0.
            gi = (weekday + 1) % 7
            x = pad + wi * (cell + gap)
            y = pad + gi * (cell + gap)
            count = int(day.get("contributionCount") or 0)
            rects.append(
                f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" '
                f'fill="{color(count)}"><title>{day["date"]}: {count}</title></rect>'
            )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="Contribution activity">'
        f'<rect width="{width}" height="{height}" fill="#0D1117" rx="8"/>'
        + "".join(rects)
        + "</svg>\n"
    )
    path.write_text(svg, encoding="utf-8")


def build_identity(repos: list[dict], oss_names: list[str], now: datetime) -> str:
    active = [repo["name"] for repo in repos[:3]]
    now_line = "  ·  ".join(active) if active else "building on-device ML systems"
    oss_line = "  ·  ".join(oss_names[:4]) if oss_names else "pytorch/rl  ·  mlx"
    stamp = now.strftime("%Y-%m-%d %H:%M UTC")
    return (
        "```text\n"
        f"whoami     Simeet Nayan · {USERNAME}\n"
        "role       Software Engineer @ Wells Fargo · Specialist @ xAI\n"
        "base       Bengaluru, India\n"
        "focus      open source · machine learning · ML systems\n"
        f"now        {now_line}\n"
        f"oss        {oss_line}\n"
        f"updated    {stamp}\n"
        "```"
    )


def main() -> None:
    now = datetime.now(timezone.utc)
    repos = owned_repos()
    events = github(f"/users/{USERNAME}/events/public", {"per_page": "30"})

    oss_names: list[str] = []
    for event in events:
        repo = (event.get("repo") or {}).get("name") or ""
        if repo and not repo.startswith(f"{USERNAME}/") and repo not in oss_names:
            oss_names.append(repo)

    shipping = repos[:4]
    recent = repos[:6]
    activity: list[str] = []
    for event in events:
        line = event_line(event)
        if line and line not in activity:
            activity.append(line)
        if len(activity) >= 8:
            break

    text = README.read_text(encoding="utf-8")
    text = replace_section(text, "IDENTITY", build_identity(repos, oss_names, now))
    text = replace_section(
        text,
        "NOW_SHIPPING",
        "      <ul>\n" + "\n".join(format_repo_line(repo, html=True) for repo in shipping) + "\n      </ul>",
    )
    try:
        write_activity_svg(ROOT / "assets" / "activity.svg")
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, json.JSONDecodeError):
        pass

    text = replace_section(text, "RECENT_REPOS", "\n".join(format_repo_line(repo) for repo in recent))
    text = replace_section(text, "RECENT_PRS", recent_contributions(events))
    text = replace_section(text, "WRITING", "\n".join(medium_posts()))
    text = replace_section(text, "ACTIVITY", "\n".join(activity or ["- No recent public events."]))
    text = replace_section(
        text,
        "FOOTER",
        f'<p align="center"><i>Dashboard last refreshed: {now.strftime("%b %d, %Y %H:%M UTC")} · '
        "stats cards and badges update on view · activity rewritten by "
        "<a href=\"./scripts/update_dashboard.py\">scripts/update_dashboard.py</a></i></p>",
    )
    README.write_text(text, encoding="utf-8")
    print(f"updated {README} at {now.isoformat()}")


if __name__ == "__main__":
    main()
