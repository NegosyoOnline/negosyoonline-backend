"""Proof fields and submissions (port of TWCinc's negosyoonline_sante_pack/proofs.py, the
checkout half). The proof is a JPEG, PNG, WebP or PDF, judged by its first bytes, never by the file
name or the browser's type. The amount is not required to equal 1,488: the Subscriber judges."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from apps.common import private_files
from apps.common.errors import ApiError
from apps.payments import services as payments
from apps.pack import constants as C
from apps.pack.models import PackProofSubmission

MSG = {
    "account_id": "Choose the account you paid to.",
    "reference_number": "Enter the reference number.",
    "amount_sent": "Enter the amount you sent.",
    "sender_bank": "Enter the bank or e-wallet you sent from.",
    "sender_name": "Enter the name on the account you sent from.",
    "sender_first_name": "Enter the sender's first name.",
    "sender_last_name": "Enter the sender's last name.",
    "sender_account_number": "Enter the account number you sent from.",
    "transferred_at": "Enter the date and exact time of your transfer.",
    "transferred_future": "The time of transfer cannot be in the future.",
}
PROOF_REQUIRED = "Attach the file."  # TWCinc private_files.REQUIRED
PROOF_TYPE = "Upload a JPEG, PNG, WebP or PDF file."  # TWCinc private_files.TYPE_ERROR[PROOF_TYPES]
PROOF_SIZE = "The file is too large. The limit is {mb} MB."  # TWCinc private_files.SIZE_ERROR
PROOF_TYPES = (*private_files.IMAGE_TYPES, "application/pdf")
TIME_FORMAT = "%Y-%m-%dT%H:%M"
CLOCK_SLACK = timedelta(minutes=5)  # a buyer's clock may run a little ahead of the server's


@dataclass(frozen=True)
class ProofInput:
    account: object
    reference: str
    amount_centavos: int
    sender_bank: str
    sender_name: str
    sender_account_number: str
    transferred_at: datetime
    content_type: str
    content: bytes


def _transfer_time(raw):
    """The buyer's datetime-local value, read as Manila time. Date and time are both required: a
    date alone is refused rather than read as midnight. Returns (value, error key)."""
    try:
        value = timezone.make_aware(datetime.strptime(str(raw or "").strip(), TIME_FORMAT))
    except ValueError:
        return None, "transferred_at"
    return (None, "transferred_future") if value > timezone.now() + CLOCK_SLACK else (value, "")


def _sender_name(data, errors):
    """The checkout form asks for a first and a last name and stores them as one name; the single
    sender_name (the lock dialog's) stays accepted."""
    if "sender_first_name" in data or "sender_last_name" in data:
        first = (data.get("sender_first_name") or "").strip()
        last = (data.get("sender_last_name") or "").strip()
        if not 1 <= len(first) <= 59:
            errors["sender_first_name"] = MSG["sender_first_name"]
        if not 1 <= len(last) <= 59:
            errors["sender_last_name"] = MSG["sender_last_name"]
        return f"{first} {last}"
    name = (data.get("sender_name") or "").strip()
    if not 1 <= len(name) <= 120:
        errors["sender_name"] = MSG["sender_name"]
    return name


def _amount_centavos(raw):
    """Centavos of a positive amount with at most two decimals up to 99,999.99, or None."""
    try:
        amount = Decimal(str(raw or "").replace(",", "").strip())
        if not amount.is_finite() or amount <= 0 or amount > Decimal("99999.99"):
            raise InvalidOperation
        if amount != amount.quantize(Decimal("0.01")):
            raise InvalidOperation
    except InvalidOperation:
        return None
    return int(amount * 100)


def _file(upload):
    """(content, content type) or (None, message). The size is judged before the file is read."""
    if upload is None:
        return None, "", PROOF_REQUIRED
    try:
        content, content_type = private_files.check(
            upload, allowed=PROOF_TYPES, max_bytes=C.PROOF_MAX_BYTES
        )
    except private_files.FileRefused as refused:
        message = (
            PROOF_SIZE.format(mb=C.PROOF_MAX_BYTES // (1024 * 1024))
            if refused.reason == private_files.TOO_LARGE else PROOF_TYPE
        )
        return None, "", message
    return content, content_type, ""


def clean(site, data, upload):
    errors = {}
    account = payments.active_accounts(site).filter(public_id=str(data.get("account_id") or "")).first()
    if account is None:
        errors["account_id"] = MSG["account_id"]
    ref = (data.get("reference_number") or "").strip()
    if not 1 <= len(ref) <= 64:
        errors["reference_number"] = MSG["reference_number"]
    amount = _amount_centavos(data.get("amount_sent"))
    if amount is None:
        errors["amount_sent"] = MSG["amount_sent"]
    sender_bank = (data.get("sender_bank") or "").strip()
    if not 1 <= len(sender_bank) <= 60:
        errors["sender_bank"] = MSG["sender_bank"]
    sender_name = _sender_name(data, errors)
    sender_account_number = (data.get("sender_account_number") or "").strip()
    if not 1 <= len(sender_account_number) <= 64:
        errors["sender_account_number"] = MSG["sender_account_number"]
    transferred_at, problem = _transfer_time(data.get("transferred_at"))
    if problem:
        errors["transferred_at"] = MSG[problem]
    content, content_type, file_error = _file(upload)
    if file_error:
        errors["proof"] = file_error
    if errors:
        raise ApiError(
            "invalid_proof", C.MSG_CHECK_FIELDS, status=422,
            fields={key: [message] for key, message in errors.items()},
        )
    return ProofInput(
        account, ref, amount, sender_bank, sender_name, sender_account_number, transferred_at,
        content_type, content,
    )


def write(order, proof, file_key, *, seq, ip=None):
    a = proof.account
    return PackProofSubmission.objects.create(
        site_id=order.site_id, order=order, seq=seq, account_public_id=a.public_id,
        account_kind=a.kind, account_provider_name=a.provider_name, account_name=a.account_name,
        account_number_last4=a.account_number[-4:], reference_number=proof.reference,
        amount_sent_centavos=proof.amount_centavos, sender_bank=proof.sender_bank,
        sender_name=proof.sender_name, sender_account_number=proof.sender_account_number,
        transferred_at=proof.transferred_at, file_key=file_key, content_type=proof.content_type,
        size=len(proof.content), ip_advisory=(ip or "")[:45],
    )
