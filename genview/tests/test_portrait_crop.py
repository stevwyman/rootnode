import io

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image, ImageDraw

from genview.models import Individual, MediaObject, Tree, TreeMembership
from genview.utils import create_portrait_from_crop, crop_image_by_percent


def _make_group_photo_bytes() -> bytes:
    img = Image.new("RGB", (400, 200), color="white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 199, 199], fill=(220, 40, 40))
    draw.rectangle([200, 0, 399, 199], fill=(40, 80, 220))
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


@override_settings(MEDIA_ROOT="/tmp/rootnode_test_media")
class PortraitCropUtilsTests(TestCase):
    def setUp(self):
        self.tree = Tree.objects.create(name="Portrait Tree")
        self.person = Individual.objects.create(
            gedcom_tree=self.tree,
            given_name="Anna",
            surname="Muster",
        )
        photo_bytes = _make_group_photo_bytes()
        self.source = MediaObject.objects.create(
            gedcom_tree=self.tree,
            title="Gruppenfoto",
            category=MediaObject.Category.PHOTO,
            file=SimpleUploadedFile(
                "group.jpg", photo_bytes, content_type="image/jpeg"
            ),
        )

    def test_crop_image_by_percent_left_half(self):
        cropped = crop_image_by_percent(
            self.source.file.path,
            x_percent=0,
            y_percent=0,
            width_percent=50,
            height_percent=100,
            padding_percent=0,
        )
        self.assertEqual(cropped.size[0], 200)
        self.assertEqual(cropped.size[1], 200)
        r, g, b = cropped.getpixel((10, 10))
        self.assertGreater(r, 150)
        self.assertLess(b, 100)

    def test_create_portrait_sets_flag_and_clears_old(self):
        old_portrait = MediaObject.objects.create(
            gedcom_tree=self.tree,
            title="Alt",
            category=MediaObject.Category.PHOTO,
            is_portrait=True,
            file=SimpleUploadedFile("old.jpg", b"jpeg", content_type="image/jpeg"),
        )
        old_portrait.individuals.add(self.person)

        portrait = create_portrait_from_crop(
            self.source,
            self.person,
            x_percent=50,
            y_percent=0,
            width_percent=50,
            height_percent=100,
        )

        self.assertTrue(portrait.is_portrait)
        self.assertIn(self.person, portrait.individuals.all())
        self.assertTrue(portrait.file.name.endswith(".jpg"))

        old_portrait.refresh_from_db()
        self.assertFalse(old_portrait.is_portrait)

        self.assertTrue(portrait.thumb_mini or portrait.thumb_small)


class PortraitCropViewTests(TestCase):
    def setUp(self):
        self.client = __import__("django.test", fromlist=["Client"]).Client()
        self.user = User.objects.create_user(username="editor", password="password")
        self.tree = Tree.objects.create(name="Portrait View Tree")
        TreeMembership.objects.create(
            user=self.user, gedcom_tree=self.tree, role="EDITOR"
        )
        self.person = Individual.objects.create(
            gedcom_tree=self.tree,
            given_name="Bernd",
            surname="Beispiel",
        )
        photo_bytes = _make_group_photo_bytes()
        self.source = MediaObject.objects.create(
            gedcom_tree=self.tree,
            title="Gruppenfoto",
            category=MediaObject.Category.PHOTO,
            file=SimpleUploadedFile(
                "group.jpg", photo_bytes, content_type="image/jpeg"
            ),
        )
        self.url = reverse(
            "genview:media-detail",
            kwargs={"tree_id": self.tree.id, "pk": self.source.pk},
        )

    def test_create_portrait_via_post(self):
        self.client.login(username="editor", password="password")
        response = self.client.post(
            self.url,
            {
                "create_portrait": "1",
                "individual_id": self.person.pk,
                "x_percent": "0",
                "y_percent": "0",
                "width_percent": "50",
                "height_percent": "100",
            },
        )
        self.assertEqual(response.status_code, 302)
        portrait = MediaObject.objects.filter(
            gedcom_tree=self.tree, is_portrait=True
        ).exclude(pk=self.source.pk)
        self.assertEqual(portrait.count(), 1)
        new_media = portrait.first()
        self.assertIn(self.person, new_media.individuals.all())
        self.assertEqual(response.url, reverse(
            "genview:media-detail",
            kwargs={"tree_id": self.tree.id, "pk": new_media.pk},
        ))
