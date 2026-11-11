"""PostgreSQL triggers behind the Django guards (design FAS-03, section 2.5, layer 2).

A no-op on any other database. This file imports nothing from the app, so it can never change
meaning later. Two deliberate departures from the design text are in the S2-0 plan: there is
no TRUNCATE trigger on ledger_event, and the period trigger refuses only a locked period.
"""
from django.db import migrations

APPLY = [
    """CREATE FUNCTION ledger_entry_guard() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE
    per RECORD;
    book_start date;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'posted' THEN
            RAISE EXCEPTION 'ledger: a posted entry cannot be deleted' USING ERRCODE = 'check_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.status = 'posted' THEN
        RAISE EXCEPTION 'ledger: a posted entry cannot be changed' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = 'posted' THEN
        IF NEW.posting_seq IS NULL OR NEW.period_id IS NULL OR NEW.posted_at IS NULL OR NEW.seal = '' THEN
            RAISE EXCEPTION 'ledger: a posted entry needs posting_seq, period, posted_at and seal' USING ERRCODE = 'check_violation';
        END IF;
        SELECT entity_id, start_date, end_date, status INTO per FROM ledger_period WHERE id = NEW.period_id;
        IF per.entity_id IS DISTINCT FROM NEW.entity_id THEN
            RAISE EXCEPTION 'ledger: the period belongs to another book' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.entry_date < per.start_date OR NEW.entry_date > per.end_date THEN
            RAISE EXCEPTION 'ledger: the entry date is outside the period' USING ERRCODE = 'check_violation';
        END IF;
        IF per.status = 'locked' THEN
            RAISE EXCEPTION 'ledger: the period is locked' USING ERRCODE = 'check_violation';
        END IF;
        SELECT books_start_date INTO book_start FROM ledger_entity WHERE id = NEW.entity_id;
        IF book_start IS NULL OR NEW.entry_date < book_start THEN
            RAISE EXCEPTION 'ledger: the entry is dated before the books start' USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END
$fn$;""",
    """CREATE TRIGGER ledger_entry_guard BEFORE UPDATE OR DELETE ON ledger_entry
    FOR EACH ROW EXECUTE FUNCTION ledger_entry_guard();""",
    """CREATE FUNCTION ledger_line_guard() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE
    parent RECORD;
    acct RECORD;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        SELECT status INTO parent FROM ledger_entry WHERE id = OLD.entry_id;
        IF FOUND AND parent.status <> 'draft' THEN
            RAISE EXCEPTION 'ledger: the lines of a posted entry cannot be changed' USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    SELECT entity_id, entry_date, kind, status INTO parent FROM ledger_entry WHERE id = NEW.entry_id;
    IF parent.status <> 'draft' THEN
        RAISE EXCEPTION 'ledger: a line cannot be added to a posted entry' USING ERRCODE = 'check_violation';
    END IF;
    SELECT entity_id, is_postable, effective_from, effective_to INTO acct FROM ledger_account WHERE id = NEW.account_id;
    IF NOT acct.is_postable THEN
        RAISE EXCEPTION 'ledger: a header account takes no line' USING ERRCODE = 'check_violation';
    END IF;
    IF acct.entity_id <> parent.entity_id THEN
        RAISE EXCEPTION 'ledger: the account belongs to another book' USING ERRCODE = 'check_violation';
    END IF;
    IF (acct.effective_from IS NOT NULL AND parent.entry_date < acct.effective_from)
       OR (acct.effective_to IS NOT NULL AND parent.entry_date > acct.effective_to) THEN
        RAISE EXCEPTION 'ledger: the entry date is outside the account''s effective dates' USING ERRCODE = 'check_violation';
    END IF;
    IF parent.kind = 'opening' AND btrim(NEW.evidence_ref) = '' THEN
        RAISE EXCEPTION 'ledger: every line of the opening entry needs an evidence reference' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$fn$;""",
    """CREATE TRIGGER ledger_line_guard BEFORE INSERT OR UPDATE OR DELETE ON ledger_line
    FOR EACH ROW EXECUTE FUNCTION ledger_line_guard();""",
    """CREATE FUNCTION ledger_event_guard() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION 'ledger: the change history is append-only' USING ERRCODE = 'check_violation';
END
$fn$;""",
    """CREATE TRIGGER ledger_event_append_only BEFORE UPDATE OR DELETE ON ledger_event
    FOR EACH ROW EXECUTE FUNCTION ledger_event_guard();""",
    """CREATE FUNCTION ledger_entry_balanced() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE
    n integer;
    debits numeric;
    credits numeric;
    bad integer;
BEGIN
    IF NEW.status <> 'posted' THEN
        RETURN NULL;
    END IF;
    SELECT count(*), COALESCE(sum(debit_centavos), 0), COALESCE(sum(credit_centavos), 0)
        INTO n, debits, credits FROM ledger_line WHERE entry_id = NEW.id;
    IF n < 2 OR debits <> credits OR debits <> NEW.total_centavos THEN
        RAISE EXCEPTION 'ledger: entry % is not balanced (% lines, debits %, credits %, total %)',
            NEW.id, n, debits, credits, NEW.total_centavos USING ERRCODE = 'check_violation';
    END IF;
    SELECT count(*) INTO bad FROM ledger_line l JOIN ledger_account a ON a.id = l.account_id
        WHERE l.entry_id = NEW.id AND (
            NOT a.is_postable OR a.entity_id <> NEW.entity_id
            OR (a.effective_from IS NOT NULL AND NEW.entry_date < a.effective_from)
            OR (a.effective_to IS NOT NULL AND NEW.entry_date > a.effective_to));
    IF bad > 0 THEN
        RAISE EXCEPTION 'ledger: entry % has a line on an account that cannot take it on that date', NEW.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$fn$;""",
    """CREATE CONSTRAINT TRIGGER ledger_entry_balanced AFTER INSERT OR UPDATE ON ledger_entry
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ledger_entry_balanced();""",
]

TRIGGERS = [
    ("ledger_entry_balanced", "ledger_entry"),
    ("ledger_event_append_only", "ledger_event"),
    ("ledger_line_guard", "ledger_line"),
    ("ledger_entry_guard", "ledger_entry"),
]
FUNCTIONS = ["ledger_entry_balanced", "ledger_event_guard", "ledger_line_guard", "ledger_entry_guard"]


def apply(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for statement in APPLY:
        schema_editor.execute(statement, None)  # None: the SQL holds a literal %


def reverse(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for name, table in TRIGGERS:
        schema_editor.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    for name in FUNCTIONS:
        schema_editor.execute(f"DROP FUNCTION IF EXISTS {name}()")


class Migration(migrations.Migration):
    dependencies = [("ledger", "0001_initial")]

    operations = [migrations.RunPython(apply, reverse)]
