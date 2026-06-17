# genview/decorators.py
from django.core.exceptions import PermissionDenied
from django.contrib.auth.decorators import login_required
from functools import wraps

def superuser_required(view_func=None, *, raise_exception=True, redirect_to=None):
    """
    Decorator, der nur Superusern Zugriff gewährt.
    """
    def decorator(func):
        @login_required
        @wraps(func)
        def _wrapped(request, *args, **kwargs):
            if request.user.is_superuser:
                return func(request, *args, **kwargs)
            if raise_exception:
                raise PermissionDenied("Nur Superuser erlaubt.")
            from django.shortcuts import redirect
            return redirect(redirect_to or "/")
        return _wrapped

    if view_func:
        return decorator(view_func)
    return decorator