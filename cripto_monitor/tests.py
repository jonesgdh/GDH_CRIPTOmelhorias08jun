from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .models import CryptoAsset, PriceAlert, PriceHistory, Simulation, SimulationAlert
from .services import SUPPORTED_ASSETS, ensure_supported_assets


class SupportedAssetsTests(TestCase):
    # Testes da lista oficial de moedas monitoradas pelo dashboard.
    def test_supported_assets_keeps_exactly_ten_active_assets(self):
        CryptoAsset.objects.create(
            name='Monero',
            symbol='XMR',
            coingecko_id='monero',
            active=True,
        )

        ensure_supported_assets()

        self.assertEqual(len(SUPPORTED_ASSETS), 10)
        self.assertEqual(CryptoAsset.objects.filter(active=True).count(), 10)
        self.assertFalse(CryptoAsset.objects.get(coingecko_id='monero').active)
        self.assertTrue(CryptoAsset.objects.get(coingecko_id='bitcoin').active)


class DashboardPeriodTests(TestCase):
    # Testes focados no dashboard: períodos, gráfico, resumo e elementos visuais principais.
    def test_invalid_period_uses_day_as_default(self):
        # Um período desconhecido na URL deve cair no padrão seguro de 24h.
        response = self.client.get('/?period=invalid')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_period'], 'day')
        self.assertContains(response, 'Variação 24h')

    def test_week_period_calculates_change_from_history(self):
        # Cria uma série simples para garantir que a variação semanal usa o histórico local.
        now = timezone.now()
        asset = CryptoAsset.objects.create(
            name='Bitcoin',
            symbol='BTC',
            coingecko_id='bitcoin',
            last_price_brl=Decimal('120.00'),
            last_price_usd=Decimal('24.00'),
            last_price_updated=now,
        )
        PriceHistory.objects.create(
            asset=asset,
            price_brl=Decimal('100.00'),
            price_usd=Decimal('20.00'),
            recorded_at=now - timedelta(days=8),
        )
        PriceHistory.objects.create(
            asset=asset,
            price_brl=Decimal('110.00'),
            price_usd=Decimal('22.00'),
            recorded_at=now - timedelta(days=2),
        )

        response = self.client.get('/?period=week')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_period'], 'week')
        self.assertEqual(response.context['selected_asset'].coingecko_id, 'bitcoin')
        self.assertEqual(response.context['selected_chart']['symbol'], 'BTC')
        bitcoin_chart = next(chart for chart in response.context['chart_data'] if chart['symbol'] == 'BTC')
        self.assertEqual(bitcoin_chart['prices'], [100.0, 110.0, 120.0])
        self.assertEqual(len(response.context['selected_chart']['candles']), 2)
        self.assertContains(response, 'Variação 7d')
        self.assertContains(response, 'Gráfico de velas')
        self.assertContains(response, '<rect')
        self.assertContains(response, 'Menor: R$ 100.00')
        self.assertContains(response, 'Maior: R$ 120.00')
        self.assertContains(response, now.strftime('%d%H%M'))
        self.assertAlmostEqual(response.context['assets'][0].period_change_percentage, Decimal('20.0'))

    def test_asset_query_selects_respective_candlestick_chart(self):
        # O parâmetro asset na URL deve trocar a moeda exibida no gráfico principal.
        now = timezone.now()
        CryptoAsset.objects.create(
            name='Ethereum',
            symbol='ETH',
            coingecko_id='ethereum',
            last_price_brl=Decimal('90.00'),
            last_price_usd=Decimal('18.00'),
            last_price_updated=now,
        )
        PriceHistory.objects.create(
            asset=CryptoAsset.objects.get(coingecko_id='ethereum'),
            price_brl=Decimal('100.00'),
            price_usd=Decimal('20.00'),
            recorded_at=now - timedelta(hours=2),
        )

        response = self.client.get('/?period=hours&asset=ethereum')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_asset'].coingecko_id, 'ethereum')
        self.assertEqual(response.context['selected_chart']['symbol'], 'ETH')
        self.assertContains(response, 'Ethereum <span class="market-symbol">(ETH)</span>')
        self.assertContains(response, 'Gráfico de velas')

    def test_dashboard_has_price_refresh_controls(self):
        # Garante que controles e links importantes continuam presentes na tela inicial.
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Atualizar agora')
        self.assertContains(response, '/api/prices/')
        self.assertContains(response, 'Cotações de criptomoedas')
        self.assertContains(response, 'Ranking CryptoCompare')
        self.assertContains(response, 'https://www.cryptocompare.com/coins/list/all/USD/1')
        self.assertContains(response, 'powered by CoinDesk Data')

    def test_dashboard_market_summary_uses_period_changes(self):
        # Valida os cartões de resumo: contagem de altas/quedas, melhor/pior ativo e média.
        now = timezone.now()
        bitcoin = CryptoAsset.objects.create(
            name='Bitcoin',
            symbol='BTC',
            coingecko_id='bitcoin',
            last_price_brl=Decimal('120.00'),
            last_price_usd=Decimal('24.00'),
            last_price_updated=now,
        )
        ethereum = CryptoAsset.objects.create(
            name='Ethereum',
            symbol='ETH',
            coingecko_id='ethereum',
            last_price_brl=Decimal('80.00'),
            last_price_usd=Decimal('16.00'),
            last_price_updated=now - timedelta(minutes=5),
        )
        PriceHistory.objects.create(
            asset=bitcoin,
            price_brl=Decimal('100.00'),
            price_usd=Decimal('20.00'),
            recorded_at=now - timedelta(days=2),
        )
        PriceHistory.objects.create(
            asset=ethereum,
            price_brl=Decimal('100.00'),
            price_usd=Decimal('20.00'),
            recorded_at=now - timedelta(days=2),
        )

        response = self.client.get('/?period=day')

        summary = response.context['market_summary']
        self.assertEqual(summary['tracked_count'], 2)
        self.assertEqual(summary['positive_count'], 1)
        self.assertEqual(summary['negative_count'], 1)
        self.assertEqual(summary['best_asset'].symbol, 'BTC')
        self.assertEqual(summary['worst_asset'].symbol, 'ETH')
        self.assertEqual(summary['latest_update'], now)
        self.assertEqual(summary['average_change'], Decimal('0.0'))
        self.assertContains(response, 'Moedas monitoradas')
        self.assertContains(response, '1 em alta · 1 em queda')
        self.assertContains(response, 'Maior alta')


class ComparisonTests(TestCase):
    # Testes da página que compara a evolução percentual das moedas em um único gráfico.
    def test_comparison_page_builds_datasets_for_active_assets(self):
        now = timezone.now()
        bitcoin = CryptoAsset.objects.create(
            name='Bitcoin',
            symbol='BTC',
            coingecko_id='bitcoin',
            last_price_brl=Decimal('120.00'),
            last_price_usd=Decimal('24.00'),
            last_price_updated=now,
        )
        ethereum = CryptoAsset.objects.create(
            name='Ethereum',
            symbol='ETH',
            coingecko_id='ethereum',
            last_price_brl=Decimal('80.00'),
            last_price_usd=Decimal('16.00'),
            last_price_updated=now,
        )
        PriceHistory.objects.create(
            asset=bitcoin,
            price_brl=Decimal('100.00'),
            price_usd=Decimal('20.00'),
            recorded_at=now - timedelta(days=2),
        )
        PriceHistory.objects.create(
            asset=ethereum,
            price_brl=Decimal('100.00'),
            price_usd=Decimal('20.00'),
            recorded_at=now - timedelta(days=2),
        )

        response = self.client.get('/comparison/?period=week')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_period'], 'week')
        self.assertContains(response, 'Comparação')
        self.assertContains(response, 'comparison-chart')
        datasets = response.context['comparison_data']['datasets']
        self.assertEqual(datasets[0]['label'], 'BTC')
        self.assertEqual(datasets[0]['data'], [0.0, 20.0])
        self.assertEqual(datasets[1]['label'], 'ETH')
        self.assertEqual(datasets[1]['data'], [0.0, -20.0])


class MySimulationsTests(TestCase):
    # Testes da porta de entrada da área pessoal de simulações.
    def test_guest_sees_signup_page_from_menu(self):
        response = self.client.get('/minhas-simulacoes/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Minhas Simulações')
        self.assertContains(response, 'Criar usuário')

        dashboard = self.client.get('/')
        self.assertContains(dashboard, 'Minhas Simulações')

    def test_signup_creates_user_and_redirects_to_simulations(self):
        response = self.client.post('/minhas-simulacoes/', {
            'username': 'Visitante 1',
            'password1': 'abcd',
            'password2': 'abcd',
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/simulations/')
        self.assertTrue(get_user_model().objects.filter(username='Visitante 1').exists())

    def test_signup_rejects_existing_username(self):
        get_user_model().objects.create_user(username='Visitante 1', password='pass12345')

        response = self.client.post('/minhas-simulacoes/', {
            'username': 'visitante 1',
            'password1': 'abcd',
            'password2': 'abcd',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Este nome de usuário já está cadastrado.')

    def test_signup_rejects_password_shorter_than_four_characters(self):
        response = self.client.post('/minhas-simulacoes/', {
            'username': 'visitante curto',
            'password1': 'abc',
            'password2': 'abc',
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(get_user_model().objects.filter(username='visitante curto').exists())

    def test_authenticated_user_goes_to_simulations(self):
        get_user_model().objects.create_user(username='admin', password='pass12345')
        self.client.login(username='admin', password='pass12345')

        response = self.client.get('/minhas-simulacoes/')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/simulations/')

    def test_simulations_page_shows_logged_username(self):
        get_user_model().objects.create_user(username='investidor', password='pass12345')
        self.client.login(username='investidor', password='pass12345')

        response = self.client.get('/simulations/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Simulando como')
        self.assertContains(response, 'investidor')
        self.assertContains(response, 'Sair')
        self.assertContains(response, 'method="post" action="/accounts/logout/"')

    def test_logout_page_shows_logo_and_signup_message(self):
        get_user_model().objects.create_user(username='investidor', password='pass12345')
        self.client.login(username='investidor', password='pass12345')

        response = self.client.post('/accounts/logout/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'GDH_CRIPTO.png')
        self.assertContains(response, 'É necessário criar usuário e senha para fazer as simulações')
        self.assertContains(response, 'Criar usuário')


class LoginTests(TestCase):
    # Testes das mensagens específicas de erro na tela de entrada.
    def test_login_shows_username_not_found(self):
        response = self.client.post('/accounts/login/', {
            'username': 'naoexiste',
            'password': 'abcd',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Usuário não existe.')

    def test_login_shows_wrong_password(self):
        get_user_model().objects.create_user(username='visitante', password='abcd')

        response = self.client.post('/accounts/login/', {
            'username': 'visitante',
            'password': 'errada',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Senha incorreta.')


class PricesApiTests(TestCase):
    # Testes do endpoint JSON usado pelo botão "Atualizar agora" e atualização automática.
    def test_prices_api_returns_saved_prices_without_refresh_when_recent(self):
        # Se os preços estão recentes, a API deve devolver o banco sem chamar a atualização externa.
        now = timezone.now()
        for asset_data in SUPPORTED_ASSETS:
            CryptoAsset.objects.create(
                name=asset_data['name'],
                symbol=asset_data['symbol'],
                coingecko_id=asset_data['coingecko_id'],
                last_price_brl=Decimal('120.00'),
                last_price_usd=Decimal('24.00'),
                last_change_percentage=Decimal('1.50'),
                last_price_updated=now,
            )

        with patch('cripto_monitor.views.update_price_history_for_assets') as update_prices:
            response = self.client.get('/api/prices/')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload['refreshed'])
        self.assertEqual(payload['refresh_interval_seconds'], 600)
        self.assertEqual(payload['assets'][0]['id'], 'bitcoin')
        update_prices.assert_not_called()

    def test_prices_api_force_refresh_updates_prices(self):
        # refresh=1 força a atualização mesmo quando a rotina normal poderia considerar recente.
        with patch('cripto_monitor.views.update_price_history_for_assets', return_value=3) as update_prices:
            response = self.client.get('/api/prices/?refresh=1')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['refreshed'])
        self.assertEqual(payload['updated_count'], 3)
        update_prices.assert_called_once()


class AlertTests(TestCase):
    # Testes da tela de alertas, incluindo ações que alteram registros.
    def test_user_can_delete_own_alert(self):
        user = get_user_model().objects.create_user(username='admin', password='pass12345')
        asset = CryptoAsset.objects.create(
            name='Bitcoin',
            symbol='BTC',
            coingecko_id='bitcoin',
            last_price_brl=Decimal('120.00'),
            last_price_usd=Decimal('24.00'),
            last_price_updated=timezone.now(),
        )
        alert = PriceAlert.objects.create(
            user=user,
            asset=asset,
            alert_type='above',
            threshold_brl=Decimal('150.00'),
        )

        self.client.login(username='admin', password='pass12345')
        response = self.client.post(f'/alerts/{alert.pk}/delete/')

        self.assertEqual(response.status_code, 302)
        self.assertFalse(PriceAlert.objects.filter(pk=alert.pk).exists())

    def test_price_alert_form_shows_current_price_helpers(self):
        user = get_user_model().objects.create_user(username='admin', password='pass12345')
        CryptoAsset.objects.create(
            name='Bitcoin',
            symbol='BTC',
            coingecko_id='bitcoin',
            last_price_brl=Decimal('120.00'),
            last_price_usd=Decimal('24.00'),
            last_price_updated=timezone.now(),
        )

        self.client.login(username='admin', password='pass12345')
        response = self.client.get('/alerts/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Preço atual')
        self.assertContains(response, 'Usar atual')
        self.assertContains(response, 'data-price-adjust="-1"')
        self.assertContains(response, 'data-price-adjust="1"')

    def test_simulation_alert_triggers_when_gain_reaches_threshold(self):
        user = get_user_model().objects.create_user(username='admin', password='pass12345')
        asset = CryptoAsset.objects.create(
            name='Bitcoin',
            symbol='BTC',
            coingecko_id='bitcoin',
            last_price_brl=Decimal('120.00'),
            last_price_usd=Decimal('24.00'),
            last_price_updated=timezone.now(),
        )
        simulation = Simulation.objects.create(
            user=user,
            asset=asset,
            invested_amount_brl=Decimal('100.00'),
            buy_date=timezone.now().date(),
            purchased_price_brl=Decimal('100.00'),
            crypto_amount=Decimal('1.000000000000'),
        )
        alert = SimulationAlert.objects.create(
            user=user,
            simulation=simulation,
            alert_type='above',
            threshold_percentage=Decimal('10.00'),
        )

        self.assertTrue(alert.check_trigger())
        alert.refresh_from_db()
        self.assertTrue(alert.triggered)

    def test_user_can_create_simulation_alert_from_alerts_page(self):
        user = get_user_model().objects.create_user(username='admin', password='pass12345')
        asset = CryptoAsset.objects.create(
            name='Bitcoin',
            symbol='BTC',
            coingecko_id='bitcoin',
            last_price_brl=Decimal('120.00'),
            last_price_usd=Decimal('24.00'),
            last_price_updated=timezone.now(),
        )
        simulation = Simulation.objects.create(
            user=user,
            asset=asset,
            invested_amount_brl=Decimal('100.00'),
            buy_date=timezone.now().date(),
            purchased_price_brl=Decimal('100.00'),
            crypto_amount=Decimal('1.000000000000'),
        )

        self.client.login(username='admin', password='pass12345')
        response = self.client.post('/alerts/', {
            'form_type': 'simulation',
            'simulation-simulation': simulation.pk,
            'simulation-alert_type': 'above',
            'simulation-threshold_percentage': '10.00',
        })

        self.assertEqual(response.status_code, 302)
        self.assertTrue(SimulationAlert.objects.filter(user=user, simulation=simulation).exists())
