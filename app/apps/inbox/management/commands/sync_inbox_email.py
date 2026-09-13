import email
import html as html_lib
import imaplib
import json
import re
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime, parseaddr, getaddresses

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.inbox.models import EmailAccount, InboundEmail
from apps.busquedas.models import SearchProfile
from apps.core.observability import emit, new_correlation_id
from apps.inbox.secrets import ImapSecretError, resolve_imap_secret


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


CAPTURE_ALIAS_RE = re.compile(
    r"^(?:(?:captaciones\+(?:busqueda-?|b|sp|search-?)?)|(?:busqueda-?|b|sp|search-?))(?P<search_id>\d+)@",
    re.IGNORECASE,
)


def extract_recipient_addresses(message):
    header_names = [
        "To",
        "Cc",
        "Delivered-To",
        "X-Original-To",
        "Envelope-To",
        "Apparently-To",
    ]

    addresses = []
    raw_headers = {}

    for header_name in header_names:
        values = message.get_all(header_name, [])
        decoded_values = [decode_mime(value) for value in values if value]

        if decoded_values:
            raw_headers[header_name] = decoded_values

        for _, addr in getaddresses(decoded_values):
            addr = (addr or "").strip().lower()
            if addr and addr not in addresses:
                addresses.append(addr)

    return addresses, raw_headers


def resolve_search_profile_from_recipients(recipient_addresses):
    for addr in recipient_addresses or []:
        match = CAPTURE_ALIAS_RE.match(addr)
        if not match:
            continue

        search_id = match.group("search_id")

        try:
            return SearchProfile.objects.select_related("owner").get(pk=search_id)
        except SearchProfile.DoesNotExist:
            return None

    return None


def persist_message(*, account, owner, search_profile, uid, message_id, payload,
                    update_existing=False, dry_run=False):
    """Serialize per-account upserts so retries cannot create two messages."""
    if dry_run:
        exists = InboundEmail.objects.filter(account=account, message_uid=uid).exists()
        return "skipped" if exists else "dry_run"

    with transaction.atomic():
        locked_account = EmailAccount.objects.select_for_update().get(pk=account.pk)
        existing = InboundEmail.objects.filter(
            account=locked_account, message_uid=uid,
        ).first()
        if existing is None and message_id:
            existing = InboundEmail.objects.filter(
                account=locked_account, message_id=message_id,
            ).first()

        if existing:
            if not update_existing:
                return "skipped"
            for field, value in payload.items():
                setattr(existing, field, value)
            existing.owner = owner
            existing.search_profile = search_profile
            existing.save(update_fields=[
                "subject", "from_name", "from_email", "received_at", "snippet",
                "body_text", "detected_urls", "raw_metadata", "owner",
                "search_profile", "updated_at",
            ])
            return "updated"

        InboundEmail.objects.create(
            owner=owner, account=locked_account, search_profile=search_profile,
            status=InboundEmail.Status.NEW, message_uid=uid,
            message_id=message_id, **payload,
        )
        return "created"


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
        correlation_id = new_correlation_id()

        accounts = EmailAccount.objects.filter(is_active=True)
        if account_id:
            accounts = accounts.filter(id=account_id)

        if not accounts.exists():
            self.stdout.write(self.style.WARNING("No hay cuentas activas para sincronizar."))
            return json.dumps({"status": "failure", "accounts": [], "reason_code": "not_found"})

        results = []
        for account in accounts:
            self.stdout.write("")
            self.stdout.write(f"== Sincronizando cuenta técnica #{account.id} ==")

            if not account.imap_host or not account.imap_username or not account.imap_secret_ref:
                self.stdout.write(self.style.ERROR("Cuenta técnica incompleta."))
                results.append({"account_id": account.pk, "status": "failure", "reason_code": "account_incomplete"})
                continue

            try:
                imap_secret = resolve_imap_secret(account.imap_secret_ref)
                if account.imap_use_ssl:
                    client = imaplib.IMAP4_SSL(account.imap_host, account.imap_port)
                else:
                    client = imaplib.IMAP4(account.imap_host, account.imap_port)

                client.login(account.imap_username, imap_secret)
                client.select("INBOX")

                criteria = "UNSEEN" if only_unseen else "ALL"
                status, data = client.search(None, criteria)

                if status != "OK":
                    self.stdout.write(self.style.ERROR("No se pudo buscar correo; consulte observabilidad técnica."))
                    client.logout()
                    results.append({"account_id": account.pk, "status": "retry", "reason_code": "provider_unavailable"})
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
                    recipient_addresses, recipient_headers = extract_recipient_addresses(msg)
                    resolved_search_profile = resolve_search_profile_from_recipients(recipient_addresses)
                    resolved_owner = resolved_search_profile.owner if resolved_search_profile else account.owner

                    # An account is an ownership boundary. Aliases may only
                    # resolve profiles owned by the same account owner.
                    if resolved_owner.pk != account.owner_id:
                        emit("imap_message", "failure", correlation_id, component="imap",
                             operation="resolve_owner", object_type="email_account",
                             object_id=account.pk, owner_id=account.owner_id,
                             reason_code="cross_owner_alias")
                        skipped_count += 1
                        continue

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
                            "recipient_addresses": recipient_addresses,
                            "recipient_headers": recipient_headers,
                            "sync_source": "imap",
                        },
                    }

                    outcome = persist_message(
                        account=account, owner=resolved_owner,
                        search_profile=resolved_search_profile, uid=uid,
                        message_id=message_id, payload=payload,
                        update_existing=update_existing, dry_run=dry_run,
                    )
                    if outcome == "created":
                        self.stdout.write("- Nuevo mensaje detectado")
                        created_count += 1
                    elif outcome == "updated":
                        updated_count += 1
                    else:
                        skipped_count += 1

                if not dry_run:
                    account.last_sync_at = timezone.now()
                    account.save(update_fields=["last_sync_at", "updated_at"])

                client.logout()

                self.stdout.write(
                    self.style.SUCCESS(
                        f"Cuenta sincronizada. Nuevos: {created_count}. Actualizados: {updated_count}. Omitidos: {skipped_count}."
                    )
                )
                emit("inbox_sync", "success", correlation_id, component="imap",
                     operation="sync", object_type="email_account", object_id=account.pk,
                     owner_id=account.owner_id, count=created_count + updated_count)
                results.append({"account_id": account.pk, "status": "success", "count": created_count + updated_count})

            except ImapSecretError as exc:
                self.stdout.write(self.style.ERROR("Secreto IMAP no disponible; sincronización bloqueada."))
                emit("inbox_sync", "failure", correlation_id, component="imap",
                     operation="sync", object_type="email_account", object_id=account.pk,
                     owner_id=account.owner_id, reason_code=exc.code)
                results.append({"account_id": account.pk, "status": "failure", "reason_code": exc.code})
            except imaplib.IMAP4.error:
                self.stdout.write(self.style.ERROR("Error IMAP; consulte observabilidad técnica."))
                emit("inbox_sync", "retry", correlation_id, component="imap",
                     operation="sync", object_type="email_account", object_id=account.pk,
                     owner_id=account.owner_id, reason_code="provider_unavailable")
                results.append({"account_id": account.pk, "status": "retry", "reason_code": "provider_unavailable"})
            except Exception:
                self.stdout.write(self.style.ERROR("Error inesperado; consulte observabilidad técnica."))
                emit("inbox_sync", "failure", correlation_id, component="imap",
                     operation="sync", object_type="email_account", object_id=account.pk,
                     owner_id=account.owner_id, reason_code="unexpected_error")
                results.append({"account_id": account.pk, "status": "failure", "reason_code": "unexpected_error"})

        states = {item["status"] for item in results}
        if states == {"success"}:
            overall = "success"
        elif "success" in states:
            overall = "partial"
        elif "retry" in states and "failure" not in states:
            overall = "retry"
        else:
            overall = "failure"
        return json.dumps({"status": overall, "accounts": results}, sort_keys=True)
