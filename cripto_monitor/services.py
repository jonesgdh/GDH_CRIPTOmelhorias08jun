import requests
from datetime import datetime
from decimal import Decimal
from django.utils import timezone

from .models import CryptoAsset, PriceHistory

# URL base da API pública usada para consultar cotações.
COINGECKO_API_BASE = 'https://api.coingecko.com/api/v3'

# Moedas que o GDH Cripto monitora por padrão.
# coingecko_id precisa bater com o identificador oficial da CoinGecko.
SUPPORTED_ASSETS = [
    {'name': 'Bitcoin', 'symbol': 'BTC', 'coingecko_id': 'bitcoin'},
    {'name': 'Ethereum', 'symbol': 'ETH', 'coingecko_id': 'ethereum'},
    {'name': 'Solana', 'symbol': 'SOL', 'coingecko_id': 'solana'},
    {'name': 'Monero', 'symbol': 'XMR', 'coingecko_id': 'monero'},
    {'name': 'Bitcoin Cash', 'symbol': 'BCH', 'coingecko_id': 'bitcoin-cash'},
    {'name': 'BNB', 'symbol': 'BNB', 'coingecko_id': 'binancecoin'},
    {'name': 'XRP', 'symbol': 'XRP', 'coingecko_id': 'ripple'},
]


def request_coingecko(endpoint, params=None):
    # Wrapper central das chamadas HTTP para padronizar URL, timeout e tratamento de erro.
    url = f'{COINGECKO_API_BASE}{endpoint}'
    response = requests.get(url, params=params or {}, timeout=12)
    response.raise_for_status()
    return response.json()


def ensure_supported_assets():
    # Garante que todas as moedas suportadas existam no banco sem regravar tudo a cada acesso.
    assets = []
    asset_ids = [asset_data['coingecko_id'] for asset_data in SUPPORTED_ASSETS]
    existing_assets = CryptoAsset.objects.in_bulk(asset_ids, field_name='coingecko_id')

    for asset_data in SUPPORTED_ASSETS:
        asset = existing_assets.get(asset_data['coingecko_id'])
        if asset is None:
            # Cria somente moedas ainda ausentes no banco.
            asset = CryptoAsset.objects.create(
                coingecko_id=asset_data['coingecko_id'],
                name=asset_data['name'],
                symbol=asset_data['symbol'],
            )
        elif asset.name != asset_data['name'] or asset.symbol != asset_data['symbol']:
            # Atualiza nome/símbolo apenas quando a definição local mudou.
            asset.name = asset_data['name']
            asset.symbol = asset_data['symbol']
            asset.save(update_fields=['name', 'symbol'])
        assets.append(asset)
    return assets


def fetch_current_prices(vs_currency='brl'):
    # Consulta preços atuais em BRL e USD para todas as moedas suportadas em uma chamada.
    ids = ','.join([asset['coingecko_id'] for asset in SUPPORTED_ASSETS])
    params = {
        'ids': ids,
        'vs_currencies': 'brl,usd',
        'include_24hr_change': 'true',
    }
    data = request_coingecko('/simple/price', params=params)
    return data


def fetch_asset_historical_price(asset_id, buy_date, vs_currency='brl'):
    # Busca o preço de uma moeda em uma data passada para criar simulações realistas.
    formatted = buy_date.strftime('%d-%m-%Y')
    try:
        data = request_coingecko(f'/coins/{asset_id}/history', params={'date': formatted, 'localization': 'false'})
        market = data.get('market_data', {})
        prices = market.get('current_price', {})
        raw_price = prices.get(vs_currency)
        if raw_price is not None:
            # Decimal evita imprecisão de float em valores financeiros.
            return Decimal(str(raw_price))
    except requests.HTTPError:
        # A view decide o fallback quando a API não entrega preço histórico.
        return None
    return None


def update_price_history_for_assets(vs_currency='brl'):
    # Atualiza o preço atual das moedas e grava um ponto novo no histórico.
    ensure_supported_assets()
    prices = fetch_current_prices(vs_currency=vs_currency)
    updated = 0
    for asset in CryptoAsset.objects.filter(active=True):
        asset_data = prices.get(asset.coingecko_id)
        if not asset_data:
            continue
        # Os valores da API chegam como números simples; convertemos para Decimal antes de salvar.
        current_brl = Decimal(str(asset_data.get('brl', 0)))
        current_usd = Decimal(str(asset_data.get('usd', 0)))
        change = asset_data.get('brl_24h_change')
        asset.last_price_brl = current_brl
        asset.last_price_usd = current_usd
        asset.last_change_percentage = Decimal(str(change)) if change is not None else None
        asset.last_price_updated = timezone.now()
        asset.save(update_fields=['last_price_brl', 'last_price_usd', 'last_change_percentage', 'last_price_updated'])
        # Cada atualização também vira histórico para ranking e gráfico por período.
        PriceHistory.objects.create(
            asset=asset,
            price_brl=current_brl,
            price_usd=current_usd,
            recorded_at=timezone.now(),
            source='coingecko',
        )
        updated += 1
    return updated


def load_asset_history(asset, max_items=30, since=None):
    # Carrega histórico em ordem cronológica, útil para gráficos e relatórios.
    history = PriceHistory.objects.filter(asset=asset)
    if since is not None:
        history = history.filter(recorded_at__gte=since)
    history = history.order_by('-recorded_at')[:max_items]
    return list(reversed(history))
