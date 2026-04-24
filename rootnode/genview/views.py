from django.views.generic import ListView, DetailView
from django.views.generic.edit import UpdateView
from django.urls import reverse_lazy
from .models import Individual, Family
from .forms import IndividualForm, FamilyForm

class IndividualListView(ListView):
    model = Individual
    template_name = 'genview/individual_list.html'
    context_object_name = 'individuals'
    paginate_by = 50  # Helpful if you have thousands of records

class IndividualDetailView(DetailView):
    model = Individual
    template_name = 'genview/individual_detail.html'
    context_object_name = 'individual'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        person = self.object
        
        # Start the Mermaid graph definition (TD = Top Down)
        graph = ["graph TD;"]
        
        # 1. Parents to this Person
        for link in person.parental_families.all():
            fam = link.family
            if fam.husband:
                # Syntax: NodeID["Display Text"] --> TargetNodeID["Display Text"];
                graph.append(f'  P_{fam.husband.pk}["{fam.husband.given_name} {fam.husband.surname}"] --> I_{person.pk}["{person.given_name} {person.surname}"];')
            if fam.wife:
                graph.append(f'  P_{fam.wife.pk}["{fam.wife.given_name} {fam.wife.surname}"] --> I_{person.pk}["{person.given_name} {person.surname}"];')

        # 2. This Person to their Children
        # Check families where they are the husband
        for fam in person.families_as_husband.all():
            for child_link in fam.children.all():
                child = child_link.child
                graph.append(f'  I_{person.pk}["{person.given_name} {person.surname}"] --> C_{child.pk}["{child.given_name} {child.surname}"];')
                # Optional: Add the wife/mother to the child as well
                if fam.wife:
                     graph.append(f'  S_{fam.wife.pk}["{fam.wife.given_name} {fam.wife.surname}"] --> C_{child.pk}["{child.given_name} {child.surname}"];')

        # Check families where they are the wife
        for fam in person.families_as_wife.all():
            for child_link in fam.children.all():
                child = child_link.child
                graph.append(f'  I_{person.pk}["{person.given_name} {person.surname}"] --> C_{child.pk}["{child.given_name} {child.surname}"];')
                # Optional: Add the husband/father to the child as well
                if fam.husband:
                     graph.append(f'  S_{fam.husband.pk}["{fam.husband.given_name} {fam.husband.surname}"] --> C_{child.pk}["{child.given_name} {child.surname}"];')

        # If there are no connections, add a fallback so Mermaid doesn't crash
        if len(graph) == 1:
            graph.append(f'  I_{person.pk}["{person.given_name} {person.surname}"];')

        # Join the list into a single string with line breaks
        context['mermaid_graph'] = "\n".join(graph)
        return context

class IndividualUpdateView(UpdateView):
    model = Individual
    form_class = IndividualForm
    template_name = 'genview/individual_form.html'
    
    # Where to redirect the user after a successful save
    def get_success_url(self):
        return reverse_lazy('genview:individual_detail', kwargs={'pk': self.object.pk})


class FamilyDetailView(DetailView):
    model = Family
    template_name = 'genview/family_detail.html'
    context_object_name = 'family'

class FamilyUpdateView(UpdateView):
    model = Family
    form_class = FamilyForm
    template_name = 'genview/family_form.html'

    def get_success_url(self):
        return reverse_lazy('genview:family_detail', kwargs={'pk': self.object.pk})
    
