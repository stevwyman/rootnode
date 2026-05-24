# genview/forms.py
from django import forms
from django.forms import ModelForm, CheckboxSelectMultiple, DateInput
from django.contrib.auth.models import User
from .models import Individual, Family, ChildFamilyLink, Event, MediaObject, Source, Place, Tree, TreeMembership


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
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "z. B. 12 JAN 1885"}
        ),
    )
    birth_date_parsed = forms.DateField(
        required=False,
        label="Geburts‑Datum (geparst)",
        widget=DateInput(format='%Y-%m-%d',attrs={"class": "form-control", "type": "date"}),
    )
    death_date_raw = forms.CharField(
        required=False,
        label="Sterbe‑Datum (Roh‑String)",
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "z. B. 5 MAY 1972"}
        ),
    )
    death_date_parsed = forms.DateField(
        required=False,
        label="Sterbe‑Datum (geparst)",
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
            "sources": CheckboxSelectMultiple(),
        }

    # --------------------------------------------------------------
    # Initial‑Daten für die virtuellen Felder befüllen
    # --------------------------------------------------------------
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

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
                gedcom_tree=person.gedcom_tree,
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
            self.save_m2m()  # speichert ManyToMany‑Beziehungen (Sources)

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
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "z. B. 15 JUN 1890"}
        ),
    )
    marriage_parsed_date = forms.DateField(
        required=False,
        label="Heirats‑Datum (geparst)",
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
        label="Heiratsort",
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = Family
        fields = [
            "gedcom_id",
            "husband",
            "wife",
            "parent",  # MPTT‑Hierarchie (optional)
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
    def __init__(self, *args, tree_id=None, **kwargs):
        super().__init__(*args, **kwargs)

        # 1. Versuche die tree_id aus den View-Argumenten zu holen
        current_tree_id = tree_id
        
        # 2. Fallback: Wenn wir bearbeiten (Update), hat die Instanz selbst eine tree_id
        if not current_tree_id and self.instance and self.instance.pk:
            current_tree_id = self.instance.gedcom_tree_id

        # 3. Dropdown befüllen!
        if current_tree_id and 'marriage_place' in self.fields:
            self.fields['marriage_place'].queryset = Place.objects.filter(gedcom_tree_id=current_tree_id)
        elif 'marriage_place' in self.fields:
            self.fields['marriage_place'].queryset = Place.objects.none()

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
        family = super().save(commit=False)  # speichert Family‑Stammdaten

        if commit:
            family.save()
            self.save_m2m()  # Sources‑ManyToMany

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
            "is_portrait",
        ]
        widgets = {
            "gedcom_id": forms.TextInput(attrs={"class": "form-control"}),
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "file": forms.ClearableFileInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            "individuals": forms.CheckboxSelectMultiple(),
            "families": forms.CheckboxSelectMultiple(),
            "sources": forms.CheckboxSelectMultiple(),
            "is_portrait": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    # Füge tree_id als optionalen Parameter hinzu, um Daten-Leaks zu verhindern!
    def __init__(self, *args, person=None, family=None, source=None, event=None, tree_id=None, **kwargs):
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

        # 2. QUERYSETS FILTERN: Nur Personen/Quellen aus DIESEM Baum anzeigen!
        if current_tree_id:
            self.fields["individuals"].queryset = Individual.objects.filter(
                gedcom_tree_id=current_tree_id
            )
            if "families" in self.fields:
                self.fields["families"].queryset = Family.objects.filter(
                    gedcom_tree_id=current_tree_id)
            if "sources" in self.fields:
                self.fields["sources"].queryset = Source.objects.filter(
                    gedcom_tree_id=current_tree_id
                )
            if "events" in self.fields:
                self.fields["events"].queryset = Event.objects.filter(
                    gedcom_tree_id=current_tree_id
                )
        else:
            # Fallback: Falls kein Baum gefunden wird, zeige sicherheitshalber nichts an
            self.fields["individuals"].queryset = Individual.objects.none()
            if "families" in self.fields:
                self.fields["families"].queryset = Family.objects.none()

        # 3. PRESELECTION: Person in der Checkbox-Liste anhaken
        if person:
            self.fields["individuals"].initial = [person]
        if family and "families" in self.fields:
            self.fields["families"].initial = [family]
        if source and "sources" in self.fields:
            self.fields["sources"].initial = [source]
        if event and "events" in self.fields:
            self.fields["events"].initial = [event]

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
        # Include your other event fields here too
        fields = ['event_type', 'raw_date', 'parsed_date', 'place', 'description', 'sources', 'individual', 'family'] 
        widgets = {
            'event_type': forms.Select(attrs={'class': 'form-select'}),
            'date_raw': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'z.B. 12 May 1850'}),
            'parsed_date': forms.DateInput(
                format='%Y-%m-%d',  # <-- DAS HIER IST DAS GEHEIMNIS!
                attrs={
                    'class': 'form-control', 
                    'type': 'date'  # Öffnet den nativen Browser-Kalender
                }
            ),
            'place': forms.Select(attrs={'class': 'form-select'}), # Change to Select
            'sources': forms.CheckboxSelectMultiple(),
        }

    # TODO: check if person, family and tree_id are necessary
    def __init__(self, *args, **kwargs):

        # 1. WICHTIG: Zuerst mit .pop() unsere Variablen herausziehen und LÖSCHEN.
        # So verhindern wir, dass Django's BaseModelForm davon erfährt!
        target_type = kwargs.pop('target_type', 'individual')
        tree_id = kwargs.pop('tree_id', None)
        
        # 2. ERST JETZT rufen wir super() auf. Die kwargs sind jetzt "sauber".
        super().__init__(*args, **kwargs)

        # 3. Dropdowns filtern
        if tree_id:
            if 'individual' in self.fields:
                self.fields['individual'].queryset = Individual.objects.filter(gedcom_tree_id=tree_id).order_by('surname', 'given_name')
            if 'family' in self.fields:
                self.fields['family'].queryset = Family.objects.filter(gedcom_tree_id=tree_id)

        # 4. Formular anpassen und irrelevante Felder komplett entfernen
        if target_type == 'individual':
            if 'family' in self.fields:
                del self.fields['family']
            
            # Sicherheits-Check: Nur ändern, wenn das Feld auch im Formular geladen wurde
            if 'individual' in self.fields:
                self.fields['individual'].required = True
                
                # Smart Workflow: Feld verstecken, wenn per URL übergeben
                if self.initial.get('individual'):
                    self.fields['individual'].widget = forms.HiddenInput()
                
        elif target_type == 'family':
            if 'individual' in self.fields:
                del self.fields['individual']
            
            # Sicherheits-Check: Nur ändern, wenn das Feld auch im Formular geladen wurde
            if 'family' in self.fields:
                self.fields['family'].required = True
                
                # Smart Workflow: Feld verstecken, wenn per URL übergeben
                if self.initial.get('family'):
                    self.fields['family'].widget = forms.HiddenInput()
        
        # SECURITY FIX: Only show sources belonging to THIS tree
        current_tree_id = tree_id
        if self.instance and self.instance.pk:
            current_tree_id = self.instance.gedcom_tree_id

        if current_tree_id:
            if 'sources' in self.fields:
                self.fields['sources'].queryset = Source.objects.filter(gedcom_tree_id=current_tree_id)
            if 'place' in self.fields:
                self.fields['place'].queryset = Place.objects.filter(gedcom_tree_id=current_tree_id) # NEW
        else:
            if 'sources' in self.fields:
                self.fields['sources'].queryset = Source.objects.none()
            if 'place' in self.fields:
                self.fields['place'].queryset = Place.objects.none() # NEW

        # 4. Wenn die ID schon übergeben wurde, verstecken wir das Feld, 
        # damit der Nutzer es nicht mehr ändern muss/kann!
        if target_type == 'individual' and self.initial.get('individual'):
            self.fields['individual'].widget = forms.HiddenInput()
            
        elif target_type == 'family' and self.initial.get('family'):
            self.fields['family'].widget = forms.HiddenInput()


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


class UserRegistrationForm(forms.ModelForm):
    """Einfaches Registrierungsformular (Username, E‑Mail, Passwort, Baum‑Auswahl)."""
    password1 = forms.CharField(label='Passwort', widget=forms.PasswordInput)
    password2 = forms.CharField(label='Passwort (Wiederholung)', widget=forms.PasswordInput)
    tree = forms.ModelChoiceField(
        queryset=Tree.objects.all(),
        label='Welchen Stammbaum möchtest du sehen?',
        required=True,
        empty_label="Bitte auswählen"
    )

    class Meta:
        model = User
        fields = ('username', 'email')

    def clean_password2(self):
        p1 = self.cleaned_data.get('password1')
        p2 = self.cleaned_data.get('password2')
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Die beiden Passwörter stimmen nicht überein.")
        return p2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password1'])
        if commit:
            user.save()
            # Nach dem Anlegen des Users gleich die Membership erzeugen
            TreeMembership.objects.create(
                user=user,
                tree=self.cleaned_data['tree'],
                role='VIEWER'          # Standard‑Rolle, später per Admin änderbar
            )
        return user