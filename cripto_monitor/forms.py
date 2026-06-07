from django import forms
from django.utils import timezone
from .models import CryptoAsset, PriceAlert


class SimulationForm(forms.Form):
    # Campos necessários para calcular quanto de cripto teria sido comprado na data informada.
    asset = forms.ModelChoiceField(
        # Mostra apenas moedas ativas no cadastro.
        queryset=CryptoAsset.objects.filter(active=True),
        label='Moeda',
        empty_label='Escolha uma moeda',
    )
    invested_amount_brl = forms.DecimalField(
        # Valor mínimo evita simulações sem sentido financeiro.
        label='Valor investido (BRL)',
        min_value=1,
        decimal_places=2,
        max_digits=14,
    )
    buy_date = forms.DateField(
        # Campo HTML date abre o seletor de data nativo do navegador.
        label='Data de compra simulada',
        initial=timezone.now().date(),
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    note = forms.CharField(
        # Observação é opcional e serve para registrar estratégia ou contexto da simulação.
        required=False,
        label='Observação',
        widget=forms.Textarea(attrs={'rows': 2}),
    )


class PriceAlertForm(forms.ModelForm):
    # Formulário baseado no model PriceAlert; a view preenche o usuário antes de salvar.
    class Meta:
        model = PriceAlert
        fields = ['asset', 'alert_type', 'threshold_brl']
        widgets = {
            # Permite digitar valores monetários com centavos.
            'threshold_brl': forms.NumberInput(attrs={'step': '0.01'}),
        }
        labels = {
            'asset': 'Moeda',
            'alert_type': 'Tipo de alerta',
            'threshold_brl': 'Limite em BRL',
        }
