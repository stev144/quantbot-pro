from django.contrib import admin
from django.urls import path
from bot.views.dashboard import live_data
from bot.views.dashboard import dashboard, portfolio_backtest_view
from  bot.views.dashboard import live_regime_monitor
from bot.views.dashboard import walk_forward_view
from bot.views.research_lab import research_lab   # claude code changed: new — Research Lab route
from bot.views.feature_intelligence import (       # claude code changed: new — Feature Intelligence module
    feature_leaderboard, feature_detail, feature_comparison, feature_correlation,
)

urlpatterns = [
    path('', dashboard, name='dashboard'),
    path('research/', research_lab, name='research_lab'),   # claude code changed: new
    #path('analytics/', views.analytics, name="analytics"),
    path('api/portfolio_backtester/', portfolio_backtest_view, name='portfolio_backtester'),
    path('api/live-data/', live_data, name='live_data'),
    path('api/regime-monitor/', live_regime_monitor, name='regime_monitor'),
    path('api/walk-forward/', walk_forward_view, name='walk_forward'),

    # claude code changed: new — Feature Intelligence module. compare/ and
    # correlation/ must be registered before <str:feature_name>/ so they
    # aren't swallowed by it.
    path('research/features/', feature_leaderboard, name='feature_leaderboard'),
    path('research/features/compare/', feature_comparison, name='feature_comparison'),
    path('research/features/correlation/', feature_correlation, name='feature_correlation'),
    path('research/features/<str:feature_name>/', feature_detail, name='feature_detail'),
]

