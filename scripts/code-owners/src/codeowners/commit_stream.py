"""Module for processing Git commit streams
   and extracting commit information."""

import datetime
import os
import subprocess
from collections import Counter
from typing import List, Tuple

COMMIT_HEADER_KEY = "Commit: "
GIT_URL_END = ".git"


class GitCommit:
    """Represents a Git commit with metadata and file changes.

    Attributes:
        name: The author name.
        email: The author email address (lowercase).
        ts: The commit timestamp as a datetime object.
        changes: A Counter mapping folder paths to change counts.
        commit_hash: The Git commit hash.
    """

    def __init__(self, commit_header: str, commit_changes: List[str]):
        """Initialize a GitCommit object.

        Args:
            commit_header: A semicolon-separated string containing commit hash,
                ISO timestamp, author email, and author name.
            commit_changes: List of tab-separated strings containing add count,
                delete count, and file path for each changed file.
        """
        # Split the commit header
        commit_hash, ts_iso_str, email, name = commit_header.split(";", 3)
        self.name = name
        self.email = email.lower()
        self.ts = datetime.datetime.fromisoformat(ts_iso_str)
        self.changes = Counter()
        self.commit_hash = commit_hash

        # Group the adds and deletes by the folder
        for line in commit_changes:
            add_count_str, del_count_str, change_path = line.split("\t")
            # Mark non-numeric/binary changes as 1
            add_count = 1 if add_count_str == "-" else int(add_count_str)
            del_count = 1 if del_count_str == "-" else int(del_count_str)
            self.changes[os.path.dirname(change_path)] += add_count + del_count

    def __repr__(self):
        """Return a string representation of the GitCommit object."""
        return (
            f"GitCommit(name={self.name}, email={self.email}, "
            f"ts={self.ts}, changes={self.changes}, "
            f"hash={self.commit_hash})"
        )


def get_commit_stream(repo_path: str):
    """Generate GitCommit objects from a repository's commit history.

    Parses the git log output to extract commit information and file changes.
    Each commit is represented as a GitCommit object containing metadata and
    a summary of changes by folder.

    Args:
        repo_path: Path to the Git repository to analyze.

    Yields:
        GitCommit: Objects representing each commit in the repository history.

    Example:
        The git log format expected:
        Commit: 2022-09-07T08:13:09+08:00;\
        66248323+bingwang-ms@users.noreply.github.com;\
        bingwang-ms
                9	0	ansible/subfolder/config_sonic_basedon_testbed.yml
                7	0	ansible/templates/minigraph_meta.j2
    """
    command = [
        "git",
        "-C",
        repo_path,
        "log",
        f"--format={COMMIT_HEADER_KEY}%H;%aI;%aE;%aN",
        "--numstat",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, text=True)

    commit_header = None
    commit_changes = []
    for line in process.stdout:
        line = line.strip()
        if line.startswith(COMMIT_HEADER_KEY):
            if commit_changes:
                yield GitCommit(commit_header, commit_changes)

            commit_header = line[len(COMMIT_HEADER_KEY) :]
            commit_changes.clear()
        elif commit_header and line:
            commit_changes.append(line)
    if commit_changes:
        yield GitCommit(commit_header, commit_changes)
    process.wait()


def get_commit_count(repo_path: str) -> int:
    """Get the total number of commits in a repository.

    Args:
        repo_path: Path to the Git repository.

    Returns:
        int: The total number of commits in the repository.
    """
    cmd = ["git", "-C", repo_path, "rev-list", "--count", "HEAD"]
    result = int(subprocess.check_output(cmd, text=True))
    return result


def get_remote_owner_repo(repo_path: str) -> Tuple[str, str]:
    """Get the GitHub owner and repo_name name based on the remote URL

    Args:
        repo_path: Path to the Git repository.

    Returns:
        tuple of 2 strings: owner and repo_name
    """
    cmd = ["git", "-C", repo_path, "config", "--get", "remote.origin.url"]
    # git@github.com:sonic-net/sonic-mgmt.git
    repo_remote_url = subprocess.check_output(cmd, text=True).strip()
    if not repo_remote_url.endswith(GIT_URL_END):
        raise ValueError(
            f"Unexpected GitHub URL end: "
            f"{repo_remote_url}, expected: {GIT_URL_END}"
        )
    host, path = repo_remote_url.split(":")
    repo_owner, repo_name = path.split("/")
    repo_name = repo_name[: -len(GIT_URL_END)]
    return repo_owner, repo_name
