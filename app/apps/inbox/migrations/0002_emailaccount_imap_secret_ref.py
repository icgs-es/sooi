from django.db import migrations, models


def assign_secret_references(apps, schema_editor):
    EmailAccount = apps.get_model("inbox", "EmailAccount")
    for account in EmailAccount.objects.all().only("pk").iterator():
        account.imap_secret_ref = f"imap-account-{account.pk}"
        account.save(update_fields=["imap_secret_ref"])


class Migration(migrations.Migration):
    dependencies = [("inbox", "0001_initial")]
    operations = [
        migrations.AddField(
            model_name="emailaccount",
            name="imap_secret_ref",
            field=models.CharField(blank=True, max_length=120, verbose_name="referencia de secreto IMAP"),
        ),
        migrations.RunPython(assign_secret_references, migrations.RunPython.noop),
    ]
