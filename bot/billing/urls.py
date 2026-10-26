# claude code changed: 2026-10-26 — new file: billing routes (included at /account/ by bot/urls.py)

from django.urls import path

from bot.billing import views

urlpatterns = [
    path('billing/', views.billing, name='billing'),
    path('billing/checkout/', views.checkout, name='billing_checkout'),
    path('billing/portal/', views.portal, name='billing_portal'),
    path('billing/webhook/', views.stripe_webhook, name='stripe_webhook'),
]
