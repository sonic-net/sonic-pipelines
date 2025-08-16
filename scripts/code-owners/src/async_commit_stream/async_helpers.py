import asyncio
import logging
import os
from typing import Tuple
import shlex
from datetime import datetime
from collections import Counter

logger = logging.getLogger(__name__)


class GitCommitLocal:
    """Represents a Git commit in local file system
     with metadata and file changes.

    Attributes:
        name: The author name.
        email: The author email address (lowercase).
        ts: The commit timestamp as a datetime object.
        changes: A Counter mapping folder paths to change counts.
        commit_hash: The Git commit hash.
    """

    def __init__(self, commit_num_stat: str):
        """Initialize a GitCommit object.

        Args:
            commit_header: A semicolon-separated string containing commit hash,
                ISO timestamp, author email, and author name.
            commit_changes: List of tab-separated strings containing add count,
                delete count, and file path for each changed file.
        """

        commit_num_stat_lines = commit_num_stat.split(os.linesep)
        commit_header = commit_num_stat_lines[0]
        if len(commit_num_stat_lines) < 2 or commit_num_stat_lines[1]:
            raise ValueError(
                "Invalid commit numstat, expecting empty second line"
            )
        commit_changes = commit_num_stat_lines[2:]
        # Split the commit header
        commit_hash, ts_iso_str, email, name = commit_header.split(";", 3)
        self.name = name
        self.email = email.lower()
        self.ts = datetime.fromisoformat(ts_iso_str)
        self.changes = Counter()
        self.commit_hash = commit_hash

        # Group the adds and deletes by the folder
        for line in commit_changes:
            add_count_str, del_count_str, change_path = line.split("\t")
            # Mark non-numeric/binary changes as 1
            add_count = 1 if add_count_str == "-" else int(add_count_str)
            del_count = 1 if del_count_str == "-" else int(del_count_str)
            total_count = add_count + del_count

            if (not change_path) or (change_path[0] != os.sep):
                change_path = os.sep + change_path
            change_path = os.path.dirname(change_path)
            while change_path != os.sep:
                self.changes[change_path] += total_count
                change_path = os.path.dirname(change_path)

    def __repr__(self):
        """Return a string representation of the GitCommit object."""
        return (
            f"GitCommit(name={self.name}, email={self.email}, "
            f"ts={self.ts}, changes={self.changes}, "
            f"hash={self.commit_hash})"
        )


GIT_URL_END = ".git"


async def get_remote_owner_repo(repo_path: str) -> Tuple[str, str]:
    """Get the GitHub owner and repo name based on the remote URL

    Args:
        repo_path: Path to the Git repository.

    Returns:
        tuple of 2 strings: owner and repo
    """
    cmd = f"git -C {repo_path} config --get remote.origin.url"
    # git@github.com:sonic-net/sonic-mgmt.git
    stdout = await async_run_cmd(cmd)
    repo_remote_url = stdout
    if not repo_remote_url.endswith(GIT_URL_END):
        raise ValueError(
            f"Unexpected GitHub URL end: "
            f"{repo_remote_url}, expected: {GIT_URL_END}"
        )
    host, path = repo_remote_url.split(":")
    repo_owner, repo_name = path.split("/")
    repo_name = repo_name[: -len(GIT_URL_END)]
    return repo_owner, repo_name


async def async_run_cmd(cmd):
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    stdout = stdout.decode().strip()
    stderr = stderr.decode()
    if proc.returncode != 0:
        msg = f"Unable to run the command {cmd}. {stderr}"
        logger.error(msg)
        raise RuntimeError(msg)
    return stdout


async def get_commit_stats(repo_path: str, commit_sha: str):
    cmd = f"git -C {shlex.quote(repo_path)} log {shlex.quote(commit_sha)} --format='%H;%aI;%aE;%aN' --numstat -n 1"
    try:
        stdout = await async_run_cmd(cmd)
        try:
            return GitCommitLocal(stdout)
        except ValueError:
            logger.info(f"Empty of missing {commit_sha} stats. Ignoring")
            return None
    except RuntimeError:
        logger.warning(
            f"Unable to find commit {commit_sha} locally, ignoring."
            "Make sure to use the correct and up-to-date repo, branch, path"
        )
        return None
