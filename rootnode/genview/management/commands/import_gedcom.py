import os
from django.core.management.base import BaseCommand
from gedcom.parser import Parser
from gedcom.element.individual import IndividualElement
from gedcom.element.family import FamilyElement
from genview.models import Individual, Family, ChildFamilyLink # Adjust import path

class Command(BaseCommand):
    help = 'Imports a GEDCOM file into the Django database'

    def add_arguments(self, parser):
        parser.add_argument('file_path', type=str, help='Path to the GEDCOM file')

    def handle(self, *args, **kwargs):
        file_path = kwargs['file_path']

        if not os.path.exists(file_path):
            self.stdout.write(self.style.ERROR(f'File not found: {file_path}'))
            return

        self.stdout.write(self.style.NOTICE(f'Parsing {file_path}...'))
        
        # Initialize the parser
        gedcom_parser = Parser()
        gedcom_parser.parse_file(file_path, False)

        root_child_elements = gedcom_parser.get_root_child_elements()

        # STEP 1: Import Individuals
        self.stdout.write('Importing Individuals...')
        for element in root_child_elements:
            if isinstance(element, IndividualElement):
                gedcom_id = element.get_pointer()
                
                # Extract names using the library's built-in methods
                (first, last) = element.get_name()
                
                # Save to database (using update_or_create to avoid duplicates if run twice)
                Individual.objects.update_or_create(
                    gedcom_id=gedcom_id,
                    defaults={
                        'given_name': first,
                        'surname': last,
                        # The library returns 'M', 'F', or 'U'
                        'sex': element.get_gender() or 'U' 
                    }
                )

        # STEP 2: Import Families and link them
        self.stdout.write('Importing Families...')
        for element in root_child_elements:
            if isinstance(element, FamilyElement):
                fam_id = element.get_pointer()
                
                # Get raw pointers (IDs) for husband and wife
                husb_id = self._get_first_child_value(element, 'HUSB')
                wife_id = self._get_first_child_value(element, 'WIFE')

                # Fetch the Django Individual objects
                husband = Individual.objects.filter(gedcom_id=husb_id).first() if husb_id else None
                wife = Individual.objects.filter(gedcom_id=wife_id).first() if wife_id else None

                family, created = Family.objects.update_or_create(
                    gedcom_id=fam_id,
                    defaults={
                        'husband': husband,
                        'wife': wife
                    }
                )

                # Link Children
                child_elements = element.get_child_elements()
                for child_elem in child_elements:
                    if child_elem.get_tag() == 'CHIL':
                        child_id = child_elem.get_value()
                        child_obj = Individual.objects.filter(gedcom_id=child_id).first()
                        
                        if child_obj:
                            ChildFamilyLink.objects.update_or_create(
                                child=child_obj,
                                family=family,
                                defaults={'relationship_type': 'B'} # Defaulting to biological
                            )

        self.stdout.write(self.style.SUCCESS('Successfully imported GEDCOM data!'))

    def _get_first_child_value(self, element, tag):
        """Helper to quickly grab a child tag's value (like HUSB or WIFE pointers)"""
        for child in element.get_child_elements():
            if child.get_tag() == tag:
                return child.get_value()
        return None