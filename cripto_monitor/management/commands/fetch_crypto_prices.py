from django.core.management.base import BaseCommand

from cripto_monitor.services import update_price_history_for_assets


class Command(BaseCommand):
    help = 'Fetch the latest cryptocurrency prices from CoinGecko and store history.'

    def handle(self, *args, **options):
        updated_count = update_price_history_for_assets()
        self.stdout.write(self.style.SUCCESS(
            f'Updated price history for {updated_count} crypto assets.'
        ))
