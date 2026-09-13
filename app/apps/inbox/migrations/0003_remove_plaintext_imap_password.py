from django.db import migrations, models


def erase_plaintext_passwords(apps, schema_editor):
    EmailAccount = apps.get_model("inbox", "EmailAccount")
    EmailAccount.objects.exclude(imap_password="").update(imap_password="")


class Migration(migrations.Migration):
    dependencies = [("inbox", "0002_emailaccount_imap_secret_ref")]
    operations = [
        migrations.RunPython(erase_plaintext_passwords, migrations.RunPython.noop),
        migrations.RemoveField(model_name="emailaccount", name="imap_password"),
        migrations.AlterField(
            model_name="emailaccount",
            name="imap_secret_ref",
            field=models.CharField(max_length=120, verbose_name="referencia de secreto IMAP"),
        ),
    ]
