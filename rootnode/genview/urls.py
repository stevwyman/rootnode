from django.urls import path
from . import views

app_name = "genview"

urlpatterns = [
    # 1. The Dashboard: Lists all trees the user has access to
    path("", views.TreeListView.as_view(), name="tree-list"),
    # 2. Tree-Specific Views:
    # ----------------------------------------------
    # --- persons / individuals
    # ----------------------------------------------
    path(
        "tree/<int:tree_id>/people",
        views.IndividualListView.as_view(),
        name="individual-list",
    ),
    path(
        "tree/<int:tree_id>/people/<int:pk>/",
        views.IndividualDetailView.as_view(),
        name="individual-detail",
    ),
    path(
        "tree/<int:tree_id>/people/add/",
        views.IndividualCreateView.as_view(),
        name="individual-add",
    ),
    path(
        "tree/<int:tree_id>/people/<int:pk>/edit/",
        views.IndividualUpdateView.as_view(),
        name="individual-edit",
    ),
    path(
        "tree/<int:tree_id>/people/<int:pk>/delete/",
        views.IndividualDeleteView.as_view(),
        name="individual-delete",
    ),
    path(
        "tree/<int:tree_id>/people/search/",
        views.IndividualSearchView.as_view(),
        name="individual-search",
    ),
    path(
        "tree/<int:tree_id>/people/search/ajax/",
        views.IndividualSearchAjaxView.as_view(),
        name="individual-search-ajax",
    ),
    path(
        "tree/<int:tree_id>/people/<int:person_pk>/media/add/",
        views.MediaObjectCreateView.as_view(),
        name="media-add-for-person",
    ),
    path(
        "tree/<int:tree_id>/people/<int:person_pk>/event/add/",
        views.EventCreateView.as_view(),
        name="event-add-for-person",
    ),
    # ----------------------------------------------
    # --- families
    # ----------------------------------------------
    path(
        "tree/<int:tree_id>/families",
        views.FamilyListView.as_view(),
        name="family-list",
    ),
    path(
        "tree/<int:tree_id>/families/<int:pk>/",
        views.FamilyDetailView.as_view(),
        name="family-detail",
    ),
    path(
        "tree/<int:tree_id>/families/add/",
        views.FamilyCreateView.as_view(),
        name="family-add",
    ),
    path(
        "tree/<int:tree_id>/families/<int:pk>/edit/",
        views.FamilyUpdateView.as_view(),
        name="family-edit",
    ),
    path(
        "tree/<int:tree_id>/families/<int:pk>/delete/",
        views.FamilyDeleteView.as_view(),
        name="family-delete",
    ),
    path(
        'tree/<int:tree_id>/family/<int:family_pk>/media/add/', 
        views.MediaObjectCreateView.as_view(), 
        name='media-create-family'
    ),
    path(
        'tree/<int:tree_id>/family/<int:family_pk>/event/add/', 
        views.EventCreateView.as_view(), 
        name='event-create-family'
    ),
    # ----------------------------------------------
    # ---- Child‑Family‑Link 
    # ----------------------------------------------
    path(
        "tree/<int:tree_id>/links/add/",
        views.ChildFamilyLinkCreateView.as_view(),
        name="link-add",
    ),
    path(
        "tree/<int:tree_id>/links/<int:pk>/delete/",
        views.ChildFamilyLinkDeleteView.as_view(),
        name="link-delete",
    ),
    # ----------------------------------------------
    # ---- media management 
    # ----------------------------------------------
    path(
        "tree/<int:tree_id>/media/<int:pk>/file/",
        views.ProtectedMediaFileView.as_view(),
        name="media-file",
    ),
    path(
        'tree/<int:tree_id>/media/<int:pk>/', 
        views.MediaObjectDetailView.as_view(), 
        name='media-detail'
    ),
    path(
        "tree/<int:tree_id>/media/",
        views.MediaObjectListView.as_view(),
        name="media-list",
    ),
    path(
        "tree/<int:tree_id>/media/add/",
        views.MediaObjectCreateView.as_view(),
        name="media-add",
    ),
    path(
        "tree/<int:tree_id>/media/<int:pk>/edit/",
        views.MediaObjectUpdateView.as_view(),
        name="media-edit",
    ),
    path(
        "tree/<int:tree_id>/media/<int:pk>/delete/",
        views.MediaObjectDeleteView.as_view(),
        name="media-delete",
    ),
    path(
        "tree/<int:tree_id>/media/<int:pk>/delete/<int:person_pk>/",
        views.MediaObjectDeleteView.as_view(),
        name="media-delete",
    ),
    # ----------------------------------------------
    # ---- event management 
    # ----------------------------------------------
    path('tree/<int:tree_id>/events/', views.EventListView.as_view(), name='event-list'),
    path(
        "tree/<int:tree_id>/event/<int:pk>/edit/",
        views.EventUpdateView.as_view(),
        name="even-edit",
    ),
    path(
        "tree/<int:tree_id>/event/<int:pk>/delete/",
        views.EventDeleteView.as_view(),
        name="event-delete",
    ),
    path(
        'tree/<int:tree_id>/people/<int:person_pk>/events/add/', 
        views.EventCreateView.as_view(), 
        name='event-create-person'
    ),

    path('tree/<int:tree_id>/sources/', views.SourceListView.as_view(), name='source-list'),
    path('tree/<int:tree_id>/sources/add/', views.SourceCreateView.as_view(), name='source-create'),
    path('tree/<int:tree_id>/sources/<int:pk>/', views.SourceDetailView.as_view(), name='source-detail'),
    path('tree/<int:tree_id>/sources/<int:pk>/edit/', views.SourceUpdateView.as_view(), name='source-edit'),
    path('tree/<int:tree_id>/sources/<int:pk>/delete/', views.SourceDeleteView.as_view(), name='source-delete'),
    path('tree/<int:tree_id>/sources/<int:source_pk>/media/add/', views.MediaObjectCreateView.as_view(), name='media-create-source'),

    # Place Management
    path('tree/<int:tree_id>/places/', views.PlaceListView.as_view(), name='place-list'),
    path('tree/<int:tree_id>/places/add/', views.PlaceCreateView.as_view(), name='place-create'),
    path('tree/<int:tree_id>/places/<int:pk>/', views.PlaceDetailView.as_view(), name='place-detail'),
    path('tree/<int:tree_id>/places/<int:pk>/edit/', views.PlaceUpdateView.as_view(), name='place-edit'),
    path('tree/<int:tree_id>/places/<int:pk>/delete/', views.PlaceDeleteView.as_view(), name='place-delete'),

]
