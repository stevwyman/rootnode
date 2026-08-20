# genview/forms.py
from django import forms
from django.forms import ModelForm, DateInput
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _
from .models import Individual, Family, ChildFamilyLink, Event, EventType, MediaObject, Source, Place, TreeMembership


# ----------------------------------------------------------------------
#  IndividualForm – für Person-Datensatz
# ----------------------------------------------------------------------
class IndividualForm(ModelForm):
    """
    Formular für das Bearbeiten einer Person inkl. Geburts-/Sterbedatum.
    Die Felder `birth_date` und `death_date` sind *virtuell* – sie werden im
    `save()`-Methoden-Override auf die zugehörigen Event-Objekte geschrieben.
    """

    # --------------------------------------------------------------
    # Virtuelle Felder
    # --------------------------------------------------------------
    birth_date_raw = forms.CharField(
        required=False,
        label=_("Geburts-Datum (Roh-String, z. B. 'ABT 1900')"),
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "z. B. 12 JAN 1885"}
        ),
    )
    birth_date_parsed = forms.DateField(
        required=False,
        label=_("Geburts-Datum (geparst)"),
        widget=DateInput(format='%Y-%m-%d',attrs={"class": "form-control", "type": "date"}),
    )
    death_date_raw = forms.CharField(
        required=False,
        label=_("Sterbe-Datum (Roh-String)"),
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "z. B. 5 MAY 1972"}
        ),
    )
    death_date_parsed = forms.DateField(
        required=False,
        label=_("Sterbe-Datum (geparst)"),
        widget=DateInput(format='%Y-%m-%d',attrs={"class": "form-control", "type": "date"}),
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
            "sources": forms.SelectMultiple(attrs={'class': 'form-control select2-sources'}),
        }

    # --------------------------------------------------------------
    # Initial-Daten für die virtuellen Felder befüllen
    # --------------------------------------------------------------
    def __init__(self, *args, tree_id=None, **kwargs):
        super().__init__(*args, **kwargs)

        current_tree_id = tree_id
        if not current_tree_id and self.instance and self.instance.pk:
            current_tree_id = self.instance.gedcom_tree_id
        if current_tree_id and "sources" in self.fields:
            self.fields["sources"].queryset = Source.objects.filter(gedcom_tree_id=current_tree_id)
        elif "sources" in self.fields:
            self.fields["sources"].queryset = Source.objects.none()

        # 1. Ist es eine komplett neue Person? 
        # (Bei existierenden Personen zum Bearbeiten ist der primary key (pk) bereits gesetzt)
        is_new_person = self.instance.pk is None
        
        # 2. Welches Geschlecht steht im Startwert?
        forced_sex = self.initial.get('sex')

        # Smart Workflow: Nur sperren, wenn es eine NEUE Person ist 
        # UND das Geschlecht explizit auf Männlich ('M') oder Weiblich ('F') forciert wurde.
        if is_new_person and forced_sex in ['M', 'F']:
            self.fields['sex'].disabled = True
            self.fields['sex'].help_text = "Das Geschlecht wurde durch die gewählte Rolle automatisch festgelegt."

        # Wenn ein bereits existierendes Individual bearbeitet wird,
        # laden wir die zugehörigen BIRT-/DEAT-Events (falls vorhanden).
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
    def _get_or_create_event(person: Individual, tag: str) -> Event:
        """
        Liefert das vorhandene Event mit GEDCOM-Tag ``tag`` (BIRT/DEAT)
        oder legt ein neues an, wenn keins existiert.
        """
        event = person.events.filter(event_type__tag=tag).first()
        if not event:
            event_type, _ = EventType.objects.get_or_create(
                tag=tag,
                defaults={
                    "name": "Birth" if tag == "BIRT" else "Death",
                    "category": EventType.Category.INDIVIDUAL,
                },
            )
            event = Event.objects.create(
                individual=person,
                event_type=event_type,
                gedcom_tree=person.gedcom_tree,
            )
        return event

    # --------------------------------------------------------------
    # Überschreiben von save() – schreibt die virtuellen Felder in die
    # zugehörigen Event-Instanzen.
    # --------------------------------------------------------------
    def save(self, commit=True):
        # 1️⃣ zuerst das Individual selbst speichern
        individual = super().save(commit=False)

        if commit:
            individual.save()
            self.save_m2m()  # speichert ManyToMany-Beziehungen (Sources)

        # 2️⃣ jetzt die Events für Geburt und Tod updaten
        #    (nur wenn mindestens ein Feld ausgefüllt ist)
        # ----------------------------------------------------------
        #   BIRTH
        # ----------------------------------------------------------
        birth_raw = self.cleaned_data.get("birth_date_raw")
        birth_parsed = self.cleaned_data.get("birth_date_parsed")
        if birth_raw or birth_parsed:
            birth_evt = self._get_or_create_event(individual, "BIRT")
            birth_evt.raw_date = birth_raw or ""
            birth_evt.parsed_date = birth_parsed
            birth_evt.save()
        else:
            # wenn beide Felder leer sind, löschen wir ggf. das Event
            Event.objects.filter(
                individual=individual,
                event_type__tag="BIRT",
            ).delete()

        # ----------------------------------------------------------
        #   DEATH
        # ----------------------------------------------------------
        death_raw = self.cleaned_data.get("death_date_raw")
        death_parsed = self.cleaned_data.get("death_date_parsed")
        if death_raw or death_parsed:
            death_evt = self._get_or_create_event(individual, "DEAT")
            death_evt.raw_date = death_raw or ""
            death_evt.parsed_date = death_parsed
            death_evt.save()
        else:
            Event.objects.filter(
                individual=individual,
                event_type__tag="DEAT",
            ).delete()

        return individual


class IndividualSearchForm(forms.Form):
    """
    Einfaches Suchformular für Personen.
    Das Feld `q` wird per GET übermittelt (kein CSRF nötig).
    """

    q = forms.CharField(
        required=False,
        label=_("Suche"),
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Name, GEDCOM-ID, Geschlecht …",
                "autocomplete": "off",
            }
        ),
    )


# ----------------------------------------------------------------------
#  FamilyForm – für Familien-Datensatz
# ----------------------------------------------------------------------
class FamilyForm(ModelForm):
    """
    Standard-Formular für Familie + zwei zusätzliche Felder für das
    Heirats-Event (Roh-String, geparstes Datum und Ort).
    """

    # ---------- Virtuelle Felder ----------
    marriage_raw_date = forms.CharField(
        required=False,
        label=_("Heirats-Datum (Roh-String, z. B. '15 JUN 1890')"),
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "z. B. 15 JUN 1890"}
        ),
    )
    marriage_parsed_date = forms.DateField(
        required=False,
        label=_("Heirats-Datum (geparst)"),
        widget=forms.DateInput(
                format='%Y-%m-%d',  # <-- DAS HIER IST DAS GEHEIMNIS!
                attrs={
                    'class': 'form-control', 
                    'type': 'date'  # Öffnet den nativen Browser-Kalender
                }
            ),
    )
    marriage_place = forms.ModelChoiceField(
        queryset=Place.objects.none(), 
        required=False,
        label=_("Heiratsort"),
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = Family
        fields = [
            "gedcom_id",
            "husband",
            "wife",
            "parent",  # MPTT-Hierarchie (optional)
            "notes",
            "sources",
        ]
        widgets = {
            "gedcom_id": forms.TextInput(attrs={"class": "form-control"}),
            "husband": forms.Select(attrs={"class": "form-select"}),
            "wife": forms.Select(attrs={"class": "form-select"}),
            "parent": forms.Select(attrs={"class": "form-select"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "sources": forms.SelectMultiple(attrs={'class': 'form-control select2-sources'}),
        }

    # --------------------------------------------------------------
    #  Initial-Daten für die virtuellen Felder befüllen
    # --------------------------------------------------------------
    def __init__(self, *args, tree_id=None, **kwargs):
        super().__init__(*args, **kwargs)

        # 1. Versuche die tree_id aus den View-Argumenten zu holen
        current_tree_id = tree_id
        
        # 2. Fallback: Wenn wir bearbeiten (Update), hat die Instanz selbst eine tree_id
        if not current_tree_id and self.instance and self.instance.pk:
            current_tree_id = self.instance.gedcom_tree_id

        # 3. Dropdowns auf den aktuellen Baum beschränken (kein Cross-Tree-POST)
        if current_tree_id:
            self.fields["husband"].queryset = Individual.objects.filter(gedcom_tree_id=current_tree_id)
            self.fields["wife"].queryset = Individual.objects.filter(gedcom_tree_id=current_tree_id)
            if "parent" in self.fields:
                self.fields["parent"].queryset = Family.objects.filter(gedcom_tree_id=current_tree_id)
            if "sources" in self.fields:
                self.fields["sources"].queryset = Source.objects.filter(gedcom_tree_id=current_tree_id)
            if "marriage_place" in self.fields:
                self.fields["marriage_place"].queryset = Place.objects.filter(gedcom_tree_id=current_tree_id)
        else:
            self.fields["husband"].queryset = Individual.objects.none()
            self.fields["wife"].queryset = Individual.objects.none()
            if "parent" in self.fields:
                self.fields["parent"].queryset = Family.objects.none()
            if "sources" in self.fields:
                self.fields["sources"].queryset = Source.objects.none()
            if "marriage_place" in self.fields:
                self.fields["marriage_place"].queryset = Place.objects.none()

        if self.instance.pk:
            ev = self.instance.marriage_event
            if ev:
                self.fields["marriage_raw_date"].initial = ev.raw_date
                self.fields["marriage_parsed_date"].initial = ev.parsed_date
                self.fields["marriage_place"].initial = ev.place

    # --------------------------------------------------------------
    #  Hilfsmethode: MARR-Event holen oder neu anlegen
    # --------------------------------------------------------------
    @staticmethod
    def _get_or_create_marriage_event(family: Family) -> Event:
        ev = family.events.filter(event_type__tag="MARR").first()
        if not ev:
            event_type, _ = EventType.objects.get_or_create(
                tag="MARR",
                defaults={
                    "name": "Marriage",
                    "category": EventType.Category.FAMILY,
                },
            )
            ev = Event.objects.create(
                family=family,
                event_type=event_type,
                gedcom_tree=family.gedcom_tree,
            )
        return ev

    # --------------------------------------------------------------
    #  Override save() – speichert die virtuellen Felder in das Event
    # --------------------------------------------------------------
    def save(self, commit=True):
        family = super().save(commit=False)  # speichert Family-Stammdaten

        if commit:
            family.save()
            self.save_m2m()  # Sources-ManyToMany

        # ----- Heirats-Daten schreiben / ggf. Event löschen ----------
        raw = self.cleaned_data.get("marriage_raw_date")
        parsed = self.cleaned_data.get("marriage_parsed_date")
        place = self.cleaned_data.get("marriage_place")

        if raw or parsed or place:
            ev = self._get_or_create_marriage_event(family)
            ev.raw_date = raw or ""
            ev.parsed_date = parsed
            ev.place = place  # FK — None clears; never assign ""
            ev.save()
        else:
            # wenn alle Felder leer sind, entfernen wir ggf. das Event
            Event.objects.filter(
                family=family,
                event_type__tag='MARR',
            ).delete()

        return family


# ----------------------------------------------------------------------
#  ChildFamilyLinkForm – Kind-zu-Familie-Verknüpfung
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

    def __init__(self, *args, tree_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        current_tree_id = tree_id
        if not current_tree_id and self.instance and self.instance.pk:
            # Prefer family tree, fall back to child tree
            if self.instance.family_id:
                current_tree_id = self.instance.family.gedcom_tree_id
            elif self.instance.child_id:
                current_tree_id = self.instance.child.gedcom_tree_id
        if current_tree_id:
            self.fields["child"].queryset = Individual.objects.filter(gedcom_tree_id=current_tree_id)
            self.fields["family"].queryset = Family.objects.filter(gedcom_tree_id=current_tree_id)
        else:
            self.fields["child"].queryset = Individual.objects.none()
            self.fields["family"].queryset = Family.objects.none()


# ----------------------------------------------------------------------
#  MediaObjectForm – für Bilder
# ----------------------------------------------------------------------
class MediaObjectForm(forms.ModelForm):
    """
    Formular zum Hochladen eines Bildes (oder anderer Medien) und zur
    Zuordnung zu einer oder mehreren Personen.
    """

    class Meta:
        model = MediaObject
        fields = [
            "gedcom_id",
            "title",
            "file",
            "description",
            "category",
            "individuals",
            "families",
            "sources",
            "events",
            "is_portrait",
            "is_private"
        ]
        widgets = {
            "gedcom_id": forms.TextInput(attrs={"class": "form-control"}),
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "file": forms.ClearableFileInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "category": forms.Select(attrs={'class': 'form-select'}),
            'individuals': forms.SelectMultiple(attrs={'class': 'form-control select2-individuals'}),
            'sources': forms.SelectMultiple(attrs={'class': 'form-control select2-sources'}),
            'events': forms.SelectMultiple(attrs={'class': 'form-control select2-events'}),
            "families": forms.SelectMultiple(attrs={'class': 'form-control select2-families'}),
            "is_portrait": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "is_private": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    # Füge tree_id als optionalen Parameter hinzu, um Daten-Leaks zu verhindern!
    def __init__(self, *args, person=None, family=None, source=None, event=None, tree_id=None, **kwargs):
        
        # 🔥 DER FIX FÜRS ANLEGEN: Wir setzen die initial-Werte VOR dem super() Aufruf!
        # So weiß Django direkt beim Aufbau des Formulars, was markiert sein soll.
        initial = kwargs.get('initial', {})
        if person: initial['individuals'] = [person.pk]
        if family: initial['families'] = [family.pk]
        if source: initial['sources'] = [source.pk]
        if event: initial['events'] = [event.pk]
        kwargs['initial'] = initial

        super().__init__(*args, **kwargs)

        # 1. SICHERHEIT: Finde heraus, in welchem Baum wir uns befinden
        current_tree_id = tree_id
        if person:
            current_tree_id = person.gedcom_tree_id
        elif family:
            current_tree_id = family.gedcom_tree_id
        elif source:
            current_tree_id = source.gedcom_tree_id
        elif event:
            current_tree_id = event.gedcom_tree_id
        elif self.instance and self.instance.pk:
            current_tree_id = self.instance.gedcom_tree_id

        # 2. QUERYSETS FILTERN & PERFORMANCE-HACK: 
        if current_tree_id:
            # --- Individuals ---
            self.fields["individuals"].queryset = Individual.objects.filter(gedcom_tree_id=current_tree_id)
            sel_inds = list(self.instance.individuals.all()) if self.instance and self.instance.pk else []
            if person and person not in sel_inds:
                sel_inds.append(person)
            
            # HTML-Optionen NUR für die ausgewählten/vorselektierten bauen
            self.fields["individuals"].widget.choices = [
                (obj.id, f"{obj.given_name} {obj.surname} ({obj.gedcom_id})") for obj in sel_inds
            ]

            # --- Families ---
            if "families" in self.fields:
                self.fields["families"].queryset = Family.objects.filter(gedcom_tree_id=current_tree_id)
                sel_fams = list(self.instance.families.all()) if self.instance and self.instance.pk else []
                if family and family not in sel_fams:
                    sel_fams.append(family)
                    
                self.fields["families"].widget.choices = [
                    (obj.id, f"{obj} ({obj.gedcom_id})") for obj in sel_fams
                ]

            # --- Sources ---
            if "sources" in self.fields:
                self.fields["sources"].queryset = Source.objects.filter(gedcom_tree_id=current_tree_id)
                sel_srcs = list(self.instance.sources.all()) if self.instance and self.instance.pk else []
                if source and source not in sel_srcs:
                    sel_srcs.append(source)
                    
                self.fields["sources"].widget.choices = [
                    (obj.id, f"{obj.title} ({obj.gedcom_id})") for obj in sel_srcs
                ]

            # --- Events ---
            if "events" in self.fields:
                self.fields["events"].queryset = Event.objects.filter(gedcom_tree_id=current_tree_id)
                sel_evts = list(self.instance.events.all()) if self.instance and self.instance.pk else []
                if event and event not in sel_evts:
                    sel_evts.append(event)
                    
                self.fields["events"].widget.choices = [
                    (obj.id, f"{obj.event_type.name if obj.event_type else 'Event'} - {obj.parsed_date or obj.raw_date or ''}") 
                    for obj in sel_evts
                ]
        else:
            # Fallback (bleibt wie vorher)
            self.fields["individuals"].queryset = Individual.objects.none()
            if "families" in self.fields: self.fields["families"].queryset = Family.objects.none()
            if "sources" in self.fields: self.fields["sources"].queryset = Source.objects.none()
            if "events" in self.fields: self.fields["events"].queryset = Event.objects.none() 

    # ------------------------------------------------------------------
    # Überschreiben von save() – Portrait-Logik sicher ausführen
    # ------------------------------------------------------------------
    def save(self, commit=True):
        media = super().save(commit=False)

        if commit:
            media.save()  # Speichert das Bild (gibt ihm eine ID)
            self.save_m2m()  # Speichert die Personen & Quellen in der Datenbank

            # WICHTIG: Erst NACHDEM save_m2m() gelaufen ist, weiß Django,
            # welche Personen mit dem Bild verknüpft sind!
            if media.is_portrait:
                for person in media.individuals.all():
                    MediaObject.objects.filter(
                        individuals=person, is_portrait=True
                    ).exclude(pk=media.pk).update(is_portrait=False)

        return media


    ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf"}
    MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MiB

    MAX_IMAGE_PIXELS = 40_000_000  # ~40 megapixels
    MAX_IMAGE_EDGE = 10_000

    def clean_file(self):
        f = self.cleaned_data.get("file")
        if not f:
            return f
        name = getattr(f, "name", "") or ""
        ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
        if ext not in self.ALLOWED_EXTENSIONS:
            raise forms.ValidationError(
                _("Nur Bilder (JPG, PNG, GIF, WEBP) oder PDF sind erlaubt.")
            )
        size = getattr(f, "size", None)
        if size is not None and size > self.MAX_UPLOAD_BYTES:
            raise forms.ValidationError(_("Datei ist zu groß (max. 20 MiB)."))

        if ext != ".pdf":
            from PIL import Image

            try:
                # Decompression-bomb protection
                Image.MAX_IMAGE_PIXELS = self.MAX_IMAGE_PIXELS
                pos = f.tell() if hasattr(f, "tell") else None
                with Image.open(f) as img:
                    width, height = img.size
                if hasattr(f, "seek"):
                    f.seek(pos or 0)
                if width > self.MAX_IMAGE_EDGE or height > self.MAX_IMAGE_EDGE:
                    raise forms.ValidationError(
                        _("Bildabmessungen sind zu groß (max. %(max)s px Kantenlänge).")
                        % {"max": self.MAX_IMAGE_EDGE}
                    )
                if width * height > self.MAX_IMAGE_PIXELS:
                    raise forms.ValidationError(_("Bild hat zu viele Pixel."))
            except forms.ValidationError:
                raise
            except Exception:
                raise forms.ValidationError(_("Datei ist kein gültiges Bild."))
        return f


class AddExistingMediaForm(forms.Form):
    media_objects = forms.ModelMultipleChoiceField(
        queryset=MediaObject.objects.none(),
        required=True,
        label=_("Bestehende Medien suchen und auswählen"),
        # 🔥 Das Checkbox-Widget fliegt raus, Select2 kommt rein:
        widget=forms.SelectMultiple(attrs={'class': 'form-control select2-media'})
    )

    def __init__(self, *args, **kwargs):
        self.tree = kwargs.pop('tree', None)
        self.target_obj = kwargs.pop('target_obj', None) # 🔥 NEU: Nimmt Person oder Familie entgegen
        super().__init__(*args, **kwargs)
        
        if self.tree:
            qs = MediaObject.objects.filter(gedcom_tree=self.tree)
            
            # 🔥 Dynamischer Ausschluss: Wir filtern bereits verknüpfte Medien heraus!
            if self.target_obj:
                if isinstance(self.target_obj, Individual):
                    qs = qs.exclude(individuals=self.target_obj)
                elif isinstance(self.target_obj, Family):
                    qs = qs.exclude(families=self.target_obj)

            self.fields['media_objects'].queryset = qs
            
            # 🔥 Der Performance-Hack: Wir leeren die HTML-Auswahlliste!
            self.fields['media_objects'].widget.choices = []
            

class PlaceForm(forms.ModelForm):
    class Meta:
        model = Place
        fields = ['name', 'latitude', 'longitude']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'z.B. Berlin, Deutschland'}),
            'latitude': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001'}),
            'longitude': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001'}),
        }


class EventForm(forms.ModelForm):
    class Meta:
        model = Event
        fields = ['event_type', 'raw_date', 'parsed_date', 'place', 'description', 'sources', 'individual', 'family'] 
        widgets = {
            'event_type': forms.Select(attrs={'class': 'form-select'}),
            # FIX: Hieß vorher 'date_raw'
            'raw_date': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'z.B. 12 May 1850'}),
            'parsed_date': forms.DateInput(
                format='%Y-%m-%d',
                attrs={
                    'class': 'form-control', 
                    'type': 'date'
                }
            ),
            'place': forms.Select(attrs={'class': 'form-control select2-place'}),
            'sources': forms.SelectMultiple(attrs={'class': 'form-control select2-sources'}),
        }

    def __init__(self, *args, **kwargs):
        # 1. Variablen herausziehen und löschen
        target_type = kwargs.pop('target_type', 'individual')
        tree_id = kwargs.pop('tree_id', None)
        
        # 2. Basis-Formular initialisieren
        super().__init__(*args, **kwargs)

        # 3. Den korrekten Stammbaum ermitteln (Priorität: Bestehendes Event > URL Parameter)
        current_tree_id = self.instance.gedcom_tree_id if self.instance and self.instance.pk else tree_id

        # 4. SECURITY FIX: Alle Dropdowns auf den aktuellen Baum filtern
        if current_tree_id:
            if 'individual' in self.fields:
                self.fields['individual'].queryset = Individual.objects.filter(gedcom_tree_id=current_tree_id).order_by('surname', 'given_name')
            if 'family' in self.fields:
                self.fields['family'].queryset = Family.objects.filter(gedcom_tree_id=current_tree_id)
            # --- Orte (Places) filtern & optimieren ---
            if "place" in self.fields:
                self.fields["place"].queryset = Place.objects.filter(gedcom_tree_id=current_tree_id)
                # Beim Update den aktuell gewählten Ort behalten, sonst HTML leeren
                sel_place = [self.instance.place] if self.instance and self.instance.place else []
                self.fields["place"].widget.choices = [(obj.id, obj.name) for obj in sel_place]

            # --- Quellen (Sources) filtern & optimieren ---
            if "sources" in self.fields:
                self.fields["sources"].queryset = Source.objects.filter(gedcom_tree_id=current_tree_id)
                # Bereits verknüpfte Quellen holen
                sel_srcs = list(self.instance.sources.all()) if self.instance and self.instance.pk else []
                self.fields["sources"].widget.choices = [(obj.id, f"{obj.title} ({obj.gedcom_id})") for obj in sel_srcs]
            
        else:
            # Sicherheits-Fallback: Wenn kein Baum bekannt ist, alles leeren!
            if "place" in self.fields: self.fields["place"].queryset = Place.objects.none()
            if "sources" in self.fields: self.fields["sources"].queryset = Source.objects.none()
            
            for field in ['individual', 'family', 'place', 'sources']:
                if field in self.fields:
                    self.fields[field].queryset = self.fields[field].queryset.none()

        # 5. Smart Workflow: Formular je nach Event-Ziel anpassen
        if target_type == 'individual':
            if 'family' in self.fields:
                del self.fields['family']  # Familienfeld bei Personen-Events löschen
            
            if 'individual' in self.fields:
                self.fields['individual'].required = True
                # Feld verstecken, wenn die Person bereits übergeben wurde
                if self.initial.get('individual'):
                    self.fields['individual'].widget = forms.HiddenInput()
                
        elif target_type == 'family':
            if 'individual' in self.fields:
                del self.fields['individual']  # Personenfeld bei Familien-Events löschen
            
            if 'family' in self.fields:
                self.fields['family'].required = True
                # Feld verstecken, wenn die Familie bereits übergeben wurde
                if self.initial.get('family'):
                    self.fields['family'].widget = forms.HiddenInput()


class EventTypeForm(forms.ModelForm):
    class Meta:
        model = EventType
        fields = ['name']
        widgets = {
            # Das Eingabefeld für den schönen Namen
            'name': forms.TextInput(attrs={'class': 'form-control'}),
        }


class SourceForm(forms.ModelForm):
    class Meta:
        model = Source
        # Adjust these fields based on exactly what is in your models.py!
        fields = ["gedcom_id", "title", "author", "publication_facts", "text"]
        widgets = {
            "gedcom_id": forms.TextInput(attrs={"class": "form-control"}),
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "author": forms.TextInput(attrs={"class": "form-control"}),
            "publication_facts": forms.TextInput(attrs={"class": "form-control"}),
            "text": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
        }


class PlaceMergeForm(forms.Form):
    """
    Erwartet `groups` als Iterable von Tupeln:
        (key, [Place, …])
    Für jede Gruppe wird ein ChoiceField mit RadioSelect erzeugt.
    """
    def __init__(self, groups, *args, **kwargs):
        super().__init__(*args, **kwargs)

        for idx, (key, places) in enumerate(groups):
            field_name = f"master_{idx}"
            choices = [(p.id, p.name or f"Place {p.id}") for p in places]

            self.fields[field_name] = forms.ChoiceField(
                label=f"Gruppe {idx + 1}",
                choices=choices,
                widget=forms.RadioSelect,
                required=True,
            )
               
#
# --- ADMIN
#

from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

class UserRegisterForm(UserCreationForm):
    email = forms.EmailField(required=True, widget=forms.EmailInput(attrs={'class': 'form-control'}))
    first_name = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    last_name = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))

    class Meta:
        model = User
        # Die Felder, die im Formular angezeigt werden sollen
        fields = ['username', 'email', 'first_name', 'last_name']
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Dem standardmäßigen Username-Feld ebenfalls die Bootstrap-Klasse geben
        self.fields['username'].widget.attrs.update({'class': 'form-control'})
 

class GedcomImportForm(forms.Form):
    """
    Formular für den Front-End-Import einer GEDCOM-Datei.
    - `gedcom_file` : UploadedFile (max. 10 MB, anpassbar)
    - `tree_name`   : Name des neuen Stammbaums, wie beim CLI-Befehl
    """
    gedcom_file = forms.FileField(
        label=_("GEDCOM-Datei"),
        help_text="Nur .ged/.gedcom-Dateien, max. 10 MB",
    )
    tree_name = forms.CharField(
        max_length=200,
        label=_("Name des Stammbaums"),
        help_text="Wie soll der neue Baum heißen?"
    )

    def clean_gedcom_file(self):
        f = self.cleaned_data["gedcom_file"]
        # optional: MIME-Check, Dateiendung, Größengrenze
        if not f.name.lower().endswith((".ged", ".gedcom")):
            raise forms.ValidationError("Bitte nur GEDCOM-Dateien (*.ged, *.gedcom) hochladen.")
        if f.size > 10 * 1024 * 1024:       # 10 MiB
            raise forms.ValidationError("Datei ist zu groß (max. 10 MiB).")
        return f


class TreeMembershipForm(forms.ModelForm):
    # Ein unsichtbares Feld, um das Löschen per Checkbox im Formset abzufangen
    DELETE = forms.BooleanField(required=False, widget=forms.HiddenInput(attrs={'class': 'delete-flag'}))

    class Meta:
        model = TreeMembership
        fields = ['role']
        widgets = {
            'role': forms.Select(attrs={'class': 'form-select form-select-sm'}),
        }

    def clean_role(self):
        role = self.cleaned_data.get("role")
        valid = {c.value for c in TreeMembership.Role}
        if role not in valid:
            raise forms.ValidationError(_("Ungültige Rolle."))
        return role

