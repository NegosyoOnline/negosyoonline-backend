"""Reading product image files from a folder, shared by catalog_seed_sante and
catalog_set_images. One rule for both: a JPEG, PNG or WebP of 2 MB or less, its type taken from
the bytes."""
from pathlib import Path

from django.core.management.base import CommandError

from apps.catalog import media
from apps.common import private_files

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


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


def read_images(folder, codes=None):
    """{CODE: (bytes, content type)} for the files of `folder` named `<CODE>.jpg|.jpeg|.png|.webp`.
    With `codes`, only files named by one of them are read and the rest are ignored; without it,
    every file with an image extension is. A file read that is not an allowed image, or two files
    for one code, refuses the run."""
    found = {}
    directory = Path(folder)
    if not directory.is_dir():
        raise CommandError("--images-dir is not a folder.")
    for path in sorted(directory.iterdir()):
        code = path.stem.upper()
        if path.suffix.lower() not in IMAGE_EXTENSIONS or not path.is_file():
            continue
        if codes is not None and code not in codes:
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
