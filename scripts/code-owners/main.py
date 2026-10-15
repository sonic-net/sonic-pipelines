import argparse
import asyncio
import os
from datetime import datetime, timezone, date, timedelta
import logging
import time
import yaml

__version__ = "0.0.5"

from async_github_repo_summary import (
    AsyncGitHubRepoSummary,
)
from async_helpers import (
    get_remote_owner_repo,
    get_commit_count,
)
from reviews import compute_review_weights, combine_scores
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
        "--half_life_days",
        type=float,
        default=0,
        help=(
            "Weight each commit by 0.5 ** (age_days / half_life_days) so "
            "recent changes count more. Default 0: no decay"
        ),
    )
    parser.add_argument(
        "--normalize",
        choices=["none", "max", "share"],
        default="none",
        help=(
            "max: weight divided by the folder's top weight; "
            "share: weight divided by the folder's total weight. "
            "Default: none (raw weights)"
        ),
    )
    parser.add_argument(
        "--reviews_file",
        help=(
            "JSON file or directory of JSON files with merged PRs, changed "
            "files and approvals. Blends approval scores into the output"
        ),
    )
    parser.add_argument(
        "--change_weight",
        type=float,
        default=0.6,
        help="Weight of the change score when blending; reviews get 1 - this",
    )
    parser.add_argument(
        "--reviews_out",
        help="Write raw (unnormalized) approval weights per folder to this file",
    )
    parser.add_argument(
        "--full_tree",
        action="store_true",
        help=(
            "List every folder that has owners instead of pruning folders "
            "whose owners are a subset of the parent's owners"
        ),
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


def normalize_weights(out_folder_dict, normalize: str):
    """Normalize each folder's weights: max (top weight) or share (total).

    Preset owners with infinite weight are kept as is and excluded
    from the normalization base.
    """
    if normalize == "none":
        return
    inf = float("inf")
    for folder, owners in out_folder_dict.items():
        finite = [w for w in owners.values() if w != inf]
        base = max(finite, default=0) if normalize == "max" else sum(finite)
        out_folder_dict[folder] = {
            o: w if w == inf else round(w / base, 4) if base else 0
            for o, w in owners.items()
        }


def process_folders_recursively(
    start_folder: str, repo_folders, out_folder_dict, full_tree: bool = False
):
    """Process the folders with counted contributors top to bottom.

    If all subfolders have contributors as the subset of a folder
    then do not descend. Iterates with DFS using recursion.

    Args:
        start_folder: A folder to start with.
        repo_folders: A collection of folders with children and owners.
        out_folder_dict: Output dictionary of folder to owners.
        full_tree: If True, do not prune folders whose owners match
            the parent's owners.
    """
    owners = repo_folders[start_folder].owners
    if owners:
        # only proceed to the folders with owners
        subfolders = repo_folders[start_folder].children
        subfolder_full_names = []
        owners_match = True
        empty_subfolders = []
        for subfolder in sorted(subfolders):
            subfolder_full_name = os.path.join(start_folder, subfolder)
            subfolder_full_names.append(subfolder_full_name)
            # make sure that subfolder owners
            # are the subset of the current folder owners
            owners_match = owners_match and (
                repo_folders[subfolder_full_name].owners.keys()
                <= owners.keys()
            )
            if not repo_folders[subfolder_full_name].owners:
                empty_subfolders.append(subfolder_full_name)

        if start_folder != os.sep:
            print_folder = start_folder + os.sep
        else:
            print_folder = start_folder

        print_owners = sorted(owners)
        out_folder_dict[print_folder] = owners
        if full_tree or not owners_match:
            # proceed to lower levels if there is a mismatched owner there
            for subfolder_full_name in subfolder_full_names:
                process_folders_recursively(
                    subfolder_full_name,
                    repo_folders,
                    out_folder_dict,
                    full_tree,
                )
            # extend the parent ownership to the empty subfolder
            # if any sibling has a different owner
            for empty_subfolder in empty_subfolders:
                out_folder_dict[f"{empty_subfolder}{os.sep}"] = owners.copy()


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
        args.half_life_days,
    )
    logging.info(f"Processed {total_commit_count} commits")
    await contributor_collection.save_to_file()
    out_folder_dict = {}
    process_folders_recursively(
        "/", repo_folders, out_folder_dict, args.full_tree
    )
    normalize_weights(out_folder_dict, args.normalize)
    if args.reviews_file:
        review_weights = compute_review_weights(
            args.reviews_file, args.half_life_days
        )
        if args.reviews_out:
            raw = {
                f: {u: round(w, 4) for u, w in o.items() if f in out_folder_dict}
                for f, o in review_weights.items()
                if f in out_folder_dict
            }
            with open(args.reviews_out, "w") as fh:
                yaml.safe_dump(raw, fh, indent=2, default_flow_style=False)
        if args.normalize != "max":
            raise SystemExit("--reviews_file requires --normalize max")
        out_folder_dict = combine_scores(
            out_folder_dict, review_weights, args.change_weight
        )
    contents = yaml.safe_dump(
        out_folder_dict,
        indent=2,
        allow_unicode=True,
        default_flow_style=False,
    )

    print(contents)


if __name__ == "__main__":
    main()
