from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.utils import timezone
from .models import CryptoAsset, PriceAlert, Simulation, SimulationAlert


class LoginForm(AuthenticationForm):
    # Login com mensagens específicas para usuário inexistente e senha incorreta.
    error_messages = {
        'invalid_login': 'Usuário ou senha inválidos.',
        'inactive': 'Esta conta está inativa.',
    }

    def clean(self):
        username = self.cleaned_data.get('username', '').strip()
        password = self.cleaned_data.get('password')

        if username and password:
            user = User.objects.filter(username__iexact=username).first()
            if user is None:
                self.add_error('username', 'Usuário não existe.')
                return self.cleaned_data

            self.user_cache = authenticate(
                self.request,
                username=user.get_username(),
                password=password,
            )
            if self.user_cache is None:
                self.add_error('password', 'Senha incorreta.')
                return self.cleaned_data

            self.confirm_login_allowed(self.user_cache)

        return self.cleaned_data


class SignUpForm(forms.Form):
    # Cadastro simples para liberar a área pessoal de simulações.
    username = forms.CharField(
        label='Usuário',
        max_length=150,
        help_text='Use qualquer nome ainda não cadastrado.',
    )
    password1 = forms.CharField(
        label='Senha',
        min_length=4,
        help_text='Use no mínimo 4 caracteres.',
        strip=False,
        widget=forms.PasswordInput,
    )
    password2 = forms.CharField(
        label='Confirmar senha',
        strip=False,
        widget=forms.PasswordInput,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        if not username:
            raise forms.ValidationError('Informe um nome de usuário.')
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError('Este nome de usuário já está cadastrado.')
        return username

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get('password1')
        password2 = cleaned_data.get('password2')
        if password1 and password2 and password1 != password2:
            self.add_error('password2', 'As senhas não conferem.')
        return cleaned_data

    def save(self):
        return User.objects.create_user(
            username=self.cleaned_data['username'],
            password=self.cleaned_data['password1'],
        )


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
            'threshold_brl': forms.NumberInput(attrs={'step': '0.01', 'class': 'form-control'}),
        }
        labels = {
            'asset': 'Moeda',
            'alert_type': 'Tipo de alerta',
            'threshold_brl': 'Limite em BRL',
        }


class SimulationAlertForm(forms.ModelForm):
    # Formulário para alertas baseados no ganho/prejuízo percentual de uma simulação.
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields['simulation'].queryset = Simulation.objects.select_related('asset').filter(user=user)

    class Meta:
        model = SimulationAlert
        fields = ['simulation', 'alert_type', 'threshold_percentage']
        widgets = {
            'threshold_percentage': forms.NumberInput(attrs={'step': '0.01'}),
        }
        labels = {
            'simulation': 'Simulação',
            'alert_type': 'Tipo de alerta',
            'threshold_percentage': 'Limite em %',
        }
