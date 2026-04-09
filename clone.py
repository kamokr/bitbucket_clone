#!/usr/bin/env python3
"""Clone Bitbucket repositories from configured workspaces as bare repos.

Edit the constants in the Configuration section before running:
1. Set BITBUCKET_USERNAME and BITBUCKET_API_TOKEN.
2. Set WORKSPACES and DESTINATION_ROOT.
3. Run: python clone.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, NoReturn, Optional
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from requests.auth import HTTPBasicAuth
from urllib3.util.retry import Retry


# =============================
# Configuration (edit these)
# =============================
BITBUCKET_USERNAME = "your_username"
BITBUCKET_API_TOKEN = "your_api_token"

# Example: ["team-workspace-a", "team-workspace-b"]
WORKSPACES = ["your_workspace"]

# Root directory where bare repositories will be cloned.
# Repositories are created as: <DESTINATION_ROOT>/<workspace>/<repo>.git
DESTINATION_ROOT = Path("./bitbucket_bare_repos")

API_BASE_URL = "https://api.bitbucket.org/2.0"
PAGELEN = 100
REQUEST_TIMEOUT_SECONDS = 30

DRY_RUN = False
OVERWRITE_EXISTING = False

# Optional JSON report output path. Set to None to disable.
REPORT_JSON_PATH: Optional[Path] = None

# Retry settings for transient API failures.
MAX_RETRIES = 3
BACKOFF_FACTOR = 1.0


@dataclass
class CloneJob:
	workspace: str
	slug: str
	full_name: str
	clone_url: str
	destination: Path


def fail(message: str, exit_code: int = 1) -> NoReturn:
	print(f"ERROR: {message}", file=sys.stderr)
	raise SystemExit(exit_code)


def validate_configuration() -> List[str]:
	username = BITBUCKET_USERNAME.strip()
	token = BITBUCKET_API_TOKEN.strip()

	if not username:
		fail("BITBUCKET_USERNAME is empty.")
	if not token:
		fail("BITBUCKET_API_TOKEN is empty.")

	workspace_list = [w.strip() for w in WORKSPACES if w.strip()]
	if not workspace_list:
		fail("WORKSPACES is empty. Add at least one workspace slug.")

	if PAGELEN < 1 or PAGELEN > 100:
		fail("PAGELEN must be between 1 and 100.")

	if REQUEST_TIMEOUT_SECONDS <= 0:
		fail("REQUEST_TIMEOUT_SECONDS must be greater than 0.")

	return workspace_list


def ensure_prerequisites(destination_root: Path) -> None:
	if shutil.which("git") is None:
		fail("git is not available in PATH.")

	try:
		destination_root.mkdir(parents=True, exist_ok=True)
	except OSError as exc:
		fail(f"Unable to create destination directory '{destination_root}': {exc}")

	if not destination_root.is_dir():
		fail(f"Destination path is not a directory: {destination_root}")


def build_session() -> requests.Session:
	session = requests.Session()
	session.auth = HTTPBasicAuth(BITBUCKET_USERNAME, BITBUCKET_API_TOKEN)
	session.headers.update({"Accept": "application/json"})

	retry = Retry(
		total=MAX_RETRIES,
		backoff_factor=BACKOFF_FACTOR,
		status_forcelist=(429, 500, 502, 503, 504),
		allowed_methods=frozenset(["GET"]),
		raise_on_status=False,
	)
	adapter = HTTPAdapter(max_retries=retry)
	session.mount("https://", adapter)
	session.mount("http://", adapter)

	return session


def api_get(
	session: requests.Session,
	url: str,
	params: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
	try:
		response = session.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
	except requests.RequestException as exc:
		fail(f"HTTP request failed for {url}: {exc}")

	if response.status_code == 401:
		fail("Authentication failed (401). Check username and API token.", 2)
	if response.status_code == 403:
		fail("Access forbidden (403). Token may lack repository permissions.", 2)
	if response.status_code == 404:
		fail(f"Resource not found (404): {url}", 2)
	if response.status_code >= 400:
		fail(f"Bitbucket API error {response.status_code} for {url}: {response.text}")

	try:
		data = response.json()
	except ValueError as exc:
		fail(f"Invalid JSON response from {url}: {exc}")

	if not isinstance(data, dict):
		fail(f"Unexpected API response shape from {url}: expected object.")

	return data


def list_workspace_repositories(session: requests.Session, workspace: str) -> Iterable[dict[str, Any]]:
	next_url: Optional[str] = f"{API_BASE_URL}/repositories/{workspace}"
	first_page = True

	while next_url:
		params = {"pagelen": PAGELEN} if first_page else None
		page_data = api_get(session, next_url, params=params)

		values = page_data.get("values", [])
		if not isinstance(values, list):
			fail(f"Unexpected 'values' payload for workspace '{workspace}'.")

		for repo in values:
			if isinstance(repo, dict):
				yield repo

		next_field = page_data.get("next")
		next_url = next_field if isinstance(next_field, str) and next_field else None
		first_page = False


def build_authenticated_clone_url(workspace: str, repository: str) -> str:
	encoded_token = quote(BITBUCKET_API_TOKEN, safe="")
	encoded_workspace = quote(workspace, safe="")
	encoded_repository = quote(repository, safe="")
	return (
		f"https://x-bitbucket-api-token-auth:{encoded_token}"
		f"@bitbucket.org/{encoded_workspace}/{encoded_repository}.git"
	)


def make_clone_jobs(session: requests.Session, workspaces: List[str], destination_root: Path) -> List[CloneJob]:
	jobs: List[CloneJob] = []

	for workspace in workspaces:
		print(f"Discovering repositories for workspace: {workspace}")
		discovered = 0

		for repo in list_workspace_repositories(session, workspace):
			discovered += 1
			slug = str(repo.get("slug") or repo.get("name") or "").strip()
			full_name = str(repo.get("full_name") or "").strip() or f"{workspace}/{slug}"

			if not slug:
				print(f"  - Skipping repository with missing slug in workspace '{workspace}'.")
				continue

			clone_url = build_authenticated_clone_url(workspace, slug)

			destination = destination_root / workspace / f"{slug}.git"
			jobs.append(
				CloneJob(
					workspace=workspace,
					slug=slug,
					full_name=full_name,
					clone_url=clone_url,
					destination=destination,
				)
			)

		print(f"  Found {discovered} repositories in workspace '{workspace}'.")

	return jobs


def run_clone(job: CloneJob) -> subprocess.CompletedProcess[str]:
	job.destination.parent.mkdir(parents=True, exist_ok=True)
	command = ["git", "clone", "--bare", job.clone_url, str(job.destination)]
	return subprocess.run(command, capture_output=True, text=True, check=False)


def remove_existing_repo(path: Path) -> None:
	if path.is_dir():
		shutil.rmtree(path)
	elif path.exists():
		path.unlink()


def main() -> int:
	workspaces = validate_configuration()
	destination_root = DESTINATION_ROOT.resolve()
	ensure_prerequisites(destination_root)

	session = build_session()
	jobs = make_clone_jobs(session, workspaces, destination_root)
	print(f"Total clone jobs queued: {len(jobs)}")

	report = {
		"destination_root": str(destination_root),
		"discovered_jobs": len(jobs),
		"cloned": [],
		"skipped": [],
		"failed": [],
	}

	for index, job in enumerate(jobs, start=1):
		target = str(job.destination)
		print(f"[{index}/{len(jobs)}] {job.full_name} -> {target}")

		if job.destination.exists():
			if OVERWRITE_EXISTING:
				print("  Existing destination found; removing before re-clone.")
				remove_existing_repo(job.destination)
			else:
				print("  Skipping (destination already exists).")
				report["skipped"].append(
					{
						"workspace": job.workspace,
						"repository": job.full_name,
						"destination": target,
						"reason": "exists",
					}
				)
				continue

		if DRY_RUN:
			print("  DRY_RUN enabled: clone command not executed.")
			report["skipped"].append(
				{
					"workspace": job.workspace,
					"repository": job.full_name,
					"destination": target,
					"reason": "dry_run",
				}
			)
			continue

		result = run_clone(job)
		if result.returncode == 0:
			print("  Clone completed.")
			report["cloned"].append(
				{
					"workspace": job.workspace,
					"repository": job.full_name,
					"destination": target,
				}
			)
		else:
			error_text = (result.stderr or result.stdout).strip()
			print("  Clone failed.")
			if error_text:
				print(f"  git output: {error_text}")
			report["failed"].append(
				{
					"workspace": job.workspace,
					"repository": job.full_name,
					"destination": target,
					"error": error_text,
				}
			)

	if REPORT_JSON_PATH is not None:
		report_path = REPORT_JSON_PATH.resolve()
		report_path.parent.mkdir(parents=True, exist_ok=True)
		report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
		print(f"Wrote report: {report_path}")

	discovered_jobs = int(report["discovered_jobs"])
	cloned_count = len(report["cloned"])
	skipped_count = len(report["skipped"])
	failed_count = len(report["failed"])

	print("\nSummary")
	print(f"- discovered jobs: {discovered_jobs}")
	print(f"- cloned: {cloned_count}")
	print(f"- skipped: {skipped_count}")
	print(f"- failed: {failed_count}")

	return 1 if failed_count > 0 else 0


if __name__ == "__main__":
	raise SystemExit(main())
