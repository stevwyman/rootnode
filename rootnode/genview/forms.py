# genview/forms.py
from django import forms
from django.forms import ModelForm, CheckboxSelectMultiple, DateInput
from .models import Individual, Family, ChildFamilyLink, Event, MediaObject, Source


# ----------------------------------------------------------------------
#  IndividualForm – für Person‑Datensatz
# ----------------------------------------------------------------------
class IndividualForm(ModelForm):
    """
    Formular für das Bearbeiten einer Person inkl. Geburts‑/Sterbedatum.
    Die Felder `birth_date` und `death_date` sind *virtuell* – sie werden im
    `save()`‑Methoden‑Override auf die zugehörigen Event‑Objekte geschrieben.
    """
    # --------------------------------------------------------------
    # Virtuelle Felder
    # --------------------------------------------------------------
    birth_date_raw = forms.CharField(
        required=False,
        label="Geburts‑Datum (Roh‑String, z. B. 'ABT 1900')",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "z. B. 12 JAN 1885"}),
    )
    birth_date_parsed = forms.DateField(
        required=False,
        label="Geburts‑Datum (geparst)",
        widget=DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    death_date_raw = forms.CharField(
        required=False,
        label="Sterbe‑Datum (Roh‑String)",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "z. B. 5 MAY 1972"}),
    )
    death_date_parsed = forms.DateField(
        required=False,
        label="Sterbe‑Datum (geparst)",
        widget=DateInput(attrs={"class": "form-control", "type": "date"}),
    )

    class Meta:
        model = Individual
        fields = [
            "gedcom_id",
            "given_name",
            "surname",
            "name_prefix",
            "name_suffix",
            "sex",
            "notes",
            "sources",
        ]
        widgets = {
            "gedcom_id": forms.TextInput(attrs={"class": "form-control"}),
            "given_name": forms.TextInput(attrs={"class": "form-control"}),
            "surname": forms.TextInput(attrs={"class": "form-control"}),
            "name_prefix": forms.TextInput(attrs={"class": "form-control"}),
            "name_suffix": forms.TextInput(attrs={"class": "form-control"}),
            "sex": forms.Select(attrs={"class": "form-select"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "sources": CheckboxSelectMultiple(),
        }

    # --------------------------------------------------------------
    # Initial‑Daten für die virtuellen Felder befüllen
    # --------------------------------------------------------------
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Wenn ein bereits existierendes Individual bearbeitet wird,
        # laden wir die zugehörigen BIRT‑/DEAT‑Events (falls vorhanden).
        if self.instance.pk:
            birth_evt = self.instance.birth_event
            death_evt = self.instance.death_event

            if birth_evt:
                self.fields["birth_date_raw"].initial = birth_evt.raw_date
                self.fields["birth_date_parsed"].initial = birth_evt.parsed_date

            if death_evt:
                self.fields["death_date_raw"].initial = death_evt.raw_date
                self.fields["death_date_parsed"].initial = death_evt.parsed_date

    # --------------------------------------------------------------
    # Hilfsmethode: Event holen (oder neu anlegen)
    # --------------------------------------------------------------
    @staticmethod
    def _get_or_create_event(person: Individual, ev_type: str) -> Event:
        """
        Liefert das vorhandene Event vom Typ ``ev_type`` (BIRT/DEAT) oder legt
        ein neues an, wenn keins existiert.
        """
        event = person.events.filter(event_type=ev_type).first()
        if not event:
            event = Event.objects.create(
                individual=person,
                event_type=ev_type,
            )
        return event

    # --------------------------------------------------------------
    # Überschreiben von save() – schreibt die virtuellen Felder in die
    # zugehörigen Event‑Instanzen.
    # --------------------------------------------------------------
    def save(self, commit=True):
        # 1️⃣ zuerst das Individual selbst speichern
        individual = super().save(commit=False)

        if commit:
            individual.save()
            self.save_m2m()   # speichert ManyToMany‑Beziehungen (Sources)

        # 2️⃣ jetzt die Events für Geburt und Tod updaten
        #    (nur wenn mindestens ein Feld ausgefüllt ist)
        # ----------------------------------------------------------
        #   BIRTH
        # ----------------------------------------------------------
        birth_raw = self.cleaned_data.get("birth_date_raw")
        birth_parsed = self.cleaned_data.get("birth_date_parsed")
        if birth_raw or birth_parsed:
            birth_evt = self._get_or_create_event(individual, Event.EventType.BIRTH)
            birth_evt.raw_date = birth_raw or ""
            birth_evt.parsed_date = birth_parsed
            birth_evt.save()
        else:
            # wenn beide Felder leer sind, löschen wir ggf. das Event
            Event.objects.filter(
                individual=individual,
                event_type=Event.EventType.BIRTH,
            ).delete()

        # ----------------------------------------------------------
        #   DEATH
        # ----------------------------------------------------------
        death_raw = self.cleaned_data.get("death_date_raw")
        death_parsed = self.cleaned_data.get("death_date_parsed")
        if death_raw or death_parsed:
            death_evt = self._get_or_create_event(individual, Event.EventType.DEATH)
            death_evt.raw_date = death_raw or ""
            death_evt.parsed_date = death_parsed
            death_evt.save()
        else:
            Event.objects.filter(
                individual=individual,
                event_type=Event.EventType.DEATH,
            ).delete()

        return individual


class IndividualSearchForm(forms.Form):
    """
    Einfaches Suchformular für Personen.
    Das Feld `q` wird per GET übermittelt (kein CSRF nötig).
    """
    q = forms.CharField(
        required=False,
        label="Suche",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Name, GEDCOM‑ID, Geschlecht …",
                "autocomplete": "off",
            }
        ),
    )

# ----------------------------------------------------------------------
#  FamilyForm – für Familien‑Datensatz
# ----------------------------------------------------------------------
class FamilyForm(ModelForm):
    """
    Standard‑Formular für Familie + zwei zusätzliche Felder für das
    Heirats‑Event (Roh‑String, geparstes Datum und Ort).
    """
    # ---------- Virtuelle Felder ----------
    marriage_raw_date = forms.CharField(
        required=False,
        label="Heirats‑Datum (Roh‑String, z. B. '15 JUN 1890')",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "z. B. 15 JUN 1890"}),
    )
    marriage_parsed_date = forms.DateField(
        required=False,
        label="Heirats‑Datum (geparst)",
        widget=DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    marriage_place = forms.CharField(
        required=False,
        label="Heirats‑Ort",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "z. B. Berlin, Deutschland"}),
    )

    class Meta:
        model = Family
        fields = [
            "gedcom_id",
            "husband",
            "wife",
            "parent",          # MPTT‑Hierarchie (optional)
            "notes",
            "sources",
        ]
        widgets = {
            "gedcom_id": forms.TextInput(attrs={"class": "form-control"}),
            "husband": forms.Select(attrs={"class": "form-select"}),
            "wife": forms.Select(attrs={"class": "form-select"}),
            "parent": forms.Select(attrs={"class": "form-select"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "sources": CheckboxSelectMultiple(),
        }

    # --------------------------------------------------------------
    #  Initial‑Daten für die virtuellen Felder befüllen
    # --------------------------------------------------------------
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance.pk:
            ev = self.instance.marriage_event
            if ev:
                self.fields["marriage_raw_date"].initial = ev.raw_date
                self.fields["marriage_parsed_date"].initial = ev.parsed_date
                self.fields["marriage_place"].initial = ev.place

    # --------------------------------------------------------------
    #  Hilfsmethode: MARR‑Event holen oder neu anlegen
    # --------------------------------------------------------------
    @staticmethod
    def _get_or_create_marriage_event(family: Family) -> Event:
        ev = family.events.filter(event_type=Event.EventType.MARRIAGE).first()
        if not ev:
            ev = Event.objects.create(
                family=family,
                event_type=Event.EventType.MARRIAGE,
            )
        return ev

    # --------------------------------------------------------------
    #  Override save() – speichert die virtuellen Felder in das Event
    # --------------------------------------------------------------
    def save(self, commit=True):
        family = super().save(commit=False)          # speichert Family‑Stammdaten

        if commit:
            family.save()
            self.save_m2m()                          # Sources‑ManyToMany

        # ----- Heirats‑Daten schreiben / ggf. Event löschen ----------
        raw = self.cleaned_data.get("marriage_raw_date")
        parsed = self.cleaned_data.get("marriage_parsed_date")
        place = self.cleaned_data.get("marriage_place")

        if raw or parsed or place:
            ev = self._get_or_create_marriage_event(family)
            ev.raw_date = raw or ""
            ev.parsed_date = parsed
            ev.place = place or ""
            ev.save()
        else:
            # wenn alle Felder leer sind, entfernen wir ggf. das Event
            Event.objects.filter(
                family=family,
                event_type=Event.EventType.MARRIAGE,
            ).delete()

        return family


# ----------------------------------------------------------------------
#  ChildFamilyLinkForm – Kind‑zu‑Familie‑Verknüpfung
# ----------------------------------------------------------------------
class ChildFamilyLinkForm(ModelForm):
    class Meta:
        model = ChildFamilyLink
        fields = ["child", "family", "relationship_type"]
        widgets = {
            "child": forms.Select(attrs={"class": "form-select"}),
            "family": forms.Select(attrs={"class": "form-select"}),
            "relationship_type": forms.Select(attrs={"class": "form-select"}),
        }
        

# ----------------------------------------------------------------------
#  MediaObjectForm – für Bilder
# ----------------------------------------------------------------------
class MediaObjectForm(ModelForm):
    """
    Formular zum Hochladen eines Bildes (oder anderer Medien) und zur
    Zuordnung zu einer oder mehreren Personen.
    """
    class Meta:
        model = MediaObject
        fields = [
            "gedcom_id",      # optional
            "title",
            "file",           # ImageField / FileField
            "description",
            "individuals",    # ManyToMany → Personen / Das versteckte Feld wird in __init__ hinzugefügt
            "sources",
            "is_portrait",
        ]
        widgets = {
            "gedcom_id": forms.TextInput(attrs={"class": "form-control"}),
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "file": forms.ClearableFileInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "individuals": CheckboxSelectMultiple(),
            "sources": CheckboxSelectMultiple(),
            "is_portrait": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, person=None, **kwargs):
        """
        `person` ist optional – wenn sie übergeben wird, erzeugen wir ein
        Hidden‑Field, das automatisch ausgefüllt ist.
        """
        super().__init__(*args, **kwargs)

        # Wir brauchen das Hidden‑Field *nur*, wenn eine Person vorgegeben ist.
        if person is not None:
            # Das Feld existiert nur intern – nicht im Model, sondern als
            # Hilfs‑Attribute, das wir später im `save()` auswerten.
            self.person_instance = person   # Merken für späteres save()
            self.fields["person_hidden"] = forms.IntegerField(
                initial=person.pk,
                widget=forms.HiddenInput(),
                required=False,
            )
        else:
            # Wenn kein `person` angegeben ist, erlauben wir die manuelle Auswahl.
            self.fields["individuals"] = forms.ModelMultipleChoiceField(
                queryset=Individual.objects.all(),
                required=False,
                widget=forms.CheckboxSelectMultiple(),
                label="Personen (optional)",
            )

    # ------------------------------------------------------------------
    # Überschreiben von save() – speichert das MediaObject und verbindet
    # automatisch die übergebene Person (falls vorhanden).
    # ------------------------------------------------------------------
    def save(self, commit=True):
        # 1️⃣ erst das Objekt speichern (damit wir eine PK haben)
        media = super().save(commit=False)

        # 2️⃣ Wenn das Bild als Portrait markiert wurde,
        #    alle anderen Bilder der beteiligten Personen zurücksetzen.
        if media.is_portrait:
            # `individuals` ist ein M2M‑Manager – wir iterieren über alle Personen,
            # für die dieses Bild gespeichert wird.
            for person in media.individuals.all():
                MediaObject.objects.filter(
                    individuals=person,
                    is_portrait=True
                ).exclude(pk=media.pk).update(is_portrait=False)

        if commit:
            media.save()
            self.save_m2m()   # speichert ggf. Sources

        # ---- automatische Zuordnung, falls wir über die URL kamen ----
        if hasattr(self, "person_instance"):
            media.individuals.add(self.person_instance)

        # ---- falls das Formular *kein* person‑Argument hat (fallback) ----
        elif "individuals" in self.cleaned_data:
            for ind in self.cleaned_data["individuals"]:
                media.individuals.add(ind)

        return media
