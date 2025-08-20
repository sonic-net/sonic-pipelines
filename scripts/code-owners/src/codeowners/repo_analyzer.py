import logging
import os
import queue
import threading

from .commit_stream import (
    get_commit_count,
    get_remote_owner_repo,
    get_commit_stream,
    GitCommit,
)
from .contributor import Contributor, ContributorCollection
from .github_api import (
    github_commit_author_id_lookup,
    github_id_lookup,
)
from .organization import ORGANIZATION, organization_by_company

logger = logging.getLogger(__name__)


def process_folders_recursively(start_folder: str, repo_folders):
    """Process the folders with counted contributors top to bottom
    If all subfolders have contributors as the subset of a folder
    then do not descend.

    Iterates with DFS using recursion

    Args:
        start_folder: A folder to start with
        repo_folders: A collection of folders with children and owners
    """
    owners = repo_folders[start_folder].owners
    if owners:
        # only proceed to the folders with owners
        subfolders = repo_folders[start_folder].children
        subfolder_full_names = []
        owners_match = True
        for subfolder in sorted(subfolders):
            subfolder_full_name = os.path.join(start_folder, subfolder)
            subfolder_full_names.append(subfolder_full_name)
            # make sure that subfolder owners
            # are the subset of the current folder owners
            owners_match = owners_match and (
                repo_folders[subfolder_full_name].owners <= owners
            )

        if not owners_match:
            # proceed to lower levels if there is a mismatched owner there
            for subfolder_full_name in subfolder_full_names:
                process_folders_recursively(subfolder_full_name, repo_folders)
        else:
            print(
                start_folder + os.sep,
                " ".join(f"@{owner}" for owner in sorted(owners)),
            )


def process_repo_commits(args, contributors):
    """Process all commits in the repository
    and update contributor information.

    Iterates through all commits in the repository,
    looks up contributors by email,
    and updates their commit history.
    For unknown contributors, attempts to find
    their GitHub information and add them to the collection.

    Args:
        args: Command line arguments containing repository path.
        contributors: ContributorCollection to update with commit information.
    """
    counter = 0
    contributor_hits = 0
    contributor_miss = 0
    total_commits = get_commit_count(args.repo)
    repo_owner, repo_name = get_remote_owner_repo(args.repo)

    # A queue of commits to be resolved in the background
    # None item in the queue will tell the workers to stop
    to_be_resolved_queue = queue.Queue()
    resolved_queue = queue.Queue()
    num_workers = 16
    workers = [
        threading.Thread(
            target=build_candidate_contributor_worker,
            args=(
                to_be_resolved_queue,
                resolved_queue,
                repo_owner,
                repo_name,
                contributors,
            ),
        )
        for _ in range(num_workers)
    ]
    for w in workers:
        w.start()
    commit_iterator = get_commit_stream(args.repo)
    commit_iterator_empty = False
    backlogged_items = 0
    while backlogged_items > 0 or not commit_iterator_empty:
        # check the backlog for processed commits
        if backlogged_items > 0 and (
            commit_iterator_empty or not resolved_queue.empty()
        ):
            item = resolved_queue.get()
            resolved_queue.task_done()
            backlogged_items -= 1
            # catch when all worker threads reported they quit
            if item is None:
                continue
            candidate_contributor, commit, already_resolved = item
            contributor = None
            if already_resolved:
                # set as the contributor
                contributor = candidate_contributor
                candidate_contributor = None
        # Get the commit from the iterator if backlog is empty
        elif not commit_iterator_empty:
            try:
                candidate_contributor = None
                commit = next(commit_iterator)
                contributor = contributors.get_contributor_by_email(
                    commit.email
                )
            except StopIteration:
                commit_iterator_empty = True
                # Send None to all workers to stop
                # Not new tasks
                for _ in range(num_workers):
                    to_be_resolved_queue.put(None)
                continue
        else:
            continue

        if contributor is not None:
            # Already resolved to the actual contributor
            contributor_hits += 1
        elif candidate_contributor is not None:
            # Resolved through GitHub backend, add to the list
            contributor = contributors.get_contributor(
                candidate_contributor, add_missing=True
            )
        else:
            contributor_miss += 1
            to_be_resolved_queue.put(commit)
            backlogged_items += 1
            continue

        contributors.update_contributor_emails(contributor, commit.email)
        contributor.add_commit(commit)
        counter += 1

        # report the counters
        if counter % 100 == 0:
            logger.debug(
                f"Processed {counter} commits of {total_commits}. "
                f"Contributor hits: {contributor_hits}"
            )
            contributors.save_to_file()
    contributors.save_to_file()
    # join the workers
    for w in workers:
        w.join()
    found_commits = sum(
        len(contributor.commits) for contributor in contributors.contributors
    )
    logger.info(f"Processed total commits {counter}")
    logger.info(f"Found contributors for: {found_commits}")
    logger.info(
        f"Expected commits by repo_name: {total_commits}",
    )


def build_candidate_contributor(
    commit: GitCommit, repo_owner: str, repo_name: str
) -> Contributor:
    """Tries to look for the commit information in GitHub
    and build the candidate contributor object
    for later adding to the contributor collection

    Args:
        commit ():  GitCommit instance with local commit info
        repo_owner ():  GitHub repo_name owner inferred from the origin
        repo_name ():  GitHub repo_name name inferred from the origin

    Returns:
        Contributor instance
    """
    candidate_contributor = Contributor(
        name=commit.name, emails=[commit.email]
    )
    author_id = github_commit_author_id_lookup(
        commit.commit_hash, repo_owner, repo_name
    )
    if author_id == -1:
        logger.warning(
            f"Commit {commit.commit_hash}, {commit.name}, "
            f"{commit.email}, unable to determine authors' GitHub id. "
            "Adding to the bundled contributor with GitHub id = -1"
        )
    github_info = github_id_lookup(author_id)
    candidate_contributor.github_login = github_info["login"]
    candidate_contributor.github_id = author_id
    if github_info["name"]:
        candidate_contributor.name = github_info["name"]
    elif not candidate_contributor.name:
        candidate_contributor.name = github_info["login"]
    if (
        candidate_contributor.organization == ORGANIZATION.OTHER
        and author_id != -1
    ):
        candidate_contributor.organization = organization_by_company(
            str(github_info.get("company")) + "_" + github_info["login"]
        )
    if github_info.get("email"):
        candidate_contributor.emails.add(github_info["email"].lower())
    return candidate_contributor


def build_candidate_contributor_worker(
    input_queue: queue.Queue,
    output_queue: queue.Queue,
    repo_owner: str,
    repo_name: str,
    contributors: ContributorCollection,
):
    """A worker thread to resolve the contributors on the GitHub

    Args:
        input_queue (): import queue for commits
        output_queue (): output queue for the resolved Contributors
        repo_owner ():  GitHub repo_name owner inferred from the origin
        repo_name ():  GitHub repo_name name inferred from the origin
        contributors (): Contributors collection for checking before lookup

    Returns:

    """
    while True:
        commit = input_queue.get()
        input_queue.task_done()
        if commit is None:
            break
        contributor = contributors.get_contributor_by_email(commit.email)
        if contributor is not None:
            # The email resolves now, return resolved
            output_queue.put((contributor, commit, True))
        else:
            # Try to resolve through the GitHub
            result = build_candidate_contributor(commit, repo_owner, repo_name)
            output_queue.put((result, commit, False))
