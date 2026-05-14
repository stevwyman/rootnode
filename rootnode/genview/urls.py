from django.urls import path
from . import views

app_name = 'genview'

urlpatterns = [
    # 1. The Dashboard: Lists all trees the user has access to
    path('', views.TreeListView.as_view(), name='tree-list'),
    # 2. Tree-Specific Views:
    # --- Personen ---
    path('tree/<int:tree_id>/people', views.IndividualListView.as_view(), name='individual-list'),
    path('tree/<int:tree_id>/people/<int:pk>/', views.IndividualDetailView.as_view(), name='individual-detail'),
    path("tree/<int:tree_id>/people/add/", views.IndividualCreateView.as_view(), name="individual-add"),
    path("tree/<int:tree_id>/people/<int:pk>/edit/", views.IndividualUpdateView.as_view(), name="individual-edit"),
    path("tree/<int:tree_id>/people/<int:pk>/delete/", views.IndividualDeleteView.as_view(), name="individual-delete"),
    path("tree/<int:tree_id>/people/search/", views.IndividualSearchView.as_view(), name="individual-search"),
    path("tree/<int:tree_id>/people/search/ajax/", views.IndividualSearchAjaxView.as_view(), name="individual-search-ajax"),
    # Bild‑Hochladen – die Person‑ID wird über die URL übergeben
    path("tree/<int:tree_id>/people/<int:person_pk>/media/add/", views.MediaObjectCreateView.as_view(), name="media-add-for-person"),
    # --- Familien ---
    path('tree/<int:tree_id>/families', views.FamilyListView.as_view(), name="family-list"),
    path('tree/<int:tree_id>/families/<int:pk>/', views.FamilyDetailView.as_view(), name="family-detail"),
    path("tree/<int:tree_id>/families/add/", views.FamilyCreateView.as_view(), name="family-add"),
    path("tree/<int:tree_id>/families/<int:pk>/edit/", views.FamilyUpdateView.as_view(), name="family-edit"),
    path("tree/<int:tree_id>/families/<int:pk>/delete/", views.FamilyDeleteView.as_view(), name="family-delete"),
    # ---- Child‑Family‑Link -------------------------------------------
    path("tree/<int:tree_id>/links/add/", views.ChildFamilyLinkCreateView.as_view(), name="link-add"),
    path("tree/<int:tree_id>/links/<int:pk>/delete/", views.ChildFamilyLinkDeleteView.as_view(), name="link-delete"),
    # ---- Medien‑Management -------------------------------------------
    # Secure Media File Tunnel
    path('tree/<int:tree_id>/media/<int:pk>/file/', views.ProtectedMediaFileView.as_view(), name='media-file'),
    path("tree/<int:tree_id>/media/", views.MediaObjectListView.as_view(), name="media-list"),
    path("tree/<int:tree_id>/media/add/", views.MediaObjectCreateView.as_view(), name="media-add"),
    # ----- Bild‑Bearbeiten (optional, wenn du das erlauben willst) -----
    path("tree/<int:tree_id>/media/<int:pk>/edit/", views.MediaObjectUpdateView.as_view(), name="media-edit"),
    # Wir benötigen die `person_pk`, damit wir nach dem Löschen zurück zur richtigen Seite gehen:
    path('tree/<int:tree_id>/media/<int:pk>/delete/', views.MediaObjectDeleteView.as_view(), name='media-delete'),
    path("tree/<int:tree_id>/media/<int:pk>/delete/<int:person_pk>/", views.MediaObjectDeleteView.as_view(),
         name="media-delete"),
]