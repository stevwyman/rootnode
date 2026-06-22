"""
URL configuration for rootnode project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.urls import path, include
from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from genview import views as genview_views
from two_factor.urls import urlpatterns as tf_urls

from debug_toolbar.toolbar import debug_toolbar_urls # TODO remove later

urlpatterns = [

    # 2FA-Routen (vor allen anderen geschützten Routen)
    path('', include(tf_urls)),

    path('i18n/', include('django.conf.urls.i18n')),
    # api also goes here
]

handler404 = 'genview.views.handle_404'   # 404-Handler
handler403 = 'genview.views.handle_403'   # 403-Handler

urlpatterns += i18n_patterns(

    # ------------------ Home / Startseite ------------------
    path('', genview_views.TreeListView.as_view(), name='tree-list'),
    # ------------------ App-bezogene URLs -----------------
    path('genview/', include('genview.urls'))
) 

# -------------------------------------------------
# DEBUG-Toolbar nur aktivieren, wenn DEBUG=True
if settings.DEBUG:
    urlpatterns += debug_toolbar_urls()

