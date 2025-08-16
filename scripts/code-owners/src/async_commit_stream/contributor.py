"""Module for managing contributor information and collections."""

import logging
from typing import Optional, Dict, List
import yaml
from yaml import MappingNode
import aiofiles

from .organization import (
    organization_by_emails,
    ORGANIZATION,
    organization_by_company,
)

logger = logging.getLogger(__name__)


class Contributor:
    """Represents a code contributor with their information and commit history.

    Attributes:
        name: The contributor's display name.
        emails: Set of email addresses associated with the contributor.
        organization: The organization the contributor belongs to.
        github_login: The contributor's GitHub username.
        github_id: The contributor's GitHub user ID.
        last_commit_ts: Timestamp of the contributor's most recent commit.
        commits: List of GitCommit objects made by this contributor.
    """

    def __init__(
        self,
        name: str,
        emails: List[str],
        organization: ORGANIZATION = None,
        github_login: str = None,
        github_id: int = None,
    ):
        """Initialize a Contributor object.

        Args:
            name: The contributor's display name.
            emails: List of email addresses associated with the contributor.
            organization: The organization the contributor belongs to.
            github_login: The contributor's GitHub username.
            github_id: The contributor's GitHub user ID.
        """
        self.name = name
        self.emails = {email.lower() for email in emails}
        if organization is None:
            self.organization = organization_by_emails(self.emails)
        else:
            self.organization = organization

        self.github_login = github_login
        self.github_id = github_id

        if github_login is None or github_id is None:
            github_info = github_info_by_emails(self.emails)
            if github_info:
                self.github_login = github_info["login"]
                self.github_id = github_info["id"]
                if github_info["email"]:
                    self.emails.add(github_info["email"].lower())
                if self.organization == ORGANIZATION.OTHER:
                    self.organization = organization_by_company(
                        str(github_info.get("company"))
                        + "_"
                        + github_info["login"]
                    )
                if github_info["name"]:
                    self.name = github_info["name"]
                elif not self.name:
                    self.name = github_info["login"]

        # The last commit TS as per git log
        self.last_commit_ts = None
        # Commits made by the contributor
        self.commit_count = 0

    def __hash__(self):
        """Hash by GitHub ID.

        Returns:
            int: Hash value based on GitHub ID.

        Raises:
            ValueError: If GitHub ID is not available.
        """
        if self.github_id is None:
            raise ValueError("Need a GitHub id to hash the contributor")
        return hash(self.github_id)

    def __eq__(self, other):
        """Compare contributors by GitHub ID.

        Args:
            other: Another Contributor object to compare with.

        Returns:
            bool: True if both contributors have the same GitHub ID.

        Raises:
            ValueError: If GitHub ID is not available on either contributor.
        """
        if self.github_id is None or other.github_id is None:
            raise ValueError(
                "Need a GitHub id on both " "contributors to compare"
            )
        return self.github_id == other.github_id

    def __repr__(self):
        """Return a string representation of the Contributor object."""
        return (
            f"{__class__.__name__}({repr(self.name)}, {self.emails}, "
            f"{self.organization}, {repr(self.github_login)})"
        )


def contributor_representer(
    dumper: yaml.SafeDumper, data: Contributor
) -> MappingNode:
    """YAML representer for Contributor objects.

    Args:
        dumper: The YAML dumper instance.
        data: The Contributor object to serialize.

    Returns:
        MappingNode: YAML representation of the Contributor object.
    """
    return dumper.represent_mapping(
        "!Contributor",  # Custom tag for the Contributor object
        {
            "name": data.name,
            "emails": sorted(data.emails),
            "organization": str(data.organization.name),
            "github_login": data.github_login,
            "github_id": data.github_id,
            "last_commit_ts": data.last_commit_ts,
            "commit_count": data.commit_count,
        },
    )


yaml.SafeDumper.add_representer(Contributor, contributor_representer)


def contributor_constructor(loader: yaml.SafeLoader, node) -> Contributor:
    """YAML constructor for Contributor objects.

    Args:
        loader: The YAML loader instance.
        node: The YAML node to deserialize.

    Returns:
        Contributor: The reconstructed Contributor object.

    Raises:
        ValueError: If GitHub ID is missing from the YAML data.
    """
    value = loader.construct_mapping(node, deep=True)
    if value["github_id"] is None:
        raise ValueError(f"Missing github id in YAML data {value}")
    return Contributor(
        name=value["name"],
        emails=value["emails"],
        organization=(ORGANIZATION[value["organization"]]),
        github_login=value["github_login"],
        github_id=value["github_id"],
    )


yaml.SafeLoader.add_constructor("!Contributor", contributor_constructor)


class ContributorCollection:
    """Collection of contributors with indexing and persistence capabilities.

    Attributes:
        contributors: List of all Contributor objects.
        by_github_id: Dictionary mapping GitHub IDs to Contributor objects.
        by_email: Dictionary mapping email addresses to Contributor objects.
        db_filename: Path to the YAML file for persistence.
    """

    def __init__(self, db_filename: str):
        """Initialize a ContributorCollection.

        Args:
            db_filename: Path to the YAML file for loading/saving contributors.
        """
        self.contributors: List[Contributor] = []
        self.by_github_id: Dict[int, Contributor] = dict()
        self.by_email: Dict[str, Contributor] = dict()

        self.db_filename = db_filename

    def get_contributor(
        self, contributor: Contributor, add_missing: bool = False
    ) -> Optional[Contributor]:
        """Get a contributor from the collection.

        Searches for the contributor by GitHub ID first,
        then by email addresses.
        If not found and add_missing is True,
        adds the contributor to the collection.

        Args:
            contributor: The Contributor object to search for.
            add_missing: If True, add the contributor
                         to the collection if not found.

        Returns:
            Optional[Contributor]: The found contributor or None if not found.
        """
        if contributor.github_id is not None:
            try:
                return self.by_github_id[contributor.github_id]
            except KeyError:
                pass

        for email in contributor.emails:
            try:
                return self.by_email[email]
            except KeyError:
                pass

        if add_missing:
            # Not found, add the contributor to the collection
            self.contributors.append(contributor)

            # Update collection indexes
            if contributor.github_id is not None:
                self.by_github_id[contributor.github_id] = contributor
            for email in contributor.emails:
                self.by_email[email] = contributor
            return contributor
        return None

    async def save_to_file(self):
        """Save all contributors to the YAML file."""
        contents = yaml.safe_dump_all(
            self.contributors,
            indent=2,
            allow_unicode=True,
            default_flow_style=False,
        )
        async with aiofiles.open(self.db_filename, "w") as out_file:
            await out_file.write(contents)

    async def load_from_file(self):
        """Load contributors from the YAML file."""
        try:
            async with aiofiles.open(self.db_filename, "r") as in_file:
                contents = await in_file.read()
            for item in yaml.safe_load_all(contents):
                self.get_contributor(item, add_missing=True)
        except FileNotFoundError:
            pass

    def __repr__(self):
        """Return a string representation of the ContributorCollection."""
        return f"{__class__.__name__}({repr(self.contributors)})"

    def update_contributor_emails(self, contributor, email: str):
        """Add an email address to a contributor.

        Args:
            contributor: The Contributor object to update.
            email: The email address to add.

        Raises:
            ValueError: If the email is already associated
            with another contributor.
        """
        if email not in contributor.emails:
            if email in self.by_email:
                logger.error(
                    f"Duplicate email for new: {contributor}, "
                    f"existing: {self.by_email[email]}"
                )
                raise ValueError(f"Duplicate email: {email}")
            self.by_email[email] = contributor
            contributor.emails.add(email)

    def get_contributor_by_email(self, email: str) -> Contributor:
        """Get a contributor by their email address.

        Args:
            email: The email address to search for.

        Returns:
            Contributor: The contributor with the given email,
            or None if not found.
        """
        return self.by_email.get(email)
