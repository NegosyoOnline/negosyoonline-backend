"""The adapter registry. No provider adapter is registered yet; the contract is in adapters/base.py.
An unknown provider fails closed: `get` answers None."""

ADAPTERS = {}


def get(provider):
    return ADAPTERS.get(provider)
