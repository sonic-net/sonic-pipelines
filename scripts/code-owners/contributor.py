"""Module for managing contributor information and collections."""

import logging
import os
from typing import Optional, Dict, List, Set
import yaml
import aiofiles

from organization import (
    organization_by_company,
    organization_by_suffix,
    organization_by_emails,
    ORGANIZATION,
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
        emails: Set[str],
        organization: ORGANIZATION = None,
        github_login: str = None,
        github_id: int = None,
        available_to_review: bool = False,
    ):
        """Initialize a Contributor object.

        Args:
            name: The contributor's display name.
            emails: Set of email addresses associated with the contributor.
            organization: The organization the contributor belongs to.
            github_login: The contributor's GitHub username.
            github_id: The contributor's GitHub user ID.
        """
        self.name = name
        self.emails = emails
        if github_id == -1:
            organization = ORGANIZATION.OTHER
        elif organization is None:
            organization = organization_by_emails(self.emails)
            if organization == ORGANIZATION.OTHER:
                organization = organization_by_company(github_login)
            if organization == ORGANIZATION.OTHER:
                organization = organization_by_company(name)
            if organization == ORGANIZATION.OTHER:
                organization = organization_by_suffix(github_login)

        self.organization = organization

        self.github_login = github_login
        self.github_id = github_id
        self.available_to_review = available_to_review
        # The last commit TS as per git log
        self.last_commit_ts = None
        # Commits made by the contributor
        self.commits = []

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
        """Return a string representation of the Contributor object.

        Returns:
            str: String representation of the Contributor.
        """
        return (
            f"{__class__.__name__}({repr(self.name)}, {self.emails}, "
            f"{self.organization}, {repr(self.github_login)})"
        )

    def to_dict(self):
        """Return a dictionary representation of the Contributor."""
        return {
            "name": self.name,
            "emails": sorted(self.emails),
            "organization": str(self.organization.name),
            "github_login": self.github_login,
            "github_id": self.github_id,
            "last_commit_ts": self.last_commit_ts,
            "commit_count": len(self.commits),
            "available_to_review": self.available_to_review,
        }


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

    def add_update_contributor(
        self, contributor: Contributor
    ) -> Optional[Contributor]:
        """Add or update a contributor in the collection.

        If a contributor with the same GitHub ID already exists, updates
        the existing contributor's information. Otherwise, adds the new
        contributor to the collection.

        Args:
            contributor: The Contributor object to add or update.

        Returns:
            Optional[Contributor]: The added/updated contributor, or None if
            failed.

        Raises:
            ValueError: If GitHub ID is missing or email conflicts exist.
        """
        if contributor.github_id is None:
            raise ValueError("Need to have the GitHub id to update")
        try:
            existing_contributor = self.by_github_id[contributor.github_id]
            existing_contributor.name = contributor.name
            existing_contributor.organization = contributor.organization
            for email in contributor.emails:
                if email not in existing_contributor.emails:
                    if email in self.by_email:
                        raise ValueError(f"Duplicate email {email}")
                    else:
                        existing_contributor.emails.add(email)
                        self.by_email[email] = existing_contributor
            return existing_contributor
        except KeyError:
            self.contributors.append(contributor)
            self.by_github_id[contributor.github_id] = contributor
            for email in contributor.emails:
                if email in self.by_email:
                    raise ValueError(f"Duplicate email {email}")
                else:
                    self.by_email[email] = contributor
            return contributor

    async def save_to_file(self):
        """Save all contributors to the YAML file.

        Serializes all contributors in the collection to the configured
        YAML file using the custom YAML representer.
        """
        contents = yaml.safe_dump(
            [contributor.to_dict() for contributor in self.contributors],
            indent=2,
            allow_unicode=True,
            default_flow_style=False,
        )
        async with aiofiles.open(self.db_filename, "w") as out_file:
            await out_file.write(contents)

    async def load_from_file(self):
        """Load contributors from the YAML file.

        Deserializes contributors from the configured YAML file using
        the custom YAML constructor. If the file doesn't exist, does nothing.
        """
        try:
            async with aiofiles.open(self.db_filename, "r") as in_file:
                contents = await in_file.read()
            for value in yaml.safe_load(contents):
                if value["github_id"] is None:
                    raise ValueError(f"Missing github id in YAML data {value}")
                org = ORGANIZATION[value["organization"]]
                contributor = Contributor(
                    name=value["name"],
                    emails=set(value["emails"]),
                    organization=org,
                    github_login=value["github_login"],
                    github_id=value["github_id"],
                    available_to_review=value.get("available_to_review", False),
                )
                self.add_update_contributor(contributor)
        except FileNotFoundError:
            pass

    def __repr__(self):
        """Return a string representation of the ContributorCollection.

        Returns:
            str: String representation of the ContributorCollection.
        """
        return f"{__class__.__name__}({repr(self.contributors)})"
