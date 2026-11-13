"""Add images to products already in the shared catalog.

    manage.py catalog_set_images --images-dir FOLDER                  # dry run: writes nothing
    manage.py catalog_set_images --images-dir FOLDER --apply --operator NAME [--replace]

Reads `<CODE>.jpg|.jpeg|.png|.webp` from the folder (a JPEG, PNG or WebP of 2 MB or less; a bad
file refuses the run). The default is a dry run: each file prints `<CODE>: would set`,
`skip (has image)` or `skip (no such product)`, then a summary. `--apply` needs an active staff
`--operator`, both SANTE_CATALOG_ENABLED and SANTE_SETTINGS_ENABLED on, and the media store
configured. It fills only products with no image unless `--replace`. All images are set in one
transaction (all or none); each product set gets a revision naming the operator. Output never
holds a token or a file key.

See docs/runbooks/catalog-seed.md.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.catalog import media, services
from apps.catalog.management.image_files import read_images


class Command(BaseCommand):
    help = "Set images on existing Santé catalog products (dry run by default)."

    def add_arguments(self, parser):
        parser.add_argument("--images-dir", required=True, help="folder of <CODE>.jpg|png|webp")
        parser.add_argument("--apply", action="store_true", help="write; without it, a dry run")
        parser.add_argument("--operator", default="", help="username of an active staff user")
        parser.add_argument("--replace", action="store_true", help="also replace an existing image")

    def handle(self, *args, **options):
        apply, replace = options["apply"], options["replace"]
        if apply:
            operator = self._refuse_unless_ready(options)
        images = read_images(options["images_dir"])
        try:
            if apply:
                result = services.set_images_as_operator(images, operator=operator, replace=replace)
            else:
                result = services.image_state(images, replace=replace)
        except media.StoreError:
            raise CommandError("An image could not be stored, nothing was written.") from None
        if result is None:
            self.stdout.write("Catalog switch is off: products were not checked.")
            result = [(code, "not checked") for code in sorted(images)]
        for code, outcome in result:
            self.stdout.write(f"{code}: {'would set' if outcome == 'set' and not apply else outcome}")
        done = sum(1 for _, outcome in result if outcome == "set")
        verb = "Set" if apply else "Dry run, would set"
        self.stdout.write(f"{verb} {done}, skipped {len(result) - done}.")
        if not apply:
            self.stdout.write("Nothing was written. To write: --apply --operator NAME.")

    def _refuse_unless_ready(self, options):
        if not services.enabled():
            raise CommandError("The catalog is switched off (SANTE_CATALOG_ENABLED and SANTE_SETTINGS_ENABLED).")
        if not options["operator"]:
            raise CommandError("--apply needs --operator.")
        operator = get_user_model().objects.filter(
            username=options["operator"], is_staff=True, is_active=True
        ).first()
        if operator is None:
            raise CommandError("--operator is not an active staff user.")
        if not media.configured():
            raise CommandError("--apply needs the media store: it is not configured.")
        return operator
