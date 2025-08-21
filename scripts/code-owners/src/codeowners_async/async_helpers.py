import asyncio
import logging
import os
from typing import Tuple, List
import shlex
from datetime import datetime
from collections import Counter

logger = logging.getLogger(__name__)

COMMIT_HEADER_KEY = "Commit: "


class GitCommitLocal:
    """Represents a Git commit in local file system with metadata and file changes.

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

            self.changes[os.path.dirname(change_path)] += total_count

    def __repr__(self):
        """Return a string representation of the GitCommit object.
        
        Returns:
            str: String representation of the GitCommit.
        """
        return (
            f"GitCommit(name={self.name}, email={self.email}, "
            f"ts={self.ts}, changes={self.changes}, "
            f"hash={self.commit_hash})"
        )


async def get_commit_count(repo_path: str) -> int:
    """Get the total number of commits in a repository.

    Args:
        repo_path: Path to the Git repository.

    Returns:
        int: The total number of commits in the repository.
        
    Raises:
        RuntimeError: If the git command fails to execute.
    """
    cmd = f"git -C {shlex.quote(repo_path)} rev-list --count HEAD"
    result = int(await async_run_cmd(cmd))
    return result


GIT_URL_END = ".git"


async def get_remote_owner_repo(repo_path: str) -> Tuple[str, str]:
    """Get the GitHub owner and repository name based on the remote URL.

    Args:
        repo_path: Path to the Git repository.

    Returns:
        Tuple[str, str]: A tuple containing (owner, repository_name).
        
    Raises:
        RuntimeError: If the git command fails to execute.
    """
    cmd = f"git -C {shlex.quote(repo_path)} config --get remote.origin.url"
    # git@github.com:sonic-net/sonic-mgmt.git
    # https://github.com/sonic-net/sonic-mgmt.git
    # https://github.com/sonic-net/sonic-mgmt/

    repo_remote_url = (await async_run_cmd(cmd)).strip()

    if repo_remote_url.lower().endswith(GIT_URL_END):
        repo_remote_url = repo_remote_url[: -len(GIT_URL_END)]

    repo_owner, repo_name = repo_remote_url.split("/")[-2:]
    repo_owner = repo_owner.split(":")[-1]
    return repo_owner, repo_name


async def async_run_cmd(cmd: str) -> str:
    """Execute a shell command asynchronously and return the output.
    
    Args:
        cmd: The shell command to execute.
        
    Returns:
        str: The command output as a string.
        
    Raises:
        RuntimeError: If the command fails to execute or returns non-zero exit code.
    """
    stdout_lines = []
    async for line in async_run_cmd_lines(cmd):
        stdout_lines.append(line)
    return "".join(stdout_lines)


async def async_run_cmd_lines(cmd: str):
    """Execute a shell command asynchronously and yield output lines.
    
    Args:
        cmd: The shell command to execute.
        
    Yields:
        str: Lines of output from the command.
        
    Raises:
        RuntimeError: If the command fails to execute or returns non-zero exit code.
    """
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        yield line.decode()
    await proc.wait()
    if proc.returncode != 0:
        stderr = (await proc.stderr.read()).decode()
        msg = f"Unable to run the command {cmd}. {stderr}, {proc.returncode}"
        logger.error(msg)
        raise RuntimeError(msg)


async def get_all_commit_stats(repo_path: str):
    """Get all commit statistics from a Git repository.
    
    Executes git log to retrieve commit information and file change statistics.
    Yields GitCommitLocal objects for each commit in the repository.
    
    Args:
        repo_path: Path to the Git repository.
        
    Yields:
        GitCommitLocal: Commit objects with metadata and change statistics.
        
    Raises:
        RuntimeError: If the git command fails to execute.
    """
    cmd = (
        f"git -C {shlex.quote(repo_path)} log "
        f"--format='{COMMIT_HEADER_KEY}%H;%aI;%aE;%aN' --numstat"
    )
    commit_header = None
    commit_changes = []
    async for line in async_run_cmd_lines(cmd):
        line = line.strip()
        if line.startswith(COMMIT_HEADER_KEY):
            if commit_header:
                yield GitCommitLocal(commit_header, commit_changes)

            commit_header = line[len(COMMIT_HEADER_KEY) :]
            commit_changes.clear()
        elif commit_header and line:
            commit_changes.append(line)
    if commit_changes:
        yield GitCommitLocal(commit_header, commit_changes)
