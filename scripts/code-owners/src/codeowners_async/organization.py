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
    HCLTECH = "HCL Technologies Ltd"
    INTC = "Intel Corporation"
    JNPR = "Juniper"
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
    "alibaba-inc.com": ORGANIZATION.BABA,
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
    "hcltech.com": ORGANIZATION.HCLTECH,
    "intel.com": ORGANIZATION.INTC,
}

ORGANIZATION_SUFFIXES = {
    "arista": ORGANIZATION.ANET,
    "ms": ORGANIZATION.MSFT,
    "nv": ORGANIZATION.NVDA,
    "mlnx": ORGANIZATION.NVDA,
    "hcl": ORGANIZATION.HCLTECH,
    "brcm": ORGANIZATION.AVGO,
    "bcm": ORGANIZATION.AVGO,
    "nexthop": ORGANIZATION.NXHP,
    "keys": ORGANIZATION.KEYS,
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
    if (
        "nvidia" in company
        or "mellanox" in company
        or "nvda" in company
        or "mlnx" in company
    ):
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
    if "juniper" in company:
        return ORGANIZATION.JNPR

    return ORGANIZATION.OTHER


def organization_by_suffix(github_login: str) -> ORGANIZATION:
    for suffix, org in ORGANIZATION_SUFFIXES.items():
        # check if the login ends with the suffix
        if (
            len(github_login) > len(suffix)
            and github_login[-len(suffix) :].lower() == suffix
        ):
            # check if the suffix is separates by the case or punctuation
            pre_suffix = github_login[-len(suffix) - 1]
            if not (pre_suffix.isalnum()):
                return org
            suffix_start = github_login[-len(suffix)]
            # case change in between the name and suffix
            if (
                pre_suffix.isalpha()
                and suffix_start.isalpha()
                and (pre_suffix.islower() ^ suffix_start.islower())
            ):
                return org

    return ORGANIZATION.OTHER
