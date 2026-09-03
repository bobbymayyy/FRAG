"""
Importing this package registers all known HostProvider plugins into
REGISTRY under slot "host:<alias>". Each provider validates its own env
eagerly at construction time, so REGISTRY.best("host:github") either
returns a working provider or raises -- it never returns a provider that
"succeeds" but can't actually reach GitHub.
"""

from frag.registry import REGISTRY
from frag.hosts.github import GitHubProvider
from frag.hosts.gitea import GiteaProvider
from frag.hosts.base import extract_ref_from_text, parse_ref  # re-exported for convenience

REGISTRY.register("host:github", "github", GitHubProvider, priority=0)
REGISTRY.register("host:gitea", "gitea", GiteaProvider, priority=0)

KNOWN_HOSTS = {"github", "gitea"}


def get_provider(host: str):
    if host not in KNOWN_HOSTS:
        raise ValueError(f"unknown host {host!r}, expected one of {sorted(KNOWN_HOSTS)}")
    return REGISTRY.best(f"host:{host}")
