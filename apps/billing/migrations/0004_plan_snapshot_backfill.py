"""Fill the plan snapshot of rows written before Billing Build B3. Data only.

The plans are written out here, not read from apps.billing.plan, so this file means the same
whatever the catalogue becomes. Only rows still empty are filled, so a second run changes
nothing.
"""
from django.db import migrations

# code -> (price in centavos, months): the two plans that could be bought before B3.
PLANS = {"monthly": (249_900, 1), "launch_promo": (499_900, 6)}
MONTHLY = "monthly"


def backfill(apps, schema_editor):
    Payment = apps.get_model("billing", "BankTransferPayment")
    Cycle = apps.get_model("billing", "BillingCycle")
    for payment in Payment.objects.filter(plan_version__isnull=True):
        payment.plan_version = 1
        payment.term_months = PLANS[payment.plan_code][1]
        payment.save(update_fields=["plan_version", "term_months"])
    for cycle in Cycle.objects.filter(kind="paid", plan_version__isnull=True):
        payment = Payment.objects.filter(cycle=cycle).first()
        if payment is not None:  # settled by a payment: the plan that was paid for
            code, price, months = payment.plan_code, payment.amount_centavos, payment.term_months
        else:  # pending or lapsed: the renewal that was due, which was always Monthly
            code = MONTHLY
            price, months = PLANS[MONTHLY]
        cycle.plan_code, cycle.plan_version = code, 1
        cycle.price_centavos, cycle.term_months = price, months
        cycle.save(update_fields=["plan_code", "plan_version", "price_centavos", "term_months"])


class Migration(migrations.Migration):

    dependencies = [("billing", "0003_plan_snapshot")]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
