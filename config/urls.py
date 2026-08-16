"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
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
from django import forms
from django.contrib import admin, messages
from django.contrib.auth import views as auth_views, login as auth_login
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.shortcuts import redirect
from django.urls import path, include, reverse_lazy
from django.views.generic import CreateView

# claude code changed: was decorated with @login_not_required to exempt it
# from LoginRequiredMiddleware — that middleware has been removed (nothing
# is gated globally anymore, see config/settings.py), so the exemption is
# no longer needed. Kept as its own class (not a bare .as_view() call)
# purely to pin the custom template_name.
class TerminalLoginView(auth_views.LoginView):
    template_name = 'registration/login.html'


# claude code changed: new — self-service account creation, per explicit
# request ("any user to see the full project but with a sign in option...
# and a welcome message after creating an account"). Extends Django's own
# UserCreationForm rather than hand-rolling password handling — it already
# runs AUTH_PASSWORD_VALIDATORS (config/settings.py) and hashes correctly
# (PBKDF2 via User.set_password()). Only real addition: a required, unique
# email — Django's default User model has the field but leaves it optional
# and non-unique.
class SignupForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")

    def clean_email(self):
        email = self.cleaned_data["email"]
        # claude code changed: new — case-insensitive uniqueness check.
        # Without this, "user@x.com" and "USER@x.com" would silently
        # create two distinct accounts most mail providers treat as the
        # same inbox.
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email


# claude code changed: new — signup view. Auto-logs the new user in
# (standard UX — no reason to make someone who just proved a password re-
# enter it) and flashes a welcome message via django.contrib.messages
# (already fully wired: in INSTALLED_APPS, MIDDLEWARE, and the template
# context processors — just never rendered anywhere until now, see
# templates/base.html's new {% if messages %} block).
class SignupView(CreateView):
    form_class = SignupForm
    template_name = 'registration/signup.html'
    success_url = reverse_lazy('dashboard')

    def form_valid(self, form):
        self.object = form.save()
        auth_login(self.request, self.object)
        messages.success(
            self.request,
            f"Welcome, {self.object.username}! Your account has been created.",
        )
        return redirect(self.get_success_url())


urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', TerminalLoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('signup/', SignupView.as_view(), name='signup'),
    path('', include('bot.urls')),
]
