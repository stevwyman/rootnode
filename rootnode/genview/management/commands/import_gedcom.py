import os
import re
from datetime import date
from django.core.management.base import BaseCommand
from gedcom.parser import Parser
from gedcom.element.individual import IndividualElement
from gedcom.element.family import FamilyElement
from genview.models import Individual, Family, ChildFamilyLink, Tree, Event # Added Event here

class Command(BaseCommand):
    help = 'Imports a GEDCOM file, parses dates, and creates Events'

    def add_arguments(self, parser):
        parser.add_argument('file_path', type=str, help='Path to the GEDCOM file')

    def handle(self, *args, **kwargs):
        file_path = kwargs['file_path']

        if not os.path.exists(file_path):
            self.stdout.write(self.style.ERROR(f'File not found: {file_path}'))
            return

        base_name = os.path.basename(file_path)
        tree_name, _ = os.path.splitext(base_name)

        target_tree = Tree.objects.create(
            name=tree_name.replace('_', ' ').title(),
            description=f"Automatically imported from {base_name}"
        )
        
        self.stdout.write(self.style.SUCCESS(f'Created new Tree: "{target_tree.name}"'))
        self.stdout.write(self.style.NOTICE(f'Parsing {base_name}...'))
        
        gedcom_parser = Parser()
        gedcom_parser.parse_file(file_path, False)
        root_child_elements = gedcom_parser.get_root_child_elements()

        # STEP 1: Import Individuals & Their Events
        self.stdout.write('Importing Individuals and Events (Birth/Death)...')
        for element in root_child_elements:
            if isinstance(element, IndividualElement):
                gedcom_id = element.get_pointer()
                (first, last) = element.get_name()
                
                # Save the individual
                person_obj, _ = Individual.objects.update_or_create(
                    gedcom_id=gedcom_id,
                    gedcom_tree=target_tree,
                    defaults={
                        'given_name': first,
                        'surname': last,
                        'sex': element.get_gender() or 'U',
                    }
                )

                # Find Birth and Death Events
                for child in element.get_child_elements():
                    tag = child.get_tag()
                    if tag in ['BIRT', 'DEAT']:
                        raw_date = self._get_first_child_value(child, 'DATE') or ""
                        place = self._get_first_child_value(child, 'PLAC') or ""
                        
                        if raw_date or place:
                            Event.objects.create(
                                event_type=Event.EventType.BIRTH if tag == 'BIRT' else Event.EventType.DEATH,
                                gedcom_tree=target_tree,
                                individual=person_obj,
                                raw_date=raw_date,
                                parsed_date=self._parse_gedcom_date(raw_date),
                                place=place
                            )

        # STEP 2: Import Families & Marriages
        self.stdout.write('Importing Families and Marriages...')
        for element in root_child_elements:
            if isinstance(element, FamilyElement):
                fam_id = element.get_pointer()
                
                husb_id = self._get_first_child_value(element, 'HUSB')
                wife_id = self._get_first_child_value(element, 'WIFE')

                husband = Individual.objects.filter(gedcom_id=husb_id, gedcom_tree=target_tree).first() if husb_id else None
                wife = Individual.objects.filter(gedcom_id=wife_id, gedcom_tree=target_tree).first() if wife_id else None

                family_obj, _ = Family.objects.update_or_create(
                    gedcom_id=fam_id,
                    gedcom_tree=target_tree,
                    defaults={
                        'husband': husband,
                        'wife': wife,
                    }
                )

                # Link Children
                for child_elem in element.get_child_elements():
                    if child_elem.get_tag() == 'CHIL':
                        child_id = child_elem.get_value()
                        child_obj = Individual.objects.filter(gedcom_id=child_id, gedcom_tree=target_tree).first()
                        
                        if child_obj:
                            ChildFamilyLink.objects.update_or_create(
                                child=child_obj,
                                family=family_obj,
                                defaults={'relationship_type': 'B'} 
                            )
                
                # Find Marriage Events
                for child in element.get_child_elements():
                    if child.get_tag() == 'MARR':
                        raw_date = self._get_first_child_value(child, 'DATE') or ""
                        place = self._get_first_child_value(child, 'PLAC') or ""
                        
                        if raw_date or place:
                            Event.objects.create(
                                event_type=Event.EventType.MARRIAGE,
                                gedcom_tree=target_tree,
                                family=family_obj,
                                raw_date=raw_date,
                                parsed_date=self._parse_gedcom_date(raw_date),
                                place=place
                            )

        self.stdout.write(self.style.SUCCESS(f'Successfully imported all data into "{target_tree.name}"!'))

    def _get_first_child_value(self, element, tag):
        for child in element.get_child_elements():
            if child.get_tag() == tag:
                return child.get_value()
        return None

    def _parse_gedcom_date(self, raw_date):
        """
        Attempts to parse a messy GEDCOM date string into a Python datetime.date object.
        Defaults to Jan 1st if only a year is provided.
        """
        if not raw_date:
            return None
            
        months_map = {
            'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
            'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12
        }
        
        # Clean the string of common GEDCOM text modifiers
        clean_str = raw_date.upper()
        for prefix in ['ABT', 'CAL', 'EST', 'BEF', 'AFT', 'BET', 'AND', 'TO', 'FROM']:
            clean_str = clean_str.replace(prefix, '')
            
        # Regex to find: (Optional 1-2 digit Day) (Optional 3-letter Month) (3-4 digit Year)
        match = re.search(r'(?:(\d{1,2})\s+)?(?:([A-Z]{3})\s+)?(\d{3,4})', clean_str)
        
        if match:
            day_str, month_str, year_str = match.groups()
            
            try:
                year = int(year_str)
                # Fallback to 1 if month or day are missing
                month = months_map.get(month_str, 1) if month_str else 1
                day = int(day_str) if day_str else 1
                
                return date(year, month, day)
            except ValueError:
                # Catches impossible dates like '31 FEB 1900'
                return None
                
        return None