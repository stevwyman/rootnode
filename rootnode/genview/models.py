from django.db import models

class Source(models.Model):
    """
    Represents a GEDCOM Source (SOUR) or Repository (REPO).
    Where did the information come from?
    """
    gedcom_id = models.CharField(max_length=20, blank=True, null=True, help_text="e.g., @S1@")
    title = models.CharField(max_length=255)
    author = models.CharField(max_length=255, blank=True)
    publication_facts = models.CharField(max_length=255, blank=True)
    text = models.TextField(blank=True)

    def __str__(self):
        return self.title

class Individual(models.Model):
    """
    Represents a GEDCOM Individual (INDI).
    """
    SEX_CHOICES = [
        ('M', 'Male'),
        ('F', 'Female'),
        ('U', 'Unknown'),
    ]

    gedcom_id = models.CharField(max_length=20, unique=True, help_text="e.g., @I1@")
    given_name = models.CharField(max_length=150, blank=True)
    surname = models.CharField(max_length=150, blank=True)
    name_prefix = models.CharField(max_length=50, blank=True, help_text="e.g., Dr., Sir")
    name_suffix = models.CharField(max_length=50, blank=True, help_text="e.g., Jr., III")
    
    sex = models.CharField(max_length=1, choices=SEX_CHOICES, default='U')
    
    # Text notes associated with the individual
    notes = models.TextField(blank=True)
    sources = models.ManyToManyField(Source, blank=True, related_name='individuals')

    class Meta:
        ordering = ['surname', 'given_name']

    def __str__(self):
        return f"{self.given_name} {self.surname} ({self.gedcom_id})"

class Family(models.Model):
    """
    Represents a GEDCOM Family (FAM). 
    Families link husbands, wives, and children.
    """
    gedcom_id = models.CharField(max_length=20, unique=True, help_text="e.g., @F1@")
    
    # In GEDCOM, families usually have one husband and one wife, though both are optional
    husband = models.ForeignKey(
        Individual, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='families_as_husband'
    )
    wife = models.ForeignKey(
        Individual, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='families_as_wife'
    )

    notes = models.TextField(blank=True)
    sources = models.ManyToManyField(Source, blank=True, related_name='families')

    class Meta:
        verbose_name_plural = "Families"

    def __str__(self):
        husb_name = self.husband.surname if self.husband else "Unknown"
        wife_name = self.wife.surname if self.wife else "Unknown"
        return f"Family: {husb_name} / {wife_name} ({self.gedcom_id})"

class ChildFamilyLink(models.Model):
    """
    Explicit through-model to handle children (CHIL / FAMC).
    An individual can belong to multiple families as a child (e.g., biological, adopted).
    """
    RELATIONSHIP_CHOICES = [
        ('B', 'Biological'),
        ('A', 'Adopted'),
        ('F', 'Foster'),
        ('S', 'Step'),
        ('U', 'Unknown'),
    ]

    child = models.ForeignKey(Individual, on_delete=models.CASCADE, related_name='parental_families')
    family = models.ForeignKey(Family, on_delete=models.CASCADE, related_name='children')
    relationship_type = models.CharField(max_length=1, choices=RELATIONSHIP_CHOICES, default='B')

    class Meta:
        unique_together = ('child', 'family')

    def __str__(self):
        return f"{self.child} -> {self.family}"

class Event(models.Model):
    """
    Represents GEDCOM Events and Facts (BIRT, DEAT, MARR, DIV, etc.).
    Events can be tied to an Individual OR a Family.
    """
    EVENT_TYPES = [
        # Individual Events
        ('BIRT', 'Birth'),
        ('CHR', 'Christening'),
        ('DEAT', 'Death'),
        ('BURI', 'Burial'),
        ('RELI', 'Religion'),
        ('OCCU', 'Occupation'),
        # Family Events
        ('MARR', 'Marriage'),
        ('DIV', 'Divorce'),
        ('ENGA', 'Engagement'),
    ]

    event_type = models.CharField(max_length=10, choices=EVENT_TYPES)
    
    # An event belongs to EITHER an individual or a family, usually not both.
    individual = models.ForeignKey(Individual, on_delete=models.CASCADE, null=True, blank=True, related_name='events')
    family = models.ForeignKey(Family, on_delete=models.CASCADE, null=True, blank=True, related_name='events')
    
    # Dates: Store the raw string, and a parsed version for querying
    raw_date = models.CharField(max_length=100, blank=True, help_text="Raw GEDCOM date string e.g. 'ABT 1900'")
    parsed_date = models.DateField(null=True, blank=True, help_text="Parsed date for sorting/filtering")
    
    place = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    
    sources = models.ManyToManyField(Source, blank=True, related_name='events')

    def __str__(self):
        target = self.individual or self.family
        return f"{self.get_event_type_display()} for {target}"
    
class MediaObject(models.Model):
    """
    Represents a GEDCOM Multimedia Object (OBJE).
    Can be a photo, a scanned document (PDF), or a link.
    """
    gedcom_id = models.CharField(max_length=20, blank=True, null=True)
    title = models.CharField(max_length=255, blank=True)
    
    # upload_to will automatically create a 'gedcom_media' folder inside your MEDIA_ROOT
    file = models.FileField(upload_to='gedcom_media/')
    description = models.TextField(blank=True)

    # Relationships: A photo/doc can belong to multiple people, families, or sources
    individuals = models.ManyToManyField(Individual, blank=True, related_name='media_objects')
    families = models.ManyToManyField(Family, blank=True, related_name='media_objects')
    sources = models.ManyToManyField(Source, blank=True, related_name='media_objects')

    def __str__(self):
        return self.title or f"Media {self.id}"

    @property
    def is_image(self):
        """Helper property to easily check if the file is an image in the template"""
        if self.file:
            return self.file.name.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp'))
        return False
    