"""Load the approved Santé product list into the shared catalog.

    manage.py catalog_seed_sante                          # dry run: reads, writes nothing
    manage.py catalog_seed_sante --apply --operator NAME --expect-sha256 HASH \
        [--images-dir FOLDER] [--file PATH]

The default is a dry run: it prints each code as `create`, `skip (same)` or
`skip (differs: fields)` and the SHA-256 of the seed file. Writing needs `--apply`, an active
staff `--operator`, `--expect-sha256` equal to the file's hash (so what runs is what was
reviewed), and both SANTE_CATALOG_ENABLED and SANTE_SETTINGS_ENABLED on. All creates are one
transaction. An existing code is never changed. `--images-dir` stores `<CODE>.jpg|.png|.webp`
files of that folder as the images of the products created, and needs the media store (see
apps/catalog/media.py). Output never holds a token or a file key.

See docs/runbooks/catalog-seed.md.
"""
import hashlib
import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.catalog import media, services
from apps.common import private_files
from apps.common.errors import ApiError

DEFAULT_FILE = Path(__file__).resolve().parents[2] / "seed" / "sante_catalog_2026-11.json"
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


def _load(path):
    try:
        raw = Path(path).read_bytes()
        document = json.loads(raw.decode("utf-8"))
        rows = document["products"]
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        raise CommandError("The seed file could not be read as a product list.") from None
    return hashlib.sha256(raw).hexdigest(), rows


def _error_text(error):
    fields = "; ".join(f"{name}: {' '.join(msgs)}" for name, msgs in (error.fields or {}).items())
    return f"{error.message} {fields}".strip()


def _images(folder, codes):
    """{code: (bytes, content type)} for the files of `folder` named by a code in the seed.
    Other files are ignored. A named file that is not an allowed image refuses the run."""
    found = {}
    directory = Path(folder)
    if not directory.is_dir():
        raise CommandError("--images-dir is not a folder.")
    for path in sorted(directory.iterdir()):
        code = path.stem.upper()
        if path.suffix.lower() not in IMAGE_EXTENSIONS or code not in codes or not path.is_file():
            continue
        if code in found:
            raise CommandError(f"More than one image file is named for {code}.")
        try:
            with open(path, "rb") as handle:
                upload = _Upload(handle.read(media.MAX_BYTES + 1))
            found[code] = private_files.check(
                upload, allowed=private_files.IMAGE_TYPES, max_bytes=media.MAX_BYTES
            )
        except (OSError, private_files.FileRefused):
            raise CommandError(f"The image for {code} is not a JPEG, PNG or WebP of 2 MB or less.") from None
    return found


class _Upload:
    """Just enough of an uploaded file for private_files.check: the browser's declared type
    is taken from the bytes, since a folder has none."""

    def __init__(self, content):
        self._content = content
        self.size = len(content)
        self.content_type = private_files.sniff(content[:16])
        self._at = 0

    def seek(self, at):
        self._at = at

    def read(self, count):
        chunk = self._content[self._at:self._at + count]
        self._at += len(chunk)
        return chunk


def _describe(outcome):
    if outcome == "create":
        return "create"
    if outcome == "same":
        return "skip (same)"
    return f"skip (differs: {', '.join(outcome)})"


class Command(BaseCommand):
    help = "Load the approved Santé product list into the shared catalog (dry run by default)."

    def add_arguments(self, parser):
        parser.add_argument("--file", default=str(DEFAULT_FILE), help="the seed file")
        parser.add_argument("--apply", action="store_true", help="write; without it, a dry run")
        parser.add_argument("--operator", default="", help="username of an active staff user")
        parser.add_argument("--expect-sha256", default="", help="the approved hash of the file")
        parser.add_argument("--images-dir", default="", help="folder of <CODE>.jpg|png|webp")

    def handle(self, *args, **options):
        digest, rows = _load(options["file"])
        self.stdout.write(f"Seed file sha256: {digest}")
        apply = options["apply"]
        if apply:
            operator = self._refuse_unless_ready(options, digest)
        images = {}
        if options["images_dir"]:
            if apply and not media.configured():
                raise CommandError("--images-dir needs the media store: it is not configured.")
            images = _images(options["images_dir"], {row.get("code") for row in rows})
        try:
            if apply:
                result = services.seed_products(
                    rows, operator=operator, file_sha256=digest, images=images
                )
            else:
                result = services.seed_state(rows)
        except ApiError as error:
            raise CommandError(f"A seed row is refused, nothing was written. {_error_text(error)}") from None
        except media.StoreError:
            raise CommandError("An image could not be stored, nothing was written.") from None
        if not apply and not services.enabled():
            self.stdout.write("Catalog switch is off: existing products were not checked.")
        for code, outcome in result:
            note = " +image" if outcome == "create" and code in images else ""
            self.stdout.write(f"{code}: {_describe(outcome)}{note}")
        created = sum(1 for _, outcome in result if outcome == "create")
        skipped = len(result) - created
        verb = "Created" if apply else "Dry run, would create"
        self.stdout.write(f"{verb} {created}, skipped {skipped}.")
        if not apply:
            self.stdout.write("Nothing was written. To write: --apply --operator NAME --expect-sha256 HASH.")

    def _refuse_unless_ready(self, options, digest):
        if not services.enabled():
            raise CommandError("The catalog is switched off (SANTE_CATALOG_ENABLED and SANTE_SETTINGS_ENABLED).")
        if not options["operator"]:
            raise CommandError("--apply needs --operator.")
        if not options["expect_sha256"]:
            raise CommandError("--apply needs --expect-sha256.")
        if options["expect_sha256"].strip().lower() != digest:
            raise CommandError("--expect-sha256 does not match the seed file. Nothing was written.")
        operator = get_user_model().objects.filter(
            username=options["operator"], is_staff=True, is_active=True
        ).first()
        if operator is None:
            raise CommandError("--operator is not an active staff user.")
        return operator
