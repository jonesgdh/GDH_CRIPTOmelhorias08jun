from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


class CryptoAsset(models.Model):
    # Cadastro principal de cada criptomoeda monitorada pelo sistema.
    name = models.CharField(max_length=120)
    symbol = models.CharField(max_length=20)
    # Identificador usado nas chamadas da API CoinGecko, por exemplo "bitcoin".
    coingecko_id = models.CharField(max_length=80, unique=True)
    active = models.BooleanField(default=True)
    # Últimos valores conhecidos; ficam no ativo para leitura rápida no dashboard.
    last_price_brl = models.DecimalField(max_digits=22, decimal_places=6, null=True, blank=True)
    last_price_usd = models.DecimalField(max_digits=22, decimal_places=6, null=True, blank=True)
    last_change_percentage = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    last_price_updated = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.symbol})'


class PriceHistory(models.Model):
    # Registro histórico de preço salvo a cada atualização de cotação.
    asset = models.ForeignKey(CryptoAsset, on_delete=models.CASCADE, related_name='price_history')
    price_brl = models.DecimalField(max_digits=22, decimal_places=6)
    price_usd = models.DecimalField(max_digits=22, decimal_places=6)
    recorded_at = models.DateTimeField(default=timezone.now)
    # Mantém a origem dos dados para auditoria e para permitir outras fontes no futuro.
    source = models.CharField(max_length=80, default='coingecko')

    class Meta:
        # Histórico aparece do mais recente para o mais antigo por padrão.
        ordering = ['-recorded_at']

    def __str__(self):
        return f'{self.asset.symbol} - R$ {self.price_brl:.2f} @ {self.recorded_at:%d/%m/%Y %H:%M}'


class Simulation(models.Model):
    # Simulação de uma posição de investimento criada pelo usuário.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='simulations',
    )
    asset = models.ForeignKey(CryptoAsset, on_delete=models.CASCADE, related_name='simulations')
    # Valor investido, data e preço usados para calcular a quantidade simulada comprada.
    invested_amount_brl = models.DecimalField(max_digits=18, decimal_places=2)
    buy_date = models.DateField()
    purchased_price_brl = models.DecimalField(max_digits=22, decimal_places=6)
    crypto_amount = models.DecimalField(max_digits=22, decimal_places=12)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Simulação {self.asset.symbol} - R$ {self.invested_amount_brl:.2f}'

    @property
    def current_value_brl(self):
        # Valor atual da posição: quantidade simulada vezes o último preço conhecido.
        if self.asset.last_price_brl is None:
            return Decimal('0.00')
        return self.asset.last_price_brl * self.crypto_amount

    @property
    def gain_loss_brl(self):
        # Resultado financeiro absoluto em reais.
        return self.current_value_brl - self.invested_amount_brl

    @property
    def gain_loss_percentage(self):
        # Resultado percentual em relação ao valor investido inicialmente.
        if self.invested_amount_brl == 0:
            return Decimal('0.00')
        return (self.current_value_brl / self.invested_amount_brl - 1) * Decimal('100')


class SimulatedTrade(models.Model):
    # Histórico de operações simuladas para uma simulação; hoje registra a compra inicial.
    TRADE_TYPE_CHOICES = [
        ('BUY', 'Compra simulada'),
        ('SELL', 'Venda simulada'),
    ]

    simulation = models.ForeignKey(Simulation, on_delete=models.CASCADE, related_name='trades')
    trade_type = models.CharField(max_length=8, choices=TRADE_TYPE_CHOICES)
    amount_brl = models.DecimalField(max_digits=18, decimal_places=2)
    crypto_amount = models.DecimalField(max_digits=22, decimal_places=12)
    price_brl = models.DecimalField(max_digits=22, decimal_places=6)
    trade_date = models.DateField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-trade_date', '-created_at']

    def __str__(self):
        return f'{self.get_trade_type_display()} {self.crypto_amount} {self.simulation.asset.symbol}'


class SimulationAlert(models.Model):
    # Alerta baseado no resultado percentual de uma simulação de investimento.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='simulation_alerts',
    )
    ALERT_TYPE_CHOICES = [
        ('above', 'Ganho acima de'),
        ('below', 'Resultado abaixo de'),
    ]

    simulation = models.ForeignKey(Simulation, on_delete=models.CASCADE, related_name='alerts')
    alert_type = models.CharField(max_length=8, choices=ALERT_TYPE_CHOICES)
    # threshold_percentage é o limite percentual. Ex.: 10 para ganho acima de 10%, -5 para queda abaixo de -5%.
    threshold_percentage = models.DecimalField(max_digits=8, decimal_places=2)
    active = models.BooleanField(default=True)
    triggered = models.BooleanField(default=False)
    triggered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.simulation.asset.symbol} {self.get_alert_type_display()} {self.threshold_percentage:.2f}%'

    def check_trigger(self):
        # Não reavalia alertas encerrados ou já disparados.
        if not self.active or self.triggered:
            return False

        current = self.simulation.gain_loss_percentage
        if self.alert_type == 'above' and current >= self.threshold_percentage:
            self.mark_triggered()
            return True
        if self.alert_type == 'below' and current <= self.threshold_percentage:
            self.mark_triggered()
            return True
        return False

    def mark_triggered(self):
        self.triggered = True
        self.triggered_at = timezone.now()
        self.save(update_fields=['triggered', 'triggered_at'])


class PriceAlert(models.Model):
    # Alertas podem ser pessoais; null/blank preserva compatibilidade com alertas antigos sem usuário.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='price_alerts',
    )
    # Define se o alerta dispara quando o preço sobe acima do limite ou cai abaixo dele.
    ALERT_TYPE_CHOICES = [
        ('above', 'Atingir acima de'),
        ('below', 'Atingir abaixo de'),
    ]

    asset = models.ForeignKey(CryptoAsset, on_delete=models.CASCADE, related_name='alerts')
    alert_type = models.CharField(max_length=8, choices=ALERT_TYPE_CHOICES)
    # threshold_brl é o preço limite informado pelo usuário em reais.
    threshold_brl = models.DecimalField(max_digits=22, decimal_places=6)
    # active controla se o alerta ainda deve ser avaliado; triggered indica que já disparou.
    active = models.BooleanField(default=True)
    triggered = models.BooleanField(default=False)
    triggered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.asset.symbol} {self.get_alert_type_display()} R$ {self.threshold_brl:.2f}'

    def check_trigger(self):
        # Alertas inativos, já disparados ou sem preço atual não devem mudar de estado.
        if not self.active or self.triggered or self.asset.last_price_brl is None:
            return False

        current = self.asset.last_price_brl
        # Compara o preço atual com o limite de acordo com o tipo escolhido.
        if self.alert_type == 'above' and current >= self.threshold_brl:
            self.mark_triggered()
            return True
        if self.alert_type == 'below' and current <= self.threshold_brl:
            self.mark_triggered()
            return True
        return False

    def mark_triggered(self):
        # Guarda o momento do disparo para auditoria e exibição futura.
        self.triggered = True
        self.triggered_at = timezone.now()
        self.save(update_fields=['triggered', 'triggered_at'])
