from django.urls import path
from . import views

app_name = 'cripto_monitor'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('api/prices/', views.prices_api, name='prices_api'),
    path('simulations/', views.simulations, name='simulations'),
    path('simulations/<int:pk>/delete/', views.delete_simulation, name='delete_simulation'),
    path('alerts/', views.alerts, name='alerts'),
    path('alerts/<int:pk>/trigger/', views.trigger_alert, name='trigger_alert'),
]
