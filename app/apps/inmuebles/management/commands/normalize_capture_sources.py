import re
import unicodedata
from urllib.parse import urlparse

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.inmuebles.models import CapturedProperty


class Command(BaseCommand):
    help = (
        "Normaliza CapturedProperty.source a partir de source_url "
        "sin tocar entry_mode."
    )

    DOMAIN_SOURCE_CANDIDATES = [
        (("idealista.com",), ["idealista"]),
        (("pisos.com",), ["pisos.com", "pisos"]),
        (("servihabitat.com",), ["servihabitat"]),
        (("fotocasa.es",), ["fotocasa"]),
        (("habitaclia.com",), ["habitaclia"]),
        (("yaencontre.com",), ["yaencontre"]),
        (("solvia.es",), ["solvia"]),
        (("haya.es",), ["haya"]),
        (("altamirainmuebles.com",), ["altamira"]),
        (("globaliza.com",), ["globaliza"]),
    ]

    SOURCE_TEXT_FIELD_CANDIDATES = [
        "slug",
        "code",
        "codigo",
        "name",
        "nombre",
        "label",
        "display_name",
        "domain",
        "canonical_domain",
        "website",
        "url",
    ]

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simula cambios sin guardar en BD.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Limita el número de registros a revisar.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]

        qs = CapturedProperty.objects.select_related("source").all().order_by("id")
        if limit:
            qs = qs[:limit]

        source_model = CapturedProperty._meta.get_field("source").remote_field.model
        source_instances = list(source_model.objects.all())

        total = 0
        corrected = 0
        ignored = 0
        invalid_url = 0

        to_update = []

        for obj in qs:
            total += 1

            host = self._extract_host(obj.source_url)
            if not host:
                invalid_url += 1
                self._log_invalid(obj)
                continue

            candidate_labels = self._detect_source_candidates(host)
            if not candidate_labels:
                ignored += 1
                self._log_ignored(obj, host, reason="dominio no reconocido")
                continue

            target_source = self._resolve_target_source_instance(
                candidate_labels=candidate_labels,
                source_instances=source_instances,
            )

            if not target_source:
                ignored += 1
                self._log_ignored(
                    obj,
                    host,
                    reason=f"no existe Source compatible para {candidate_labels}",
                )
                continue

            current_source_id = getattr(obj, "source_id", None)
            if current_source_id == target_source.pk:
                ignored += 1
                self._log_ignored(obj, host, reason="ya estaba correcto")
                continue

            corrected += 1

            message = (
                f"{'[DRY-RUN]' if dry_run else '[UPDATE]'} "
                f"id={obj.id} | host={host} | "
                f"source: {obj.source!r} -> {target_source!r} | "
                f"entry_mode={getattr(obj, 'entry_mode', None)!r}"
            )
            self.stdout.write(self.style.WARNING(message))

            if not dry_run:
                obj.source = target_source
                to_update.append(obj)

        if not dry_run and to_update:
            with transaction.atomic():
                CapturedProperty.objects.bulk_update(
                    to_update,
                    ["source"],
                    batch_size=500,
                )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Resumen final"))
        self.stdout.write(f"  Total revisados: {total}")
        self.stdout.write(f"  Corregidos: {corrected}")
        self.stdout.write(f"  Ignorados: {ignored}")
        self.stdout.write(f"  Sin URL válida: {invalid_url}")
        self.stdout.write(f"  Modo: {'DRY-RUN' if dry_run else 'EJECUCIÓN REAL'}")

    def _log_invalid(self, obj):
        self.stdout.write(
            self.style.ERROR(
                f"[INVALID_URL] id={obj.id} | source_url={obj.source_url!r}"
            )
        )

    def _log_ignored(self, obj, host, reason):
        self.stdout.write(
            f"[IGNORED] id={obj.id} | host={host} | reason={reason}"
        )

    def _extract_host(self, url_value):
        if not url_value:
            return None

        raw = str(url_value).strip()
        if not raw:
            return None

        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw):
            raw = f"https://{raw}"

        try:
            parsed = urlparse(raw)
        except Exception:
            return None

        host = parsed.hostname
        if not host:
            return None

        host = host.lower().strip()
        if host.startswith("www."):
            host = host[4:]

        return host or None

    def _detect_source_candidates(self, host):
        for domains, candidates in self.DOMAIN_SOURCE_CANDIDATES:
            for domain in domains:
                if host == domain or host.endswith(f".{domain}"):
                    return candidates
        return None

    def _resolve_target_source_instance(self, candidate_labels, source_instances):
        candidate_norms = [self._normalize_text(x) for x in candidate_labels]

        # 1) match exacto
        for candidate in candidate_norms:
            for source in source_instances:
                for value in self._source_search_values(source):
                    if candidate == value:
                        return source

        # 2) match flexible por inclusión
        for candidate in candidate_norms:
            for source in source_instances:
                for value in self._source_search_values(source):
                    if candidate and (candidate in value or value in candidate):
                        return source

        return None

    def _source_search_values(self, source):
        values = set()

        for field_name in self.SOURCE_TEXT_FIELD_CANDIDATES:
            if hasattr(source, field_name):
                raw = getattr(source, field_name, None)
                if raw:
                    values.add(self._normalize_text(raw))

        values.add(self._normalize_text(str(source)))

        return values

    def _normalize_text(self, value):
        if value is None:
            return ""

        text = str(value).strip().lower()
        text = unicodedata.normalize("NFKD", text)
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        text = re.sub(r"[^a-z0-9]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text