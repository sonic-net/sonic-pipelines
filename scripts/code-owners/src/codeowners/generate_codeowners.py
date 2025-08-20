"""Module for generating CODEOWNERS files based on repository analysis."""

import argparse
import datetime
import logging
import os
import time
from collections import Counter

from .repo_analyzer import (
    logger,
    process_folders_recursively,
    process_repo_commits,
)
from .contributor import ContributorCollection
from .folders import get_repo_folders, FolderType, load_folder_metadata


# Scale ~100-1000 commiters
# 1000-10000 folders
# Full git log dump is ~Megabytes


def main():
    """Main function to analyze repository and generate folder statistics.

    Processes repository commits, analyzes contributor activity, and generates
    statistics for each folder based on contributor changes.

    Args:

    """
    main_start_time = time.time()
    args = parse_params()
    contributors = ContributorCollection(args.contributors_file)
    load_folder_metadata(args.folder_presets_file)
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(
        level=LOGGING_LEVELS[args.log_level],
        format=(
            "%(asctime)s %(levelname)s %(filename)s:%(lineno)d "
            "Thread:%(thread)d %(message)s"
        ),
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    process_repo_commits(args, contributors)

    repo_folders = get_repo_folders(args.repo)

    repo_folders_stats = {folder: Counter() for folder in repo_folders}

    # Summarize folder statistics
    cutoff_ts = datetime.datetime.combine(
        args.active_after, datetime.datetime.min.time(), datetime.timezone.utc
    )
    for contributor in contributors.contributors:
        # Take only contributors active after the cutoff time
        if contributor.last_commit_ts is None:
            continue
        if contributor.last_commit_ts >= cutoff_ts:
            logger.info(f"Analyzing: {contributor.github_login}")
            # Process every commit for each
            for commit in contributor.commits:
                # For every folder
                for folder, change_count in commit.changes.items():
                    folder = os.sep + folder
                    # Apply the changes from the folder up
                    while True:
                        try:
                            if (
                                repo_folders[folder].folder_type
                                != FolderType.CLOSED_OWNERS
                            ):
                                # Unless the owners are already defined,
                                # count the statistics
                                repo_folders_stats[folder][
                                    contributor
                                ] += change_count
                        except KeyError:
                            # Ignore non-existent folders
                            pass
                        if folder == os.sep:
                            break
                        folder = os.path.dirname(folder)
    # select contributors for each folder
    for folder, contributor_stat in sorted(repo_folders_stats.items()):
        folder_settings = repo_folders[folder]
        if folder_settings.folder_type in [
            FolderType.OPEN_OWNERS,
            FolderType.REGULAR,
        ]:
            need_extra_owners = max(
                0, (args.max_owners - len(folder_settings.owners))
            )
            if need_extra_owners > 0:
                # try to get double of the owners in case of the reps
                for contributor, _ in contributor_stat.most_common(
                    need_extra_owners << 1
                ):
                    folder_settings.owners.add(contributor.github_login)
                    if len(folder_settings.owners) >= args.max_owners:
                        break

    # recursively process folders,
    # find the highest possible folders with non-empty and distinct owners
    print()
    print("CODEOWNERS output:")
    process_folders_recursively("/", repo_folders)

    logger.debug(f"Runtime {time.time() - main_start_time} seconds")


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
        type=datetime.date.fromisoformat,
        help=(
            "Active user considered committed "
            "after this date "
            "(ISO format). Default last 730 days"
        ),
        default=(datetime.date.today() - datetime.timedelta(days=730)),
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


if __name__ == "__main__":
    main()
