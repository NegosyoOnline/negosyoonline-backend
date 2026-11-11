"""PostgreSQL triggers behind the Django guards (Billing Build B5, plan section 5.4, layer 3).

A no-op on any other database. Imports nothing from the apps, so it can never change meaning
later. Insert-only tables: no UPDATE or DELETE, from Django, raw SQL, a script or a later
migration. No TRUNCATE trigger. Kept in its own migration so it can be dropped without touching
the tables. It also covers invoices_invoiceevent, hence the dependency on invoices.0001.
"""
from django.db import migrations

TABLES = ["statements_statement", "statements_statementline", "invoices_invoiceevent"]

FUNCTION = """CREATE FUNCTION billing_record_guard() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION 'billing: % is final and cannot be changed or deleted', TG_TABLE_NAME
        USING ERRCODE = 'check_violation';
END
$fn$;"""


def apply(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(FUNCTION, None)  # None: the SQL holds a literal %
    for table in TABLES:
        schema_editor.execute(
            f"CREATE TRIGGER billing_record_guard BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION billing_record_guard()"
        )


def reverse(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for table in TABLES:
        schema_editor.execute(f"DROP TRIGGER IF EXISTS billing_record_guard ON {table}")
    schema_editor.execute("DROP FUNCTION IF EXISTS billing_record_guard()")


class Migration(migrations.Migration):
    dependencies = [
        ("statements", "0001_initial"),
        ("invoices", "0001_initial"),
    ]

    operations = [migrations.RunPython(apply, reverse)]
