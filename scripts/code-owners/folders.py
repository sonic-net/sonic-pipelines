"""Module for managing folder settings and repository folder analysis."""

import os
from collections import namedtuple
from enum import Enum
import shlex
from typing import Dict, Tuple
import aiofiles
import aiofiles.os

import yaml

from async_helpers import (
    async_run_cmd_lines,
)


class FolderType(Enum):
    """Enumeration of folder types for analysis and ownership."""

    REGULAR = "Regular folder, analyze the code contributors"
    IGNORE = "Skip this folder and subfolders"
    CLOSED_OWNERS = (
        "The folder and subfolders " "already have a fixed set of owners"
    )
    OPEN_OWNERS = "The folder has a set of code owners and more can be added"


FolderSettings = namedtuple(
    "FolderSettings", ["folder_type", "owners", "children"]
)


def is_subfolder(prefix: str, folder: str) -> bool:
    """Check if a folder is a subfolder of a given prefix.

    Args:
        prefix: The parent folder path.
        folder: The folder path to check.

    Returns:
        bool: True if folder is a subfolder of prefix, False otherwise.
    """
    return (
        len(folder) > len(prefix)
        and folder[len(prefix)] == os.sep
        and folder.startswith(prefix)
    )


def get_folder_settings(folder: str, preset_folders) -> FolderSettings:
    """Get the settings for a specific folder.

    Checks for explicitly defined folders first, then checks if the folder
    is a subfolder of any explicitly defined folder. If neither condition
    is met, returns regular folder settings.

    Args:
        folder: The folder path to get settings for.
        preset_folders: Dictionary of the folders and their presets

    Returns:
        FolderSettings: The settings for the specified folder.
    """
    # Explicitly defined folder
    if folder in preset_folders:
        # return the copy of a named tuple
        return preset_folders[folder]._replace()

    # Ignore all subfolders of the explicitly defined folders
    if any(is_subfolder(prefix, folder) for prefix in preset_folders):
        return FolderSettings(FolderType.IGNORE, {}, [])

    # Regular folder is the default
    return FolderSettings(FolderType.REGULAR, {}, [])


async def get_repo_folders(
    repo: str, preset_folders: Dict[str, FolderSettings]
) -> Tuple[Dict[str, FolderSettings], Dict[str, FolderSettings]]:
    """Get all folders in a repository with their settings.

    Walks through the repository directory tree and returns a dictionary
    mapping folder paths to their settings. Folders marked as IGNORE
    are excluded from the result.

    Args:
        repo: Path to the repository root directory.
        preset_folders: Dictionary of the folders and their presets

    Returns:
        Tuple[Dict[str, FolderSettings], Dict[str, FolderSettings]]:
        Tuple of dictionaries of the folder presets and the actual
        folders found in the repo
        Dictionary mapping folder paths to their settings.

    Raises:
        ValueError: If a folder is found outside the repository path.
    """
    result = {}
    if repo[-1] != os.sep:
        repo += os.sep
    cmd = f"find {shlex.quote(repo)} -mount -type d"
    async for folder in async_run_cmd_lines(cmd):
        folder = folder.rstrip(os.linesep)
        if not folder.startswith(repo):
            raise ValueError(
                f"Folder: {folder} is outside of repo_name {repo}"
            )
        folder = folder[len(repo) - 1 :]
        folder_settings = get_folder_settings(folder, preset_folders)
        if folder_settings.folder_type != FolderType.IGNORE:
            result[folder] = folder_settings
            # update the parent folder with the child name
            if folder != "/":
                (
                    result[os.path.dirname(folder)].children.append(
                        os.path.basename(folder)
                    )
                )

    return preset_folders, result


def folder_settings_constructor(
    loader: yaml.SafeLoader, node
) -> FolderSettings:
    """YAML constructor for FolderSettings objects.

    Args:
        loader: The YAML loader instance.
        node: The YAML node to deserialize.

    Returns:
        FolderSettings: The reconstructed FolderSettings object.
    """
    value = loader.construct_mapping(node, deep=True)
    return FolderSettings(
        folder_type=FolderType[value["type"]],
        owners=set(value.get("owners", [])),
        children=[],
    )


yaml.SafeLoader.add_constructor("!FolderSettings", folder_settings_constructor)


async def load_folder_metadata(
    filename: str, repo: str
) -> Tuple[Dict[str, FolderSettings], Dict[str, FolderSettings]]:
    """Load folder metadata from a YAML file and get repository folder
    structure.

    Loads preset folder configurations from a YAML file and then scans the
    repository to build a complete folder structure with settings.

    Args:
        filename: Path to the YAML file containing folder presets.
        repo: Path to the repository root directory.

    Returns:
        Tuple[Dict[str, FolderSettings], Dict[str, FolderSettings]]:
        Tuple of dictionaries of the folder presets and the actual
        folders found in the repo

    Raises:
        ValueError: If a folder is found outside the repository path.
    """
    preset_folders: Dict[str, FolderSettings] = {}
    if filename:
        async with aiofiles.open(filename, "r") as folder_file:
            contents = await folder_file.read()
        loaded_folders = {}
        for folder_name, value in yaml.safe_load(contents).items():
            loaded_folders[folder_name] = FolderSettings(
                folder_type=FolderType[value["type"]],
                owners=value.get("owners", {}),
                children=[],
            )
        preset_folders.update(loaded_folders)
    return await get_repo_folders(repo, preset_folders)
