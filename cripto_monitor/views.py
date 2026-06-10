import json
from datetime import timedelta
from decimal import Decimal

import requests
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import PriceAlertForm, SignUpForm, SimulationAlertForm, SimulationForm
from .models import CryptoAsset, PriceAlert, PriceHistory, SimulatedTrade, Simulation, SimulationAlert
from .services import (
    SUPPORTED_ASSETS,
    ensure_supported_assets,
    fetch_asset_historical_price,
    update_price_history_for_assets,
)

# Intervalo mínimo para considerar uma cotação recente antes de buscar novos preços.
PRICE_REFRESH_INTERVAL = timedelta(minutes=10)

# Opções usadas no seletor do dashboard e nas abas do gráfico.
PERIOD_OPTIONS = {
    'hours': {
        'label': 'Últimas horas',
        'short_label': '6H',
        'variation_label': 'Variação últimas horas',
        'delta': timedelta(hours=6),
    },
    'day': {
        'label': 'Último dia',
        'short_label': '1D',
        'variation_label': 'Variação 24h',
        'delta': timedelta(days=1),
    },
    'week': {
        'label': 'Última semana',
        'short_label': '1W',
        'variation_label': 'Variação 7d',
        'delta': timedelta(days=7),
    },
    'month': {
        'label': 'Último mês',
        'short_label': '1M',
        'variation_label': 'Variação 30d',
        'delta': timedelta(days=30),
    },
}

COMPARISON_COLORS = [
    '#f7931a',
    '#627eea',
    '#26a17b',
    '#8f98a8',
    '#f3ba2f',
    '#14f195',
    '#2775ca',
    '#ff4f6d',
    '#c2a633',
    '#3468d1',
]


# Mantém a ordem visual das moedas igual à ordem definida em services.SUPPORTED_ASSETS.
SUPPORTED_ASSET_ORDER = {
    asset['coingecko_id']: index
    for index, asset in enumerate(SUPPORTED_ASSETS)
}


def sort_supported_assets(assets):
    # Ordena as moedas monitoradas pela ordem oficial do projeto, com nome como desempate.
    return sorted(
        assets,
        key=lambda asset: (SUPPORTED_ASSET_ORDER.get(asset.coingecko_id, len(SUPPORTED_ASSET_ORDER)), asset.name),
    )


def get_selected_period(period):
    # Protege contra valores inválidos vindos da URL.
    if period in PERIOD_OPTIONS:
        return period
    return 'day'


def prices_are_stale(now=None):
    # Se alguma moeda ativa nunca foi atualizada, a API deve buscar cotações novas.
    now = now or timezone.now()
    if CryptoAsset.objects.filter(active=True, last_price_updated__isnull=True).exists():
        return True
    newest_update = CryptoAsset.objects.filter(active=True).order_by('-last_price_updated').first()
    if newest_update is None or newest_update.last_price_updated is None:
        return True
    return newest_update.last_price_updated <= now - PRICE_REFRESH_INTERVAL


def serialize_asset_price(asset):
    # Converte Decimal/datetime em tipos simples para resposta JSON.
    return {
        'id': asset.coingecko_id,
        'name': asset.name,
        'symbol': asset.symbol,
        'price_brl': float(asset.last_price_brl) if asset.last_price_brl is not None else None,
        'price_usd': float(asset.last_price_usd) if asset.last_price_usd is not None else None,
        'change_24h': float(asset.last_change_percentage) if asset.last_change_percentage is not None else None,
        'updated_at': asset.last_price_updated.isoformat() if asset.last_price_updated else None,
    }


def prices_api(request):
    # Endpoint consumido pelo dashboard para atualizar preços sem navegar para outra página.
    ensure_supported_assets()
    force_refresh = request.GET.get('refresh') in {'1', 'true', 'yes'}
    refreshed = False
    updated_count = 0
    error = None

    if force_refresh or prices_are_stale():
        try:
            # Atualiza CryptoAsset e grava PriceHistory para cada moeda retornada pela API.
            updated_count = update_price_history_for_assets()
            refreshed = True
        except requests.RequestException as exc:
            error = str(exc)

    assets = sort_supported_assets(CryptoAsset.objects.filter(active=True))
    payload = {
        'refreshed': refreshed,
        'updated_count': updated_count,
        'refresh_interval_seconds': int(PRICE_REFRESH_INTERVAL.total_seconds()),
        'assets': [serialize_asset_price(asset) for asset in assets],
    }
    if error:
        payload['error'] = error
        return JsonResponse(payload, status=502)
    return JsonResponse(payload)


def calculate_period_change(asset, since):
    # Calcula a variação percentual entre o preço atual e o preço-base do período.
    current_price = asset.last_price_brl
    if current_price is None:
        latest_history = PriceHistory.objects.filter(asset=asset).order_by('-recorded_at').first()
        current_price = latest_history.price_brl if latest_history else None

    if current_price is None:
        return None

    # Preferimos o último preço antes do início do período para ter uma base estável.
    baseline = PriceHistory.objects.filter(asset=asset, recorded_at__lte=since).order_by('-recorded_at').first()
    if baseline is None:
        # Se não existir preço anterior, usamos o primeiro preço dentro do período.
        baseline = PriceHistory.objects.filter(asset=asset, recorded_at__gte=since).order_by('recorded_at').first()

    if baseline is None or baseline.price_brl == 0:
        # Fallback para a variação 24h vinda da API quando falta histórico local suficiente.
        return asset.last_change_percentage

    return (current_price / baseline.price_brl - 1) * Decimal('100')


def build_period_chart_points(asset, since):
    # Monta a série temporal usada no gráfico para uma moeda e período.
    points = []
    baseline = PriceHistory.objects.filter(asset=asset, recorded_at__lte=since).order_by('-recorded_at').first()
    if baseline is not None:
        # Inclui um ponto-base anterior ao período para a linha começar com referência real.
        points.append((baseline.recorded_at, baseline.price_brl))

    # Limita a quantidade de pontos para manter o SVG leve.
    history = PriceHistory.objects.filter(asset=asset, recorded_at__gte=since).order_by('recorded_at')[:30]
    points.extend((item.recorded_at, item.price_brl) for item in history)

    if asset.last_price_brl is not None:
        current_at = asset.last_price_updated or timezone.now()
        # Garante que o último preço conhecido apareça mesmo sem novo registro no histórico.
        if not points or points[-1][0] != current_at or points[-1][1] != asset.last_price_brl:
            points.append((current_at, asset.last_price_brl))

    if not points:
        points.append((timezone.now(), Decimal('0')))

    return points


def build_comparison_chart_data(assets, since):
    # Normaliza cada moeda para 0% no primeiro ponto do período, permitindo comparar preços diferentes.
    datasets = []
    longest_labels = []
    rows = []

    for index, asset in enumerate(assets):
        points = build_period_chart_points(asset, since)
        valid_points = [
            (recorded_at, price)
            for recorded_at, price in points
            if price is not None and price > 0
        ]
        if not valid_points:
            continue

        base_price = valid_points[0][1]
        values = [
            float((price / base_price - 1) * Decimal('100'))
            for recorded_at, price in valid_points
        ]
        labels = [recorded_at.strftime('%d/%m %H:%M') for recorded_at, price in valid_points]
        color = COMPARISON_COLORS[index % len(COMPARISON_COLORS)]
        final_change = Decimal(str(values[-1]))

        if len(labels) > len(longest_labels):
            longest_labels = labels

        datasets.append({
            'label': asset.symbol,
            'assetName': asset.name,
            'data': values,
            'borderColor': color,
            'backgroundColor': color,
            'pointRadius': 2,
            'pointHoverRadius': 5,
            'borderWidth': 2,
            'tension': 0.28,
        })
        rows.append({
            'asset': asset,
            'color': color,
            'final_change': final_change,
            'is_positive': final_change >= 0,
        })

    rows.sort(key=lambda row: row['final_change'], reverse=True)
    return {
        'labels': longest_labels,
        'datasets': datasets,
        'rows': rows,
    }


def build_candles(points):
    # Transforma pontos de preço em velas simples: abertura = ponto anterior, fechamento = ponto atual.
    if not points:
        return []

    candles = []
    if len(points) == 1:
        recorded_at, price = points[0]
        candles.append({
            'label': recorded_at.strftime('%d/%m/%Y %H:%M'),
            'compact_label': recorded_at.strftime('%d%H%M'),
            'open': float(price),
            'high': float(price),
            'low': float(price),
            'close': float(price),
        })
        return candles

    for previous, current in zip(points, points[1:]):
        recorded_at, close_price = current
        open_price = previous[1]
        candles.append({
            'label': recorded_at.strftime('%d/%m/%Y %H:%M'),
            'compact_label': recorded_at.strftime('%d%H%M'),
            'open': float(open_price),
            'high': float(max(open_price, close_price)),
            'low': float(min(open_price, close_price)),
            'close': float(close_price),
        })

    return candles


def build_candlestick_shapes(candles, width=520, height=240, padding=24):
    # Converte valores de vela em coordenadas SVG e posições de tooltip.
    if not candles:
        return []

    min_price = min(candle['low'] for candle in candles)
    max_price = max(candle['high'] for candle in candles)
    price_range = max_price - min_price
    drawable_width = width - padding * 2
    drawable_height = height - padding * 2
    step = drawable_width / max(len(candles), 1)
    candle_width = min(24, max(8, step * 0.55))
    shapes = []

    def y_for(price):
        # Mapeia preço para coordenada Y invertida, já que no SVG o topo é zero.
        if price_range == 0:
            return height / 2
        return padding + drawable_height - ((price - min_price) / price_range * drawable_height)

    for index, candle in enumerate(candles):
        x = padding + step * index + step / 2
        open_y = y_for(candle['open'])
        close_y = y_for(candle['close'])
        high_y = y_for(candle['high'])
        low_y = y_for(candle['low'])
        body_y = min(open_y, close_y)
        body_height = max(abs(close_y - open_y), 3)
        is_positive = candle['close'] >= candle['open']
        tooltip_width = 142
        tooltip_x = x + 14
        if tooltip_x + tooltip_width > width - 8:
            tooltip_x = x - tooltip_width - 14
        tooltip_x = max(8, tooltip_x)
        tooltip_y = max(8, min(high_y - 18, height - padding - 106))
        shapes.append({
            **candle,
            'x': f'{x:.2f}',
            'wick_top_y': f'{high_y:.2f}',
            'wick_bottom_y': f'{low_y:.2f}',
            'body_x': f'{(x - candle_width / 2):.2f}',
            'body_y': f'{body_y:.2f}',
            'body_width': f'{candle_width:.2f}',
            'body_height': f'{body_height:.2f}',
            'hover_x': f'{(x - max(candle_width, 18) / 2):.2f}',
            'hover_y': f'{padding:.2f}',
            'hover_width': f'{max(candle_width, 18):.2f}',
            'hover_height': f'{(height - padding * 2):.2f}',
            'tooltip_x': f'{tooltip_x:.2f}',
            'tooltip_y': f'{tooltip_y:.2f}',
            'tooltip_label_x': f'{(tooltip_x + 12):.2f}',
            'tooltip_value_x': f'{(tooltip_x + tooltip_width - 12):.2f}',
            'tooltip_row_1_y': f'{(tooltip_y + 22):.2f}',
            'tooltip_row_2_y': f'{(tooltip_y + 40):.2f}',
            'tooltip_row_3_y': f'{(tooltip_y + 58):.2f}',
            'tooltip_row_4_y': f'{(tooltip_y + 76):.2f}',
            'tooltip_row_5_y': f'{(tooltip_y + 94):.2f}',
            'color': '#198754' if is_positive else '#dc3545',
        })

    return shapes


def build_svg_points(prices, width=420, height=180, padding=18):
    # Gera uma string "x,y x,y" usada por polyline em mini gráficos.
    if not prices:
        return ''

    min_price = min(prices)
    max_price = max(prices)
    drawable_width = width - padding * 2
    drawable_height = height - padding * 2
    price_range = max_price - min_price
    last_index = max(len(prices) - 1, 1)
    points = []

    for index, price in enumerate(prices):
        x = padding + (drawable_width * index / last_index)
        if price_range == 0:
            y = height / 2
        else:
            y = padding + drawable_height - ((price - min_price) / price_range * drawable_height)
        points.append(f'{x:.2f},{y:.2f}')

    return ' '.join(points)


def build_market_chart_shapes(prices, compact_labels=None, width=760, height=360, padding_x=34, padding_y=30):
    # Prepara todos os elementos SVG do gráfico principal de linha.
    if not prices:
        return {
            'line_points': '',
            'area_points': '',
            'volume_bars': [],
            'hover_points': [],
            'first_point': None,
            'last_point': None,
        }

    min_price = min(prices)
    max_price = max(prices)
    price_range = max_price - min_price
    drawable_width = width - padding_x * 2
    drawable_height = height - padding_y * 2
    last_index = max(len(prices) - 1, 1)
    points = []

    for index, price in enumerate(prices):
        # Distribui os pontos igualmente no eixo X e escala preços no eixo Y.
        x = padding_x + (drawable_width * index / last_index)
        if price_range == 0:
            y = padding_y + drawable_height / 2
        else:
            y = padding_y + drawable_height - ((price - min_price) / price_range * drawable_height)
        points.append((x, y))

    line_points = ' '.join(f'{x:.2f},{y:.2f}' for x, y in points)
    baseline_y = height - padding_y
    area_points = f'{padding_x:.2f},{baseline_y:.2f} {line_points} {(width - padding_x):.2f},{baseline_y:.2f}'

    if len(prices) == 1:
        deltas = [0]
    else:
        deltas = [0]
        deltas.extend(abs(current - previous) for previous, current in zip(prices, prices[1:]))
    # As barras azuis representam intensidade da variação de preço, não volume real negociado.
    max_delta = max(deltas) or 1
    bar_width = max(4, min(16, drawable_width / max(len(prices), 1) * 0.72))
    volume_base_y = height - padding_y
    max_bar_height = 52
    volume_bars = []
    for index, ((x, _), delta) in enumerate(zip(points, deltas)):
        bar_height = 12 + (delta / max_delta * (max_bar_height - 12))
        volume_bars.append({
            'x': f'{(x - bar_width / 2):.2f}',
            'y': f'{(volume_base_y - bar_height):.2f}',
            'width': f'{bar_width:.2f}',
            'height': f'{bar_height:.2f}',
        })

    hover_points = []
    compact_labels = compact_labels or [''] * len(prices)
    for (x, y), price, compact_label in zip(points, prices, compact_labels):
        tooltip_width = 118
        tooltip_x = x + 12
        if tooltip_x + tooltip_width > width - 8:
            tooltip_x = x - tooltip_width - 12
        tooltip_x = max(8, tooltip_x)
        tooltip_y = max(8, y - 60)
        hover_points.append({
            'x': f'{x:.2f}',
            'y': f'{y:.2f}',
            'price': price,
            'compact_label': compact_label,
            'tooltip_x': f'{tooltip_x:.2f}',
            'tooltip_y': f'{tooltip_y:.2f}',
            'text_x': f'{(tooltip_x + tooltip_width / 2):.2f}',
            'price_text_y': f'{(tooltip_y + 22):.2f}',
            'date_text_y': f'{(tooltip_y + 40):.2f}',
        })

    return {
        'line_points': line_points,
        'area_points': area_points,
        'volume_bars': volume_bars,
        'hover_points': hover_points,
        'first_point': {'x': f'{points[0][0]:.2f}', 'y': f'{points[0][1]:.2f}'},
        'last_point': {'x': f'{points[-1][0]:.2f}', 'y': f'{points[-1][1]:.2f}'},
    }


def build_market_summary(assets):
    # Consolida números exibidos nos cartões superiores do dashboard.
    tracked_assets = [asset for asset in assets if asset.last_price_brl is not None]
    assets_with_change = [
        asset for asset in assets
        if asset.period_change_percentage is not None
    ]
    positive_assets = [
        asset for asset in assets_with_change
        if asset.period_change_percentage >= 0
    ]
    negative_assets = [
        asset for asset in assets_with_change
        if asset.period_change_percentage < 0
    ]
    latest_update = max(
        (asset.last_price_updated for asset in assets if asset.last_price_updated is not None),
        default=None,
    )
    average_change = None
    if assets_with_change:
        # Média simples das variações percentuais das moedas com dados suficientes.
        average_change = (
            sum((asset.period_change_percentage for asset in assets_with_change), Decimal('0'))
            / len(assets_with_change)
        )

    return {
        'tracked_count': len(tracked_assets),
        'positive_count': len(positive_assets),
        'negative_count': len(negative_assets),
        'best_asset': max(
            assets_with_change,
            key=lambda asset: asset.period_change_percentage,
            default=None,
        ),
        'worst_asset': min(
            assets_with_change,
            key=lambda asset: asset.period_change_percentage,
            default=None,
        ),
        'latest_update': latest_update,
        'average_change': average_change,
        'is_average_positive': average_change is not None and average_change >= 0,
    }


def dashboard(request):
    # Página principal: ranking, ticker, resumo e gráfico da moeda selecionada.
    ensure_supported_assets()
    selected_period = get_selected_period(request.GET.get('period'))
    period_option = PERIOD_OPTIONS[selected_period]
    since = timezone.now() - period_option['delta']
    assets = sort_supported_assets(CryptoAsset.objects.filter(active=True))
    selected_asset_id = request.GET.get('asset')
    # Se a URL não indicar moeda válida, usamos a primeira moeda monitorada.
    selected_asset = next(
        (asset for asset in assets if asset.coingecko_id == selected_asset_id),
        assets[0] if assets else None,
    )
    chart_data = []
    for asset in assets:
        # Anexa atributos temporários aos objetos para simplificar o template.
        asset.period_change_percentage = calculate_period_change(asset, since)
        asset.is_selected = selected_asset is not None and asset.pk == selected_asset.pk
        points = build_period_chart_points(asset, since)
        labels = [recorded_at.strftime('%d/%m/%Y %H:%M') for recorded_at, price in points]
        prices = [float(price) for recorded_at, price in points]
        asset.quote_svg_points = build_svg_points(prices)
        asset.is_period_positive = asset.period_change_percentage is not None and asset.period_change_percentage >= 0
        chart_data.append({
            'name': asset.name,
            'symbol': asset.symbol,
            'labels': labels,
            'prices': prices,
            'svg_points': asset.quote_svg_points,
            'min_price': min(prices) if prices else 0,
            'max_price': max(prices) if prices else 0,
        })

    carousel_assets = assets
    selected_chart = None
    if selected_asset is not None:
        # Monta o pacote completo de dados do gráfico grande para a moeda escolhida.
        selected_points = build_period_chart_points(selected_asset, since)
        selected_candles = build_candles(selected_points)
        selected_prices = [float(price) for recorded_at, price in selected_points]
        selected_compact_labels = [recorded_at.strftime('%d%H%M') for recorded_at, price in selected_points]
        candle_prices = [
            price
            for candle in selected_candles
            for price in (candle['open'], candle['high'], candle['low'], candle['close'])
        ]
        market_shapes = build_market_chart_shapes(selected_prices, selected_compact_labels)
        current_price = selected_asset.last_price_brl or (Decimal(str(selected_prices[-1])) if selected_prices else Decimal('0'))
        first_price = Decimal(str(selected_prices[0])) if selected_prices else current_price
        absolute_change = current_price - first_price
        period_change = calculate_period_change(selected_asset, since) or Decimal('0')
        selected_chart = {
            'name': selected_asset.name,
            'symbol': selected_asset.symbol,
            'candles': build_candlestick_shapes(selected_candles),
            'market_candles': build_candlestick_shapes(selected_candles, width=760, height=360, padding=34),
            'line_points': market_shapes['line_points'],
            'area_points': market_shapes['area_points'],
            'volume_bars': market_shapes['volume_bars'],
            'hover_points': market_shapes['hover_points'],
            'first_point': market_shapes['first_point'],
            'last_point': market_shapes['last_point'],
            'current_price': current_price,
            'absolute_change': absolute_change,
            'period_change_percentage': period_change,
            'is_positive': absolute_change >= 0,
            'min_price': min(candle_prices) if candle_prices else 0,
            'max_price': max(candle_prices) if candle_prices else 0,
            'start_label': selected_points[0][0].strftime('%d/%m/%Y %H:%M') if selected_points else '',
            'end_label': selected_points[-1][0].strftime('%d/%m/%Y %H:%M') if selected_points else '',
        }

    rankings = sorted(
        assets,
        key=lambda asset: asset.period_change_percentage if asset.period_change_percentage is not None else Decimal('-999999'),
        reverse=True,
    )
    market_summary = build_market_summary(assets)
    # O contexto reúne dados já formatados para manter o template focado na apresentação.
    return render(request, 'cripto_monitor/dashboard.html', {
        'assets': assets,
        'carousel_assets': carousel_assets,
        'chart_data': chart_data,
        'chart_data_json': json.dumps(chart_data),
        'market_summary': market_summary,
        'rankings': rankings,
        'period_options': PERIOD_OPTIONS,
        'selected_period': selected_period,
        'selected_asset': selected_asset,
        'selected_chart': selected_chart,
        'variation_label': period_option['variation_label'],
    })


def comparison(request):
    # Página dedicada a comparar a evolução percentual das 10 moedas monitoradas.
    ensure_supported_assets()
    selected_period = get_selected_period(request.GET.get('period'))
    period_option = PERIOD_OPTIONS[selected_period]
    since = timezone.now() - period_option['delta']
    assets = sort_supported_assets(CryptoAsset.objects.filter(active=True))
    comparison_data = build_comparison_chart_data(assets, since)

    return render(request, 'cripto_monitor/comparison.html', {
        'assets': assets,
        'comparison_data': comparison_data,
        'comparison_data_json': json.dumps({
            'labels': comparison_data['labels'],
            'datasets': comparison_data['datasets'],
        }),
        'period_options': PERIOD_OPTIONS,
        'selected_period': selected_period,
        'variation_label': period_option['variation_label'],
    })


def my_simulations(request):
    # Porta de entrada da área pessoal: cadastra novos usuários ou leva logados às simulações.
    if request.user.is_authenticated:
        return redirect('cripto_monitor:simulations')

    form = SignUpForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, 'Cadastro criado. Agora você pode salvar suas simulações.')
        return redirect('cripto_monitor:simulations')

    return render(request, 'cripto_monitor/my_simulations.html', {
        'form': form,
    })


def app_logout(request):
    # Mostra a tela de saída do sistema após encerrar a sessão.
    if request.user.is_authenticated:
        logout(request)
    return render(request, 'cripto_monitor/logged_out.html')


@login_required
def simulations(request):
    ensure_supported_assets()
    editing_simulation = None
    # A mesma tela cria e edita; o id pode vir pela query string ou pelo POST.
    edit_id = request.POST.get('simulation_id') or request.GET.get('edit')
    if edit_id:
        editing_simulation = get_object_or_404(Simulation, pk=edit_id, user=request.user)

    initial = None
    if editing_simulation is not None and request.method != 'POST':
        # Preenche o formulário com os dados atuais quando o usuário clica em editar.
        initial = {
            'asset': editing_simulation.asset,
            'invested_amount_brl': editing_simulation.invested_amount_brl,
            'buy_date': editing_simulation.buy_date,
            'note': editing_simulation.note,
        }

    form = SimulationForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        asset = form.cleaned_data['asset']
        invested_amount_brl = form.cleaned_data['invested_amount_brl']
        buy_date = form.cleaned_data['buy_date']
        note = form.cleaned_data['note']

        # Primeiro tenta buscar o preço histórico da data escolhida; se falhar, usa o preço atual.
        purchased_price = fetch_asset_historical_price(asset.coingecko_id, buy_date)
        if purchased_price is None:
            purchased_price = asset.last_price_brl

        if purchased_price is None or purchased_price == 0:
            messages.error(request, 'Não foi possível determinar o preço de compra para a data selecionada.')
        else:
            # Quantidade simulada = valor investido dividido pelo preço de compra encontrado.
            crypto_amount = (Decimal(invested_amount_brl) / purchased_price).quantize(Decimal('0.00000001'))
            if editing_simulation is None:
                simulation = Simulation.objects.create(
                    asset=asset,
                    invested_amount_brl=invested_amount_brl,
                    buy_date=buy_date,
                    purchased_price_brl=purchased_price,
                    crypto_amount=crypto_amount,
                    note=note,
                    user=request.user,
                )
                # Guarda a compra inicial no histórico de operações simuladas.
                SimulatedTrade.objects.create(
                    simulation=simulation,
                    trade_type='BUY',
                    amount_brl=invested_amount_brl,
                    crypto_amount=crypto_amount,
                    price_brl=purchased_price,
                    trade_date=buy_date,
                )
                messages.success(request, 'Simulação criada com sucesso.')
            else:
                editing_simulation.asset = asset
                editing_simulation.invested_amount_brl = invested_amount_brl
                editing_simulation.buy_date = buy_date
                editing_simulation.purchased_price_brl = purchased_price
                editing_simulation.crypto_amount = crypto_amount
                editing_simulation.note = note
                editing_simulation.save()
                # Mantém a compra inicial sincronizada quando a simulação é editada.
                SimulatedTrade.objects.update_or_create(
                    simulation=editing_simulation,
                    trade_type='BUY',
                    defaults={
                        'amount_brl': invested_amount_brl,
                        'crypto_amount': crypto_amount,
                        'price_brl': purchased_price,
                        'trade_date': buy_date,
                    },
                )
            messages.success(request, 'Simulação atualizada com sucesso.')
            return redirect('cripto_monitor:simulations')

    # Cada usuário enxerga apenas suas próprias simulações.
    simulations = Simulation.objects.select_related('asset').filter(user=request.user)
    return render(request, 'cripto_monitor/simulations.html', {
        'form': form,
        'simulations': simulations,
        'editing_simulation': editing_simulation,
    })


@login_required
def delete_simulation(request, pk):
    simulation = get_object_or_404(Simulation, pk=pk, user=request.user)
    if request.method == 'POST':
        # A exclusão é limitada ao dono da simulação e só acontece via POST.
        simulation.delete()
        messages.success(request, 'Simulação removida com sucesso.')
    return redirect('cripto_monitor:simulations')


@login_required
def alerts(request):
    ensure_supported_assets()
    form_type = request.POST.get('form_type')
    price_form_data = request.POST if request.method == 'POST' and form_type == 'price' else None
    simulation_form_data = request.POST if request.method == 'POST' and form_type == 'simulation' else None
    form = PriceAlertForm(price_form_data, prefix='price')
    simulation_form = SimulationAlertForm(
        simulation_form_data,
        prefix='simulation',
        user=request.user,
    )
    if request.method == 'POST' and form_type == 'price' and form.is_valid():
        # O alerta fica ligado ao usuário logado para separar regras pessoais.
        alert = form.save(commit=False)
        alert.user = request.user
        alert.save()
        messages.success(request, 'Alerta cadastrado com sucesso.')
        return redirect('cripto_monitor:alerts')
    if request.method == 'POST' and form_type == 'simulation' and simulation_form.is_valid():
        # O alerta de simulação observa o ganho/prejuízo percentual da carteira simulada.
        simulation_alert = simulation_form.save(commit=False)
        simulation_alert.user = request.user
        simulation_alert.save()
        messages.success(request, 'Alerta de simulação cadastrado com sucesso.')
        return redirect('cripto_monitor:alerts')

    alerts = PriceAlert.objects.select_related('asset').filter(Q(user=request.user) | Q(user__isnull=True))
    for alert in alerts:
        # Ao abrir a página, conferimos se algum alerta ativo já atingiu o preço limite.
        alert.check_trigger()
    simulation_alerts = SimulationAlert.objects.select_related('simulation', 'simulation__asset').filter(user=request.user)
    for simulation_alert in simulation_alerts:
        # Também conferimos se alguma simulação atingiu o ganho/prejuízo configurado.
        simulation_alert.check_trigger()
    price_alert_assets = {
        str(asset.pk): {
            'symbol': asset.symbol,
            'price_brl': float(asset.last_price_brl) if asset.last_price_brl is not None else None,
        }
        for asset in CryptoAsset.objects.filter(active=True)
    }

    return render(request, 'cripto_monitor/alerts.html', {
        'form': form,
        'alerts': alerts,
        'price_alert_assets_json': json.dumps(price_alert_assets),
        'simulation_form': simulation_form,
        'simulation_alerts': simulation_alerts,
    })


@login_required
def trigger_alert(request, pk):
    alert = get_object_or_404(PriceAlert, pk=pk, user=request.user)
    # Atalho manual para encerrar um alerta sem esperar uma nova cotação.
    alert.mark_triggered()
    messages.success(request, 'Alerta marcado como disparado.')
    return redirect('cripto_monitor:alerts')


@login_required
def delete_alert(request, pk):
    alert = get_object_or_404(PriceAlert, pk=pk, user=request.user)
    if request.method == 'POST':
        # A exclusão é limitada ao dono do alerta e só acontece via POST.
        alert.delete()
        messages.success(request, 'Alerta removido com sucesso.')
    return redirect('cripto_monitor:alerts')


@login_required
def delete_simulation_alert(request, pk):
    simulation_alert = get_object_or_404(SimulationAlert, pk=pk, user=request.user)
    if request.method == 'POST':
        simulation_alert.delete()
        messages.success(request, 'Alerta de simulação removido com sucesso.')
    return redirect('cripto_monitor:alerts')
