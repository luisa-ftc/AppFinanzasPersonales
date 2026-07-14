from django.db import migrations


def normalize_currency(apps, schema_editor):
    Account = apps.get_model("core", "Account")
    Account.objects.exclude(currency="COP").update(currency="COP")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0013_alter_sharedexpensepayment_account"),
    ]

    operations = [
        migrations.RunPython(normalize_currency, migrations.RunPython.noop),
    ]
