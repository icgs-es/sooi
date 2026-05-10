import email
import html as html_lib
import imaplib
import re
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime, parseaddr

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.inbox.models import EmailAccount, InboundEmail


URL_RE = re.compile(r'''https?://[^\s<>"']+''', re.IGNORECASE)
HREF_RE = re.compile(r"href=[\"'](https?://[^\"']+)[\"']", re.IGNORECASE)


def decode_mime(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def decode_part(part):
    payload = part.get_payload(decode=True)
    if not payload:
        return ""

    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except Exception:
        return payload.decode("utf-8", errors="replace")


def strip_html(value):
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value or "")
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html_lib.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def extract_body_text_and_html(message):
    text_parts = []
    html_parts = []

    if message.is_multipart():
        for part in message.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "").lower()

            if "attachment" in disposition:
                continue

            if content_type == "text/plain":
                value = decode_part(part).strip()
                if value:
                    text_parts.append(value)

            elif content_type == "text/html":
                value = decode_part(part).strip()
                if value:
                    html_parts.append(value)
    else:
        content_type = message.get_content_type()
        value = decode_part(message).strip()
        if content_type == "text/html":
            html_parts.append(value)
        else:
            text_parts.append(value)

    body_text = "\n".join(text_parts).strip()

    if not body_text and html_parts:
        body_text = strip_html("\n".join(html_parts))

    body_html = "\n".join(html_parts).strip()
    return body_text, body_html


def prepare_text_for_url_detection(value):
    value = value or ""
    value = value.replace("=\r\n", "").replace("=\n", "")

    repairs = [
        (r"(https?://(?:www\.)?ideali)\s+(sta\.com)", r"\1\2"),
        (r"(https?://(?:www\.)?fotoca)\s+(sa\.es)", r"\1\2"),
        (r"(https?://(?:www\.)?habita)\s+(clia\.com)", r"\1\2"),
        (r"(https?://(?:www\.)?servi)\s+(habitat\.com)", r"\1\2"),
        (r"(https?://(?:www\.)?terre)\s+(nos\.es)", r"\1\2"),
        (r"(https?://(?:www\.)?yaen)\s+(contre\.com)", r"\1\2"),
    ]

    for pattern, replacement in repairs:
        value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)

    return value


def clean_url(url):
    url = html_lib.unescape(url or "")
    url = url.strip().rstrip(").,;]\n\r\t ")
    return url


def extract_urls(body_text, body_html):
    candidates = []

    html_prepared = prepare_text_for_url_detection(body_html)
    text_prepared = prepare_text_for_url_detection(body_text)

    for match in HREF_RE.findall(html_prepared):
        candidates.append(match)

    for match in URL_RE.findall(text_prepared):
        candidates.append(match)

    for match in URL_RE.findall(html_prepared):
        candidates.append(match)

    urls = []
    bad_incomplete = {
        "https://www.ideali",
        "https://ideali",
        "https://www.iono",
        "https://my.iono",
        "https://mail.iono",
    }

    for candidate in candidates:
        url = clean_url(candidate)
        if not url:
            continue

        if url.lower() in bad_incomplete:
            continue

        if url not in urls:
            urls.append(url)

    return urls


class Command(BaseCommand):
    help = "Sincroniza correos IMAP hacia Inbox Email de SOOI."

    def add_arguments(self, parser):
        parser.add_argument("--account-id", type=int, default=None)
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument("--only-unseen", action="store_true")
        parser.add_argument("--update-existing", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        account_id = options["account_id"]
        limit = options["limit"]
        only_unseen = options["only_unseen"]
        update_existing = options["update_existing"]
        dry_run = options["dry_run"]

        accounts = EmailAccount.objects.filter(is_active=True)
        if account_id:
            accounts = accounts.filter(id=account_id)

        if not accounts.exists():
            self.stdout.write(self.style.WARNING("No hay cuentas activas para sincronizar."))
            return

        for account in accounts:
            self.stdout.write("")
            self.stdout.write(f"== Sincronizando cuenta #{account.id}: {account.email_address} ==")

            if not account.imap_host or not account.imap_username or not account.imap_password:
                self.stdout.write(self.style.ERROR("Cuenta incompleta: falta host, usuario o contraseña IMAP."))
                continue

            try:
                if account.imap_use_ssl:
                    client = imaplib.IMAP4_SSL(account.imap_host, account.imap_port)
                else:
                    client = imaplib.IMAP4(account.imap_host, account.imap_port)

                client.login(account.imap_username, account.imap_password)
                client.select("INBOX")

                criteria = "UNSEEN" if only_unseen else "ALL"
                status, data = client.search(None, criteria)

                if status != "OK":
                    self.stdout.write(self.style.ERROR(f"No se pudo buscar correos: {status}"))
                    client.logout()
                    continue

                ids = data[0].split()
                ids = ids[-limit:]

                created_count = 0
                updated_count = 0
                skipped_count = 0

                for msg_num in ids:
                    status, msg_data = client.fetch(msg_num, "(RFC822)")
                    if status != "OK" or not msg_data or not msg_data[0]:
                        skipped_count += 1
                        continue

                    raw_email = msg_data[0][1]
                    msg = email.message_from_bytes(raw_email)

                    subject = decode_mime(msg.get("Subject", ""))
                    from_header = decode_mime(msg.get("From", ""))
                    from_name, from_addr = parseaddr(from_header)
                    message_id = (msg.get("Message-ID") or "").strip()
                    uid = msg_num.decode("utf-8", errors="replace")

                    received_at = timezone.now()
                    date_header = msg.get("Date")
                    if date_header:
                        try:
                            parsed = parsedate_to_datetime(date_header)
                            if parsed:
                                received_at = parsed
                                if timezone.is_naive(received_at):
                                    received_at = timezone.make_aware(received_at)
                        except Exception:
                            pass

                    body_text, body_html = extract_body_text_and_html(msg)
                    snippet = body_text[:240]
                    detected_urls = extract_urls(body_text, body_html)

                    existing = InboundEmail.objects.filter(
                        account=account,
                        message_uid=uid,
                    ).first()

                    if existing is None and message_id:
                        existing = InboundEmail.objects.filter(
                            account=account,
                            message_id=message_id,
                        ).first()

                    payload = {
                        "subject": subject[:255],
                        "from_name": (from_name or from_header)[:180],
                        "from_email": from_addr or "",
                        "received_at": received_at,
                        "snippet": snippet,
                        "body_text": body_text,
                        "detected_urls": detected_urls,
                        "raw_metadata": {
                            "from_header": from_header,
                            "sync_source": "imap",
                        },
                    }

                    if existing:
                        if update_existing and not dry_run:
                            for field, value in payload.items():
                                setattr(existing, field, value)

                            existing.save(update_fields=[
                                "subject",
                                "from_name",
                                "from_email",
                                "received_at",
                                "snippet",
                                "body_text",
                                "detected_urls",
                                "raw_metadata",
                                "updated_at",
                            ])
                            updated_count += 1
                        else:
                            skipped_count += 1
                        continue

                    self.stdout.write(f"- Nuevo: {subject or '(Sin asunto)'}")

                    if not dry_run:
                        InboundEmail.objects.create(
                            owner=account.owner,
                            account=account,
                            status=InboundEmail.Status.NEW,
                            message_uid=uid,
                            message_id=message_id,
                            **payload,
                        )
                        created_count += 1

                if not dry_run:
                    account.last_sync_at = timezone.now()
                    account.save(update_fields=["last_sync_at", "updated_at"])

                client.logout()

                self.stdout.write(
                    self.style.SUCCESS(
                        f"Cuenta sincronizada. Nuevos: {created_count}. Actualizados: {updated_count}. Omitidos: {skipped_count}."
                    )
                )

            except imaplib.IMAP4.error as exc:
                self.stdout.write(self.style.ERROR(f"Error IMAP: {exc}"))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"Error inesperado: {exc}"))
