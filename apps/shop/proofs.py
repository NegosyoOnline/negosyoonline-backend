"""The payment proof sent with a shop order (the pack proof, reduced to what the shop asks:
the account paid to, the reference number, the amount sent and the file). The file is a JPEG,
PNG, WebP or PDF, judged by its first bytes, never by its name or the browser's type. The amount
is advisory: the Subscriber compares it with the total by eye."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from apps.common import private_files
from apps.common.errors import ApiError
from apps.payments import services as payments
from apps.shop import constants as C
from apps.shop.models import ShopProofSubmission

MSG = {
    "account_id": "Choose the account you paid to.",
    "reference_number": "Enter the reference number.",
    "amount_sent": "Enter the amount you sent.",
}
PROOF_REQUIRED = "Attach the file."
PROOF_TYPE = "Upload a JPEG, PNG, WebP or PDF file."
PROOF_SIZE = "The file is too large. The limit is {mb} MB."
PROOF_TYPES = (*private_files.IMAGE_TYPES, "application/pdf")


@dataclass(frozen=True)
class ProofInput:
    account: object
    reference: str
    amount_centavos: int
    content_type: str
    content: bytes


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
    """(content, content type, error). The size is judged before the file is read."""
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
    """The proof fields as a ProofInput, or 422 `invalid_proof` naming each bad field."""
    errors = {}
    account = payments.active_accounts(site).filter(public_id=str(data.get("account_id") or "")).first()
    if account is None:
        errors["account_id"] = MSG["account_id"]
    ref = (data.get("reference_number") or "").strip()
    if not 1 <= len(ref) <= C.REFERENCE_MAX:
        errors["reference_number"] = MSG["reference_number"]
    amount = _amount_centavos(data.get("amount_sent"))
    if amount is None:
        errors["amount_sent"] = MSG["amount_sent"]
    content, content_type, file_error = _file(upload)
    if file_error:
        errors["proof"] = file_error
    if errors:
        raise ApiError(
            "invalid_proof", C.MSG_CHECK_FIELDS, status=422,
            fields={key: [message] for key, message in errors.items()},
        )
    return ProofInput(account, ref, amount, content_type, content)


def write(order, proof, file_key, *, ip=None):
    a = proof.account
    return ShopProofSubmission.objects.create(
        order=order, account_public_id=a.public_id, account_kind=a.kind,
        account_provider_name=a.provider_name, account_name=a.account_name,
        account_number_last4=a.account_number[-4:], reference_number=proof.reference,
        amount_sent_centavos=proof.amount_centavos, file_key=file_key,
        content_type=proof.content_type, size=len(proof.content), ip_advisory=(ip or "")[:45],
    )
