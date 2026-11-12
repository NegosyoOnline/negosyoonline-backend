"""The one registry of retention rules, and the only thing that runs them.

A rule has a name and a function taking `now` and `dry_run` and returning the count: the rows
it removed or blanked, or, on a dry run, the rows it would. Each app registers its own rules
from its `retention.py`, loaded in its `AppConfig.ready()`.

`RETENTION_ENABLED` is checked inside the rules that are wrapped with `behind_switch`, not
here: with the switch off such a rule touches nothing, whoever calls `run_all`. A rule with its
own switch (`behind_setting`) answers to that setting instead and not to RETENTION_ENABLED.
"""
import functools
import logging
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

# Expired sessions, sign-in codes and tokens are deleted this long after they expire.
CREDENTIAL_GRACE = timedelta(days=30)

_rules = {}


@dataclass
class Report:
    dry_run: bool
    counts: dict = field(default_factory=dict)
    switched_off: list = field(default_factory=list)
    errors: dict = field(default_factory=dict)


def enabled():
    return settings.RETENTION_ENABLED


def _guarded(fn, is_on):
    @functools.wraps(fn)
    def rule(now, dry_run):
        if not dry_run and not is_on():
            return 0
        return fn(now, dry_run)

    rule.behind_switch = True
    rule.switch_is_on = is_on
    return rule


def behind_switch(fn):
    """A rule that deletes or blanks only while RETENTION_ENABLED is on. Its dry run always
    counts, because a dry run changes nothing."""
    return _guarded(fn, enabled)


def behind_setting(name):
    """Like behind_switch, for a rule that has its own switch: the setting `name` (a boolean
    in settings) replaces RETENTION_ENABLED for that rule, and run_all reports it as switched
    off while that setting is off."""

    def decorate(fn):
        return _guarded(fn, lambda: getattr(settings, name))

    return decorate


def register(name, fn):
    if name in _rules:
        raise ValueError(f"Retention rule {name!r} is already registered.")
    _rules[name] = fn


def names():
    return list(_rules)


def remove(rows, dry_run):
    """Delete these rows, or only count them on a dry run. Returns the count."""
    if dry_run:
        return rows.count()
    return rows.delete()[1].get(rows.model._meta.label, 0)


def run_all(now=None, dry_run=False, only=None):
    """Run every registered rule, or only the named ones, each in its own transaction.

    One rule failing never stops the others. A real run writes the run log: one audit entry
    with the count per rule. A dry run writes nothing at all.
    """
    chosen = names() if only is None else list(only)
    unknown = [name for name in chosen if name not in _rules]
    if unknown:
        raise ValueError(f"No such retention rule: {', '.join(unknown)}.")
    now = now or timezone.now()
    report = Report(dry_run=dry_run)
    for name in chosen:
        rule = _rules[name]
        if not dry_run and getattr(rule, "behind_switch", False) and not rule.switch_is_on():
            report.switched_off.append(name)
            continue
        try:
            with transaction.atomic():
                report.counts[name] = rule(now, dry_run)
        except Exception as exc:  # noqa: BLE001 - one rule never stops the run
            logger.exception("Retention rule %s failed", name)
            report.errors[name] = type(exc).__name__
    if not dry_run:
        from apps.operators import audit  # here, not at the top: operators imports this app

        audit.record(
            None,
            "retention_run",
            detail={
                "counts": report.counts,
                "switched_off": report.switched_off,
                "errors": report.errors,
            },
        )
    return report
