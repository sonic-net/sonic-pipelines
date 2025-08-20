"""Module for managing organization information and classification."""

import enum
from typing import Set


class ORGANIZATION(enum.Enum):
    """Enumeration of supported organizations."""

    ANET = "Arista"
    AVGO = "Broadcom"
    BABA = "Alibaba Inc"
    CSCO = "Cisco"
    DELL = "Dell technologies"
    KEYS = "Keysight Technologies"
    MRVL = "Marvell Technology Inc"
    MSFT = "Microsoft"
    NOK = "Nokia"
    NVDA = "Nvidia"
    NXHP = "Nexthop AI"
    ORANY = "Orange"
    OTHER = "Other"


# A lookup dictionary for domains
ORGANIZATION_LOOKUP_DOMAIN = {
    "alibaba.com": ORGANIZATION.BABA,
    "broadcom.com": ORGANIZATION.AVGO,
    "dell.com": ORGANIZATION.DELL,
    "microsoft.com": ORGANIZATION.MSFT,
    "nvidia.com": ORGANIZATION.NVDA,
    "mellanox.com": ORGANIZATION.NVDA,
    "marvell.com": ORGANIZATION.MRVL,
    "nexthop.ai": ORGANIZATION.NXHP,
    "orange.com": ORGANIZATION.ORANY,
    "nokia.com": ORGANIZATION.NOK,
    "cisco.com": ORGANIZATION.CSCO,
    "arista.com": ORGANIZATION.ANET,
    "keysight.com": ORGANIZATION.KEYS,
}


def organization_by_emails(emails: Set[str]) -> ORGANIZATION:
    """Determine organization based on email domain addresses.

    Checks each email address against known organization domains and returns
    the corresponding organization. If no match is found, returns OTHER.

    Args:
        emails: Set of email addresses to check.

    Returns:
        ORGANIZATION: The organization associated with the email domains.
    """
    for email in emails:
        _, domain = email.split("@")
        try:
            return ORGANIZATION_LOOKUP_DOMAIN[domain.lower()]
        except KeyError:
            pass
    return ORGANIZATION.OTHER


def organization_by_company(company: str) -> ORGANIZATION:
    """Determine organization based on company name.

    Uses a cached function to match company names (case-insensitive) to
    known organizations. If no match is found, returns OTHER.

    Args:
        company: The company name to match.

    Returns:
        ORGANIZATION: The organization associated with the company name.
    """
    company = company.lower()
    if "nvidia" in company or "mellanox" in company:
        return ORGANIZATION.NVDA
    if "microsoft" in company or "azure" in company or "msft" in company:
        return ORGANIZATION.MSFT
    if "cisco" in company:
        return ORGANIZATION.CSCO
    if "arista" in company:
        return ORGANIZATION.ANET
    if "keysight" in company:
        return ORGANIZATION.KEYS
    if "marvell" in company:
        return ORGANIZATION.MRVL
    if "dell" in company:
        return ORGANIZATION.DELL
    if "alibaba" in company:
        return ORGANIZATION.BABA
    if "broadcom" in company:
        return ORGANIZATION.AVGO
    if "nokia" in company:
        return ORGANIZATION.NOK
    if "nexthop" in company:
        return ORGANIZATION.NXHP
    if "orange" in company:
        return ORGANIZATION.ORANY

    return ORGANIZATION.OTHER
