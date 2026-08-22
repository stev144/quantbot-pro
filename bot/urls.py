from django.urls import path, include

from bot.views.dashboard import (
    dashboard, live_data, portfolio_backtest_view, live_regime_monitor, walk_forward_view,
)
from bot.views.market_intelligence import market_intelligence
from bot.views.research_lab import research_lab
from bot.views.feature_intelligence import (
    feature_leaderboard, feature_detail, feature_comparison, feature_correlation,
)
from bot.views.strategy_research import strategy_research
from bot.views.strategy_pipeline import strategy_pipeline   # claude code changed: new — Phase 3 (UI pipeline view)
from bot.views.pairs_performance import pairs_performance   # claude code changed: new — Pairs Performance page
from bot.views.backtesting_view import backtesting
from bot.views.robustness_analysis import robustness_analysis
from bot.views.research_archive import rejected_research, research_archive
from bot.views.portfolio_risk import portfolio_risk
from bot.views.execution_monitor import execution_monitor
from bot.views.venue_comparison import venue_comparison   # claude code changed: new — Venue Comparison page
from bot.views.system_health import system_health

# claude code changed: regrouped into the RESEARCH -> MEASUREMENT ->
# VALIDATION -> RISK -> EXECUTION clusters the platform redesign is
# meant to communicate (see the implementation plan) — URL grouping
# doubles as free documentation of that flow for anyone reading this
# file, and mirrors the same 4 clusters templates/partials/_topbar.html
# renders as nav groups.
urlpatterns = [
    # ---- EXECUTIVE ----
    path('', dashboard, name='dashboard'),

    # ---- MARKET ----
    path('market/', market_intelligence, name='market_intelligence'),

    # ---- ACADEMY ----
    # claude code changed: new — one include() line, per the approved
    # Academy architecture report, rather than importing every academy
    # view individually into this file the way every other section does.
    # Keeps this file's flat route list from doubling in size as the
    # curriculum grows past its first course.
    path('academy/', include('bot.academy.urls')),

    # ---- RESEARCH LAB (MVP) ----
    # claude code changed: new — same one-include-line pattern as Academy
    # above. Deliberately 'research-lab/' (hyphen), distinct from the
    # existing 'research/' path below (bot/views/research_lab.py's
    # dashboard panel, a different, pre-existing feature with the same
    # short name) — no route or name collision with it.
    path('research-lab/', include('bot.research_lab.urls')),

    # ---- RESEARCH ----
    path('research/', research_lab, name='research_lab'),
    # compare/ and correlation/ must be registered before
    # <str:feature_name>/ so they aren't swallowed by it.
    path('research/features/', feature_leaderboard, name='feature_leaderboard'),
    path('research/features/compare/', feature_comparison, name='feature_comparison'),
    path('research/features/correlation/', feature_correlation, name='feature_correlation'),
    path('research/features/<str:feature_name>/', feature_detail, name='feature_detail'),
    path('research/strategies/', strategy_research, name='strategy_research'),
    path('research/pipeline/', strategy_pipeline, name='strategy_pipeline'),   # claude code changed: new — Phase 3 (UI pipeline view)
    path('research/pairs/', pairs_performance, name='pairs_performance'),   # claude code changed: new — Pairs Performance page
    path('research/backtests/', backtesting, name='backtesting'),
    path('research/robustness/', robustness_analysis, name='robustness_analysis'),
    path('research/rejected/', rejected_research, name='rejected_research'),
    path('research/archive/', research_archive, name='research_archive'),

    # ---- RISK & EXECUTION ----
    path('portfolio/', portfolio_risk, name='portfolio_risk'),
    path('execution/', execution_monitor, name='execution_monitor'),
    path('execution/venues/', venue_comparison, name='venue_comparison'),   # claude code changed: new — Venue Comparison page

    # ---- SYSTEM ----
    path('system/health/', system_health, name='system_health'),

    # ---- JSON APIs ----
    path('api/portfolio_backtester/', portfolio_backtest_view, name='portfolio_backtester'),
    path('api/live-data/', live_data, name='live_data'),
    path('api/regime-monitor/', live_regime_monitor, name='regime_monitor'),
    path('api/walk-forward/', walk_forward_view, name='walk_forward'),
]
