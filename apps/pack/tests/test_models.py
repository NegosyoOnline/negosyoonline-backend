"""Database rules: append-only evidence, no delete, the fixed amount, one live order."""
from django.db import IntegrityError, transaction

from apps.pack import constants as C
from apps.pack.models import (
    ImmutableRecordError, PackAgreementAcceptance, PackEnrollmentAuthorization, PackOrder,
    PackOrderEvent, PackProofSubmission,
)
from apps.pack.tests.base import PackTestCase


class ModelRuleTests(PackTestCase):
    def setUp(self):
        super().setUp()
        self.placed()
        self.order = PackOrder.objects.get()

    def test_orders_are_never_deleted(self):
        with self.assertRaises(ImmutableRecordError):
            self.order.delete()

    def test_evidence_rows_refuse_save_delete_and_queryset_writes(self):
        for model in (PackProofSubmission, PackOrderEvent, PackAgreementAcceptance, PackEnrollmentAuthorization):
            row = model.objects.get()
            with self.assertRaises(ImmutableRecordError, msg=model.__name__):
                row.save()
            with self.assertRaises(ImmutableRecordError, msg=model.__name__):
                row.delete()
            with self.assertRaises(ImmutableRecordError, msg=model.__name__):
                model.objects.all().update(site=self.site)
            with self.assertRaises(ImmutableRecordError, msg=model.__name__):
                model.objects.all().delete()

    def test_the_price_is_fixed_by_the_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            PackOrder.objects.filter(pk=self.order.pk).update(price_centavos=100)

    def test_one_live_order_per_member(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            PackOrder.objects.create(
                site=self.site, member=self.order.member, order_number="SMP00002",
                price_centavos=C.PRICE_CENTAVOS, buyer_name="x", payment_status=C.PENDING_REVIEW,
                idempotency_key="other",
            )

    def test_order_number_and_idempotency_key_are_unique_per_site(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            PackOrder.objects.create(
                site=self.site, member=self.order.member, order_number=self.order.order_number,
                price_centavos=C.PRICE_CENTAVOS, buyer_name="x", payment_status=C.REJECTED,
                idempotency_key="other",
            )

    def test_proof_amount_is_range_checked(self):
        sub = PackProofSubmission.objects.get()
        sub.pk = None
        sub._state.adding = True
        sub.public_id, sub.seq, sub.amount_sent_centavos = "pps_x", 2, 0
        with self.assertRaises(IntegrityError), transaction.atomic():
            sub.save()

    def test_an_acceptance_needs_the_orders_site_and_the_versions_checksum(self):
        acceptance = PackAgreementAcceptance.objects.get()
        with self.assertRaises(ImmutableRecordError):
            PackAgreementAcceptance(
                site=self.other_site, order=self.order, agreement=acceptance.agreement,
                version=acceptance.version, sha256=acceptance.sha256, accepted_at=acceptance.accepted_at,
            ).save()
        with self.assertRaises(ImmutableRecordError):
            PackAgreementAcceptance(
                site=self.site, order=self.order, agreement=acceptance.agreement,
                version=acceptance.version, sha256="0" * 64, accepted_at=acceptance.accepted_at,
            ).save()
