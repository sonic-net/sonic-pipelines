"""Module for managing contributor information and collections."""

import logging
from typing import Optional, Dict, List, Set
import yaml
from yaml import MappingNode
import aiofiles

from codeowners.organization import organization_by_company
from .organization import organization_by_emails, ORGANIZATION

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
        commit_count: Count of GitCommit objects made by this contributor.
    """

    def __init__(
        self,
        name: str,
        emails: Set[str],
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
        self.emails = emails
        if organization is None:
            organization = organization_by_emails(self.emails)
            if organization == ORGANIZATION.OTHER:
                organization = organization_by_company(github_login)

        self.organization = organization

        self.github_login = github_login
        self.github_id = github_id

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

    def add_update_contributor(
        self, contributor: Contributor
    ) -> Optional[Contributor]:
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
                self.add_update_contributor(item)
        except FileNotFoundError:
            pass

    def __repr__(self):
        """Return a string representation of the ContributorCollection."""
        return f"{__class__.__name__}({repr(self.contributors)})"
