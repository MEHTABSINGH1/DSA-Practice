#!/usr/bin/env python3
"""Pull accepted LeetCode submissions and commit them into this repository.

Same folder layout and README format as the browser extension, so both can
run side by side: anything the extension already pushed is skipped here.

Env:
    LEETCODE_SESSION, LEETCODE_CSRF_TOKEN  cookies from a logged-in browser
    LAYOUT     difficulty | topic | flat   (default: difficulty)
    MAX_PAGES  pages of 20 submissions to scan, 0 = whole history (default: 3)
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LEETCODE = "https://leetcode.com"
SESSION = os.environ.get("LEETCODE_SESSION", "")
CSRF = os.environ.get("LEETCODE_CSRF_TOKEN", "")
LAYOUT = os.environ.get("LAYOUT", "difficulty")
MAX_PAGES = int(os.environ.get("MAX_PAGES") or 3)

EXTENSIONS = {
    "python": "py", "python3": "py", "pythondata": "py", "pandas": "py",
    "cpp": "cpp", "c": "c", "java": "java", "csharp": "cs",
    "javascript": "js", "typescript": "ts", "golang": "go", "kotlin": "kt",
    "swift": "swift", "rust": "rs", "ruby": "rb", "scala": "scala", "php": "php",
    "dart": "dart", "elixir": "ex", "erlang": "erl", "racket": "rkt",
    "mysql": "sql", "mssql": "sql", "oraclesql": "sql", "postgresql": "sql", "bash": "sh",
}

STATS_START, STATS_END = "<!-- stats:start -->", "<!-- stats:end -->"
LIST_START, LIST_END = "<!-- problems:start -->", "<!-- problems:end -->"

README_TEMPLATE = f"""# DSA Practice

My solutions to LeetCode problems.

{STATS_START}
{STATS_END}

## Problems

{LIST_START}
{LIST_END}
"""

QUESTION_QUERY = """
query questionData($titleSlug: String!) {
  question(titleSlug: $titleSlug) {
    questionFrontendId
    title
    titleSlug
    content
    difficulty
    topicTags { name }
  }
}"""


# ---------------------------------------------------------------- LeetCode

def request(path, payload=None):
    headers = {
        "Cookie": f"LEETCODE_SESSION={SESSION}; csrftoken={CSRF}",
        "x-csrftoken": CSRF,
        "Referer": LEETCODE + "/",
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/json",
    }
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(LEETCODE + path, data=data, headers=headers)

    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                return json.load(res)
        except urllib.error.HTTPError as err:
            if err.code in (403, 401):
                sys.exit("LeetCode rejected the cookies. Refresh LEETCODE_SESSION and LEETCODE_CSRF_TOKEN.")
            if err.code == 429 or err.code >= 500:
                time.sleep(3 * 2 ** attempt)
                continue
            raise
    raise RuntimeError(f"LeetCode kept failing for {path}")


def accepted_submissions():
    """Latest accepted submission per (problem, language), oldest first."""
    latest = {}
    offset, last_key, page = 0, "", 0

    while MAX_PAGES == 0 or page < MAX_PAGES:
        query = urllib.parse.urlencode({"offset": offset, "limit": 20, "lastkey": last_key})
        data = request(f"/api/submissions/?{query}")
        for sub in data.get("submissions_dump", []):
            if sub.get("status_display") != "Accepted":
                continue
            latest.setdefault((sub["title_slug"], sub["lang"]), sub)
        if not data.get("has_next"):
            break
        offset += 20
        last_key = data.get("last_key", "")
        page += 1
        time.sleep(1.5)

    return sorted(latest.values(), key=lambda s: int(s["timestamp"]))


_questions = {}

def question(slug):
    if slug not in _questions:
        data = request("/graphql/", {"query": QUESTION_QUERY, "variables": {"titleSlug": slug}})
        _questions[slug] = data["data"]["question"]
        time.sleep(0.5)
    return _questions[slug]


# ------------------------------------------------------------------ Format

def number(q):
    return str(q["questionFrontendId"]).zfill(4)


def folder_for(q):
    name = f"{number(q)}-{q['titleSlug']}"
    if LAYOUT == "difficulty":
        return f"{q['difficulty']}/{name}"
    if LAYOUT == "topic":
        tags = q.get("topicTags") or []
        topic = re.sub(r"\s+", "-", tags[0]["name"]) if tags else "Misc"
        return f"{topic}/{name}"
    return name


def problem_url(q):
    return f"https://leetcode.com/problems/{q['titleSlug']}/"


def problem_readme(q):
    tags = " ".join(f"`{t['name']}`" for t in q.get("topicTags") or [])
    return "\n".join([
        f"# {q['questionFrontendId']}. {q['title']}",
        "",
        f"**Difficulty:** {q['difficulty']}  ",
        f"**Topics:** {tags or '-'}  ",
        f"**Link:** {problem_url(q)}",
        "",
        "## Problem",
        "",
        q.get("content") or "_Premium problem, description not available._",
        "",
    ])


def with_newline(code):
    return code if code.endswith("\n") else code + "\n"


def parse_rows(readme):
    rows = {}
    start, end = readme.find(LIST_START), readme.find(LIST_END)
    if start == -1 or end == -1:
        return rows
    for line in readme[start + len(LIST_START):end].split("\n"):
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) != 5 or cells[0] == "#" or re.fullmatch(r":?-+:?", cells[0]):
            continue
        solutions = dict(re.findall(r"\[([^\]]+)\]\(([^)]+)\)", cells[4]))
        rows[cells[0]] = {
            "num": cells[0], "title": cells[1], "difficulty": cells[2],
            "topics": cells[3], "solutions": solutions,
        }
    return rows


def natural_key(text):
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", text)]


def render_list(rows):
    lines = ["| # | Title | Difficulty | Topics | Solution |", "|---|---|---|---|---|"]
    for r in sorted(rows.values(), key=lambda r: natural_key(r["num"])):
        links = " · ".join(f"[{lang}]({path})" for lang, path in r["solutions"].items())
        lines.append(f"| {r['num']} | {r['title']} | {r['difficulty']} | {r['topics']} | {links} |")
    return "\n".join(lines)


def render_stats(rows):
    count = {"Easy": 0, "Medium": 0, "Hard": 0}
    for r in rows.values():
        if r["difficulty"] in count:
            count[r["difficulty"]] += 1
    return "\n".join([
        "| Easy | Medium | Hard | Total |",
        "|:---:|:---:|:---:|:---:|",
        f"| {count['Easy']} | {count['Medium']} | {count['Hard']} | {len(rows)} |",
    ])


def replace_between(text, start, end, body):
    s, e = text.find(start), text.find(end)
    if s == -1 or e == -1:
        return text
    return text[:s + len(start)] + "\n" + body + "\n" + text[e:]


def update_readme(existing, entry):
    text = existing or README_TEMPLATE
    if LIST_START not in text or LIST_END not in text:
        text = f"{text.rstrip()}\n\n{STATS_START}\n{STATS_END}\n\n## Problems\n\n{LIST_START}\n{LIST_END}\n"

    rows = parse_rows(text)
    previous = rows.get(entry["num"])
    merged = dict(previous["solutions"]) if previous else {}
    merged.update(entry["solutions"])
    rows[entry["num"]] = {**entry, "solutions": merged}

    text = replace_between(text, LIST_START, LIST_END, render_list(rows))
    return replace_between(text, STATS_START, STATS_END, render_stats(rows))


# --------------------------------------------------------------------- Git

def git(*args, env=None):
    subprocess.run(["git", *args], check=True, env=env)


def main():
    if not SESSION or not CSRF:
        print("LEETCODE_SESSION / LEETCODE_CSRF_TOKEN secrets not set, skipping.")
        return

    readme_path = Path("README.md")
    added = 0

    for sub in accepted_submissions():
        q = question(sub["title_slug"])
        if not q:
            continue

        lang_name = sub.get("lang_name") or sub["lang"]
        folder = folder_for(q)
        solution_path = f"{folder}/solution.{EXTENSIONS.get(sub['lang'], 'txt')}"
        code = with_newline(sub["code"])

        solution_file = Path(solution_path)
        if solution_file.exists() and solution_file.read_text(encoding="utf-8") == code:
            continue

        readme = readme_path.read_text(encoding="utf-8") if readme_path.exists() else None
        num = number(q)
        previous = parse_rows(readme or "").get(num)
        is_update = bool(previous and lang_name in previous["solutions"])

        solution_file.parent.mkdir(parents=True, exist_ok=True)
        solution_file.write_text(code, encoding="utf-8")
        Path(folder, "README.md").write_text(problem_readme(q), encoding="utf-8")
        readme_path.write_text(update_readme(readme, {
            "num": num,
            "title": f"[{q['title']}]({problem_url(q)})",
            "difficulty": q["difficulty"],
            "topics": ", ".join(t["name"] for t in q.get("topicTags") or []) or "-",
            "solutions": {lang_name: urllib.parse.quote(solution_path)},
        }), encoding="utf-8")

        subject = f"{'Update' if is_update else 'Add'} {num}. {q['title']} ({lang_name})"
        body = [f"Difficulty: {q['difficulty']}"]
        if sub.get("runtime"):
            body.append(f"Runtime: {sub['runtime']}")
        if sub.get("memory"):
            body.append(f"Memory: {sub['memory']}")

        # Commit with the time you actually solved it.
        when = datetime.fromtimestamp(int(sub["timestamp"]), tz=timezone.utc).isoformat()
        env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
        git("add", "--", folder, "README.md")
        git("commit", "-q", "-m", subject, "-m", "\n".join(body), env=env)
        print(subject)
        added += 1

    print(f"{added} new commit(s)." if added else "Already up to date.")


if __name__ == "__main__":
    main()
