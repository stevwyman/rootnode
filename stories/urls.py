from django.urls import path

from . import views

app_name = "stories"

urlpatterns = [
    path(
        "tree/<int:tree_id>/stories/",
        views.StoryIndexView.as_view(),
        name="index",
    ),
    path(
        "tree/<int:tree_id>/stories/add/",
        views.StoryCreateView.as_view(),
        name="create",
    ),
    path(
        "tree/<int:tree_id>/stories/<int:pk>/",
        views.StoryDetailView.as_view(),
        name="detail",
    ),
    path(
        "tree/<int:tree_id>/stories/<int:pk>/edit/",
        views.StoryUpdateView.as_view(),
        name="edit",
    ),
    path(
        "tree/<int:tree_id>/stories/<int:pk>/delete/",
        views.StoryDeleteView.as_view(),
        name="delete",
    ),
]