from django.contrib import admin

from .models import CryptoAsset, PriceAlert, PriceHistory, SimulatedTrade, Simulation, SimulationAlert


@admin.register(CryptoAsset)
class CryptoAssetAdmin(admin.ModelAdmin):
    list_display = ('name', 'symbol', 'coingecko_id', 'last_price_brl', 'last_change_percentage', 'last_price_updated', 'active')
    list_filter = ('active',)
    search_fields = ('name', 'symbol', 'coingecko_id')


@admin.register(PriceHistory)
class PriceHistoryAdmin(admin.ModelAdmin):
    list_display = ('asset', 'price_brl', 'price_usd', 'recorded_at', 'source')
    list_filter = ('source', 'asset')
    search_fields = ('asset__name', 'asset__symbol')


@admin.register(Simulation)
class SimulationAdmin(admin.ModelAdmin):
    list_display = ('asset', 'invested_amount_brl', 'buy_date', 'purchased_price_brl', 'crypto_amount', 'created_at')
    list_filter = ('asset', 'buy_date')
    search_fields = ('asset__name', 'asset__symbol')


@admin.register(SimulatedTrade)
class SimulatedTradeAdmin(admin.ModelAdmin):
    list_display = ('simulation', 'trade_type', 'amount_brl', 'crypto_amount', 'price_brl', 'trade_date')
    list_filter = ('trade_type', 'trade_date')
    search_fields = ('simulation__asset__name', 'simulation__asset__symbol')


@admin.register(PriceAlert)
class PriceAlertAdmin(admin.ModelAdmin):
    list_display = ('asset', 'alert_type', 'threshold_brl', 'active', 'triggered', 'triggered_at', 'created_at')
    list_filter = ('active', 'triggered', 'alert_type', 'asset')
    search_fields = ('asset__name', 'asset__symbol')


@admin.register(SimulationAlert)
class SimulationAlertAdmin(admin.ModelAdmin):
    list_display = ('simulation', 'alert_type', 'threshold_percentage', 'active', 'triggered', 'triggered_at', 'created_at')
    list_filter = ('active', 'triggered', 'alert_type')
    search_fields = ('simulation__asset__name', 'simulation__asset__symbol')
