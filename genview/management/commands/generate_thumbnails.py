from django.core.management.base import BaseCommand

from genview.models import MediaObject
from genview.utils import generate_thumbnail_for_instance


class Command(BaseCommand):
    help = "Generate missing (or regenerate all) MediaObject thumbnails."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Regenerate thumbnails even when thumb_* fields are already set.",
        )
        parser.add_argument(
            "--tree-id",
            type=int,
            default=None,
            help="Limit to a single tree id.",
        )

    def handle(self, *args, **options):
        qs = MediaObject.objects.exclude(file="").exclude(file__isnull=True).order_by("pk")
        if options["tree_id"] is not None:
            qs = qs.filter(gedcom_tree_id=options["tree_id"])

        force = options["all"]
        # Materialize IDs first so updates during the run cannot affect iteration.
        ids = list(qs.values_list("pk", flat=True))
        total = len(ids)
        ok = failed = skipped = 0

        self.stdout.write(f"Processing {total} media object(s)…")

        for pk in ids:
            media = MediaObject.objects.get(pk=pk)
            for size in ("mini", "small"):
                field = getattr(media, f"thumb_{size}")
                if not force and field and field.name:
                    skipped += 1
                    continue
                try:
                    generate_thumbnail_for_instance(media, size)
                    ok += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  #{media.pk} {size}: {getattr(media, f'thumb_{size}').name}"
                        )
                    )
                except Exception as exc:
                    failed += 1
                    self.stderr.write(
                        self.style.ERROR(f"  #{media.pk} {size}: {exc}")
                    )

        self.stdout.write(
            self.style.NOTICE(f"Done. created={ok} skipped={skipped} failed={failed}")
        )
