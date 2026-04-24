from django import forms
from .models import Individual, Family

class IndividualForm(forms.ModelForm):
    class Meta:
        model = Individual
        # Decide which fields the user is allowed to edit
        fields = ['given_name', 'surname', 'name_prefix', 'name_suffix', 'sex', 'notes']
        
        # Optional: Add CSS classes or custom widgets to make the form look nicer
        widgets = {
            'notes': forms.Textarea(attrs={'rows': 4, 'cols': 40}),
        }

class FamilyForm(forms.ModelForm):
    class Meta:
        model = Family
        fields = ['husband', 'wife', 'notes']
        