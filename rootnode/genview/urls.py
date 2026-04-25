from django.urls import path
from . import views

app_name = 'genview'

urlpatterns = [
    # --- Personen ---
    path('people', views.IndividualListView.as_view(), name='individual-list'),
    path('people/<int:pk>/', views.IndividualDetailView.as_view(), name='individual-detail'),
    path("people/add/", views.IndividualCreateView.as_view(), name="individual-add"),
    path("people/<int:pk>/edit/", views.IndividualUpdateView.as_view(), name="individual-edit"),
    path("people/<int:pk>/delete/", views.IndividualDeleteView.as_view(), name="individual-delete"),
    path("people/search/", views.IndividualSearchView.as_view(), name="individual-search"),
    path("people/search/ajax/", views.IndividualSearchAjaxView.as_view(), name="individual-search-ajax"),
    # Bild‑Hochladen – die Person‑ID wird über die URL übergeben
    path("people/<int:person_pk>/media/add/", views.MediaObjectCreateView.as_view(), name="media-add-for-person"),
    # --- Familien ---
    path('families', views.FamilyListView.as_view(), name="family-list"),
    path('families/<int:pk>/', views.FamilyDetailView.as_view(), name="family-detail"),
    path("families/add/", views.FamilyCreateView.as_view(), name="family-add"),
    path("families/<int:pk>/edit/", views.FamilyUpdateView.as_view(), name="family-edit"),
    path("families/<int:pk>/delete/", views.FamilyDeleteView.as_view(), name="family-delete"),
    # ---- Child‑Family‑Link -------------------------------------------
    path("links/add/", views.ChildFamilyLinkCreateView.as_view(), name="link-add"),
    path("links/<int:pk>/delete/", views.ChildFamilyLinkDeleteView.as_view(), name="link-delete"),
    # ---- Medien‑Management -------------------------------------------
    path("media/", views.MediaObjectListView.as_view(), name="media-list"),
    path("media/add/", views.MediaObjectCreateView.as_view(), name="media-add"),
    # ----- Bild‑Bearbeiten (optional, wenn du das erlauben willst) -----
    path("media/<int:pk>/edit/", views.MediaObjectUpdateView.as_view(), name="media-edit"),
    # Wir benötigen die `person_pk`, damit wir nach dem Löschen zurück zur richtigen Seite gehen:
    path("media/<int:pk>/delete/<int:person_pk>/", views.MediaObjectDeleteView.as_view(),
         name="media-delete"),
]