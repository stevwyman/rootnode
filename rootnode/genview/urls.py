from django.urls import path
from . import views

app_name = 'genview'

urlpatterns = [
    path('', views.IndividualListView.as_view(), name='individual_list'),
    path('person/<int:pk>/', views.IndividualDetailView.as_view(), name='individual_detail'),
    path('person/<int:pk>/edit/', views.IndividualUpdateView.as_view(), name='individual_edit'),
    path('family/<int:pk>/', views.FamilyDetailView.as_view(), name='family_detail'),
    path('family/<int:pk>/edit/', views.FamilyUpdateView.as_view(), name='family_edit'),
]