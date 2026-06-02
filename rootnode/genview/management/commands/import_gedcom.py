import os
import re
from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction
from genview.models import Tree, Individual, Family, Event, EventType, Source, Place, ChildFamilyLink, AlternativeName

class Command(BaseCommand):
    help = 'Importiert eine GEDCOM-Datei und erstellt dabei einen neuen Stammbaum, Orte und Quellen.'

    def add_arguments(self, parser):
        parser.add_argument('gedcom_file', type=str, help='Pfad zur GEDCOM-Datei')
        parser.add_argument('--tree-name', type=str, help='Name des neuen Stammbaums', required=True)

    def handle(self, *args, **options):
        file_path = options['gedcom_file']
        tree_name = options['tree_name']

        if not os.path.exists(file_path):
            self.stdout.write(self.style.ERROR(f'Datei {file_path} nicht gefunden.'))
            return

        # Alles in einer Transaktion: Schlägt der Import fehl, wird nichts in der DB gespeichert!
        with transaction.atomic():
            self.stdout.write(self.style.SUCCESS(f'Erstelle neuen Stammbaum: {tree_name}'))
            tree = Tree.objects.create(name=tree_name)

            # Dictionary-Caches für schnelle Zuweisungen (GEDCOM-ID -> Django-Objekt)
            self.source_map = {}
            self.person_map = {}
            self.family_map = {}

            # Die Datei in Abschnitte zerlegen (getrennt durch Level 0)
            records = self._parse_to_records(file_path)

            self.stdout.write('Phase 0: Importiere MediaObjects (OBJ)...')
            self._import_media_records(tree, records)

            self.stdout.write('Phase 1: Importiere Quellen (SOUR)...')
            self._import_sources(tree, records)

            self.stdout.write('Phase 2: Importiere Personen (INDI) und Orte (PLAC)...')
            self._import_individuals(tree, records)

            self.stdout.write('Phase 3: Importiere Familien (FAM)...')
            self._import_families(tree, records)

            self.stdout.write(self.style.SUCCESS('GEDCOM Import erfolgreich abgeschlossen!'))

    # -------------------------------------------------------------------------
    # HILFSFUNKTIONEN FÜR DAS PARSING
    # -------------------------------------------------------------------------
    def _parse_to_records(self, file_path):
        """Teilt die GEDCOM Datei in logische Blöcke und repariert mehrzeilige Texte."""
        records = []
        current_record = []
        
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            for line in f:
                # WICHTIG: Hier nutzen wir rstrip('\r\n') statt strip(). 
                # Ein Leerzeichen am Ende einer Zeile MUSS erhalten bleiben, 
                # da sonst Wörter bei CONC ungewollt zusammenkleben!
                line = line.rstrip('\r\n')
                if not line: continue
                
                parts = line.split(' ', 2)
                level = parts[0]
                tag = parts[1] if len(parts) > 1 else ""
                value = parts[2] if len(parts) > 2 else ""
                
                # --- DER MAGISCHE REPARATUR-BLOCK FÜR CONC UND CONT ---
                if tag == 'CONC':
                    if current_record:
                        # Nahtlos an die vorherige Zeile anhängen
                        current_record[-1] += value
                    continue  # Diese Zeile NICHT als eigene Zeile im Record speichern!
                    
                elif tag == 'CONT':
                    if current_record:
                        # Mit einem echten Python-Zeilenumbruch (\n) anhängen
                        current_record[-1] += "\n" + value
                    continue  # Diese Zeile NICHT als eigene Zeile im Record speichern!
                # -------------------------------------------------------

                if level == '0' and current_record:
                    records.append(current_record)
                    current_record = []
                    
                current_record.append(line)
                
        if current_record:
            records.append(current_record)
            
        return records

    def _extract_tags(self, record, parent_tag):
        """Findet alle Unter-Tags für ein bestimmtes Tag (z.B. PLAC, DATE, SOUR innerhalb von BIRT)."""
        tags = {}
        in_target_block = False
        target_level = None

        for line in record:
            parts = line.split(' ', 2)
            level = int(parts[0])
            tag = parts[1] if len(parts) > 1 else ""
            value = parts[2] if len(parts) > 2 else ""

            # Block starten
            if tag == parent_tag:
                in_target_block = True
                target_level = level
                continue
            
            # Block beenden, wenn wir wieder auf der gleichen oder einer höheren Ebene sind
            if in_target_block and level <= target_level:
                in_target_block = False

            if in_target_block:
                if tag not in tags:
                    tags[tag] = []
                tags[tag].append(value)
                
        return tags

    # -------------------------------------------------------------------------
    # PHASE 1: QUELLEN
    # -------------------------------------------------------------------------
    def _import_sources(self, tree, records):
        for record in records:
            header = record[0].split(' ', 2)
            if len(header) > 2 and header[2] == 'SOUR':
                gedcom_id = header[1]
                
                # Sehr simples Parsing für Quellen-Metadaten
                title = ""
                author = ""
                for line in record:
                    if line.startswith("1 TITL "): title = line[7:]
                    if line.startswith("1 AUTH "): author = line[7:]

                source = Source.objects.create(
                    gedcom_tree=tree,
                    gedcom_id=gedcom_id,
                    title=title or f"Unbenannte Quelle ({gedcom_id})",
                    author=author
                )
                self.source_map[gedcom_id] = source

    # -------------------------------------------------------------------------
    # PHASE 2: PERSONEN (Und ihre Ereignisse/Orte)
    # -------------------------------------------------------------------------
    def _import_individuals(self, tree, records):
        for record in records:
            header = record[0].split(' ', 2)
            if len(header) > 2 and header[2] == 'INDI':
                gedcom_id = header[1]
                
                # --- NAMENS-PARSING UPDATE ---
                given_name = "Unbekannt"
                surname = "Unbekannt"
                sex = "U"
                
                primary_name_set = False
                alt_names_to_create = [] # Temporärer Speicher für zusätzliche Namen

                for i, line in enumerate(record):
                    if line.startswith("1 NAME "):
                        raw_name = line[7:].strip()
                        
                        # Lokale Variablen für diesen spezifischen Namensblock
                        current_given = ""
                        current_surname = ""
                        current_type = "unknown" # Fallback, falls kein 2 TYPE kommt

                        # 1. Basis-Parsing des Strings (mit Slashes)
                        if '/' in raw_name:
                            parts = raw_name.split('/')
                            if len(parts) >= 2:
                                current_given = parts[0].strip()
                                current_surname = parts[1].strip()
                        else:
                            current_given = raw_name
                            
                        # 2. Sub-Tags (GIVN, SURN, TYPE) für DIESEN Namen scannen
                        j = i + 1
                        while j < len(record) and record[j].startswith("2 "):
                            if record[j].startswith("2 GIVN "):
                                current_given = record[j][7:].strip()
                            elif record[j].startswith("2 SURN "):
                                current_surname = record[j][7:].strip()
                            elif record[j].startswith("2 TYPE "):
                                # Liest z.B. "married" oder "aka" aus
                                current_type = record[j][7:].strip().lower()
                            j += 1

                        # 3. Entscheidung: Ist es der Hauptname oder ein alternativer Name?
                        if not primary_name_set:
                            if current_given: given_name = current_given
                            if current_surname: surname = current_surname
                            primary_name_set = True
                        else:
                            # Es ist ein zweiter/dritter Name -> ab auf die Warteliste
                            alt_names_to_create.append({
                                'given_name': current_given,
                                'surname': current_surname,
                                'type': current_type
                            })
                    
                    elif line.startswith("1 SEX "):
                        parsed_sex = line[6:].strip().upper()
                        sex = parsed_sex if parsed_sex in ['M', 'F'] else "U"

                # Person in der Haupttabelle erstellen
                person = Individual.objects.create(
                    gedcom_tree=tree, 
                    gedcom_id=gedcom_id,
                    given_name=given_name, 
                    surname=surname,
                    sex=sex  
                )
                self.person_map[gedcom_id] = person

                # 🔥 JETZT: Die gesammelten Alternativnamen in die neue Tabelle schreiben
                for alt in alt_names_to_create:
                    # Sicherstellen, dass wir gültige Choices nutzen (Mapping)
                    valid_type = AlternativeName.NameType.UNKNOWN
                    if alt['type'] == 'married': valid_type = AlternativeName.NameType.MARRIED
                    elif alt['type'] == 'maiden': valid_type = AlternativeName.NameType.MAIDEN
                    elif alt['type'] == 'aka': valid_type = AlternativeName.NameType.AKA
                    elif alt['type'] == 'immigrant': valid_type = AlternativeName.NameType.IMMIGRANT

                    AlternativeName.objects.create(
                        individual=person,
                        given_name=alt['given_name'],
                        surname=alt['surname'],
                        name_type=valid_type
                    )

                # Ereignisse parsen (Geburt, Tod...)
                self._create_events_from_record(tree, record, individual=person)

                # 🔥 NEU: 2. Medien (Bilder, Dokumente) für diese Person parsen
                self._create_media_from_record(tree, record, individual=person)

    # -------------------------------------------------------------------------
    # PHASE 3: FAMILIEN (Mit Eltern UND Kindern über ChildFamilyLink)
    # -------------------------------------------------------------------------
    def _import_families(self, tree, records):
        for record in records:
            header = record[0].split(' ', 2)
            if len(header) > 2 and header[2] == 'FAM':
                gedcom_id = header[1]
                
                # Familie in DB erstellen
                family = Family.objects.create(
                    gedcom_tree=tree,
                    gedcom_id=gedcom_id
                )
                self.family_map[gedcom_id] = family

                for line in record:
                    # 1. Ehemann verknüpfen (ForeignKey)
                    if line.startswith("1 HUSB "):
                        husb_id = line[7:].strip()
                        if husb_id in self.person_map:
                            family.husband = self.person_map[husb_id]
                            
                    # 2. Ehefrau verknüpfen (ForeignKey)
                    elif line.startswith("1 WIFE "):
                        wife_id = line[7:].strip()
                        if wife_id in self.person_map:
                            family.wife = self.person_map[wife_id]
                            
                    # 3. NEU: Kinder über ChildFamilyLink verknüpfen!
                    elif line.startswith("1 CHIL "):
                        chil_id = line[7:].strip()
                        if chil_id in self.person_map:
                            # Wir erstellen den Link explizit. Standard ist BIOLOGICAL,
                            # wie in deinem Model definiert.
                            ChildFamilyLink.objects.create(
                                child=self.person_map[chil_id],
                                family=family,
                                relationship_type=ChildFamilyLink.Relationship.BIOLOGICAL
                            )

                # Änderungen für husband und wife speichern
                family.save()

                # Ereignisse parsen (z.B. Hochzeit)
                self._create_events_from_record(tree, record, family=family)

                # 🔥 NEU: 2. Medien für diese Familie parsen
                self._create_media_from_record(tree, record, family=family)

    
    def _import_media_records(self, tree, records):
        """
        Sucht nach eigenständigen Level-0 Medien-Records (z.B. 0 @M1@ OBJE) 
        und aktualisiert die Metadaten wie Dateipfad und Titel.
        """
        from genview.models import MediaObject

        for record in records:
            if not record:
                continue
                
            first_line = record[0]
            
            # Prüfen, ob es ein Medien-Hauptrecord ist ("0 @M123@ OBJE")
            if first_line.startswith("0 ") and first_line.endswith(" OBJE"):
                parts = first_line.split(' ', 2)
                
                if len(parts) >= 3:
                    gedcom_id = parts[1]  # Das "@M123@"
                    
                    # MAGIE: Hole das Objekt (falls die Personenschleife es als 
                    # leere Hülle schon angelegt hat) oder erstelle ein neues.
                    media_obj, created = MediaObject.objects.get_or_create(
                        gedcom_tree=tree,
                        gedcom_id=gedcom_id
                    )
                    
                    # Jetzt lesen wir die Eigenschaften (FILE, TITL) aus
                    for line in record[1:]:
                        l_parts = line.split(' ', 2)
                        level = l_parts[0]
                        tag = l_parts[1] if len(l_parts) > 1 else ""
                        value = l_parts[2].strip() if len(l_parts) > 2 else ""
                        
                        # Da es ein Level-0 Record ist, sind die Eigenschaften auf Level 1
                        if level == '1':
                            if tag == 'FILE':
                                media_obj.gedcom_original_filepath = value
                            elif tag == 'TITL':
                                media_obj.title = value
                            elif tag == 'FORM':
                                pass # (Optional) Formatbehandlung
                                
                    # Metadaten speichern
                    media_obj.save()
    
    # -------------------------------------------------------------------------
    # EREIGNIS-ERSTELLUNG (Hier passiert die Magie für PLAC und SOUR!)
    # -------------------------------------------------------------------------
    def _create_events_from_record(self, tree, record, individual=None, family=None):
        """
        Liest alle Events (Ereignisse) und Attribute (Eigenschaften) aus einem GEDCOM-Record 
        und speichert sie in der Event-Tabelle.
        """
        # PERFORMANCE-FIX: Wir laden alle EventTypes einmalig in ein Dictionary (Cache).
        # Das verhindert Hunderte unnötiger Datenbankabfragen während des Imports!
        if not hasattr(self, '_event_types_cache'):
            from genview.models import EventType
            self._event_types_cache = {et.tag: et for et in EventType.objects.all()}

        current_event = None

        for line in record:
            # Zeile aufteilen in: Level (z.B. '1'), Tag (z.B. 'OCCU'), Wert (z.B. 'Bäcker')
            parts = line.split(' ', 2)
            level = parts[0]
            tag = parts[1] if len(parts) > 1 else ""
            value = parts[2].strip() if len(parts) > 2 else ""

            # --- LEVEL 1: Ein neues Ereignis / Attribut beginnt ---
            if level == '1':
                # Prüfen, ob wir diesen Tag kennen (BIRT, OCCU, MARR, etc.)
                if tag in self._event_types_cache:
                    
                    # Falls wir vorher schon ein Event bearbeitet haben, jetzt speichern!
                    if current_event:
                        current_event.save()
                    
                    # Neues Event im Arbeitsspeicher vorbereiten
                    current_event = Event(
                        gedcom_tree=tree,
                        individual=individual,
                        family=family,
                        event_type=self._event_types_cache[tag],
                        # 🔥 HIER PASSIERT DIE MAGIE FÜR OCCU/EDUC:
                        # Wenn die Zeile einen Wert hat (z.B. "Bäcker"), landet er in description!
                        description=value 
                    )
                else:
                    # Es ist ein Level-1-Tag, den wir nicht als Event verarbeiten (z.B. NAME, SEX, FAMC).
                    # Wir setzen current_event auf None, damit Unter-Tags (Level 2) ignoriert werden.
                    current_event = None

            # --- LEVEL 2: Details zum aktuellen Ereignis (Datum, Ort, Notizen) ---
            elif level == '2' and current_event:
                if tag == 'DATE':
                    current_event.raw_date = value
                    
                    # (Optional) Hier könntest du deinen Date-Parser aufrufen:
                    # current_event.parsed_date = parse_gedcom_date(value)
                    
                elif tag == 'PLAC':
                    # Je nachdem, wie du Orte verwaltest. Meist ein ForeignKey:
                    # place_obj, created = Place.objects.get_or_create(gedcom_tree=tree, name=value)
                    # current_event.place = place_obj
                    pass # Passe dies an deine bisherige Orte-Logik an!

                elif tag == 'NOTE':
                    # Manchmal hat ein Beruf ("Bäcker") zusätzlich noch eine Notiz.
                    # Wir hängen die Notiz einfach mit einem Zeilenumbruch an die Description an.
                    if current_event.description:
                        current_event.description += f"\n\nNotiz: {value}"
                    else:
                        current_event.description = value

                elif tag == 'SOUR':
                    # Falls du Quellen verknüpfst, passiert das meist erst NACH dem .save()
                    # (wegen der Many-To-Many Beziehung). Das würde man extra behandeln.
                    pass 

        # Ganz am Ende der Schleife: Das allerletzte Event des Records noch speichern!
        if current_event:
            current_event.save()

    # -------------------------------------------------------------------------
    # DATUM-PARSING LOGIK
    # -------------------------------------------------------------------------
    def _parse_gedcom_date(self, raw_date_str):
        """
        Versucht, aus einem chaotischen GEDCOM-Datum ein echtes Python Date-Objekt 
        für die chronologische Sortierung zu machen.
        """
        if not raw_date_str:
            return None

        # 1. Bereinigen und in Großbuchstaben umwandeln
        d = raw_date_str.upper().strip()
        
        # Wenn es eine Zeitspanne ist (z.B. BET 1850 AND 1860), 
        # nehmen wir das erste Datum für die Sortierung in der Timeline.
        if d.startswith('BET ') and ' AND ' in d:
            d = d.split(' AND ')[0].replace('BET ', '').strip()
        elif d.startswith('FROM ') and ' TO ' in d:
            d = d.split(' TO ')[0].replace('FROM ', '').strip()

        # GEDCOM-Präfixe (Ungefähr, Vor, Nach, Geschätzt) entfernen
        prefixes = ['ABT ', 'CAL ', 'EST ', 'BEF ', 'AFT ', 'ABOUT ']
        for p in prefixes:
            if d.startswith(p):
                d = d.replace(p, '').strip()

        # 2. Jahr, Monat, Tag extrahieren
        months = {
            'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
            'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12
        }

        # Suchen nach einem 4-stelligen Jahr (Das Wichtigste!)
        year_match = re.search(r'\d{4}', d)
        if not year_match:
            return None # Ohne Jahr können wir kein Date-Objekt bauen
        
        year = int(year_match.group())
        month = 1 # Fallback, falls nur das Jahr da ist
        day = 1   # Fallback, falls nur Jahr/Monat da ist

        # Suchen nach dem Monat
        for m_str, m_num in months.items():
            if m_str in d:
                month = m_num
                break

        # Suchen nach dem Tag (1 oder 2 Ziffern, meist ganz vorne)
        day_match = re.search(r'^\d{1,2}\b', d)
        if day_match:
            day = int(day_match.group())

        # Sicherstellen, dass das Datum gültig ist (z.B. kein 31. Februar)
        try:
            return date(year, month, day)
        except ValueError:
            return None


    def _create_media_from_record(self, tree, record, individual=None, family=None, event=None):
        """
        Liest Medien-Referenzen (1 OBJE @M1@) oder Inline-Medien (1 OBJE) aus einem Record 
        und verknüpft sie mit der Person, Familie oder dem Event.
        """
        from genview.models import MediaObject

        in_inline_obje = False
        current_media = None

        for line in record:
            parts = line.split(' ', 2)
            level = parts[0]
            tag = parts[1] if len(parts) > 1 else ""
            value = parts[2].strip() if len(parts) > 2 else ""

            if level == '1' and tag == 'OBJE':
                if value.startswith('@') and value.endswith('@'):
                    # FALL A: Referenz auf ein Level-0-Medienobjekt (z.B. @M1@)
                    # Wir holen oder erstellen das leere Objekt (es wird später 
                    # in einem eigenen Durchlauf mit Titel/File gefüllt, falls 
                    # du Level-0-Objekte als eigenen Schritt importierst).
                    media_obj, created = MediaObject.objects.get_or_create(
                        gedcom_tree=tree, 
                        gedcom_id=value
                    )
                    
                    # JETZT verknüpfen wir das Many-To-Many Feld!
                    if individual: media_obj.individuals.add(individual)
                    if family: media_obj.families.add(family)
                    if event: media_obj.events.add(event)
                    
                else:
                    # FALL B: Inline-Medium (Das Bild wird direkt hier definiert)
                    # Wir müssen vorheriges speichern, falls es mehrere gibt
                    if current_media:
                        current_media.save()
                        # Verknüpfungen nach dem Save!
                        if individual: current_media.individuals.add(individual)
                        if family: current_media.families.add(family)
                        if event: current_media.events.add(event)
                        
                    in_inline_obje = True
                    current_media = MediaObject(gedcom_tree=tree)

            # Details für das Inline-Medium auslesen
            elif level == '2' and in_inline_obje and current_media:
                if tag == 'FILE':
                    # Wir speichern es in unser neues Textfeld, NICHT in 'file'!
                    current_media.gedcom_original_filepath = value
                elif tag == 'TITL':
                    current_media.title = value
                elif tag == 'FORM':
                    pass # Könntest du theoretisch in 'description' oder ein neues Format-Feld schreiben
            
            # Wenn der OBJE Block vorbei ist
            elif level == '1' and in_inline_obje:
                if current_media:
                    current_media.save()
                    if individual: current_media.individuals.add(individual)
                    if family: current_media.families.add(family)
                    if event: current_media.events.add(event)
                in_inline_obje = False
                current_media = None

        # Schleifenende: Letztes Inline-Objekt speichern
        if in_inline_obje and current_media:
            current_media.save()
            if individual: current_media.individuals.add(individual)
            if family: current_media.families.add(family)
            if event: current_media.events.add(event)
