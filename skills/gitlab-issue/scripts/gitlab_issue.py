from __future__ import annotations

"""
skills/gitlab-issue/scripts/gitlab_issue.py

业务目的
- 统一管理当前仓库对应 GitLab 项目的 issue：创建、读取、列表、更新、评论、删除评论、删除。

安全约束（AGENTS.md）
- 不在日志/输出中打印 token。

默认约定
- 启动时先加载项目根目录 `.env`，再读取 `GITLAB_PRIVATE_TOKEN`。
- 若不显式传 `--base-url / --project`，则从 `git remote get-url origin` 推导当前 GitLab 项目。

示例
- 创建 issue：
  .venv/bin/python skills/gitlab-issue/scripts/gitlab_issue.py create \
    --title "docs: 补齐 GitLab issue 协作 skill" \
    --labels "(中),文档" \
    --body-file .tmp/gitlab_issue_body.md

- 读取 issue（包含评论）：
  .venv/bin/python skills/gitlab-issue/scripts/gitlab_issue.py read --iid 40 --notes

- 列出全部 issue：
  .venv/bin/python skills/gitlab-issue/scripts/gitlab_issue.py list --state all

- 更新 issue description：
  .venv/bin/python skills/gitlab-issue/scripts/gitlab_issue.py update \
    --iid 40 \
    --labels "(高),bug" \
    --body-file .tmp/gitlab_issue_body.md

- 追加到既有 description：
  .venv/bin/python skills/gitlab-issue/scripts/gitlab_issue.py update \
    --iid 40 \
    --append \
    --body-file .tmp/gitlab_issue_body.md

- 回复 issue：
  .venv/bin/python skills/gitlab-issue/scripts/gitlab_issue.py comment \
    --iid 40 \
    --body "已完成实现，等待验收。"

- 删除 issue 评论：
  .venv/bin/python skills/gitlab-issue/scripts/gitlab_issue.py delete-note \
    --iid 40 \
    --note-id 1234

- 删除 issue：
  .venv/bin/python skills/gitlab-issue/scripts/gitlab_issue.py delete --iid 40 --yes
"""

import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from dotenv import load_dotenv


_TOKEN_ENV = "GITLAB_PRIVATE_TOKEN"
_REQUEST_TIMEOUT = 30


@dataclass(frozen=True)
class GitlabTarget:
    base_url: str
    project_path: str


def _load_repo_dotenv() -> None:
    """
    尝试加载项目根目录 `.env`，但不覆盖调用方已经显式设置的环境变量。
    """
    env_path = Path(__file__).resolve().parents[3] / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return (proc.stdout or "").strip()


def _infer_from_origin() -> GitlabTarget:
    """
    从 `git remote get-url origin` 推导 GitLab base_url 与 project_path。
    """
    origin = _run(["git", "remote", "get-url", "origin"])
    if origin.endswith(".git"):
        origin = origin[: -len(".git")]

    http_match = re.match(r"^(https?://)(?P<host>[^/]+)(?P<path>/.*)$", origin)
    if http_match:
        base_url = f"{http_match.group(1)}{http_match.group('host')}"
        project_path = http_match.group("path").lstrip("/")
        if not project_path:
            raise RuntimeError(f"Cannot infer project path from origin: {origin!r}")
        return GitlabTarget(base_url=base_url, project_path=project_path)

    ssh_match = re.match(r"^git@(?P<host>[^:]+):(?P<path>.+)$", origin)
    if ssh_match:
        base_url = f"https://{ssh_match.group('host')}"
        project_path = ssh_match.group("path").lstrip("/")
        if not project_path:
            raise RuntimeError(f"Cannot infer project path from origin: {origin!r}")
        return GitlabTarget(base_url=base_url, project_path=project_path)

    raise RuntimeError(f"Unsupported origin url: {origin!r}")


def _load_token() -> str:
    token = os.getenv(_TOKEN_ENV)
    if isinstance(token, str) and token.strip():
        return token.strip()
    raise RuntimeError(
        "GitLab token not configured. The script has already checked environment variables "
        "and the repo .env file. Please provide or export: "
        + _TOKEN_ENV
        + " (token content will not be printed)."
    )


def _project_api_path(project_path: str) -> str:
    return quote(project_path, safe="")


def _request(
    *,
    method: str,
    url: str,
    token: str,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> requests.Response:
    resp = requests.request(
        method=method,
        url=url,
        headers={"PRIVATE-TOKEN": token},
        params=params,
        data=data,
        timeout=_REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp


def _response_json(resp: requests.Response) -> Any:
    if not resp.content:
        return None
    return resp.json()


def _gitlab_project_id(*, base_url: str, token: str, project_path: str) -> int:
    url = f"{base_url.rstrip('/')}/api/v4/projects/{_project_api_path(project_path)}"
    resp = _request(method="GET", url=url, token=token)
    data = _response_json(resp) or {}
    return int(data["id"])


def list_project_labels(
    *,
    base_url: str,
    token: str,
    project_id: int,
    per_page: int = 100,
) -> list[dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/api/v4/projects/{project_id}/labels"
    page = 1
    items: list[dict[str, Any]] = []
    while True:
        resp = _request(
            method="GET",
            url=url,
            token=token,
            params={
                "page": page,
                "per_page": per_page,
            },
        )
        data = _response_json(resp) or []
        items.extend([dict(item) for item in data])
        next_page = (resp.headers.get("X-Next-Page") or "").strip()
        if not next_page:
            return items
        page = int(next_page)


def _labels_payload(labels: list[str] | None) -> str | None:
    if not labels:
        return None
    return ",".join(labels)


def create_issue(
    *,
    base_url: str,
    token: str,
    project_id: int,
    title: str,
    description: str,
    labels: list[str] | None = None,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/api/v4/projects/{project_id}/issues"
    data: dict[str, Any] = {"title": title, "description": description}
    labels_payload = _labels_payload(labels)
    if isinstance(labels_payload, str) and labels_payload:
        data["labels"] = labels_payload
    resp = _request(
        method="POST",
        url=url,
        token=token,
        data=data,
    )
    return dict(_response_json(resp) or {})


def get_issue(*, base_url: str, token: str, project_id: int, iid: int) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/api/v4/projects/{project_id}/issues/{int(iid)}"
    resp = _request(method="GET", url=url, token=token)
    return dict(_response_json(resp) or {})


def update_issue(
    *,
    base_url: str,
    token: str,
    project_id: int,
    iid: int,
    title: str | None = None,
    description: str | None = None,
    state_event: str | None = None,
    labels: list[str] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if isinstance(title, str) and title.strip():
        data["title"] = title.strip()
    if isinstance(description, str):
        data["description"] = description
    if isinstance(state_event, str) and state_event.strip():
        data["state_event"] = state_event.strip()
    labels_payload = _labels_payload(labels)
    if isinstance(labels_payload, str) and labels_payload:
        data["labels"] = labels_payload
    if not data:
        raise RuntimeError("update 至少需要传一个变更项：--title / --body / --body-file / --state-event / --labels")

    url = f"{base_url.rstrip('/')}/api/v4/projects/{project_id}/issues/{int(iid)}"
    resp = _request(method="PUT", url=url, token=token, data=data)
    return dict(_response_json(resp) or {})


def list_project_issues(
    *,
    base_url: str,
    token: str,
    project_id: int,
    state: str = "all",
    search: str | None = None,
    labels: str | None = None,
    max_items: int | None = None,
    per_page: int = 100,
) -> list[dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/api/v4/projects/{project_id}/issues"
    page = 1
    items: list[dict[str, Any]] = []
    while True:
        params: dict[str, Any] = {
            "state": state,
            "page": page,
            "per_page": per_page,
            "order_by": "updated_at",
            "sort": "desc",
        }
        if isinstance(search, str) and search.strip():
            params["search"] = search.strip()
        if isinstance(labels, str) and labels.strip():
            params["labels"] = labels.strip()
        resp = _request(method="GET", url=url, token=token, params=params)
        data = _response_json(resp) or []
        for item in data:
            items.append(dict(item))
            if isinstance(max_items, int) and max_items > 0 and len(items) >= max_items:
                return items
        next_page = (resp.headers.get("X-Next-Page") or "").strip()
        if not next_page:
            return items
        page = int(next_page)


def list_issue_notes(
    *,
    base_url: str,
    token: str,
    project_id: int,
    iid: int,
    max_items: int = 100,
    per_page: int = 100,
) -> list[dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/api/v4/projects/{project_id}/issues/{int(iid)}/notes"
    page = 1
    notes: list[dict[str, Any]] = []
    while True:
        resp = _request(
            method="GET",
            url=url,
            token=token,
            params={
                "page": page,
                "per_page": per_page,
                "sort": "asc",
            },
        )
        data = _response_json(resp) or []
        for item in data:
            notes.append(dict(item))
            if len(notes) >= max_items:
                return notes
        next_page = (resp.headers.get("X-Next-Page") or "").strip()
        if not next_page:
            return notes
        page = int(next_page)


def create_issue_note(
    *,
    base_url: str,
    token: str,
    project_id: int,
    iid: int,
    body: str,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/api/v4/projects/{project_id}/issues/{int(iid)}/notes"
    resp = _request(
        method="POST",
        url=url,
        token=token,
        data={"body": body},
    )
    return dict(_response_json(resp) or {})


def delete_issue_note(
    *,
    base_url: str,
    token: str,
    project_id: int,
    iid: int,
    note_id: int,
) -> dict[str, Any] | None:
    url = f"{base_url.rstrip('/')}/api/v4/projects/{project_id}/issues/{int(iid)}/notes/{int(note_id)}"
    resp = _request(method="DELETE", url=url, token=token)
    data = _response_json(resp)
    if isinstance(data, dict):
        return dict(data)
    return None


def delete_issue(
    *,
    base_url: str,
    token: str,
    project_id: int,
    iid: int,
) -> dict[str, Any] | None:
    url = f"{base_url.rstrip('/')}/api/v4/projects/{project_id}/issues/{int(iid)}"
    resp = _request(method="DELETE", url=url, token=token)
    data = _response_json(resp)
    if isinstance(data, dict):
        return dict(data)
    return None


def _load_body(*, body: str | None, body_file: Path | None, allow_empty: bool = False) -> str | None:
    if isinstance(body, str):
        if body or allow_empty:
            return body
        raise RuntimeError("--body 不能为空；若需清空 description，请使用 --body \"\"")

    if body_file is None:
        return None
    if not body_file.exists():
        raise RuntimeError(f"body-file not found: {body_file}")
    return body_file.read_text(encoding="utf-8")


def _parse_labels_arg(raw: str | None) -> list[str] | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    seen: set[str] = set()
    labels: list[str] = []
    for part in raw.split(","):
        label = part.strip()
        if not label or label in seen:
            continue
        seen.add(label)
        labels.append(label)
    return labels or None


def _assert_labels_exist(
    *,
    base_url: str,
    token: str,
    project_id: int,
    labels: list[str] | None,
) -> list[str] | None:
    if not labels:
        return None
    existing = {
        str(item.get("name") or "").strip()
        for item in list_project_labels(base_url=base_url, token=token, project_id=project_id)
    }
    missing = [label for label in labels if label not in existing]
    if missing:
        raise RuntimeError(
            "These labels do not exist in the current GitLab project, "
            "and the CLI refuses to create new labels implicitly: "
            + ", ".join(missing)
        )
    return labels


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def _resolve_target(args: argparse.Namespace) -> GitlabTarget:
    inferred = _infer_from_origin()
    return GitlabTarget(
        base_url=str(args.base_url or inferred.base_url).strip(),
        project_path=str(args.project or inferred.project_path).strip(),
    )


def _build_common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified GitLab issue CLI for the current repo project.")
    parser.add_argument("--base-url", help="GitLab base url, e.g. https://scisaga.cc:233")
    parser.add_argument("--project", help="GitLab project path, e.g. rewind/raelyn")
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = _build_common_parser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create", help="创建新 issue")
    create_parser.add_argument("--title", required=True, help="Issue 标题")
    create_parser.add_argument("--labels", help="Issue labels，逗号分隔；仅允许使用项目内已有 labels")
    create_body_group = create_parser.add_mutually_exclusive_group(required=True)
    create_body_group.add_argument("--body", help="Issue description 文本")
    create_body_group.add_argument("--body-file", type=Path, help="Issue description Markdown 文件")

    read_parser = subparsers.add_parser("read", help="读取单个 issue")
    read_parser.add_argument("--iid", type=int, required=True, help="Issue IID")
    read_parser.add_argument("--notes", action="store_true", help="同时拉取 issue 评论")
    read_parser.add_argument("--notes-limit", type=int, default=100, help="评论最大返回条数（默认 100）")

    list_parser = subparsers.add_parser("list", help="列出当前项目 issue")
    list_parser.add_argument("--state", choices=("opened", "closed", "all"), default="all", help="Issue 状态过滤")
    list_parser.add_argument("--search", help="按标题/description 搜索")
    list_parser.add_argument("--labels", help="按 GitLab labels 过滤，逗号分隔")
    list_parser.add_argument("--limit", type=int, help="最多返回多少条；默认拉取全部分页")

    update_parser = subparsers.add_parser("update", help="更新 issue 标题/description/状态")
    update_parser.add_argument("--iid", type=int, required=True, help="Issue IID")
    update_parser.add_argument("--title", help="新的 issue 标题")
    update_parser.add_argument("--labels", help="新的 issue labels，逗号分隔；仅允许使用项目内已有 labels")
    update_body_group = update_parser.add_mutually_exclusive_group()
    update_body_group.add_argument("--body", help="新的 issue description 文本；传空串可清空")
    update_body_group.add_argument("--body-file", type=Path, help="新的 issue description Markdown 文件")
    update_parser.add_argument("--append", action="store_true", help="把新内容追加到既有 description 后")
    update_parser.add_argument(
        "--state-event",
        choices=("close", "reopen"),
        help="状态变更；close 表示关闭，reopen 表示重开",
    )

    comment_parser = subparsers.add_parser("comment", help="回复 issue")
    comment_parser.add_argument("--iid", type=int, required=True, help="Issue IID")
    comment_body_group = comment_parser.add_mutually_exclusive_group(required=True)
    comment_body_group.add_argument("--body", help="评论文本")
    comment_body_group.add_argument("--body-file", type=Path, help="评论 Markdown 文件")

    delete_note_parser = subparsers.add_parser("delete-note", help="删除 issue 评论")
    delete_note_parser.add_argument("--iid", type=int, required=True, help="Issue IID")
    delete_note_parser.add_argument("--note-id", type=int, required=True, help="评论 note ID")

    delete_parser = subparsers.add_parser("delete", help="删除 issue")
    delete_parser.add_argument("--iid", type=int, required=True, help="Issue IID")
    delete_parser.add_argument("--yes", action="store_true", help="确认执行删除")

    return parser


def run_cli(args: argparse.Namespace) -> int:
    _load_repo_dotenv()
    token = _load_token()
    target = _resolve_target(args)
    project_id = _gitlab_project_id(base_url=target.base_url, token=token, project_path=target.project_path)

    if args.command == "create":
        description = _load_body(body=args.body, body_file=args.body_file)
        labels = _assert_labels_exist(
            base_url=target.base_url,
            token=token,
            project_id=project_id,
            labels=_parse_labels_arg(args.labels),
        )
        issue = create_issue(
            base_url=target.base_url,
            token=token,
            project_id=project_id,
            title=str(args.title).strip(),
            description=str(description),
            labels=labels,
        )
        _print_json(issue)
        return 0

    if args.command == "read":
        issue = get_issue(base_url=target.base_url, token=token, project_id=project_id, iid=args.iid)
        if args.notes:
            notes = list_issue_notes(
                base_url=target.base_url,
                token=token,
                project_id=project_id,
                iid=args.iid,
                max_items=args.notes_limit,
            )
            _print_json({"issue": issue, "notes": notes})
        else:
            _print_json(issue)
        return 0

    if args.command == "list":
        issues = list_project_issues(
            base_url=target.base_url,
            token=token,
            project_id=project_id,
            state=args.state,
            search=args.search,
            labels=args.labels,
            max_items=args.limit,
        )
        _print_json({"count": len(issues), "issues": issues})
        return 0

    if args.command == "update":
        new_desc = _load_body(body=args.body, body_file=args.body_file, allow_empty=True)
        description: str | None = new_desc
        if args.append:
            if new_desc is None:
                raise RuntimeError("--append 只能与 --body 或 --body-file 一起使用")
            current = get_issue(base_url=target.base_url, token=token, project_id=project_id, iid=args.iid)
            current_desc = str(current.get("description") or "")
            description = (current_desc.rstrip() + "\n\n" + new_desc.lstrip()).strip() + "\n"
        labels = _assert_labels_exist(
            base_url=target.base_url,
            token=token,
            project_id=project_id,
            labels=_parse_labels_arg(args.labels),
        )
        issue = update_issue(
            base_url=target.base_url,
            token=token,
            project_id=project_id,
            iid=args.iid,
            title=args.title,
            description=description,
            state_event=args.state_event,
            labels=labels,
        )
        _print_json(issue)
        return 0

    if args.command == "comment":
        body = _load_body(body=args.body, body_file=args.body_file)
        note = create_issue_note(
            base_url=target.base_url,
            token=token,
            project_id=project_id,
            iid=args.iid,
            body=str(body),
        )
        _print_json(note)
        return 0

    if args.command == "delete-note":
        result = delete_issue_note(
            base_url=target.base_url,
            token=token,
            project_id=project_id,
            iid=args.iid,
            note_id=args.note_id,
        )
        _print_json({"deleted": True, "note": result})
        return 0

    if args.command == "delete":
        if not args.yes:
            raise RuntimeError("删除 issue 前必须显式确认，请追加 --yes")
        result = delete_issue(
            base_url=target.base_url,
            token=token,
            project_id=project_id,
            iid=args.iid,
        )
        _print_json({"deleted": True, "issue": result})
        return 0

    raise RuntimeError(f"Unsupported command: {args.command!r}")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return run_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
