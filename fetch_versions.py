#!/usr/bin/env python3
"""
Fetch all repos from the GitHub actions organization and their tags via the API,
and generate a versions.txt file with the latest vINTEGER tags.

No git cloning required - uses GitHub REST API only.

Repos known to have no vINTEGER tags are cached in unversioned.txt to skip
API calls on future runs.
"""

import json
import re
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent.resolve()
VERSIONS_FILE = SCRIPT_DIR / "versions.txt"
UNVERSIONED_FILE = SCRIPT_DIR / "unversioned.txt"
README_FILE = SCRIPT_DIR / "README.md"

# Markers for the README section
README_START_MARKER = "<!-- VERSIONS_START -->"
README_END_MARKER = "<!-- VERSIONS_END -->"
ORG_NAME = "actions"
GITHUB_API_URL = "https://api.github.com"

# Additional non-official GitHub Actions to track
# Format: "owner/repo"
EXTRA_REPOS = [
    "DeLaGuardo/setup-clojure",
    "DeterminateSystems/determinate-nix-action",
    "DeterminateSystems/flake-checker-action",
    "DeterminateSystems/flakehub-cache-action",
    "DeterminateSystems/flakehub-push",
    "DeterminateSystems/magic-nix-cache-action",
    "DeterminateSystems/nix-installer-action",
    "docker/build-push-action",
    "tailscale/github-action",
]


def load_unversioned() -> set[str]:
    """Load the set of repos known to have no vINTEGER tags."""
    if not UNVERSIONED_FILE.exists():
        return set()
    return set(
        line.strip()
        for line in UNVERSIONED_FILE.read_text().splitlines()
        if line.strip()
    )


def save_unversioned(repos: set[str]) -> None:
    """Save the set of repos known to have no vINTEGER tags."""
    with open(UNVERSIONED_FILE, "w") as f:
        for repo_name in sorted(repos):
            f.write(f"{repo_name}\n")


def update_readme(versions_content: str) -> None:
    """Update the README.md with the latest versions in a fenced code block."""
    if not README_FILE.exists():
        print(f"Warning: {README_FILE} not found, skipping README update")
        return

    readme_text = README_FILE.read_text()

    # Build the new section content
    new_section = f"""{README_START_MARKER}
## Latest versions

```
{versions_content}```
{README_END_MARKER}"""

    # Check if markers already exist
    if README_START_MARKER in readme_text and README_END_MARKER in readme_text:
        # Replace existing section
        pattern = re.compile(
            re.escape(README_START_MARKER) + r".*?" + re.escape(README_END_MARKER),
            re.DOTALL,
        )
        new_readme = pattern.sub(new_section, readme_text)
    else:
        # Append to end of file
        new_readme = readme_text.rstrip() + "\n\n" + new_section + "\n"

    README_FILE.write_text(new_readme)
    print(f"Updated {README_FILE} with latest versions")


def fetch_repos(org: str) -> list[dict]:
    """Fetch all repos for an organization using curl."""
    repos = []
    page = 1
    per_page = 100

    while True:
        url = f"{GITHUB_API_URL}/orgs/{org}/repos?per_page={per_page}&page={page}"
        result = subprocess.run(
            ["curl", "-s", "-H", "Accept: application/vnd.github+json", url],
            capture_output=True,
            text=True,
            check=True,
        )

        page_repos = json.loads(result.stdout)

        if not page_repos:
            break

        repos.extend(page_repos)

        if len(page_repos) < per_page:
            break

        page += 1

    return repos


def fetch_tags(org: str, repo_name: str) -> list[str]:
    """Fetch all tags for a repository using the GitHub API."""
    tags = []
    page = 1
    per_page = 100

    while True:
        url = f"{GITHUB_API_URL}/repos/{org}/{repo_name}/tags?per_page={per_page}&page={page}"
        result = subprocess.run(
            ["curl", "-s", "-H", "Accept: application/vnd.github+json", url],
            capture_output=True,
            text=True,
            check=True,
        )

        page_tags = json.loads(result.stdout)

        # Handle error responses (e.g., rate limiting)
        if isinstance(page_tags, dict) and "message" in page_tags:
            print(
                f"  API error for {repo_name}: {page_tags['message']}", file=sys.stderr
            )
            break

        if not page_tags:
            break

        tags.extend(tag["name"] for tag in page_tags)

        if len(page_tags) < per_page:
            break

        page += 1

    return tags


def get_latest_version_tag(tags: list[str]) -> str | None:
    """Get the latest version tag from a list of tags.

    Supports multiple version formats in priority order:
    1. vINTEGER (e.g., v1, v2, v10) - standard GitHub Actions format
    2. INTEGER (e.g., 1, 2, 13) - plain major version
    3. MAJOR.MINOR (e.g., 13.4, 12.1) - semver-like without v prefix
    """
    # Try vINTEGER format first (e.g., v1, v2, v10)
    vinteger_pattern = re.compile(r"^v(\d+)$")
    vinteger_tags = []
    for tag in tags:
        match = vinteger_pattern.match(tag.strip())
        if match:
            vinteger_tags.append((int(match.group(1)), tag.strip()))

    if vinteger_tags:
        vinteger_tags.sort(reverse=True, key=lambda x: x[0])
        return vinteger_tags[0][1]

    # Try plain INTEGER format (e.g., 1, 2, 13)
    integer_pattern = re.compile(r"^(\d+)$")
    integer_tags = []
    for tag in tags:
        match = integer_pattern.match(tag.strip())
        if match:
            integer_tags.append((int(match.group(1)), tag.strip()))

    if integer_tags:
        integer_tags.sort(reverse=True, key=lambda x: x[0])
        return integer_tags[0][1]

    # Try MAJOR.MINOR format (e.g., 13.4, 12.1)
    major_minor_pattern = re.compile(r"^(\d+)\.(\d+)$")
    major_minor_tags = []
    for tag in tags:
        match = major_minor_pattern.match(tag.strip())
        if match:
            major = int(match.group(1))
            minor = int(match.group(2))
            major_minor_tags.append((major, minor, tag.strip()))

    if major_minor_tags:
        # Sort by major then minor descending
        major_minor_tags.sort(reverse=True, key=lambda x: (x[0], x[1]))
        return major_minor_tags[0][2]

    return None


def main():
    """Main function to fetch repos, get tags via API, and generate versions.txt."""
    # Load cached unversioned repos
    unversioned = load_unversioned()
    if unversioned:
        print(f"Loaded {len(unversioned)} known unversioned repos from cache")

    print(f"Fetching repos for {ORG_NAME}...")
    repos = fetch_repos(ORG_NAME)
    print(f"Found {len(repos)} repos")

    versions = []
    new_unversioned = set()

    # Process official GitHub Actions from the 'actions' org
    for repo in repos:
        repo_name = repo["name"]

        # Skip repos known to have no vINTEGER tags
        if repo_name in unversioned:
            print(f"Skipping {repo_name} (cached as unversioned)")
            new_unversioned.add(repo_name)
            continue

        print(f"Fetching tags for {repo_name}...", end=" ")
        tags = fetch_tags(ORG_NAME, repo_name)
        latest_tag = get_latest_version_tag(tags)

        if latest_tag:
            versions.append((f"{ORG_NAME}/{repo_name}", latest_tag))
            print(f"{latest_tag}")
        else:
            print("no vINTEGER tag")
            new_unversioned.add(repo_name)

    # Process additional non-official GitHub Actions
    if EXTRA_REPOS:
        print(f"\nProcessing {len(EXTRA_REPOS)} extra repos...")
        for full_repo in EXTRA_REPOS:
            org, repo_name = full_repo.split("/", 1)

            # Skip repos known to have no vINTEGER tags (use full path for extra repos)
            if full_repo in unversioned:
                print(f"Skipping {full_repo} (cached as unversioned)")
                new_unversioned.add(full_repo)
                continue

            print(f"Fetching tags for {full_repo}...", end=" ")
            tags = fetch_tags(org, repo_name)
            latest_tag = get_latest_version_tag(tags)

            if latest_tag:
                versions.append((full_repo, latest_tag))
                print(f"{latest_tag}")
            else:
                print("no vINTEGER tag")
                new_unversioned.add(full_repo)

    # Sort alphabetically by full repo path
    versions.sort(key=lambda x: x[0].lower())

    # Build versions content
    versions_content = (
        "\n".join(f"{full_repo}@{tag}" for full_repo, tag in versions) + "\n"
    )

    # Write versions.txt
    with open(VERSIONS_FILE, "w") as f:
        f.write(versions_content)

    # Update README.md with the versions
    update_readme(versions_content)

    # Update unversioned.txt
    save_unversioned(new_unversioned)

    print(f"\nWrote {len(versions)} versions to {VERSIONS_FILE}")
    print(f"Cached {len(new_unversioned)} unversioned repos to {UNVERSIONED_FILE}")


if __name__ == "__main__":
    main()
