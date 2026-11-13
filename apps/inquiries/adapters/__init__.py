"""The adapter registry. An unknown provider fails closed: `get` answers None."""
from apps.inquiries.adapters.pancake import PancakeAdapter

ADAPTERS = {PancakeAdapter.provider: PancakeAdapter()}


def get(provider):
    return ADAPTERS.get(provider)
