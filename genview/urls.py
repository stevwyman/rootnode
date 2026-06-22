from django.contrib.auth import views as auth_views
from django.urls import path
from . import views

app_name = "genview"

urlpatterns = [
    # ----------------------------------------------
    # --- Trees 
    # ----------------------------------------------
    
    # 1. The Dashboard: Lists all trees the user has access to
    path("", views.TreeListView.as_view(), name="tree-list"),
    # 2. import trees
    path('gedcom/import/', views.GedcomImportView.as_view(), name='gedcom-import'),
    # 3. Tree-Specific Views:
    path('tree/<int:tree_id>/search/', views.GlobalSearchView.as_view(), name='global-search'),
    path('tree/<int:tree_id>/delete/', views.TreeDeleteView.as_view(), name='tree-delete'),
    # 4. match users to trees and enable/disable public flag 
    path('tree/<int:tree_id>/memberships/', views.TreeMembershipManageView.as_view(), name='manage-memberships'),

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
    path(
        'tree/<int:tree_id>/people/<int:person_pk>/add-existing-media/', 
        views.IndividualAddExistingMediaView.as_view(), 
        name='add-existing-media-to-person'
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
    path(
        'tree/<int:tree_id>/family/<int:family_pk>/add-existing-media/', 
        views.FamilyAddExistingMediaView.as_view(), 
        name='add-existing-media-to-family'
    ),
    # ----------------------------------------------
    # ---- Child-Family-Link 
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
    path('tree/<int:tree_id>/media/bulk-upload/', views.BulkMediaUploadView.as_view(), name='bulk-media-upload'),
    path(
        "tree/<int:tree_id>/media/toggle-category/",
        views.ToggleMediaCategoryView.as_view(),
        name="media-toggle-category",
    ),

    
    # ----------------------------------------------
    # ---- event management 
    # ----------------------------------------------
    path('tree/<int:tree_id>/events/', views.EventListView.as_view(), name='event-list'),
    path('tree/<int:tree_id>/event/<int:pk>/', views.EventDetailView.as_view(), name='event-detail'),
    path(
        "tree/<int:tree_id>/event/<int:pk>/edit/",
        views.EventUpdateView.as_view(),
        name="event-edit",
    ),
    path(
        "tree/<int:tree_id>/event/<int:pk>/delete/",
        views.EventDeleteView.as_view(),
        name="event-delete",
    ),
    path(
        'tree/<int:tree_id>/event/add/person/', 
        views.EventCreateView.as_view(), {'target_type': 'individual'}, 
        name='event-create-person'
    ),
    path(
        'tree/<int:tree_id>/event/add/family/', 
        views.EventCreateView.as_view(), {'target_type': 'family'}, 
        name='event-create-family'
    ),
    path(
        'tree/<int:tree_id>/event/<int:event_pk>/media/add/', 
         views.MediaObjectCreateView.as_view(), 
         name='media-create-event'
    ),
    path(
        'tree/<int:tree_id>/event/<int:event_id>/add-existing-media/', 
        views.AddExistingMediaToEventView.as_view(), 
        name='add-existing-media-to-event',
    ),

    # EventType Administration
    path('event-types/', views.EventTypeListView.as_view(), name='eventtype-list'),
    path('event-types/add/', views.EventTypeCreateView.as_view(), name='eventtype-add'),
    path('event-types/<int:pk>/edit/', views.EventTypeUpdateView.as_view(), name='eventtype-edit'),
    path('event-types/<int:pk>/delete/', views.EventTypeDeleteView.as_view(), name='eventtype-delete'),
    path('event-types/<int:pk>/toggle/', views.toggle_eventtype_visibility, name='eventtype-toggle'),

    path('tree/<int:tree_id>/sources/', views.SourceListView.as_view(), name='source-list'),
    path('tree/<int:tree_id>/sources/add/', views.SourceCreateView.as_view(), name='source-create'),
    path('tree/<int:tree_id>/sources/<int:pk>/', views.SourceDetailView.as_view(), name='source-detail'),
    path('tree/<int:tree_id>/sources/<int:pk>/edit/', views.SourceUpdateView.as_view(), name='source-edit'),
    path('tree/<int:tree_id>/sources/<int:pk>/delete/', views.SourceDeleteView.as_view(), name='source-delete'),
    path('tree/<int:tree_id>/sources/<int:source_pk>/media/add/', views.MediaObjectCreateView.as_view(), name='media-create-source'),
    path(
        'tree/<int:tree_id>/source/<int:source_id>/add-existing-media/', 
        views.AddExistingMediaToSourceView.as_view(), 
        name='add-existing-media-to-source'
    ),

    # Place Management
    path('tree/<int:tree_id>/places/', views.PlaceListView.as_view(), name='place-list'),
    path('tree/<int:tree_id>/places/add/', views.PlaceCreateView.as_view(), name='place-create'),
    path('tree/<int:tree_id>/places/<int:pk>/', views.PlaceDetailView.as_view(), name='place-detail'),
    path('tree/<int:tree_id>/places/<int:pk>/edit/', views.PlaceUpdateView.as_view(), name='place-edit'),
    path('tree/<int:tree_id>/places/<int:pk>/delete/', views.PlaceDeleteView.as_view(), name='place-delete'),

    # API for drop-down search
    path('tree/<int:tree_id>/api/search/individuals/', views.IndividualSearchAPIView.as_view(), name='api-search-individuals'),
    path('tree/<int:tree_id>/api/search/sources/', views.SourceSearchAPIView.as_view(), name='api-search-sources'),
    path('tree/<int:tree_id>/api/search/places/', views.PlaceSearchAPIView.as_view(), name='api-search-places'),
    path('tree/<int:tree_id>/api/search/families/', views.FamilySearchAPIView.as_view(), name='api-search-families'),
    path('tree/<int:tree_id>/api/search/events/', views.EventSearchAPIView.as_view(), name='api-search-events'),
    path('tree/<int:tree_id>/api/search/media/', views.MediaSearchAPIView.as_view(), name='api-search-media'),
    path('api/search/users/', views.UserSearchAPIView.as_view(), name='api-search-users'),

    # ----------------------------------------------
    # --- User Management
    # ----------------------------------------------

    path('accounts/register/', views.RegisterView.as_view(), name='register'),
    path('admin/users/', views.UserManagementListView.as_view(), name='user-management-list'),
    path('admin/users/<int:user_id>/<str:action>/', views.UserManagementActionView.as_view(), name='user-management-action'),
    
    # ----------------------------------------------
    # --- ADMIN
    # ----------------------------------------------

    # Logout
    path('logout/', auth_views.LogoutView.as_view(),name='logout'),

    # Registrierung
    path('register/', views.RegisterView.as_view(), name='register'),
]
