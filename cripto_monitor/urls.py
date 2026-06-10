from django.urls import path
from . import views

app_name = 'cripto_monitor'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('api/prices/', views.prices_api, name='prices_api'),
    path('comparison/', views.comparison, name='comparison'),
    path('minhas-simulacoes/', views.my_simulations, name='my_simulations'),
    path('simulations/', views.simulations, name='simulations'),
    path('simulations/<int:pk>/delete/', views.delete_simulation, name='delete_simulation'),
    path('alerts/', views.alerts, name='alerts'),
    path('alerts/<int:pk>/trigger/', views.trigger_alert, name='trigger_alert'),
    path('alerts/<int:pk>/delete/', views.delete_alert, name='delete_alert'),
    path('simulation-alerts/<int:pk>/delete/', views.delete_simulation_alert, name='delete_simulation_alert'),
]
