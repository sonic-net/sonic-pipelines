import argparse
import asyncio
from datetime import datetime, timezone, date, timedelta
import logging
import time

from async_commit_stream.async_commit_stream import AsyncGitHubRepoSummary
from async_commit_stream.async_helpers import get_remote_owner_repo
from async_commit_stream.contributor import ContributorCollection
from async_commit_stream.folders import load_folder_metadata, PRESET_FOLDERS

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
        "--repo", help="Path to the repo to analyze", required=True
    )
    parser.add_argument(
        "--active_from",
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


async def async_loop(args: argparse.Namespace):
    repo_summarizer = AsyncGitHubRepoSummary()
    contributor_collection = ContributorCollection(args.contributors_file)

    (owner, repo), repo_folders, _ = await asyncio.gather(
        get_remote_owner_repo(args.repo),
        load_folder_metadata(args.folder_presets_file, args.repo),
        contributor_collection.load_from_file(),
    )
    logging.info("Loaded all folder presets and contributors if any")

    await repo_summarizer.process_repository(
        contributor_collection,
        PRESET_FOLDERS,
        args.repo,
        owner,
        repo,
        (
            datetime.combine(
                args.active_from,
                datetime.min.time(),
                timezone.utc,
            )
        ).isoformat()
        + "Z",
    )
    await contributor_collection.save_to_file()


if __name__ == "__main__":
    main()
