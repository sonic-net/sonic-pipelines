import argparse
import asyncio
import os
from datetime import datetime, timezone, date, timedelta
import logging
import time

__version__ = "0.0.5"

from async_github_repo_summary import (
    AsyncGitHubRepoSummary,
)
from async_helpers import (
    get_remote_owner_repo,
    get_commit_count,
)
from contributor import ContributorCollection
from folders import load_folder_metadata

logger = logging.getLogger(__name__)

LOGGING_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}


def parse_params() -> argparse.Namespace:
    """Parse command line arguments.

    Returns:
        argparse.Namespace: Parsed command line arguments.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo", help="Path to the repo_name to analyze", required=True
    )
    parser.add_argument(
        "--active_after",
        type=date.fromisoformat,
        help=(
            "Active user considered committed "
            "after this date "
            "(ISO format). Default last 730 days"
        ),
        default=(date.today() - timedelta(days=730)),
    )
    parser.add_argument(
        "--contributors_file",
        help="YAML file with the contributor information",
        default="contributors.yaml",
    )
    parser.add_argument(
        "--folder_presets_file",
        help="YAML file with the preset folder information",
    )
    parser.add_argument(
        "--max_owners",
        type=int,
        help="The maximal number of owners per folder",
        default=3,
    )
    parser.add_argument(
        "--log_level",
        default="info",  # Default logging level
        choices=LOGGING_LEVELS.keys(),
        help=(
            "Set the logging level. "
            f"Choices: {', '.join(LOGGING_LEVELS.keys())}. "
            "Default: %(default)s"
        ),
    )
    return parser.parse_args()


def main():
    """Main entry point for the codeowners generation script.

    Parses command line arguments, sets up logging,
    and runs the async processing loop.
    """
    main_start_time = time.time()
    args = parse_params()
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(
        level=LOGGING_LEVELS[args.log_level],
        format=(
            "%(asctime)s %(levelname)s %(filename)s:%(lineno)d "
            "Thread:%(thread)d %(message)s"
        ),
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    asyncio.run(async_loop(args))
    logger.debug(f"Runtime {time.time() - main_start_time} seconds")


def process_folders_recursively(start_folder: str, repo_folders):
    """Process the folders with counted contributors top to bottom.

    If all subfolders have contributors as the subset of a folder
    then do not descend. Iterates with DFS using recursion.

    Args:
        start_folder: A folder to start with.
        repo_folders: A collection of folders with children and owners.
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
        if start_folder == os.sep:
            print_folder = "*"
        else:
            print_folder = start_folder + os.sep
        print(
            print_folder,
            " ".join(f"@{owner}" for owner in sorted(owners)),
        )
        if not owners_match:
            # proceed to lower levels if there is a mismatched owner there
            for subfolder_full_name in subfolder_full_names:
                process_folders_recursively(subfolder_full_name, repo_folders)


async def async_loop(args: argparse.Namespace):
    """Main async processing loop for repository analysis.

    Args:
        args: Parsed command line arguments
        containing repository path and settings.
    """
    repo_summarizer = AsyncGitHubRepoSummary()
    contributor_collection = ContributorCollection(args.contributors_file)

    (
        (owner, repo_name),
        (preset_folders, repo_folders),
        _,
        total_commit_count,
    ) = await asyncio.gather(
        get_remote_owner_repo(args.repo),
        load_folder_metadata(args.folder_presets_file, args.repo),
        contributor_collection.load_from_file(),
        get_commit_count(args.repo),
    )
    logging.info("Loaded all folder presets and contributors if any")

    await repo_summarizer.process_repository(
        contributor_collection,
        preset_folders,
        repo_folders,
        args.repo,
        total_commit_count,
        owner,
        repo_name,
        datetime.combine(args.active_after, datetime.min.time(), timezone.utc),
        args.max_owners,
    )
    logging.info(f"Processed {total_commit_count} commits")
    await contributor_collection.save_to_file()

    process_folders_recursively("/", repo_folders)


if __name__ == "__main__":
    main()
