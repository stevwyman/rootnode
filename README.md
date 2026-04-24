# rootnode
simple gedcom management

## models.py

Key Design Decisions

The Event Model: Instead of hardcoding birth_date and death_date onto the Individual model, this schema uses a separate Event table. This is fully compliant with how GEDCOM works. A person can technically have multiple birth records (if sources disagree), and this schema supports that natively.

The ChildFamilyLink Model: In a simple world, a child just has a ForeignKey to a family. However, in genealogy, a person might have a biological family, an adoptive family, and a foster family. The explicitly defined through-model handles these edge cases perfectly.

Dual Foreign Keys on Events: The Event model has optional keys to both Individual and Family. A birth is tied to an Individual, but a marriage is tied to a Family.

Are you planning to write your own GEDCOM parser to import the text files into the database, or are you looking to use an existing Python library (like python-gedcom) to handle the extraction?
